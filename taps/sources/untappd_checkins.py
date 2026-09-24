"""Source 4.3 (👀): recent check-ins on an Untappd venue page; the parser also serves brewery pages (4.2)."""
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup, Tag

from taps.config import Config, Place
from taps.fetch import FetchError, UntappdClient
from taps.model import Sighting, SourceResult, VenueCheckin, u_key

BEER_HREF_RE = re.compile(r"^/b/[^/]+/(\d+)/?$")
VENUE_HREF_RE = re.compile(r"^/v/[^/]+/(\d+)/?$")
AT_HOME = "untappd at home"


@dataclass(frozen=True)
class Checkin:
    checkin_id: int
    beer_id: int
    beer_name: str
    brewery: str
    venue_id: int | None
    venue_name: str | None
    serving: str | None
    at_home: bool
    created_at: datetime
    venue_url: str | None = None
    beer_url: str | None = None   # canonical /b/<slug>/<id> link from the check-in's own beer href
    logo: str | None = None       # beer label image (a.label img, before p.text)


def _text(tag: Tag | None) -> str:
    return " ".join(tag.get_text(" ").split()) if tag else ""


def _created_at(text: str) -> datetime | None:
    """Server HTML has 'Thu, 31 Oct 2024 15:50:09 +0000'. In a real browser the page script
    refreshTime(".timezoner", "D MMM YY") rewrites it to '31 Oct 24', a browser-local date:
    that is read as 00:00 UTC of that day."""
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            return datetime.strptime(text, "%d %b %y").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    if dt.tzinfo is None:   # RFC 2822 "-0000": UTC, source zone unknown
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_item(item: Tag) -> Checkin | None:
    """One div.item; None when the beer link or a readable time is missing.

    Only links inside p.text count: p.purchased also holds a /v/ link (where it was bought)."""
    text = item.select_one("p.text")
    created_at = _created_at(_text(item.select_one("a.time")))
    if text is None or created_at is None:
        return None
    beer = venue = brewery = None
    for a in text.find_all("a", href=True):   # user, beer, brewery, venue - in this order
        if beer is None:
            if m := BEER_HREF_RE.match(a["href"]):
                beer = (int(m.group(1)), _text(a), a["href"])
        elif m := VENUE_HREF_RE.match(a["href"]):
            venue = (int(m.group(1)), _text(a), a["href"])
        elif brewery is None:
            brewery = _text(a)
    if beer is None:
        return None
    serving = _text(item.select_one("p.serving span")) or None
    logo = item.select_one("a.label img[src]")
    return Checkin(
        checkin_id=int(item["data-checkin-id"]),
        beer_id=beer[0],
        beer_name=beer[1],
        brewery=brewery or "",
        venue_id=venue[0] if venue else None,
        venue_name=venue[1] if venue else None,
        serving=serving,
        at_home=venue is not None and AT_HOME in venue[1].lower(),
        created_at=created_at,
        venue_url=f"https://untappd.com{venue[2]}" if venue else None,
        beer_url=f"https://untappd.com{beer[2]}",
        logo=logo["src"] if logo else None,
    )


def parse_checkins(html: str) -> list[Checkin]:
    """Check-ins of a venue or brewery page in page order, each id once.

    Venue pages may also have a compact div.venue-activity list: it repeats the same
    check-ins without venue and serving, so only div.item is read."""
    soup = BeautifulSoup(html, "html.parser")
    seen, out = set(), []
    for item in soup.select("div.item[data-checkin-id]"):
        cid = item["data-checkin-id"]
        if not cid.isdigit() or cid in seen:
            continue
        if checkin := _parse_item(item):
            seen.add(cid)
            out.append(checkin)
    return out


def checkins_to_venue_checkins(checkins: list[Checkin]) -> list[VenueCheckin]:
    """Every venue seen in check-ins (v1.1 discovery), tracked or not; "Untappd at Home" is skipped."""
    return [
        VenueCheckin(venue_id=c.venue_id, venue_name=c.venue_name, venue_url=c.venue_url,
                    checkin_id=c.checkin_id, at=c.created_at)
        for c in checkins if c.venue_id is not None and not c.at_home
    ]


GENERIC_LOGO_HOST = "ss3.4sqi.net"           # Foursquare's generic category icon, not a real venue logo
GENERIC_LOGO_PATH = "/img/categories_v2/"


def _is_generic_category_icon(url: str) -> bool:
    return GENERIC_LOGO_HOST in url and GENERIC_LOGO_PATH in url


def parse_venue_meta(html: str) -> dict | None:
    """logo and the Untappd "Verified" badge from a venue page's own header; None if the header is missing.
    A generic Foursquare category icon (venues Untappd has no real photo for) is reported as no logo (M-6)."""
    soup = BeautifulSoup(html, "html.parser")
    logo_div = soup.select_one("div.venue-header div.logo")
    if logo_div is None:
        return None
    img = logo_div.select_one("img[src]")
    src = img["src"] if img else None
    verified = logo_div.find("span", string="Verified") is not None
    return {"logo": None if src and _is_generic_category_icon(src) else src, "verified": verified}


ARMENIA_LOCALITY_MARKERS = ("yerevan", "ереван", "երևան")
# M-1: Untappd's JSON-LD has no addressCountry for Armenian venues we've seen -- addressLocality packs
# city and country together with no separator, e.g. "Yerevan Հայաստան" or "Gyumri Հայաստան".
COUNTRY_SUFFIX_MARKERS = ("armenia", "հայաստան")


def _split_locality(locality: str) -> tuple[str, str | None]:
    """Split a trailing Armenia spelling off addressLocality (M-1), e.g. "Gyumri Հայաստան" -> ("Gyumri",
    "Armenia"). Left alone (no split) when the trailing word isn't a recognized country spelling."""
    city, _, suffix = locality.rpartition(" ")
    if city and suffix.lower() in COUNTRY_SUFFIX_MARKERS:
        return city, "Armenia"
    return locality, None


def parse_venue_location(html: str) -> dict | None:
    """City/country from the venue's own JSON-LD "Location" block (v1.1 city check, §4); None if the
    page carries no such block. A JSON-LD script may hold a single object or a list of them (I-1); any
    entry that isn't a dict, or whose "address" isn't a dict, is skipped rather than raising."""
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.get_text())
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if not isinstance(item, dict) or item.get("@type") != "Location":
                continue
            address = item.get("address")
            if not isinstance(address, dict):
                continue
            locality, country = address.get("addressLocality"), address.get("addressCountry")
            if isinstance(locality, str) and not country:
                locality, country = _split_locality(locality)
            if locality or country:
                return {"locality": locality, "country": country}
    return None


def is_armenia_location(loc: dict | None) -> bool:
    """True when parse_venue_location() found an Armenian address: addressCountry if present (a foreign
    venue is expected to carry one), else a Yerevan spelling in addressLocality."""
    if loc is None:
        return False
    country = (loc.get("country") or "").strip().lower()
    if country:
        return country in ("armenia", "am")
    locality = (loc.get("locality") or "").lower()
    return any(marker in locality for marker in ARMENIA_LOCALITY_MARKERS)


def is_yerevan_city(city: str | None) -> bool:
    """True when city (rec.city, already split from addressLocality) is a Yerevan spelling (I-3): other
    Armenian cities (Gyumri, ...) are hidden from "Все места" and the weekly report just like foreign
    venues -- the project's scope is Yerevan, not Armenia."""
    if not city:
        return False
    return any(marker in city.lower() for marker in ARMENIA_LOCALITY_MARKERS)


def checkins_to_sightings(checkins: list[Checkin], config: Config, source: str, now: datetime,
                          brewery_aliases: Mapping[str, str]) -> list[Sighting]:
    """Sightings for check-ins at enabled places; other venues and check-ins without a venue are dropped.

    now and brewery_aliases are unused: seen_at is the check-in time and keys are u:<beer_id>.
    Rating/style/ABV stay empty: a check-in only carries one person's rating."""
    out = []
    for c in checkins:
        place = config.place_by_venue(c.venue_id) if c.venue_id is not None else None
        if place is None:
            continue
        out.append(Sighting(
            place_id=place.id, source=source, beer_key=u_key(c.beer_id),
            title=f"{c.brewery} {c.beer_name}".strip(), name=c.beer_name, seen_at=c.created_at,
            brewery=c.brewery or None, untappd_beer_id=c.beer_id, serving=c.serving,
            url=c.beer_url or f"https://untappd.com/beer/{c.beer_id}", checkin_id=c.checkin_id, at_home=c.at_home,
            logo=c.logo,
        ))
    return out


def fetch_venue_checkins(client: UntappdClient, place: Place, config: Config, now: datetime,
                         brewery_aliases: Mapping[str, str]) -> SourceResult:
    """v1.1: a place may list several Untappd venues (place.sources["untappd_checkins"]["venues"]); each
    is fetched in turn and their check-ins merged into one result. Any single venue's failure fails the
    whole (small, fixed-size) result rather than risk a silently incomplete merge."""
    checkins_source = place.sources["untappd_checkins"]
    venues = checkins_source["venues"] if "venues" in checkins_source else [checkins_source]
    result = SourceResult(key=f"untappd_checkins:{place.id}", source="untappd_checkins", ok=False,
                          place_id=place.id)
    all_checkins: list[Checkin] = []
    for v in venues:
        url = f"https://untappd.com/v/{v['slug']}/{v['venue_id']}"
        try:
            html = client.get(url)
        except FetchError as e:
            result.error = e.kind
            return result
        checkins = parse_checkins(html)
        if not checkins:
            result.error = "empty"
            return result
        if all(c.venue_id != v["venue_id"] for c in checkins):
            result.error = "bad_response"   # e.g. Untappd merged the venue and redirected to a new id
            return result
        all_checkins += checkins
        if result.venue_meta is None:   # the place's own venue: the first (main) one that carries a header
            meta = parse_venue_meta(html)
            if meta is not None:
                result.venue_meta = {"venue_id": v["venue_id"], "name": place.name, "url": url, **meta}
    result.sightings = checkins_to_sightings(all_checkins, config, "untappd_checkins", now, brewery_aliases)
    result.venue_checkins = checkins_to_venue_checkins(all_checkins)
    result.ok = True
    return result
