"""Source 🛒 Parma (spec 4.6): beer category listing plus product pages for new codes."""
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from taps.config import Place
from taps.fetch import FetchError, Http, shop_photo
from taps.model import Sighting, SourceResult, n_key

BASE = "https://parma.am"
PHOTO_HOSTS = ("parma.am", "static.parma.am")   # listing cards carry the shop's own thumb on static.parma.am
CATEGORY = "beer"
LIST_URL = "https://parma.am/en/product/category?slug=beer&available=false&page={page}"
HEADERS = {"Accept-Encoding": "gzip"}
PAGE_SIZE = 60           # a shorter page is the last one
MAX_PAGES = 8            # shortcut: spec cap of 480 cards, fine at ~224 beers; cards past page 8 would be missed
MIN_CARDS = 150          # fewer distinct cards: the run does not count
ABV_RE = re.compile(r"(\d+(?:[.,\u2024]\d+)?)\s*%")
# Product link on parma.am; the code is the part after the last "_" of the slug.
_PRODUCT_URL_RE = re.compile(r"https://parma\.am/en/product/product\?slug=[^&#]*_(\d+)")
_PRICE_RE = re.compile(r"\d+(?:\.\d+)?")
_PREFIX_RE = re.compile(r"^\s*beer\b", re.I)
_QUOTES_RE = re.compile(r"[\"«»“”„]")
_VOLUME_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(ml|l)\b", re.I)


@dataclass(frozen=True)
class ParmaCard:
    item_id: str
    title: str
    price_amd: int | None
    in_stock: bool
    url: str
    photo: str | None = None


@dataclass(frozen=True)
class ParmaProduct:
    manufacturer: str | None
    country: str | None
    abv: float | None


def _text(tag) -> str:
    return " ".join(tag.get_text(" ").split())


def parse_listing(html: str) -> list[ParmaCard]:
    """Cards of one category page; a card without a parma.am product link, code or title is skipped."""
    cards = []
    for card in BeautifulSoup(html, "html.parser").select("div.product_item"):
        link = card.select_one("a.item_name[href]")
        span = link.find("span", recursive=False) if link else None
        m = _PRODUCT_URL_RE.fullmatch(urljoin(BASE, link["href"])) if link else None
        title = _text(span) if span else ""
        if m is None or not title:
            continue
        price = card.select_one("span.product_price[data-price]")
        p = _PRICE_RE.fullmatch(price["data-price"].strip()) if price else None
        img = card.select_one("img.product-image[src]")
        cards.append(ParmaCard(
            item_id=m.group(1),
            title=title,
            price_amd=round(float(p.group(0))) if p else None,
            in_stock=card.select_one("div.not_av_content") is None,
            url=m.group(0),
            photo=shop_photo(img["src"], BASE, PHOTO_HOSTS) if img else None,
        ))
    return cards


def parse_product(html: str) -> ParmaProduct:
    """Manufacturer and country rows; ABV only from the description (the page has other % badges)."""
    soup = BeautifulSoup(html, "html.parser")
    rows: dict[str, str] = {}
    for row in soup.select("p.description-item"):
        spans = row.find_all("span", recursive=False)
        if len(spans) >= 2:
            rows.setdefault(_text(spans[0]).rstrip(" :").lower(), _text(spans[1]))
    desc = soup.select_one("div.ingredients")
    m = ABV_RE.search(desc.get_text(" ")) if desc else None
    return ParmaProduct(
        manufacturer=rows.get("manufacturer") or None,
        country=rows.get("production country") or None,
        abv=float(m.group(1).replace(",", ".").replace("\u2024", ".")) if m else None,
    )


def clean_name(title: str) -> str:
    """'Beer "379" cherry, dark 330ml' -> '379 cherry, dark'."""
    name = _VOLUME_RE.sub(" ", _QUOTES_RE.sub(" ", _PREFIX_RE.sub("", title)))
    return " ".join(name.split()).strip(" ,")


def volume_ml(title: str) -> int | None:
    m = _VOLUME_RE.search(title)
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    return round(value if m.group(2).lower() == "ml" else value * 1000)


def fetch_parma(http: Http, place: Place, known_item_ids: set[str], now: datetime,
                brewery_aliases: Mapping[str, str]) -> SourceResult:
    """Listing pages until a short one (max 8), then product pages for new codes only;
    a new code whose product page fails is left out of this run."""
    key = f"parma:{place.id}"
    cards: dict[str, ParmaCard] = {}
    try:
        for page in range(1, MAX_PAGES + 1):
            found = parse_listing(http.get(LIST_URL.format(page=page), headers=HEADERS).text)
            for card in found:
                cards.setdefault(card.item_id, card)
            if len(found) < PAGE_SIZE:
                break
    except FetchError as e:
        return SourceResult(key=key, source="parma", ok=False, error=e.kind, place_id=place.id)
    if len(cards) < MIN_CARDS:
        return SourceResult(key=key, source="parma", ok=False, error="empty", place_id=place.id)

    sightings = []
    for card in cards.values():
        beer_key = n_key(card.title, brewery_aliases)
        if beer_key == "n:":
            continue
        product = None
        if card.item_id not in known_item_ids:
            try:
                product = parse_product(http.get(card.url, headers=HEADERS).text)
            except FetchError:
                continue
        sightings.append(Sighting(
            place_id=place.id, source="parma", beer_key=beer_key, title=card.title,
            name=clean_name(card.title), seen_at=now,
            brewery=product.manufacturer if product else None,
            shop_item_id=card.item_id,
            abv=product.abv if product else None,
            country=product.country if product else None, logo=card.photo,
            price_amd=card.price_amd, volume_ml=volume_ml(card.title),
            in_stock=card.in_stock, category=CATEGORY, url=card.url, shop_url=card.url,
        ))
    return SourceResult(key=key, source="parma", ok=True, sightings=sightings, place_id=place.id)
