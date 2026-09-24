"""Source 4.2: Untappd brewery pages: recent check-ins (👀) and the full beer list (🏭)."""
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime

from bs4 import BeautifulSoup, Tag

from taps.config import Brewery, Config
from taps.fetch import FetchError, UntappdClient
from taps.model import BreweryBeer, SourceResult
from taps.sources.untappd_checkins import checkins_to_sightings, parse_checkins

# The page links the list as /w/<slug>/<brewery_id>/beer. The slug in places.yaml is unverified for
# most breweries; that Untappd accepts it is checked on the live page at launch (list_enabled).
BEER_LIST_URL = "https://untappd.com/w/{slug}/{brewery_id}/beer"
BEER_HREF_RE = re.compile(r"^/b/[^/]+/(\d+)/?$")
TOTAL_RE = re.compile(r"^(\d[\d,]*)\s+Beers?$", re.I)
ABV_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")


@dataclass(frozen=True)
class BeerList:
    total: int | None          # "N Beers" counter of the brewery header; None if not found
    beers: list[BreweryBeer]   # brewery_id is 0 here, fetch_brewery_list sets it


def _text(tag: Tag | None) -> str:
    return " ".join(tag.get_text(" ").split()) if tag else ""


def fetch_brewery_checkins(client: UntappdClient, brewery: Brewery, config: Config, now: datetime,
                           brewery_aliases: Mapping[str, str]) -> SourceResult:
    """Recent check-ins from the brewery page, kept only at venues of enabled places."""
    key = f"untappd_brewery:{brewery.brewery_id}"
    try:
        html = client.get(brewery.url)
    except FetchError as e:
        return SourceResult(key=key, source="untappd_brewery", ok=False, error=e.kind,
                            brewery_id=brewery.brewery_id)
    checkins = parse_checkins(html)
    if not checkins:   # the page always lists recent check-ins: none means changed markup or a login wall
        return SourceResult(key=key, source="untappd_brewery", ok=False, error="empty",
                            brewery_id=brewery.brewery_id)
    sightings = checkins_to_sightings(checkins, config, "untappd_brewery", now, brewery_aliases)
    return SourceResult(key=key, source="untappd_brewery", ok=True, brewery_id=brewery.brewery_id,
                        sightings=[replace(s, brewery_id=brewery.brewery_id) for s in sightings])


def parse_beer_list(html: str) -> BeerList:
    """Brewery beer list page. Rows without a /b/ link or name are skipped, so the total check fails."""
    soup = BeautifulSoup(html, "html.parser")
    total = next((int(m.group(1).replace(",", "")) for p in soup.select("p.count")
                  if (m := TOTAL_RE.match(_text(p)))), None)
    brewery = _text(soup.select_one("div.name h1"))
    beers = []
    for item in soup.select("div.beer-item"):
        link = item.select_one("p.name a[href]")
        m = BEER_HREF_RE.match(link["href"]) if link else None
        if not m or not _text(link):
            continue
        abv = ABV_RE.search(_text(item.select_one("div.abv")))   # "N/A ABV" -> None
        beers.append(BreweryBeer(
            brewery_id=0, untappd_beer_id=int(m.group(1)), name=_text(link), brewery=brewery,
            style=_text(item.select_one("p.style")) or None,
            abv=float(abv.group(1)) if abv else None,
            url=f"https://untappd.com/beer/{m.group(1)}",
        ))
    return BeerList(total=total, beers=beers)


def fetch_brewery_list(client: UntappdClient, brewery: Brewery, now: datetime) -> SourceResult:
    """All beers of a brewery; ok only if the page shows as many beers as its "N Beers" counter.

    now is unused: the contract keeps it for symmetry with the other sources.
    """
    key = f"untappd_brewery_list:{brewery.brewery_id}"
    try:
        page = parse_beer_list(client.get(BEER_LIST_URL.format(slug=brewery.slug, brewery_id=brewery.brewery_id)))
    except FetchError as e:
        return SourceResult(key=key, source="untappd_brewery_list", ok=False, error=e.kind,
                            brewery_id=brewery.brewery_id)
    if page.total != len(page.beers):   # no counter (None), or e.g. only the first page without login
        return SourceResult(key=key, source="untappd_brewery_list", ok=False, error="incomplete",
                            brewery_id=brewery.brewery_id)
    beers = [replace(b, brewery_id=brewery.brewery_id, brewery=b.brewery or brewery.name) for b in page.beers]
    return SourceResult(key=key, source="untappd_brewery_list", ok=True, brewery_id=brewery.brewery_id,
                        brewery_beers=beers)
