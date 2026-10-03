"""Source 🛒 Carrefour (carrefour.am, English store): beer products from the sitemap, the first page of each
beer category, product pages for new items and a rolling weekly refresh.

robots.txt forbids every query string and /catalog/, so categories are read at page 1 only (no pagination);
everything else comes from the sitemap's product slugs, which carry the English product title."""
import re
import xml.etree.ElementTree as ET
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from taps.config import Place
from taps.fetch import FetchError, Http, shop_photo
from taps.model import Sighting, SourceResult, n_key
from taps.shop_filter import brand_matches
from taps.sources.robots import guarded_get, read_robots

BASE = "https://carrefour.am"
PHOTO_HOSTS = ("carrefour.am",)
SITEMAP_URL = BASE + "/sitemap_en.xml"
CATEGORY_URL = BASE + "/en/everyday-products/alcoholic-beverages/beer"
CATEGORIES = (("beer", CATEGORY_URL), ("armenian-beer", CATEGORY_URL + "/armenian-beer"),
              ("beer-imported", CATEGORY_URL + "/beer-imported"))   # page 1 of each: robots.txt forbids a query string
REFRESH_BUCKETS = 14     # two runs a day: every known item's page is read once a week
MAX_PRODUCT_PAGES = 100  # per run, new items first; the first run reads ~45, later runs 1-10
PRODUCT_URL = BASE + "/en/{slug}"
MIN_ITEMS = 50           # fewer beer items (~125 today): the run does not count
SM = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
IMAGE = "{http://www.google.com/schemas/sitemap-image/1.1}"
# A sitemap title that may be a beer; the product page's breadcrumbs make the final call.
CANDIDATE_RE = re.compile(
    r"\b(?:beer|lager|ipa|apa|stout|porter|pilsner|pilsener|radler|weizen|weissbier|tripel|saison|gose|cider|shandy)\b"
    r"|գարեջուր|пиво|сидр", re.I)
NOT_BEER_RE = re.compile(r"\b(?:snack|whisky|whiskey|vinegar|chips|sauce|lavash)\b", re.I)
BEER_CRUMB_RE = re.compile(r"/alcoholic-beverages/beer(?:/|$)")
COUNTRY_RE = re.compile(r"Country of manufacture\s*-\s*(.+)", re.I)
SALABLE_RE = re.compile(r'"is_salable":"(\d)"')
VOLUME_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(ml|l)(?![a-z])", re.I)
ABV_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
NOISE_RE = re.compile(r"\bbeer\b|\bdrink\b|\(can\)|\bg/[bc]\b|\bm/c\b|\bpet\b", re.I)


@dataclass(frozen=True)
class CarrefourCard:
    slug: str
    title: str
    brand: str | None
    country: str | None
    price_amd: int | None
    photo: str | None = None


@dataclass(frozen=True)
class CarrefourProduct:
    title: str
    brand: str | None
    country: str | None
    price_amd: int | None
    in_stock: bool | None
    photo: str | None
    is_beer: bool


def _text(tag) -> str:
    return " ".join(tag.get_text(" ").split())


def _slug(url: str) -> str | None:
    """'https://carrefour.am/en/<slug>' -> slug; None for any other address (a category has more path)."""
    parts = urlsplit(url)
    m = re.fullmatch(r"/en/([^/]+)", parts.path)
    return m.group(1) if m and parts.scheme == "https" and parts.hostname == "carrefour.am" and not parts.query else None


def parse_sitemap(xml: str) -> dict[str, str]:
    """{product slug: English title}; entries without an image title (home, categories) are not products.
    ValueError when the text is not a sitemap."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise ValueError(f"not xml: {e}") from e
    if root.tag != f"{SM}urlset":
        raise ValueError("not a sitemap")
    titles = {}
    for url in root.findall(f"{SM}url"):
        loc, title = url.findtext(f"{SM}loc"), url.findtext(f"{IMAGE}image/{IMAGE}title")
        slug = _slug(loc or "")
        if slug and title and title.strip():
            titles[slug] = " ".join(title.split())
    return titles


def is_beer_candidate(title: str) -> bool:
    return bool(CANDIDATE_RE.search(title)) and not NOT_BEER_RE.search(title)


def parse_listing(html: str) -> list[CarrefourCard]:
    """Cards of one category page (all of them are beer: the category says so)."""
    cards = []
    for card in BeautifulSoup(html, "html.parser").select("li.product-item"):
        link = card.select_one("a.product-item-link[href]")
        slug = _slug(link["href"]) if link else None
        title = _text(link) if link else ""
        if slug is None or not title:
            continue
        brand, country = card.select_one(".attribute-manufacturer"), card.select_one(".manufact-text.bold")
        price, img = card.select_one("span.price-wrapper[data-price-amount]"), card.select_one("img.product-image-photo[src]")
        cards.append(CarrefourCard(
            slug=slug, title=title, brand=_text(brand) if brand else None,
            country=_text(country) if country and _text(country) else None,
            price_amd=round(float(price["data-price-amount"])) if price else None,
            photo=shop_photo(img["src"], BASE, PHOTO_HOSTS) if img else None,
        ))
    return cards


def parse_product(html: str) -> CarrefourProduct | None:
    """The product page; None when it has no title (an error page). Beer means the breadcrumbs pass through
    the beer category; stock is the page's own is_salable flag."""
    soup = BeautifulSoup(html, "html.parser")
    name = soup.select_one("h1.page-title [itemprop=name]") or soup.select_one("h1.page-title")
    if name is None or not _text(name):
        return None
    brand, price = soup.select_one(".product-attribute-manufacturer"), soup.select_one("meta[itemprop=price]")
    country = next((m.group(1).strip() for c in soup.select(".country-of-manufacture")
                    if (m := COUNTRY_RE.search(_text(c)))), None)
    salable, og = SALABLE_RE.search(html), soup.select_one("meta[property='og:image']")
    return CarrefourProduct(
        title=_text(name), brand=_text(brand) if brand and _text(brand) else None, country=country,
        price_amd=round(float(price["content"])) if price and price.get("content") else None,
        in_stock=salable.group(1) == "1" if salable else None,
        photo=shop_photo(og.get("content"), BASE, PHOTO_HOSTS) if og else None,
        is_beer=any(BEER_CRUMB_RE.search(a["href"]) for a in soup.select(".breadcrumbs a[href]")),
    )


def clean_name(title: str) -> str:
    """'Dargett Beer Belgian Tripel g/b 0.33l' -> 'Dargett Belgian Tripel': no 'beer', volume, strength or marks."""
    name = NOISE_RE.sub(" ", ABV_RE.sub(" ", VOLUME_RE.sub(" ", title)))
    return " ".join(name.split()).strip(" ,")


def volume_ml(title: str) -> int | None:
    m = VOLUME_RE.search(title)
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    return round(value if m.group(2).lower() == "ml" else value * 1000)


def abv(title: str) -> float | None:
    m = ABV_RE.search(title)
    return float(m.group(1).replace(",", ".")) if m else None


def container(title: str) -> str | None:
    """From the marks in the title: (can), g/b glass bottle, pet; 'M/C' and 'g/c' are not understood."""
    lowered = title.lower()
    if "(can)" in lowered:
        return "can"
    if re.search(r"\bg/b\b|\bpet\b", lowered):
        return "bottle"
    return "draft" if re.search(r"\bdraft\b", lowered) else None


def _bucket(slug: str) -> int:
    return zlib.crc32(slug.encode()) % REFRESH_BUCKETS


def is_due_for_refresh(slug: str, now: datetime) -> bool:
    """Each run owns one of 14 buckets of slugs (the runs are ~12 hours apart), so a week reads every item once
    without remembering when it was read."""
    return _bucket(slug) == (now.toordinal() * 2 + (now.hour >= 12)) % REFRESH_BUCKETS


def fetch_carrefour(http: Http, place: Place, known: Mapping[str, str | None], now: datetime,
                    brewery_aliases: Mapping[str, str]) -> SourceResult:
    """robots.txt, the sitemap (candidate beer slugs and their titles) and page 1 of the three beer categories
    (confirmed beers with brand, country and price). Product pages are read for new items no card describes
    (they must sit under the beer category) and, as a rolling refresh, for the items whose weekly bucket is
    due. A failed page of a new item leaves it out until the next run; a failed refresh is ignored. A known
    item in neither the sitemap nor a card is gone. `known` maps the slugs the state already has to the brand
    stored for them: the title often omits the brand, which the name and key get from the shop's brand field.
    Any sitemap or listing failure fails the run."""
    key = f"carrefour:{place.id}"

    def fail(error: str) -> SourceResult:
        return SourceResult(key=key, source="carrefour", ok=False, error=error, place_id=place.id)

    try:
        robots = read_robots(http, BASE)
        titles = parse_sitemap(guarded_get(http, robots, SITEMAP_URL).text)
        cards: dict[str, CarrefourCard] = {}
        for _, url in CATEGORIES:
            for card in parse_listing(guarded_get(http, robots, url).text):
                cards.setdefault(card.slug, card)
    except FetchError as e:
        return fail(e.kind)
    except ValueError:
        return fail("bad_response")
    items = {slug: title for slug, title in titles.items() if is_beer_candidate(title)}
    items.update({slug: card.title for slug, card in cards.items()})
    if len(items) < MIN_ITEMS:
        return fail("empty")

    new = [s for s in items if s not in known and s not in cards]   # only the sitemap knows them
    to_read = new + [s for s in items if s in known and is_due_for_refresh(s, now)]
    products: dict[str, CarrefourProduct] = {}
    for slug in to_read[:MAX_PRODUCT_PAGES]:
        try:
            product = parse_product(guarded_get(http, robots, PRODUCT_URL.format(slug=slug)).text)
        except FetchError:
            continue
        if product is not None and product.is_beer:
            products[slug] = product

    sightings = []
    for slug, listed_title in items.items():
        card, product = cards.get(slug), products.get(slug)
        if slug in new and product is None:
            continue   # unread, failed or no beer: not recorded in this run
        title = product.title if product else listed_title
        brand = (product.brand if product else None) or (card.brand if card else None) or known.get(slug)
        named = f"{brand} {title}" if brand and not brand_matches(title, brand) else title
        name = clean_name(named)
        beer_key = n_key(name, brewery_aliases)   # no volume, strength or container marks in the key
        if beer_key == "n:":
            continue
        url = PRODUCT_URL.format(slug=slug)
        sightings.append(Sighting(
            place_id=place.id, source="carrefour", beer_key=beer_key, title=title, name=name, seen_at=now,
            brewery=brand, shop_item_id=slug,
            abv=abv(title), country=(product.country if product else None) or (card.country if card else None),
            country_checked=True if product else None,
            logo=(product.photo if product else None) or (card.photo if card else None),
            price_amd=(product.price_amd if product else None) or (card.price_amd if card else None),
            volume_ml=volume_ml(title), container=container(title),
            in_stock=product.in_stock if product else None, category="beer", url=url, shop_url=url,
        ))
    return SourceResult(key=key, source="carrefour", ok=True, sightings=sightings, place_id=place.id)
