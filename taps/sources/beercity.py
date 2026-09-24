"""Source 🛒 Beer City (spec §4.5): XHR catalog listing plus product pages for new item ids."""
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from taps.config import Place
from taps.fetch import FetchError, Http
from taps.model import Sighting, SourceResult, n_key

BASE = "https://www.beer-city.am"
LIST_URL = BASE + "/en/catalog/{category}/?sorting=-id&page={page}"
CATEGORIES = ("sshalcavac-garejur", "lcnovi-garejur")   # bottles and cans; draught to take away
DRAFT_CATEGORY = "lcnovi-garejur"
XHR = {"X-Requested-With": "XMLHttpRequest"}
MAX_PAGES = 100   # sanity bound on the page counter; a full walk is ~26 pages
# Spec: titles start with "Beer" or "Draught beer"; the oldest items are titled "Пиво" (id 266).
BEER_TITLE_RE = re.compile(r"^\s*(?:draught beer|beer|пиво)\b", re.I)
PAGES_RE = re.compile(r"Page\s+(\d+)\s+of\s+(\d+)")
VOLUME_RE = re.compile(r"\d+(?:[.,]\d+)?\s*(?:ml|cl|l|мл|л)(?!\w)", re.I)
QUOTES_RE = re.compile(r"''|[\"«»“”„″]")   # '' = two apostrophes used as a double quote in some titles
NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
CONTAINERS = {"tin": "can", "can": "can", "glass": "bottle", "bottle": "bottle", "pet": "bottle",
              "plastic": "bottle", "keg": "keg"}


@dataclass(frozen=True)
class BCItem:
    item_id: str
    title: str
    price_amd: int | None
    in_stock: bool
    url: str


@dataclass(frozen=True)
class BCListing:
    items: list[BCItem]
    page: int
    pages: int


@dataclass(frozen=True)
class BCProduct:
    brand: str | None
    volume_ml: int | None
    abv: float | None
    country: str | None
    container: str | None


def parse_listing(text: str) -> BCListing:
    """XHR JSON {link, products} -> cards and the "Page i of N" counter; ValueError on a wrong shape."""
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get("products"), str):
        raise ValueError("no products html")
    soup = BeautifulSoup(data["products"], "html.parser")
    items = []
    for card in soup.select(".product-item"):
        button = card.select_one("button.wish-list-icon[data-product]")
        link = card.select_one(".prod-item-name a[href]")
        item_id = button["data-product"].strip() if button else ""
        if not item_id or link is None:
            continue
        price = card.select_one(".new-price .wh-point")
        digits = re.sub(r"\D", "", price.get_text()) if price else ""
        items.append(BCItem(
            item_id=item_id,
            title=link.get_text(" ", strip=True),
            price_amd=int(digits) if digits else None,
            in_stock=card.select_one("a.addtocart-but.diss-prod") is None,
            url=urljoin(BASE, link["href"]),
        ))
    m = PAGES_RE.search(soup.get_text(" ", strip=True))
    return BCListing(items, int(m.group(1)), int(m.group(2))) if m else BCListing(items, 1, 1)


def _fields(soup: BeautifulSoup, row: str, name: str, value: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in soup.select(row):
        n, v = r.select_one(name), r.select_one(value)
        if n and v:
            out.setdefault(n.get_text(strip=True).rstrip(":"), v.get_text(" ", strip=True))
    return out


def _num(text: str | None) -> float | None:
    m = NUM_RE.search(text or "")
    return float(m.group(0).replace(",", ".")) if m else None


def parse_product(html: str) -> BCProduct:
    """Brand from the options list; volume, alcohol, country and packaging from the characteristics."""
    soup = BeautifulSoup(html, "html.parser")
    fields = _fields(soup, "ul.shop-options li", ".opt", ".val")
    fields |= _fields(soup, ".card-character-block", ".card-character-name", ".card-character-value")
    volume = fields.get("Volume", "")
    amount = _num(volume)
    if amount is not None and "ml" not in volume.lower():
        amount *= 1000   # "0.45 liter"
    packaging = fields.get("Type of packaging", "").lower().split()
    return BCProduct(
        brand=fields.get("Brand") or None,
        volume_ml=round(amount) if amount else None,
        abv=_num(fields.get("Alcohol")),
        country=fields.get("Country of origin") or None,
        container=next((CONTAINERS[w] for w in packaging if w in CONTAINERS), None),
    )


def clean_name(title: str) -> str:
    """'Beer "Hard root" Double IPA 0.45 l' -> 'Hard root Double IPA'."""
    name = VOLUME_RE.sub(" ", QUOTES_RE.sub(" ", BEER_TITLE_RE.sub("", title)))
    return " ".join(name.split())


def fetch_beercity(http: Http, place: Place, known_item_ids: set[str], full: bool, now: datetime,
                   brewery_aliases: Mapping[str, str]) -> SourceResult:
    """Full run: every page of both categories. Partial run: page 1, then the next page only while
    the page had unseen beer ids. Product pages only for unseen ids; a failed one drops that item.
    Any listing failure fails the whole run, so a full run is never incomplete."""
    key = f"beercity:{place.id}"

    def fail(error: str) -> SourceResult:
        return SourceResult(key=key, source="beercity", ok=False, error=error, full=full, place_id=place.id)

    sightings: list[Sighting] = []
    done: set[str] = set()
    for category in CATEGORIES:
        page = pages = 1
        while page <= pages:
            try:
                listing = parse_listing(http.get(LIST_URL.format(category=category, page=page), headers=XHR).text)
            except FetchError as e:
                return fail(e.kind)
            except ValueError:
                return fail("bad_response")
            if listing.page != page or listing.pages > MAX_PAGES:
                return fail("bad_response")   # page parameter ignored or a broken counter
            pages = listing.pages
            unseen = False
            for item in listing.items:
                beer_key = n_key(item.title, brewery_aliases)
                if item.item_id in done or not BEER_TITLE_RE.match(item.title) or beer_key == "n:":
                    continue   # repeated card, gift card or bundle, or a title without a name
                product = None
                if item.item_id not in known_item_ids:
                    unseen = True
                    try:
                        product = parse_product(http.get(item.url).text)
                    except FetchError:
                        continue   # spec §4.5: not recorded in this run, retried next run
                done.add(item.item_id)
                sightings.append(Sighting(
                    place_id=place.id, source="beercity", beer_key=beer_key, title=item.title,
                    name=clean_name(item.title), seen_at=now,
                    brewery=product.brand if product else None,
                    shop_item_id=item.item_id,
                    abv=product.abv if product else None,
                    price_amd=item.price_amd,
                    volume_ml=product.volume_ml if product else None,
                    container="draft" if category == DRAFT_CATEGORY else (product.container if product else None),
                    in_stock=item.in_stock, category=category, url=item.url, shop_url=item.url,
                ))
            if not full and not unseen:
                break
            page += 1
    return SourceResult(key=key, source="beercity", ok=True, sightings=sightings, full=full, place_id=place.id)
