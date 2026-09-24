import html
import json
from datetime import datetime, timezone

import pytest
from bs4 import BeautifulSoup

from taps.config import Place
from taps.fetch import FetchError, HttpResponse
from taps.model import Sighting
from taps.sources.beercity import (
    MAX_PAGES, BCItem, BCProduct, clean_name, fetch_beercity, parse_listing, parse_product,
)
from tests.helpers import fixture_text

NOW = datetime(2026, 9, 24, 6, 17, tzinfo=timezone.utc)
PLACE = Place(id="beer-city", name="Beer City", kind="shop", sources={"beercity": {}})
XHR = {"X-Requested-With": "XMLHttpRequest"}

BOTTLES_P1 = fixture_text("beercity/list_bottles_p1.json")      # Page 1 of 23, ids 2053..2040
BOTTLES_LAST = fixture_text("beercity/list_bottles_last.json")  # Page 23 of 23, id 266
DRAFT_LAST = fixture_text("beercity/list_draft_last.json")      # Page 3 of 3, ids 18..7
PRODUCT_IPA = fixture_text("beercity/product_ipa.html")         # item 2047
PRODUCT_NONALC = fixture_text("beercity/product_nonalc.html")   # item 2048
P1_IDS = ["2053", "2052", "2051", "2050", "2049", "2048", "2047", "2046", "2045", "2044", "2041", "2040"]
DRAFT_IDS = ["18", "14", "12", "11", "10", "7"]


def bottles(page):
    return f"https://www.beer-city.am/en/catalog/sshalcavac-garejur/?sorting=-id&page={page}"


def draft(page):
    return f"https://www.beer-city.am/en/catalog/lcnovi-garejur/?sorting=-id&page={page}"


def product(slug):
    return f"https://www.beer-city.am/en/products/{slug}/"


IPA_URL = product("garejur-hard-rut-dabl-ipa-045l")
NONALC_URL = product("garejur-pur-vayv-ipa-non-alco-045l")


def cards(listing_json):
    """Real card HTML of a captured listing, by item id."""
    soup = BeautifulSoup(json.loads(listing_json)["products"], "html.parser")
    return {c.select_one("[data-product]")["data-product"]: str(c) for c in soup.select(".product-item")}


P1_CARDS = cards(BOTTLES_P1)
DRAFT_CARDS = cards(DRAFT_LAST)


def card(item_id, title):
    """A card in the real markup (cloned from item 2053) with another id and title."""
    return (P1_CARDS["2053"].replace('data-product="2053"', f'data-product="{item_id}"')
            .replace("garejur-bronx-05l", f"item-{item_id}")
            .replace('Beer "Bronx" 0.5L', html.escape(title)))


def page_json(card_html, page, pages):
    counter = f'<div class="number-show-pagination m-none"><span>Page <b>{page}</b> of {pages}</span></div>'
    return json.dumps({"link": f"sorting=-id&page={page}", "products": "".join(card_html) + counter})


class FakeHttp:
    """Answers GET by URL; an Exception value is raised."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, headers=None):
        self.calls.append((url, dict(headers or {})))
        if url not in self.responses:
            pytest.fail(f"unexpected GET {url}")
        r = self.responses[url]
        if isinstance(r, Exception):
            raise r
        return HttpResponse(200, {}, r)


def fetch(responses, known=(), full=False, aliases=None):
    http = FakeHttp(responses)
    return fetch_beercity(http, PLACE, set(known), full, NOW, aliases or {}), http


def by_id(result):
    return {s.shop_item_id: s for s in result.sightings}


def ids(result):
    return [s.shop_item_id for s in result.sightings]


# --- parse_listing -----------------------------------------------------------

def test_parse_listing_first_bottles_page():
    listing = parse_listing(BOTTLES_P1)
    assert (listing.page, listing.pages) == (1, 23)
    assert [i.item_id for i in listing.items] == P1_IDS
    assert listing.items[0] == BCItem("2053", 'Beer "Bronx" 0.5L', 730, True, product("garejur-bronx-05l"))
    assert listing.items[6] == BCItem("2047", 'Beer "Hard root" Double IPA 0.45 l', 2200, True, IPA_URL)
    assert listing.items[8].title == 'Beer "Starnberger helles" 0,45l'
    assert all(i.in_stock for i in listing.items)


def test_parse_listing_last_pages_and_out_of_stock():
    last = parse_listing(BOTTLES_LAST)
    assert (last.page, last.pages) == (23, 23)
    assert last.items == [BCItem("266", 'Пиво "379 American Wheat Ale" Citrus 0,33л', 740, True,
                                 product("garejowr-379-american-wheat-ale-citrus-033l"))]
    draft_page = parse_listing(DRAFT_LAST)
    assert (draft_page.page, draft_page.pages) == (3, 3)
    assert [i.item_id for i in draft_page.items] == DRAFT_IDS
    # a.addtocart-but.diss-prod marks the only card that is out of stock
    assert draft_page.items[2] == BCItem("12", 'Draught beer "379" non-filtered 1l', 1700, False,
                                         product("garejur-379-chfiltrvac-1l"))
    assert [i.item_id for i in draft_page.items if not i.in_stock] == ["12"]


def test_parse_listing_without_counter_is_a_single_page():
    listing = parse_listing(json.dumps({"link": "", "products": P1_CARDS["2053"]}))
    assert (listing.page, listing.pages) == (1, 1)
    assert [i.item_id for i in listing.items] == ["2053"]


def test_parse_listing_missing_price_and_incomplete_cards():
    no_price = P1_CARDS["2052"].replace('<span class="wh-point">730</span>', "")
    no_id = P1_CARDS["2051"].replace('data-product="2051"', "")
    no_name = P1_CARDS["2050"].replace("prod-item-name", "prod-item-title")
    listing = parse_listing(page_json([no_price, no_id, no_name], 1, 1))
    assert listing.items == [BCItem("2052", 'Beer "Bronx black cherry" 0.5L', None, True,
                                    product("garejur-bronx-black-cheri-05l"))]


@pytest.mark.parametrize("text", ["<html>Just a moment...</html>", "[]", '{"link": "x"}', '{"products": null}'])
def test_parse_listing_rejects_wrong_shape(text):
    with pytest.raises(ValueError):
        parse_listing(text)


# --- parse_product / clean_name ----------------------------------------------

def test_parse_product_reads_brand_and_characteristics():
    assert parse_product(PRODUCT_IPA) == BCProduct(brand="Konix", volume_ml=450, abv=7.6, country="Russia",
                                                   container="can")   # "0.45 liter", "7.6 %", "Tin"
    assert parse_product(PRODUCT_NONALC) == BCProduct("Konix", 450, 0.5, "Russia", "can")


def test_parse_product_units_packaging_and_missing_fields():
    glass = (PRODUCT_IPA.replace("<span>0.45 liter</span>", "<span>500ml</span>")
             .replace("<span>7.6 %</span>", "<span>4,7 %</span>")
             .replace("<span>Tin </span>", "<span>Glass </span>"))
    p = parse_product(glass)
    assert (p.volume_ml, p.abv, p.container) == (500, 4.7, "bottle")
    assert parse_product("<html><body>Not found</body></html>") == BCProduct(None, None, None, None, None)


@pytest.mark.parametrize("title, name", [
    ('Beer "Hard root" Double IPA 0.45 l', "Hard root Double IPA"),
    ('Beer "Starnberger helles" 0,45l', "Starnberger helles"),
    ("Beer «Estrella Galicia» 0,5l", "Estrella Galicia"),
    ('Beer "Corona cero 0%" 0.33l', "Corona cero 0%"),
    ('Draught beer "Kellers" non-filtered 1l', "Kellers non-filtered"),
    ('Пиво "379 American Wheat Ale" Citrus 0,33л', "379 American Wheat Ale Citrus"),
])
def test_clean_name_drops_type_word_quotes_and_volume(title, name):
    assert clean_name(title) == name


# --- fetch_beercity ------------------------------------------------------------

def test_partial_run_without_new_ids_reads_only_first_page_of_each_category():
    result, http = fetch({bottles(1): BOTTLES_P1, draft(1): page_json(DRAFT_CARDS.values(), 1, 3)},
                         known=P1_IDS + DRAFT_IDS, aliases={"starnberger": "starnberger brauhaus"})
    assert http.calls == [(bottles(1), XHR), (draft(1), XHR)]   # no product pages, no page 2
    assert (result.ok, result.full, result.error) == (True, False, None)
    assert (result.key, result.source, result.place_id) == ("beercity:beer-city", "beercity", "beer-city")
    assert ids(result) == P1_IDS + DRAFT_IDS   # known items are reported too
    s = by_id(result)
    assert s["2045"] == Sighting(
        place_id="beer-city", source="beercity", beer_key="n:starnberger brauhaus helles",   # alias applied
        title='Beer "Starnberger helles" 0,45l', name="Starnberger helles", seen_at=NOW,
        shop_item_id="2045", price_amd=730, in_stock=True, category="sshalcavac-garejur",
        url=product("garejur-starnberger-heles-045l"),   # known: no product page, so no brand/abv/volume
        shop_url=product("garejur-starnberger-heles-045l"))
    assert s["12"] == Sighting(
        place_id="beer-city", source="beercity", beer_key="n:379 non",
        title='Draught beer "379" non-filtered 1l', name="379 non-filtered", seen_at=NOW,
        shop_item_id="12", price_amd=1700, container="draft", in_stock=False, category="lcnovi-garejur",
        url=product("garejur-379-chfiltrvac-1l"), shop_url=product("garejur-379-chfiltrvac-1l"))
    assert s["2050"].beer_key == s["2049"].beer_key == "n:mythos"   # 0.3L and 0.5L share a key


def test_partial_run_fetches_new_product_pages_and_walks_on_while_a_page_had_new_ids():
    page2 = page_json([card("2039", 'Beer "Tomato method Adjika" 0.45l'),
                       card("2038", 'Beer "1715 lvivske" 0.45l')], 2, 23)
    known = [i for i in P1_IDS if i not in ("2048", "2047")] + ["2039", "2038"] + DRAFT_IDS
    result, http = fetch({bottles(1): BOTTLES_P1, NONALC_URL: PRODUCT_NONALC, IPA_URL: PRODUCT_IPA,
                          bottles(2): page2, draft(1): page_json(DRAFT_CARDS.values(), 1, 3)}, known=known)
    # page 1 had new ids -> page 2; page 2 had none -> stop before page 3
    assert http.calls == [(bottles(1), XHR), (NONALC_URL, {}), (IPA_URL, {}), (bottles(2), XHR), (draft(1), XHR)]
    assert result.ok and result.full is False
    assert ids(result) == P1_IDS + ["2039", "2038"] + DRAFT_IDS
    s = by_id(result)
    assert s["2047"] == Sighting(
        place_id="beer-city", source="beercity", beer_key="n:hard root double ipa",
        title='Beer "Hard root" Double IPA 0.45 l', name="Hard root Double IPA", seen_at=NOW,
        brewery="Konix", shop_item_id="2047", abv=7.6, price_amd=2200, volume_ml=450, container="can",
        in_stock=True, category="sshalcavac-garejur", url=IPA_URL, shop_url=IPA_URL)
    assert (s["2048"].brewery, s["2048"].abv, s["2048"].name) == ("Konix", 0.5, "Pure wave IPA non alco")
    assert s["2053"].brewery is None


def test_failed_product_page_drops_only_that_item():
    known = [i for i in P1_IDS if i != "2048"] + DRAFT_IDS
    result, http = fetch({bottles(1): BOTTLES_P1, NONALC_URL: FetchError("http", "404"),
                          bottles(2): page_json([], 2, 23), draft(1): page_json(DRAFT_CARDS.values(), 1, 3)},
                         known=known)
    assert result.ok
    assert ids(result) == [i for i in P1_IDS if i != "2048"] + DRAFT_IDS
    # the dropped id was the only new one on page 1 and still made the walk go on to page 2
    assert [url for url, _ in http.calls] == [bottles(1), NONALC_URL, bottles(2), draft(1)]


def test_full_run_walks_every_page_and_fetches_no_known_product_pages():
    responses = {bottles(n): page_json([], n, 23) for n in range(2, 23)}
    responses |= {bottles(1): BOTTLES_P1, bottles(23): BOTTLES_LAST,
                  draft(1): page_json([], 1, 3), draft(2): page_json([], 2, 3), draft(3): DRAFT_LAST}
    result, http = fetch(responses, known=P1_IDS + ["266"] + DRAFT_IDS, full=True)
    assert http.calls == [(bottles(n), XHR) for n in range(1, 24)] + [(draft(n), XHR) for n in (1, 2, 3)]
    assert (result.ok, result.full) == (True, True)
    assert ids(result) == P1_IDS + ["266"] + DRAFT_IDS
    old = by_id(result)["266"]   # the oldest items are titled "Пиво"
    assert (old.name, old.beer_key) == ("379 American Wheat Ale Citrus", "n:379 american wheat ale citrus")


def test_full_run_follows_a_list_that_grows_during_the_walk():
    # a new item pushed card 2051 from page 1 onto page 2 and added a third page
    responses = {bottles(1): page_json([P1_CARDS[i] for i in ("2053", "2052", "2051")], 1, 2),
                 bottles(2): page_json([P1_CARDS["2051"], P1_CARDS["2050"]], 2, 3),
                 bottles(3): page_json([P1_CARDS["2049"]], 3, 3),
                 draft(1): page_json(DRAFT_CARDS.values(), 1, 1)}
    result, http = fetch(responses, known=P1_IDS + DRAFT_IDS, full=True)
    assert [url for url, _ in http.calls] == [bottles(1), bottles(2), bottles(3), draft(1)]
    assert ids(result) == ["2053", "2052", "2051", "2050", "2049"] + DRAFT_IDS   # 2051 reported once


def test_titles_not_starting_with_beer_are_skipped_and_never_count_as_new():
    page1 = page_json([card("2060", "Gift card 10000 AMD"), card("2059", 'Set "Beer lovers" 4 bottles + glass'),
                       card("2058", "Beer 0.5L"),   # nothing left after normalization: key "n:"
                       P1_CARDS["2053"]], 1, 23)
    result, http = fetch({bottles(1): page1, draft(1): page_json(DRAFT_CARDS.values(), 1, 3)},
                         known=["2053"] + DRAFT_IDS)
    assert http.calls == [(bottles(1), XHR), (draft(1), XHR)]   # no product pages, no page 2
    assert ids(result) == ["2053"] + DRAFT_IDS


@pytest.mark.parametrize("responses, full, error", [
    ({bottles(1): FetchError("network", "timeout")}, False, "network"),
    ({bottles(1): FetchError("cloudflare")}, True, "cloudflare"),
    ({bottles(1): "<html>Just a moment...</html>"}, False, "bad_response"),
    ({bottles(1): BOTTLES_P1, draft(1): FetchError("http", "503")}, False, "http"),
    ({bottles(1): BOTTLES_P1, bottles(2): FetchError("network")}, True, "network"),
    ({bottles(1): BOTTLES_P1, bottles(2): page_json([], 1, 23)}, True, "bad_response"),   # page param ignored
    ({bottles(1): BOTTLES_P1, bottles(2): page_json([], 2, MAX_PAGES + 1)}, True, "bad_response"),
])
def test_any_listing_failure_fails_the_whole_run(responses, full, error):
    result, _ = fetch(responses, known=P1_IDS + DRAFT_IDS, full=full)
    assert (result.ok, result.error, result.full, result.sightings) == (False, error, full, [])
    assert (result.key, result.place_id) == ("beercity:beer-city", "beer-city")


def test_clean_name_strips_double_apostrophe_quotes():
    from taps.sources.beercity import clean_name
    assert clean_name("Beer ''Forged'' irish stout 0,5l") == "Forged irish stout"
    assert clean_name("Beer ''Ayinger celebrator '' dunkles 0,33l") == "Ayinger celebrator dunkles"
    assert clean_name("Beer «Brewer's Choice» IPA 0,5l") == "Brewer's Choice IPA"
