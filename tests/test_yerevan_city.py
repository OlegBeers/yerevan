import copy
from datetime import datetime, timezone

import pytest

from taps.config import Place
from taps.fetch import FetchError
from taps.sources.yerevan_city import (
    HY_BRANDS, YCItem, YCName, brand_from, fetch_yerevan_city, parse_by_category, parse_search,
)
from tests.helpers import fixture_json

NOW = datetime(2026, 9, 24, 14, 17, tzinfo=timezone.utc)
PLACE = Place(id="yerevan-city", name="Yerevan City", kind="shop", sources={"yerevan_city": {}})
BY_CATEGORY = fixture_json("yerevan_city/by_category.json")   # 243 items, itemCount 243
SEARCH = fixture_json("yerevan_city/search.json")             # 209 products, all with nameEn


class FakeHttp:
    """Answers post_json in call order; an exception instance is raised instead of returned."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def post_json(self, url, payload, headers=None):
        self.calls.append((url, payload))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def fetch(by_category=BY_CATEGORY, search=SEARCH, aliases=None):
    http = FakeHttp(by_category, search)
    return fetch_yerevan_city(http, PLACE, NOW, aliases or {}), http


def sightings(**kw):
    return {s.shop_item_id: s for s in fetch(**kw)[0].sightings}


# --- parse_by_category -----------------------------------------------------------

def test_parse_by_category_reads_whole_category():
    listing = parse_by_category(BY_CATEGORY)
    assert (listing.item_count, len(listing.items)) == (243, 243)
    assert listing.items[0] == YCItem("3102", "Գարեջուր «Կրոմբախեր» Փիլս թ/տ 5լ", 12990, "Imported beer")
    assert {i.category for i in listing.items} == {"Imported beer", "Armenian beer", "Low alcohol cocktails and cider"}


def test_parse_by_category_takes_discounted_price():
    items = {i.item_id: i for i in parse_by_category(BY_CATEGORY).items}
    assert items["73930"].price_amd == 1300    # Duvel Blond: price 1460.0, discountedPrice 1299.984
    assert items["112509"].price_amd == 1250   # Primator IPA: price 1250.0, discountedPrice 0


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(success=False),
    lambda d: d.update(success="true"),
    lambda d: d.update(data=None),
    lambda d: d["data"].update(list=None),
    lambda d: d["data"].update(itemCount="243"),
    lambda d: d["data"]["list"].append("3102"),
    lambda d: d["data"]["list"][0].update(id="3102"),
    lambda d: d["data"]["list"][0].update(name=" "),
    lambda d: d["data"]["list"][0].update(price=0),
    lambda d: d["data"]["list"][0].update(price=float("inf")),
    lambda d: d["data"]["list"][0].update(discountedPrice="500"),
    lambda d: d["data"]["list"][0].update(categoryName=None),
], ids=["not_success", "success_text", "no_data", "no_list", "item_count_text", "item_not_object", "id_text",
        "blank_name", "zero_price", "infinite_price", "discount_text", "no_category"])
def test_parse_by_category_rejects_malformed(mutate):
    data = copy.deepcopy(BY_CATEGORY)
    mutate(data)
    with pytest.raises(ValueError):
        parse_by_category(data)


# --- parse_search ------------------------------------------------------------------

def test_parse_search_maps_id_to_latin_name():
    names = parse_search(SEARCH)
    assert len(names) == 209
    assert (names["8158"].name_en, names["8158"].brand_id) == ('Beer "Kilikia" 1l', 2788)
    assert (names["166205"].name_en, names["166205"].brand_id) == ('Beer "Paulaner" Munchner hell (can) 5l', None)
    assert names["192953"].name_en == 'Beer "Grevensteiner" unfiltered, light g/b 0.5l'  # leading space
    assert "175294" not in names    # beer drink: its Armenian name lacks the search word


def test_parse_search_skips_blank_name_and_ignores_odd_brand_id():
    data = copy.deepcopy(SEARCH)
    by_id = {p["id"]: p for p in data["data"]["products"]}
    by_id[8158]["nameEn"] = "  "
    by_id[13620]["brandId"] = "2835"
    names = parse_search(data)
    assert "8158" not in names and len(names) == 208
    assert (names["13620"].name_en, names["13620"].brand_id) == ('Beer "Kotayk" 1l', None)


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(success=False),
    lambda d: d["data"].update(products=None),
    lambda d: d["data"]["products"].append(None),
    lambda d: d["data"]["products"][0].update(id="8158"),
    lambda d: d["data"]["products"][0].update(nameEn=8158),
], ids=["not_success", "no_products", "product_not_object", "id_text", "name_not_text"])
def test_parse_search_rejects_malformed(mutate):
    data = copy.deepcopy(SEARCH)
    mutate(data)
    with pytest.raises(ValueError):
        parse_search(data)


# --- brand_from ------------------------------------------------------------------------

@pytest.mark.parametrize("name_en,brand", [
    ('Beer "Kilikia" 1l', "Kilikia"),
    ('Draft wheat beer "Dargett" Weizen 1l', "Dargett"),
    ('Beer "Volfas Engelman" Ipa (can) 0.568l', "Volfas Engelman"),
    ("Beer ''Corona'' Zero light (can) 330ml", "Corona"),
    ("Beer Kilikia 1l", "Kilikia"),                  # no quotes: first word after "beer"
    ("Draft beer Gyumri, light 1l", "Gyumri"),
])
def test_brand_from_latin_name(name_en, brand):
    assert brand_from(name_en, "Գարեջուր «Կոտայք» 1լ") == brand    # the Armenian name is not used


@pytest.mark.parametrize("name_hy,brand", [
    ("Գարեջուր «Կիլիկիա» 1լ", "Kilikia"),
    ("Գարեջուր «Կոտայք» Գոլդ 1լ", "Kotayk"),
    ("Գարեջուր «Բալտիկա» 7 թ/տ 0.9լ", "Baltika"),
    ("Գարեջուր «Գյումրի» 1լ", "Gyumri"),
    ("Գարեջուր «Հայնեկեն» ա/տ 0.5լ", "Heineken"),         # spelling of the shop's brand list
    ("Գարեջուր «Հեյնեկեն» ա/տ 0.5լ", "Heineken"),         # spelling of its item names
    ("Գարեջուր «Ժիգուլի» Բառնոե ա/տ 0.9լ", "Zhiguli"),     # stop-list spelling; nameEn says Jiguly
])
def test_brand_from_armenian_name_via_table(name_hy, brand):
    assert brand_from(None, name_hy) == brand


def test_brand_from_armenian_name_fallbacks():
    assert brand_from(None, "Գարեջրային ըմպելիք «Կրոմբախեր» Ռադլեր թ/տ 0.5լ") == "Կրոմբախեր"   # not in table
    assert brand_from("Lager 1l", "Գարեջուր «Կոտայք» 1լ") == "Kotayk"     # nameEn names no brand
    assert brand_from(None, "Գարեջուր 1լ") is None


# The shop's Latin spelling -> the stop-list spelling HY_BRANDS uses for that brand.
STOP_LIST_SPELLING = {"Jiguly": "Zhiguli", "Jigulyovskoye": "Zhigulevskoe", "Cooler": "Kuler",
                      "Prazacka": "Pražečka", "Staroceske": "Staročeské", "Lvovskoe": "Lvivske"}


def test_armenian_table_agrees_with_latin_names_in_fixture():
    names = parse_search(SEARCH)
    checked = set()
    for item in parse_by_category(BY_CATEGORY).items:
        if item.item_id not in names:
            continue
        hy = item.name_hy.split("«")[1].split("»")[0]
        latin = brand_from(names[item.item_id].name_en, item.name_hy)
        stop = STOP_LIST_SPELLING.get(latin, latin)
        if hy in HY_BRANDS or stop in HY_BRANDS.values():
            assert HY_BRANDS.get(hy) == stop, item
            checked.add(hy)
    assert set(HY_BRANDS) - checked == {"Հայնեկեն"}
    assert "Հայնեկեն" in {b["name"] for b in SEARCH["data"]["searchBrands"]}


# --- fetch_yerevan_city -------------------------------------------------------------------

def test_fetch_posts_both_requests_with_spec_bodies():
    result, http = fetch()
    assert http.calls == [
        ("https://apishopv2.yerevan-city.am/api/Product/GetByCategory",
         {"categoryId": 119, "parentId": 119, "count": 1000, "page": 1}),
        ("https://apishopv2.yerevan-city.am/api/Product/Search",
         {"search": "գարեջուր", "count": 500, "page": 1, "countries": [], "categories": [], "tags": [],
          "brands": [], "isDiscounted": False, "sortBy": 3}),
    ]
    assert (result.ok, result.error, result.full) == (True, None, True)
    assert (result.key, result.source, result.place_id) == ("yerevan_city:yerevan-city", "yerevan_city", "yerevan-city")
    assert len(result.sightings) == 243
    for s in result.sightings:
        assert (s.place_id, s.source, s.kind, s.seen_at, s.in_stock) == ("yerevan-city", "yerevan_city", "shop", NOW, True)
        assert s.url == f"https://yerevan-city.am/shop/product-details/{s.shop_item_id}"


def test_fetch_sighting_from_latin_name():
    s = sightings()["112509"]
    assert s.title == 'Beer "Primator" IPA, light g/b 0.5l'
    assert (s.brewery, s.name) == ("Primator", "Primator IPA, light")
    assert s.beer_key == "n:primator ipa light g b"
    assert s.category == "Imported beer"
    assert (s.price_amd, s.volume_ml, s.container) == (1250, 500, "bottle")


@pytest.mark.parametrize("item_id,brewery,name,beer_key,volume_ml,container,category", [
    ("51047", "Dargett", "Dargett Pilsner", "n:dargett pilsner", 1000, "draft", "Armenian beer"),
    ("3102", "Krombacher", "Krombacher Pils", "n:krombacher pils", 5000, "can", "Imported beer"),
    ("195013", "Corona", "Corona Zero light", "n:corona zero light", 330, "can", "Imported beer"),
    ("8158", "Kilikia", "Kilikia", "n:kilikia", 1000, None, "Armenian beer"),   # nothing after the brand
])
def test_fetch_latin_names_volume_and_container(item_id, brewery, name, beer_key, volume_ml, container, category):
    s = sightings()[item_id]
    assert (s.brewery, s.name, s.beer_key) == (brewery, name, beer_key)
    assert (s.volume_ml, s.container, s.category) == (volume_ml, container, category)


def test_fetch_sighting_from_armenian_name_when_missing_in_search():
    found = sightings()
    s = found["175294"]
    assert s.title == "Գարեջրային ըմպ. «Տրյոխգորնոե»Բլանշ,բաց ա/տ 0.45լ"
    assert s.beer_key == "n:գարեջրային ըմպ տրյոխգորնոե բլանշ բաց ա տ"
    assert (s.brewery, s.name) == ("Տրյոխգորնոե", "Տրյոխգորնոե Բլանշ,բաց")
    assert (s.price_amd, s.volume_ml, s.container, s.category) == (500, 450, "bottle", "Imported beer")
    assert found["173458"].container == "can"     # Krombacher Radler, թ/տ


def test_fetch_stop_list_brand_missing_in_search_gets_latin_brand():
    search = copy.deepcopy(SEARCH)
    search["data"]["products"] = [p for p in search["data"]["products"] if p["id"] not in (8158, 195013)]
    found = sightings(search=search)
    s = found["8158"]
    assert s.title == "Գարեջուր «Կիլիկիա» 1լ"
    assert s.beer_key == "n:գարեջուր կիլիկիա"
    assert (s.brewery, s.name, s.volume_ml) == ("Kilikia", "Kilikia", 1000)
    s = found["195013"]
    assert s.title == "Գարեջուր «Կորոնա» զերո, բաց ա/տ 330մլ"
    assert (s.brewery, s.name, s.volume_ml, s.container) == ("Corona", "Corona զերո, բաց", 330, "bottle")


def test_fetch_applies_brewery_aliases_to_key_only():
    s = sightings(aliases={"Jiguly": "Zhiguli"})["3989"]
    assert s.beer_key == "n:zhiguli barnoe g b"
    assert s.brewery == "Jiguly"


def test_fetch_skips_item_whose_title_gives_empty_key():
    search = copy.deepcopy(SEARCH)
    next(p for p in search["data"]["products"] if p["id"] == 8158)["nameEn"] = "Beer 1l"
    result, _ = fetch(search=search)
    assert result.ok and len(result.sightings) == 242
    assert "8158" not in {s.shop_item_id for s in result.sightings}


@pytest.mark.parametrize("mutate,requests", [
    (lambda c, s: c.update(success=False), 1),
    (lambda c, s: c["data"].update(list=[], itemCount=0), 1),
    (lambda c, s: c["data"]["list"].pop(), 1),
    (lambda c, s: s.update(success=False), 2),
    (lambda c, s: s["data"].update(products=None), 2),
    (lambda c, s: s["data"].update(products=[]), 2),
], ids=["listing_not_success", "empty_list", "fewer_than_item_count", "search_not_success", "search_malformed",
        "search_found_nothing"])
def test_fetch_bad_response(mutate, requests):
    c, s = copy.deepcopy(BY_CATEGORY), copy.deepcopy(SEARCH)
    mutate(c, s)
    result, http = fetch(c, s)
    assert (result.ok, result.error, result.sightings) == (False, "bad_response", [])
    assert (result.key, result.source, result.place_id) == ("yerevan_city:yerevan-city", "yerevan_city", "yerevan-city")
    assert len(http.calls) == requests


@pytest.mark.parametrize("by_category,search,error,requests", [
    (FetchError("network", "timeout"), SEARCH, "network", 1),
    (BY_CATEGORY, FetchError("cloudflare"), "cloudflare", 2),
    (BY_CATEGORY, FetchError("http", "invalid JSON"), "http", 2),
])
def test_fetch_request_failure_keeps_fetch_error_kind(by_category, search, error, requests):
    result, http = fetch(by_category, search)
    assert (result.ok, result.error, result.sightings) == (False, error, [])
    assert len(http.calls) == requests


def test_fetch_name_keeps_brand_when_title_has_only_a_colour():
    """Yerevan City writes 'Beer "Cernovar" dark (can) 0.5l': without the brand the name was just "dark"."""
    search = copy.deepcopy(SEARCH)
    product = next(p for p in search["data"]["products"] if p["id"] == 112509)
    product["nameEn"] = 'Beer "Cernovar" dark (can) 0.5l'
    s = sightings(search=search)["112509"]
    assert (s.brewery, s.name) == ("Cernovar", "Cernovar dark")


def test_fetch_takes_shop_photo_as_small_label_image():
    search = copy.deepcopy(SEARCH)
    product = next(p for p in search["data"]["products"] if p["id"] == 112509)
    product["photo"] = "https://media.yerevan-city.am/api/Image/Resize/ProductPhoto/1144516.png"
    found = sightings(search=search)
    assert found["112509"].logo == "https://media.yerevan-city.am/api/Image/Resize/ProductPhoto/1144516.png/160/160/false"
    product["photo"] = "javascript:alert(1)"
    assert sightings(search=search)["112509"].logo is None
