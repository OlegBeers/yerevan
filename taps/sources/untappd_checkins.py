"""Source 4.3 (👀): recent check-ins on an Untappd venue page; the parser also serves brewery pages (4.2)."""
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup, Tag

from taps.config import Config, Place
from taps.fetch import FetchError, UntappdClient
from taps.model import Sighting, SourceResult, u_key

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
                beer = (int(m.group(1)), _text(a))
        elif m := VENUE_HREF_RE.match(a["href"]):
            venue = (int(m.group(1)), _text(a))
        elif brewery is None:
            brewery = _text(a)
    if beer is None:
        return None
    serving = _text(item.select_one("p.serving span")) or None
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
            url=f"https://untappd.com/beer/{c.beer_id}", checkin_id=c.checkin_id, at_home=c.at_home,
        ))
    return out


def fetch_venue_checkins(client: UntappdClient, place: Place, config: Config, now: datetime,
                         brewery_aliases: Mapping[str, str]) -> SourceResult:
    params = place.sources["untappd_checkins"]
    result = SourceResult(key=f"untappd_checkins:{place.id}", source="untappd_checkins", ok=False,
                          place_id=place.id)
    try:
        html = client.get(f"https://untappd.com/v/{params['slug']}/{params['venue_id']}")
    except FetchError as e:
        result.error = e.kind
        return result
    checkins = parse_checkins(html)
    if not checkins:
        result.error = "empty"
        return result
    if all(c.venue_id != params["venue_id"] for c in checkins):
        result.error = "bad_response"   # e.g. Untappd merged the venue and redirected to a new id
        return result
    result.sightings = checkins_to_sightings(checkins, config, "untappd_checkins", now, brewery_aliases)
    result.ok = True
    return result
