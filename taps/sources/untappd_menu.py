"""Source 4.1: Untappd venue menu (all beer tabs)."""
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from bs4 import BeautifulSoup, Tag

from taps.config import Place
from taps.fetch import FetchError, UntappdClient
from taps.model import Sighting, SourceResult, u_key
from taps.sources.untappd_checkins import parse_venue_meta
from taps.timeutil import parse_iso

SKIP_TAB_RE = re.compile(r"(food|wine|cocktail|spirit|kitchen|кухн|вино)", re.I)
BEER_HREF_RE = re.compile(r"^/b/[^/]+/(\d+)/?$")
BREWERY_HREF_RE = re.compile(r"^/w/[^/]+/(\d+)/?$")
ABV_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*ABV", re.I)
IBU_RE = re.compile(r"(\d+(?:\.\d+)?)\s*IBU", re.I)
NUMBERING_RE = re.compile(r"^\d+\.\s+")   # "1. Hell" in numbered sections
SECTION_COUNT_RE = re.compile(r"\s*\(\s*\d+\s*items?\s*\)$", re.I)
AMD_RE = re.compile(r"AMD|֏|դր", re.I)
PRICE_RE = re.compile(r"\d[\d\s,]*(?:\.\d+)?")
VOLUME_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(ml|cl|l)\b")
CONTAINER_RE = re.compile(r"\b(draft|can|bottle|keg)\b")
ML_PER_UNIT = {"ml": 1, "cl": 10, "l": 1000}


@dataclass(frozen=True)
class MenuItem:
    beer_id: int
    name: str
    brewery: str
    brewery_id: int | None
    style: str | None
    abv: float | None
    ibu: int | None
    rating: float | None
    price_amd: int | None
    volume_ml: int | None
    container: str | None
    section: str
    url: str = ""                 # canonical /b/<slug>/<id>, from the row's own beer link
    logo: str | None = None       # beer label image (div.beer-label img)


@dataclass(frozen=True)
class MenuPage:
    tabs: list[tuple[str, str]]   # (menu_id, tab name) in selector order
    active_menu_id: str | None    # tab shown on this page; None if unknown or single-menu venue
    updated_at: datetime | None
    items: list[MenuItem]


def _text(tag: Tag | None) -> str:
    return " ".join(tag.get_text(" ").split()) if tag else ""


def _updated_at(soup: BeautifulSoup) -> datetime | None:
    span = soup.select_one("span.updated-time[data-time]")
    try:
        return parse_iso(span["data-time"]) if span else None
    except ValueError:
        return None


def _tabs(soup: BeautifulSoup) -> tuple[list[tuple[str, str]], str | None]:
    options = [o for o in soup.select("select.menu-selector option[value]") if o["value"].strip()]
    tabs = [(o["value"].strip(), _text(o)) for o in options]
    selected = [o["value"].strip() for o in options if o.has_attr("selected")]
    if selected:
        return tabs, selected[0]
    # The default venue page selects no option; p.menu-total holds the shown tab's name.
    shown = _text(soup.select_one("p.menu-total"))
    matches = [menu_id for menu_id, name in tabs if name == shown]
    return tabs, matches[0] if len(matches) == 1 else None


def _rating(li: Tag) -> float | None:
    caps = li.select_one("div.caps[data-rating]")
    try:
        rating = float(caps["data-rating"]) if caps else None
    except ValueError:
        return None
    return rating or None   # 0 means no ratings yet


def _price(li: Tag) -> tuple[int | None, int | None, str | None]:
    """(price_amd, volume_ml, container) from the first AMD price row, else from the first row."""
    rows = li.select("div.beer-prices p")
    if not rows:
        return None, None, None
    row = next((p for p in rows if AMD_RE.search(_text(p.select_one("span.price")))), rows[0])
    price_text = _text(row.select_one("span.price"))
    m = PRICE_RE.search(price_text) if AMD_RE.search(price_text) else None
    price = round(float(re.sub(r"[\s,]", "", m.group()))) if m else None
    size = _text(row.select_one("span.size")).lower()
    v = VOLUME_RE.search(size)
    volume = round(float(v.group(1).replace(",", ".")) * ML_PER_UNIT[v.group(2)]) if v else None
    c = CONTAINER_RE.search(size)
    return price, volume, c.group(1) if c else None


def _item(li: Tag, section: str) -> MenuItem | None:
    link = li.select_one("h5 a[href]")
    m = BEER_HREF_RE.match(link["href"]) if link else None
    if m is None:
        return None   # food and other items without an Untappd beer link
    stats = li.select_one("h6 span")   # "4.8% ABV • 20 IBU • <a>Dahook</a> •"
    stats_text = _text(stats)
    brewery_link = stats.find("a", href=True) if stats else None
    if brewery_link:
        brewery = _text(brewery_link)
    else:   # unlinked brewery: the last "•" part that is not ABV or IBU
        parts = [p.strip() for p in stats_text.split("•")]
        brewery = next((p for p in reversed(parts) if p and "ABV" not in p and "IBU" not in p), "")
    bm = BREWERY_HREF_RE.match(brewery_link["href"]) if brewery_link else None
    abv, ibu = ABV_RE.search(stats_text), IBU_RE.search(stats_text)
    price, volume, container = _price(li)
    logo = li.select_one("div.beer-label img[src]")
    return MenuItem(
        beer_id=int(m.group(1)),
        name=NUMBERING_RE.sub("", _text(link)),
        brewery=brewery,
        brewery_id=int(bm.group(1)) if bm else None,
        style=_text(li.select_one("h5 em")) or None,
        abv=float(abv.group(1)) if abv else None,
        ibu=round(float(ibu.group(1))) if ibu else None,
        rating=_rating(li),
        price_amd=price,
        volume_ml=volume,
        container=container,
        section=section,
        url=f"https://untappd.com{link['href'].rstrip('/')}",   # canonical /b/<slug>/<id>: opens in the app
        logo=logo["src"] if logo else None,
    )


def parse_menu_page(html: str) -> MenuPage:
    soup = BeautifulSoup(html, "html.parser")
    tabs, active = _tabs(soup)
    items = []
    for sec in soup.select("div.menu-section"):
        section = SECTION_COUNT_RE.sub("", _text(sec.select_one(".menu-section-header h4")))
        items += [item for li in sec.select("li.menu-item") if (item := _item(li, section))]
    return MenuPage(tabs=tabs, active_menu_id=active, updated_at=_updated_at(soup), items=items)


def fetch_menu(client: UntappdClient, place: Place, now: datetime,
               brewery_aliases: Mapping[str, str]) -> SourceResult:
    """Venue page plus every other beer tab via ?menu_id=. One failed page fails the whole menu.

    brewery_aliases is unused: menu keys are u:<beer_id>.
    """
    key = f"untappd_menu:{place.id}"
    params = place.sources["untappd_menu"]
    url = f"https://untappd.com/v/{params['slug']}/{params['venue_id']}"
    try:
        first_html = client.get(url)
        first = parse_menu_page(first_html)
        # A single-menu venue page is the menu. Otherwise read every beer tab in selector order;
        # the venue page stands for the tab it shows and is not used when that tab is unknown.
        pages = [] if first.tabs else [(first.active_menu_id, first)]
        for menu_id, name in first.tabs:
            if SKIP_TAB_RE.search(name):
                continue
            if menu_id == first.active_menu_id:
                pages.append((menu_id, first))
            else:
                pages.append((menu_id, parse_menu_page(client.get(f"{url}?menu_id={menu_id}"))))
    except FetchError as e:
        return SourceResult(key=key, source="untappd_menu", ok=False, error=e.kind, place_id=place.id)

    meta = parse_venue_meta(first_html)
    venue_meta = {"venue_id": params["venue_id"], "name": place.name, "url": url, **meta} if meta else None
    sightings, seen = [], set()
    for menu_id, page in pages:
        for it in page.items:
            if it.beer_id in seen:
                continue   # listed twice (on tap and in bottles, or in two sections): first row wins
            seen.add(it.beer_id)
            sightings.append(Sighting(
                place_id=place.id, source="untappd_menu", beer_key=u_key(it.beer_id),
                title=f"{it.brewery} {it.name}".strip(), name=it.name, seen_at=now,
                brewery=it.brewery or None, brewery_id=it.brewery_id, untappd_beer_id=it.beer_id,
                style=it.style, abv=it.abv, ibu=it.ibu, rating=it.rating, price_amd=it.price_amd,
                volume_ml=it.volume_ml, container=it.container, menu_id=menu_id,
                url=it.url, logo=it.logo,
            ))
    return SourceResult(
        key=key, source="untappd_menu", ok=bool(sightings), sightings=sightings,
        error=None if sightings else "empty", place_id=place.id,
        menu_updated_at=max((p.updated_at for _, p in pages if p.updated_at), default=None),
        venue_meta=venue_meta,
    )
