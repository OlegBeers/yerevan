"""Source ✅ buy.am (spec §4.4): the «Draught beer» department of a restaurant.

The restaurant page's __NEXT_DATA__ lists departments with product counts but no products (the browser
loads them), so the page gives the ids and the site's own listing API gives the items.
"""
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup

from taps.config import Place
from taps.fetch import FetchError, Http
from taps.model import Sighting, SourceResult, untappd_n_key

DEPARTMENT = "draught beer"
LISTING_URL = "https://api.buy.am/products/listing?skip=0&s={supplier}&f={department}&take=100"
LISTING_HEADERS = {"Accept": "application/json", "Content-Language": "en"}
_DRAUGHT_RE = re.compile(r"^\s*draught\s+beer\b", re.I)
_VOLUME_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(ml|l)\b", re.I)


@dataclass(frozen=True)
class BuyamPage:
    supplier_id: int
    department_id: int
    products_count: int


@dataclass(frozen=True)
class BuyamItem:
    item_id: str
    name: str               # as on buy.am: "Draught beer Dargett Bohemian Pilsner 1l"
    price_amd: int | None


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _get(obj: Any, *keys: str) -> Any:
    for key in keys:
        obj = obj.get(key) if isinstance(obj, dict) else None
    return obj


def parse_page(html: str) -> BuyamPage:
    """Restaurant page -> supplier id and its «Draught beer» department; ValueError when missing."""
    tag = BeautifulSoup(html, "html.parser").find("script", id="__NEXT_DATA__")
    if tag is None or not tag.string:
        raise ValueError("no __NEXT_DATA__")
    restaurant = _get(json.loads(tag.string), "props", "pageProps", "restaurant")
    departments = _get(restaurant, "departments")
    if not _is_int(_get(restaurant, "id")) or not isinstance(departments, list):
        raise ValueError("no restaurant in __NEXT_DATA__")
    for dep in departments:
        name = _get(dep, "name")
        if isinstance(name, str) and name.strip().lower() == DEPARTMENT:
            if not (_is_int(dep.get("id")) and _is_int(dep.get("productsCount"))):
                raise ValueError(f"bad Draught beer department: {dep!r}")
            return BuyamPage(restaurant["id"], dep["id"], dep["productsCount"])
    raise ValueError("no Draught beer department")


def parse_buyam(text: str) -> list[BuyamItem]:
    """api.buy.am /products/listing JSON -> items; ValueError when the shape is wrong."""
    rows = _get(json.loads(text), "data", "items")
    if not isinstance(rows, list):
        raise ValueError("no data.items")
    items = []
    for row in rows:
        item_id, name, price = _get(row, "id"), _get(row, "nameEn") or _get(row, "name"), _get(row, "basePrice")
        if not _is_int(item_id) or not isinstance(name, str) or not name.strip():
            raise ValueError(f"bad item {item_id!r}")
        items.append(BuyamItem(str(item_id), name, price if _is_int(price) and price > 0 else None))
    return items


def clean_name(title: str, brewery: str | None) -> str:
    """'Draught beer Dargett Bohemian Pilsner 1l', 'Dargett' -> 'Bohemian Pilsner'."""
    name = _VOLUME_RE.sub(" ", _DRAUGHT_RE.sub("", title))
    if brewery:
        name = re.sub(rf"^\s*{re.escape(brewery)}(?!\w)", "", name, flags=re.I)
    return " ".join(name.split()) or title


def _volume_ml(title: str) -> int | None:
    m = _VOLUME_RE.search(title)
    if m is None:
        return None
    value = float(m.group(1).replace(",", "."))
    return round(value if m.group(2).lower() == "ml" else value * 1000)


def fetch_buyam(http: Http, place: Place, now: datetime, brewery_aliases: Mapping[str, str]) -> SourceResult:
    """Page for the ids, then the department listing; any failure discards the whole result."""
    key, url = f"buyam:{place.id}", place.sources["buyam"]["url"]

    def fail(error: str) -> SourceResult:
        return SourceResult(key=key, source="buyam", ok=False, error=error, place_id=place.id)

    try:
        page = parse_page(http.get(url).text)
        listing_url = LISTING_URL.format(supplier=page.supplier_id, department=page.department_id)
        items = parse_buyam(http.get(listing_url, headers=LISTING_HEADERS).text)
    except FetchError as e:
        return fail(e.kind)
    except ValueError:
        return fail("bad_response")
    if len(items) != page.products_count:   # e.g. the API ignored the department filter and sent the whole menu
        return fail("bad_response")

    sightings = []
    for item in items:
        name = clean_name(item.name, place.brewery_name)
        beer_key = untappd_n_key(place.brewery_name, name, brewery_aliases)
        if beer_key == "n:":
            continue
        sightings.append(Sighting(
            place_id=place.id, source="buyam", beer_key=beer_key, title=item.name, name=name, seen_at=now,
            brewery=place.brewery_name, price_amd=item.price_amd, volume_ml=_volume_ml(item.name),
            container="draft", url=url,
        ))
    return SourceResult(key=key, source="buyam", ok=True, sightings=sightings, place_id=place.id)
