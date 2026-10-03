"""Source 🛒 SAS (sas.am): the two beer sections of the English catalog, product pages for new item ids."""
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from taps.config import Place
from taps.fetch import FetchError, Http, shop_photo
from taps.model import Sighting, SourceResult, n_key
from taps.sources.robots import guarded_get, read_robots

BASE = "https://www.sas.am"
PHOTO_HOSTS = ("www.sas.am",)
SECTIONS = ("armyanskoe", "importnoe_pivo")   # Armenian and imported beer; robots.txt allows the ?offset= pagination
LIST_URL = BASE + "/en/catalog/{section}/{query}"
PAGE_SIZE = 24           # a shorter page is the last one
MAX_PAGES = 15           # per section; the walk is ~3 and ~7 pages today
MIN_CARDS = 100          # fewer distinct cards (206 today): the run does not count
ID_RE = re.compile(r"/en/catalog/[a-z_]+/(\d+)/")
ABV_RE = re.compile(r"alc\w*[\s.:]{0,4}(\d+(?:[.,]\d+)?)\s*%", re.I)
# the opening and closing quote may differ: 'Beer ''Baltika Premium" 0.45l'
QUOTED_RE = re.compile(r"(?:\"|''|«|“)(.+?)(?:\"|''|»|”)")
BEER_WORDS_RE = re.compile(r"^.*?\bbeer\b", re.I)
VOLUME_RE = re.compile(r"(\d+(?:[.,․]\d+)?)\s*(ml|l|мл|л)(?![a-zа-яё])", re.I)   # "0.33l", "330ml", "0․33л"
ABV_IN_TITLE_RE = re.compile(r"alc\.?\s*\d+(?:[.,]\d+)?\s*%", re.I)


@dataclass(frozen=True)
class SasCard:
    item_id: str
    title: str
    price_amd: int | None
    in_stock: bool
    url: str
    section: str
    photo: str | None = None


@dataclass(frozen=True)
class SasProduct:
    brand: str | None
    country: str | None
    abv: float | None


def _text(tag) -> str:
    return " ".join(tag.get_text(" ").split())


def _photo(card) -> str | None:
    picture = card.select_one("v-picture[\\:sources]")
    try:
        sources = json.loads(picture[":sources"]) if picture else {}
    except ValueError:
        return None
    return shop_photo(sources.get("middle") or sources.get("small"), BASE, PHOTO_HOSTS)


def parse_listing(html: str, section: str) -> list[SasCard]:
    """Cards of one listing page; a card without a catalog link or a title is skipped. A sold-out card has
    neither a price nor a quantity counter."""
    cards = []
    for card in BeautifulSoup(html, "html.parser").select("div.product.js-product"):
        link = card.select_one("a.product__cover-link[href]")
        name = card.select_one("div.product__name")
        m = ID_RE.fullmatch(link["href"]) if link else None
        title = _text(name) if name else ""
        if m is None or not title:
            continue
        price = card.select_one(".price__new .price__text")
        digits = re.sub(r"\D", "", price.find(string=True, recursive=False) or "") if price else ""
        counter = card.select_one(".js-basket-counter-field[data-max-quantity]")
        quantity = counter["data-max-quantity"] if counter else ""
        cards.append(SasCard(
            item_id=m.group(1), title=title, price_amd=int(digits) if digits else None,
            in_stock=quantity.isdigit() and int(quantity) > 0, url=urljoin(BASE, link["href"]),
            section=section, photo=_photo(card),
        ))
    return cards


def parse_product(html: str) -> SasProduct:
    """Brand and country from the details list; the strength only from the 'alc. N%' phrase of the description."""
    soup = BeautifulSoup(html, "html.parser")
    details = {}
    for item in soup.select(".card__detail-item"):
        title, value = item.select_one(".card__detail-item-title"), item.select_one(".card__detail-item-value")
        if title and value:
            details.setdefault(_text(title), _text(value))
    description = soup.select_one(".card__subtitle")
    m = ABV_RE.search(_text(description)) if description else None
    return SasProduct(
        brand=details.get("Brand") or None,
        country=details.get("Country of origin") or None,
        abv=float(m.group(1).replace(",", ".")) if m else None,
    )


def clean_name(title: str) -> str:
    """'Cherry flavored beer "Dargett Cherry Ale" 330ml' -> 'Dargett Cherry Ale': the quoted brand and name."""
    m = QUOTED_RE.search(title)
    if m:
        return " ".join(m.group(1).split())
    return " ".join(VOLUME_RE.sub(" ", BEER_WORDS_RE.sub("", ABV_IN_TITLE_RE.sub(" ", title))).split())


def volume_ml(title: str) -> int | None:
    m = VOLUME_RE.search(title)
    if not m:
        return None
    value = float(m.group(1).replace(",", ".").replace("․", "."))
    return round(value if m.group(2).lower() in ("ml", "мл") else value * 1000)


def fetch_sas(http: Http, place: Place, known_item_ids: set[str], now: datetime,
              brewery_aliases: Mapping[str, str]) -> SourceResult:
    """robots.txt, then every listing page of both sections (offset walk), then the product page of each item
    id not seen before; a new item whose page fails is left out of this run and retried the next one.
    Prices and stock come from the listing, so known items cost no request. Any listing failure fails the run."""
    key = f"sas:{place.id}"

    def fail(error: str) -> SourceResult:
        return SourceResult(key=key, source="sas", ok=False, error=error, place_id=place.id)

    cards: dict[str, SasCard] = {}
    try:
        robots = read_robots(http, BASE)
        for section in SECTIONS:
            for page in range(1, MAX_PAGES + 1):
                query = f"?offset={(page - 1) * PAGE_SIZE}" if page > 1 else ""
                found = parse_listing(guarded_get(http, robots, LIST_URL.format(section=section, query=query)).text,
                                      section)
                new = [c for c in found if c.item_id not in cards]
                cards.update((c.item_id, c) for c in new)
                if len(found) < PAGE_SIZE or not new:   # the last page, or the site ignored the offset
                    break
    except FetchError as e:
        return fail(e.kind)
    if len(cards) < MIN_CARDS:
        return fail("empty")

    sightings = []
    for card in cards.values():
        beer_key = n_key(clean_name(card.title), brewery_aliases)   # the quoted name: no flavour words, volume or strength
        if beer_key == "n:":
            continue
        product = None
        if card.item_id not in known_item_ids:
            try:
                product = parse_product(guarded_get(http, robots, card.url).text)
            except FetchError:
                continue
        sightings.append(Sighting(
            place_id=place.id, source="sas", beer_key=beer_key, title=card.title, name=clean_name(card.title),
            seen_at=now, brewery=product.brand if product else None, shop_item_id=card.item_id,
            abv=product.abv if product else None, country=product.country if product else None,
            country_checked=True if product else None, logo=card.photo, price_amd=card.price_amd,
            volume_ml=volume_ml(card.title), in_stock=card.in_stock, category=card.section,
            url=card.url, shop_url=card.url,
        ))
    return SourceResult(key=key, source="sas", ok=True, sightings=sightings, place_id=place.id)
