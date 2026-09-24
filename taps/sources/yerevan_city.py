"""Source 🛒 Yerevan City (spec 4.6): the whole beer category from the shop's JSON API, Latin names from Search."""
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from taps.config import Place
from taps.fetch import FetchError, Http
from taps.model import Sighting, SourceResult, n_key

API = "https://apishopv2.yerevan-city.am/api/Product"
BY_CATEGORY_URL = f"{API}/GetByCategory"
BY_CATEGORY_BODY = {"categoryId": 119, "parentId": 119, "count": 1000, "page": 1}
SEARCH_URL = f"{API}/Search"
# shortcut: one Search page of 500; ~209 products match today, past 500 the rest would lose their Latin names
SEARCH_BODY = {"search": "գարեջուր", "count": 500, "page": 1, "countries": [], "categories": [],
               "tags": [], "brands": [], "isDiscounted": False, "sortBy": 3}
PRODUCT_URL = "https://yerevan-city.am/shop/product-details/{}"
PHOTO_PREFIX = "https://media.yerevan-city.am/"
PHOTO_SIZE = "/160/160/false"   # the shop's own resize path; the bare photo is ~650 KB

# Stop-list (not_craft) brands as the shop writes them in «» of its Armenian names -> stop-list spelling,
# so the shop filter still knows them when an item is missing from Search. Հայնեկեն is the spelling of
# the shop's brand list; its item names use Հեյնեկեն.
# shortcut: covers the stop-list brands the shop sold on 2026-09-23; a new one needs a line here.
HY_BRANDS = {
    # Armenia
    "Կոտայք": "Kotayk", "Գյումրի": "Gyumri", "Կիլիկիա": "Kilikia", "Արարատ": "Ararat",
    "Ալեքսանդրապոլ": "Aleksandrapol", "Դիլիջան": "Dilijan", "Դեբեդ": "Debed", "Լինքոլն": "Lincoln",
    # Russia and CIS
    "Բալտիկա": "Baltika", "Ժիգուլյովսկոե": "Zhigulevskoe", "Ժիգուլի": "Zhiguli",
    "Զոլոտայա Բոչկա": "Zolotaya Bochka", "Բելիյ Մեդվեդ": "Beliy Medved", "Մոտոր": "Motor",
    "Ժատեցկիյ Գուս": "Zatecky Gus", "Կոզել": "Kozel", "Լվովսկոյե": "Lvivske",
    # international lagers
    "Հեյնեկեն": "Heineken", "Հայնեկեն": "Heineken", "Ստելլա Արտուա": "Stella Artois",
    "Կորոնա": "Corona", "Կոռոնա": "Corona", "Բադ": "Bud", "Բուդվայզեր": "Budweiser", "Միլլեր": "Miller",
    "Կարլսբերգ": "Carlsberg", "Տուբորգ": "Tuborg", "Էստրելլա Դամմ": "Estrella Damm", "Կուլեր": "Kuler",
    "Ալմազա": "Almaza",
    # Georgia and Czechia
    "Նատախտարի": "Natakhtari", "Կազբեգի": "Kazbegi", "Զեդազենի": "Zedazeni",
    "Պրաժեչկա": "Pražečka", "Ստարոչեսկոյե": "Staročeské", "Սանտանոս": "Santanos",
}

_QUOTED_RE = re.compile(r'"([^"]+)"|\'\'(.+?)\'\'|«([^»]+)»')     # "Kilikia", ''Corona'', «Կիլիկիա»
_AFTER_BEER_RE = re.compile(r"\bbeer\s+([^\s,]+)", re.I)
_VOLUME_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(ml|մլ|l|լ)(?!\w)", re.I)
_MARKS_RE = re.compile(r"\b\d+\s*x\b|\(can\)|\bg/b\b|[աթ]/տ", re.I)   # multipack, can, glass bottle


@dataclass(frozen=True)
class YCItem:
    item_id: str
    name_hy: str
    price_amd: int
    category: str


@dataclass(frozen=True)
class YCListing:
    items: list[YCItem]
    item_count: int


@dataclass(frozen=True)
class YCName:
    name_en: str
    brand_id: int | None
    photo: str | None = None


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _body(data: Any) -> dict:
    """The 'data' object of a successful API answer; ValueError otherwise."""
    if not isinstance(data, dict) or data.get("success") is not True or not isinstance(data.get("data"), dict):
        raise ValueError("success != true or no data")
    return data["data"]


def parse_by_category(data: dict) -> YCListing:
    """GetByCategory answer -> the category's items; ValueError when unsuccessful or malformed."""
    body = _body(data)
    rows, item_count = body.get("list"), body.get("itemCount")
    if not isinstance(rows, list) or not _is_int(item_count):
        raise ValueError("no data.list or data.itemCount")
    items = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("item is not an object")
        item_id, name, category = row.get("id"), row.get("name"), row.get("categoryName")
        price, discounted = row.get("price"), row.get("discountedPrice") or 0   # 0 = no discount
        if not (_is_int(item_id) and isinstance(name, str) and name.strip() and isinstance(category, str)
                and _is_num(price) and price > 0 and _is_num(discounted)):
            raise ValueError(f"bad item {item_id!r}")
        items.append(YCItem(str(item_id), name.strip(), round(discounted if discounted > 0 else price), category))
    return YCListing(items, item_count)


def parse_search(data: dict) -> dict[str, YCName]:
    """Search answer -> {item id: YCName}; products without nameEn are left out."""
    products = _body(data).get("products")
    if not isinstance(products, list):
        raise ValueError("no data.products")
    names = {}
    for p in products:
        if not isinstance(p, dict):
            raise ValueError("product is not an object")
        item_id, name_en, brand_id = p.get("id"), p.get("nameEn"), p.get("brandId")
        if not _is_int(item_id) or not (name_en is None or isinstance(name_en, str)):
            raise ValueError(f"bad product {item_id!r}")
        if name_en and name_en.strip():
            photo = p.get("photo")
            photo = photo + PHOTO_SIZE if isinstance(photo, str) and photo.startswith(PHOTO_PREFIX) else None
            names[str(item_id)] = YCName(name_en.strip(), brand_id if _is_int(brand_id) else None, photo)
    return names


def _brand_match(text: str) -> re.Match | None:
    return _QUOTED_RE.search(text) or _AFTER_BEER_RE.search(text)


def _inner(m: re.Match) -> str:
    return next(g for g in m.groups() if g).strip()


def brand_from(name_en: str | None, name_hy: str) -> str | None:
    """Quoted text of nameEn, else its first word after 'beer'; otherwise the «» of the Armenian name
    through HY_BRANDS, or the raw Armenian text for a brand not in the table."""
    m = _brand_match(name_en) if name_en else None
    if m:
        return _inner(m)
    m = _QUOTED_RE.search(name_hy)
    if not m:
        return None
    brand = _inner(m)
    return HY_BRANDS.get(brand, brand)


def _clean_name(title: str, brand: str | None) -> str:
    """Human name: brand + the title after it, without volume, multipack and container marks
    (the shop's own variant is often just a colour: 'Beer "Cernovar" dark')."""
    m = _brand_match(title)
    rest = title[m.end():] if m else title
    rest = " ".join(_MARKS_RE.sub(" ", _VOLUME_RE.sub(" ", rest)).split())
    if brand and rest and not rest.lower().startswith(brand.lower()):
        return f"{brand} {rest}"
    return rest or brand or title


def _volume_ml(title: str) -> int | None:
    m = _VOLUME_RE.search(title)
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    return round(value if m.group(2).lower() in ("ml", "մլ") else value * 1000)


def _container(name_en: str | None, name_hy: str) -> str | None:
    """From nameEn ('Draft beer', '(can)', 'g/b') or the Armenian marks թ/տ (tin) and ա/տ (glass)."""
    en = (name_en or "").lower()
    if en.startswith("draft"):
        return "draft"
    if "(can)" in en or "թ/տ" in name_hy:
        return "can"
    if "g/b" in en or "ա/տ" in name_hy:
        return "bottle"
    return None


def fetch_yerevan_city(http: Http, place: Place, now: datetime, brewery_aliases: Mapping[str, str]) -> SourceResult:
    """Two POSTs. Spec 4.6: the run fails on success != true, an empty or short list, or a failed Search."""
    key = f"yerevan_city:{place.id}"

    def fail(error: str) -> SourceResult:
        return SourceResult(key=key, source="yerevan_city", ok=False, error=error, place_id=place.id)

    try:
        listing = parse_by_category(http.post_json(BY_CATEGORY_URL, BY_CATEGORY_BODY))
        if not listing.items or len(listing.items) < listing.item_count:
            return fail("bad_response")
        names = parse_search(http.post_json(SEARCH_URL, SEARCH_BODY))
    except FetchError as e:
        return fail(e.kind)
    except ValueError:
        return fail("bad_response")
    if not names:   # the shop always has beer, so an empty search is a failed one
        return fail("bad_response")

    sightings = []
    for item in listing.items:
        # beer drinks, ciders and cocktails lack the search word, so they have no Latin name
        found = names.get(item.item_id)
        name_en = found.name_en if found else None
        title = name_en or item.name_hy
        beer_key = n_key(title, brewery_aliases)
        if beer_key == "n:":
            continue
        brand = brand_from(name_en, item.name_hy)
        sightings.append(Sighting(
            place_id=place.id, source="yerevan_city", beer_key=beer_key, title=title,
            name=_clean_name(title, brand), seen_at=now, brewery=brand, shop_item_id=item.item_id,
            price_amd=item.price_amd, volume_ml=_volume_ml(title), container=_container(name_en, item.name_hy),
            in_stock=True,   # no stock flag: a sold-out item just leaves the list
            category=item.category, url=PRODUCT_URL.format(item.item_id),
            shop_url=PRODUCT_URL.format(item.item_id), logo=found.photo if found else None,
        ))
    return SourceResult(key=key, source="yerevan_city", ok=True, sightings=sightings, place_id=place.id)
