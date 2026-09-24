import json
from datetime import datetime, timezone

import pytest

from taps.config import Place
from taps.fetch import FetchError, HttpResponse
from taps.model import Sighting, untappd_n_key
from taps.sources.buyam import BuyamItem, BuyamPage, clean_name, fetch_buyam, parse_buyam, parse_page
from tests.helpers import fixture_text

NOW = datetime(2026, 9, 24, 6, 17, tzinfo=timezone.utc)
PAGE_URL = "https://buy.am/en/restaurants/dargett"
LIST_URL = "https://api.buy.am/products/listing?skip=0&s=1162&f=9890&take=100"
PAGE = fixture_text("buyam/dargett.html")
PLACE = Place(id="dargett-brewpub", name="Dargett Brewpub", kind="brewpub", sources={"buyam": {"url": PAGE_URL}},
              brewery_id=265165, brewery_name="Dargett", untappd_venue_id=4640403)

# Real reply of LIST_URL (api.buy.am, 2026-09-24), cut down to the fields the parser reads.
DRAUGHT = [
    (174894, "Draught beer Dargett Bohemian Pilsner 1l", 2000),
    (174895, "Draught beer Dargett Bavarian Weizen 1l", 2000),
    (174896, "Draught beer Dargett Oatmeal Stout 1l", 2000),
    (174897, "Draught beer Dargett Munich Lager 1l", 2000),
    (174898, "Draught beer Dargett Vienna Lager 1l", 2000),
    (174900, "Draught beer Dargett Biere Blanche 1l", 2000),
    (174901, "Draught beer Dargett Apricot Ale 1l", 2500),
    (174902, "Draught beer Dargett Belgian Tripel 1l", 2000),
    (174903, "Draught beer Dargett American Pale Ale 1l", 2500),
    (174904, "Draught beer Dargett Session IPA 1l", 2500),
    (174905, "Draught beer Dargett India Pale Ale 1l", 2500),
    (174906, "Draught beer Dargett Black IPA 1l", 2500),
    (174907, "Draught beer Dargett Apple Cider 1l", 3000),
    (174908, "Draught beer Dargett Cherry Ale 1l", 2500),
    (174909, "Draught beer Dargett Baltic Porter 1l", 2500),
    (174911, "Draught beer Dargett Imperial IPA 1l", 3000),
]


def listing(rows=DRAUGHT) -> str:
    items = [{"id": i, "name": n, "nameEn": n, "basePrice": p} for i, n, p in rows]
    return json.dumps({"code": 200, "data": {"items": items, "totalCount": len(items)}})


def next_data_page(restaurant: dict) -> str:
    data = json.dumps({"props": {"pageProps": {"restaurant": restaurant}}})
    return f'<html><body><script id="__NEXT_DATA__" type="application/json">{data}</script></body></html>'


class FakeHttp:
    def __init__(self, pages: dict):
        self.pages = pages
        self.calls = []

    def get(self, url, headers=None):
        self.calls.append((url, headers))
        body = self.pages[url]
        if isinstance(body, Exception):
            raise body
        return HttpResponse(200, {"content-type": "text/html"}, body)


# --- parse_page ----------------------------------------------------------------

def test_parse_page_finds_draught_department_in_next_data():
    assert parse_page(PAGE) == BuyamPage(supplier_id=1162, department_id=9890, products_count=16)


def test_parse_page_fixture_has_no_products_only_counts():
    # Why the listing API is needed: the saved page renders skeletons, the items come from the browser.
    assert "Bohemian Pilsner" not in PAGE
    assert '"name":"Draught beer","productsCount":16' in PAGE


@pytest.mark.parametrize("html", [
    "<html><body><div class='skeleton'></div></body></html>",                   # no __NEXT_DATA__
    '<script id="__NEXT_DATA__" type="application/json">{"props":</script>',    # broken JSON
    '<script id="__NEXT_DATA__" type="application/json"></script>',             # empty
    # supplier id is a string
    next_data_page({"id": "1162", "departments": [{"id": 9890, "name": "Draught beer", "productsCount": 16}]}),
    next_data_page({"id": 1162, "departments": None}),                                    # no department list
    next_data_page({"id": 1162, "departments": [{"id": 9890, "name": "Draught beer"}]}),  # no productsCount
])
def test_parse_page_rejects_broken_pages(html):
    with pytest.raises(ValueError):
        parse_page(html)


def test_parse_page_without_draught_department_raises():
    assert PAGE.count('"name":"Draught beer"') == 1
    with pytest.raises(ValueError, match="Draught beer"):
        parse_page(PAGE.replace('"name":"Draught beer"', '"name":"Lemonades"'))


def test_parse_page_matches_department_name_loosely():
    html = next_data_page({"id": 7, "departments": [{"id": 1, "name": "Beer", "productsCount": 9},
                                                    {"id": 2, "name": " Draught Beer ", "productsCount": 3}]})
    assert parse_page(html) == BuyamPage(7, 2, 3)


# --- parse_buyam ---------------------------------------------------------------

def test_parse_buyam_reads_real_listing():
    items = parse_buyam(listing())
    assert len(items) == 16
    assert items[0] == BuyamItem("174894", "Draught beer Dargett Bohemian Pilsner 1l", 2000)
    assert items[-1] == BuyamItem("174911", "Draught beer Dargett Imperial IPA 1l", 3000)
    prices = {i.name: i.price_amd for i in items}
    assert prices["Draught beer Dargett Apricot Ale 1l"] == 2500
    assert prices["Draught beer Dargett Apple Cider 1l"] == 3000
    assert sorted(set(prices.values())) == [2000, 2500, 3000]


def test_parse_buyam_prefers_english_name():
    # Without Content-Language the API sends an Armenian `name`; `nameEn` stays English.
    row = {"id": 174894, "name": "Լցնովի գարեջուր Dargett Bohemian Pilsner 1լ",
           "nameEn": "Draught beer Dargett Bohemian Pilsner 1l", "basePrice": 2000}
    fallback = {"id": 5, "name": "Draught beer Dargett Gose 1l", "nameEn": "", "basePrice": 2500}
    text = json.dumps({"data": {"items": [row, fallback]}})
    assert [i.name for i in parse_buyam(text)] == ["Draught beer Dargett Bohemian Pilsner 1l",
                                                  "Draught beer Dargett Gose 1l"]


@pytest.mark.parametrize("price", [None, 0, -5, "2000", True, 2000.5])
def test_parse_buyam_unusable_price_is_none(price):
    text = json.dumps({"data": {"items": [{"id": 1, "nameEn": "Draught beer Dargett Gose 1l", "basePrice": price}]}})
    assert parse_buyam(text) == [BuyamItem("1", "Draught beer Dargett Gose 1l", None)]


@pytest.mark.parametrize("text", [
    "<html>maintenance</html>",
    json.dumps({"code": 500, "data": None}),
    json.dumps({"code": 200, "data": {"items": {"174894": "x"}}}),
    json.dumps({"data": {"items": ["Draught beer Dargett Gose 1l"]}}),
    json.dumps({"data": {"items": [{"nameEn": "Draught beer Dargett Gose 1l", "basePrice": 2000}]}}),
    json.dumps({"data": {"items": [{"id": True, "nameEn": "Draught beer Dargett Gose 1l"}]}}),
    json.dumps({"data": {"items": [{"id": 1, "nameEn": "", "name": " "}]}}),
])
def test_parse_buyam_rejects_broken_listing(text):
    with pytest.raises(ValueError):
        parse_buyam(text)


# --- clean_name ----------------------------------------------------------------

@pytest.mark.parametrize("title, brewery, name", [
    ("Draught beer Dargett Bohemian Pilsner 1l", "Dargett", "Bohemian Pilsner"),
    ("Draught beer Dargett Apple Cider 1l", "Dargett", "Apple Cider"),
    ("draught beer DARGETT Session IPA 0.5 L", "Dargett", "Session IPA"),
    ("Draught beer Dargett Gose 500ml", "Dargett", "Gose"),
    ("Draught beer Dargetts Ale 1l", "Dargett", "Dargetts Ale"),          # brewery only as a whole word
    ("Draught beer Dargett Bohemian Pilsner 1l", None, "Dargett Bohemian Pilsner"),
    ("Draught beer Dargett 1l", "Dargett", "Draught beer Dargett 1l"),   # nothing left: keep the title
])
def test_clean_name(title, brewery, name):
    assert clean_name(title, brewery) == name


# --- fetch_buyam ---------------------------------------------------------------

def test_fetch_buyam_builds_menu_sightings():
    http = FakeHttp({PAGE_URL: PAGE, LIST_URL: listing()})
    result = fetch_buyam(http, PLACE, NOW, {})
    assert (result.key, result.source, result.ok, result.error, result.place_id, result.full) == \
        ("buyam:dargett-brewpub", "buyam", True, None, "dargett-brewpub", True)
    assert [url for url, _ in http.calls] == [PAGE_URL, LIST_URL]
    assert http.calls[1][1] == {"Accept": "application/json", "Content-Language": "en"}
    assert len(result.sightings) == 16
    assert result.sightings[0] == Sighting(
        place_id="dargett-brewpub", source="buyam", beer_key="n:dargett bohemian pilsner",
        title="Draught beer Dargett Bohemian Pilsner 1l", name="Bohemian Pilsner", seen_at=NOW,
        brewery="Dargett", price_amd=2000, volume_ml=1000, container="draft", url=PAGE_URL,
    )
    assert result.sightings[0].kind == "menu"
    by_name = {s.name: s for s in result.sightings}
    assert by_name["Imperial IPA"].beer_key == "n:dargett imperial ipa"
    assert by_name["Imperial IPA"].price_amd == 3000
    assert by_name["Biere Blanche"].beer_key == "n:dargett biere blanche"
    assert len({s.beer_key for s in result.sightings}) == 16
    assert {s.brewery for s in result.sightings} == {"Dargett"}
    assert all(s.untappd_beer_id is None and s.shop_item_id is None for s in result.sightings)


def test_fetch_buyam_key_is_brewery_plus_name_like_untappd():
    result = fetch_buyam(FakeHttp({PAGE_URL: PAGE, LIST_URL: listing()}), PLACE, NOW, {})
    black_ipa = next(s for s in result.sightings if s.name == "Black IPA")
    assert black_ipa.beer_key == untappd_n_key("Dargett", "Black IPA") == "n:dargett black ipa"


def test_fetch_buyam_applies_brewery_aliases():
    result = fetch_buyam(FakeHttp({PAGE_URL: PAGE, LIST_URL: listing()}), PLACE, NOW, {"Dargett": "Darget"})
    assert result.sightings[0].beer_key == "n:darget bohemian pilsner"


def test_fetch_buyam_reads_volume_from_title():
    page = next_data_page({"id": 1162, "departments": [{"id": 9890, "name": "Draught beer", "productsCount": 4}]})
    rows = [(1, "Draught beer Dargett Gose 1l", 2500), (2, "Draught beer Dargett Kvass 0,5 l", 900),
            (3, "Draught beer Dargett Mild 330ml", 1200), (4, "Draught beer Dargett Pint Special", 1500)]
    result = fetch_buyam(FakeHttp({PAGE_URL: page, LIST_URL: listing(rows)}), PLACE, NOW, {})
    assert [(s.name, s.volume_ml) for s in result.sightings] == \
        [("Gose", 1000), ("Kvass", 500), ("Mild", 330), ("Pint Special", None)]


def test_fetch_buyam_place_without_brewery_name_skips_empty_keys():
    place = Place(id="some-pub", name="Some Pub", kind="bar", sources={"buyam": {"url": PAGE_URL}})
    page = next_data_page({"id": 1162, "departments": [{"id": 9890, "name": "Draught beer", "productsCount": 2}]})
    rows = [(1, "Draught beer Dargett Bohemian Pilsner 1l", 2000), (2, "Draught beer 1l", 1500)]
    result = fetch_buyam(FakeHttp({PAGE_URL: page, LIST_URL: listing(rows)}), place, NOW, {})
    assert result.ok
    assert [(s.beer_key, s.name, s.brewery) for s in result.sightings] == \
        [("n:dargett bohemian pilsner", "Dargett Bohemian Pilsner", None)]


@pytest.mark.parametrize("pages, error, calls", [
    ({PAGE_URL: FetchError("network", PAGE_URL)}, "network", 1),
    ({PAGE_URL: FetchError("cloudflare", PAGE_URL)}, "cloudflare", 1),
    ({PAGE_URL: "<html><body>skeleton</body></html>"}, "bad_response", 1),
    ({PAGE_URL: PAGE.replace('"name":"Draught beer"', '"name":"Lemonades"')}, "bad_response", 1),
    ({PAGE_URL: PAGE, LIST_URL: FetchError("http", "503 " + LIST_URL)}, "http", 2),
    ({PAGE_URL: PAGE, LIST_URL: "<html>maintenance</html>"}, "bad_response", 2),
    ({PAGE_URL: PAGE, LIST_URL: listing(DRAUGHT[:15])}, "bad_response", 2),                    # fewer than 16
    ({PAGE_URL: PAGE, LIST_URL: listing(DRAUGHT + [(1, "Pizza Margherita", 3900)])}, "bad_response", 2),
])
def test_fetch_buyam_failures(pages, error, calls):
    http = FakeHttp(pages)
    result = fetch_buyam(http, PLACE, NOW, {})
    assert (result.ok, result.error, result.sightings) == (False, error, [])
    assert (result.key, result.source, result.place_id) == ("buyam:dargett-brewpub", "buyam", "dargett-brewpub")
    assert len(http.calls) == calls
