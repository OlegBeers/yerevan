import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from taps.config import Config, Place, Settings
from taps.corrections import Corrections
from taps.fetch import FetchError, HttpResponse
from taps.state import empty_state
from taps.rules import merge_results
from taps.run import collect_shops
from taps.sources.robots import Robots
from taps.sources.sas import (
    MAX_PAGES, MIN_CARDS, SasCard, SasProduct, clean_name, fetch_sas, parse_listing, parse_product, volume_ml,
)
from tests.helpers import fixture_text

NOW = datetime(2026, 10, 3, 14, 17, tzinfo=timezone.utc)
PLACE = Place(id="sas", name="SAS", kind="shop", sources={"sas": {}})
ROBOTS_TXT = fixture_text("sas/robots.txt")
ROBOTS = Robots.parse(ROBOTS_TXT)
ARM_P1 = fixture_text("sas/list_armyanskoe_p1.html")         # 24 cards, ids 135838 .. 135536
ARM_LAST = fixture_text("sas/list_armyanskoe_last.html")     # 9 cards
IMP_P1 = fixture_text("sas/list_importnoe_p1.html")          # 24 cards
IMP_LAST = fixture_text("sas/list_importnoe_last.html")      # 5 cards and the sold-out item 682628
PRODUCT_APRICOT = fixture_text("sas/product_dargett_apricot.html")   # item 135534
PRODUCT_SOLDOUT = fixture_text("sas/product_sherwood_soldout.html")  # item 682628
PRODUCT_VOLFAS = fixture_text("sas/product_volfas_ipa.html")         # item 471115


# --- parse_listing ------------------------------------------------------------

def test_parse_listing_reads_every_card_of_a_page():
    cards = parse_listing(ARM_P1, "armyanskoe")
    assert len(cards) == 24
    assert cards[0] == SasCard(
        item_id="135838", title='Beer "Kilikia" 0.5l', price_amd=560, in_stock=True,
        url="https://www.sas.am/en/catalog/armyanskoe/135838/",
        photo="https://www.sas.am/upload/Sh/imageCache/300/250/250309700146285.webp", section="armyanskoe")


def test_parse_listing_price_with_a_thousands_space():
    by_id = {c.item_id: c for c in parse_listing(ARM_P1, "armyanskoe")}
    assert by_id["948851"].price_amd == 1450   # "1 450 AMD"


def test_parse_listing_sold_out_card_has_no_price_and_is_not_in_stock():
    sold = {c.item_id: c for c in parse_listing(IMP_LAST, "importnoe_pivo")}["682628"]
    assert (sold.price_amd, sold.in_stock) == (None, False)
    assert sold.title == 'Beer cocktail "Sherwood" 0.5l Strawberry & Lemon'


def test_parse_listing_without_cards_is_empty():
    assert parse_listing("<html><body>nothing</body></html>", "armyanskoe") == []


def test_parse_listing_card_without_a_product_link_is_skipped():
    html = ARM_P1.replace('href="/en/catalog/armyanskoe/135838/"', 'href="/en/catalog/armyanskoe/"')
    assert "135838" not in {c.item_id for c in parse_listing(html, "armyanskoe")}


# --- parse_product ------------------------------------------------------------

def test_parse_product_brand_country_and_abv():
    assert parse_product(PRODUCT_APRICOT) == SasProduct(brand="Dargett", country="Armenia", abv=6.0)


@pytest.mark.parametrize("description, abv", [
    ("Beer, alc. 6%. A fine combination", 6.0),
    ("Light beer, unfiltered, alc.: 6%.", 6.0),
    ("Beer cocktail, alc: 4.5%.", 4.5),
    ("Beer, alc. 4,8%.", 4.8),
    ("Made from 100% natural malt", None),
])
def test_parse_product_abv_is_read_from_the_alcohol_phrase(description, abv):
    html = PRODUCT_APRICOT.replace("Beer, alc. 6%. A fine combination", description)
    assert parse_product(html).abv == abv


def test_parse_product_other_pages():
    assert parse_product(PRODUCT_VOLFAS) == SasProduct("Volfas Engelman", "Lithuania", 6.0)
    assert parse_product(PRODUCT_SOLDOUT) == SasProduct("Sherwood", "Lithuania", 4.5)


def test_parse_product_page_without_details_is_empty():
    assert parse_product("<html></html>") == SasProduct(None, None, None)


# --- names --------------------------------------------------------------------

@pytest.mark.parametrize("title, name", [
    ('Beer "Dargett Apricot" 0.33l', "Dargett Apricot"),
    ('Cherry flavored beer "Dargett Cherry Ale" 330ml', "Dargett Cherry Ale"),
    ("Beer ''Bud King of Beers'' 0.5l", "Bud King of Beers"),
    ('Beer  "Velkopopovicky Kozel"  0.5l', "Velkopopovicky Kozel"),
    ('Beer "Miller Genuine Draft" alc. 4,4% 0.47l', "Miller Genuine Draft"),
    ('Beer cocktail "Sherwood" 0.5l Strawberry & Lemon', "Sherwood"),
    ("Beer Hoegaarden 0.33l", "Hoegaarden"),
])
def test_clean_name_keeps_the_quoted_brand_and_name(title, name):
    assert clean_name(title) == name


@pytest.mark.parametrize("title, ml", [
    ('Beer "Kilikia" 0.5l', 500), ('Beer "379 Weizen" 330ml', 330), ('Beer "Krombacher Weizen" 0,5l', 500),
    ('Beer "Bitburger Premium" 0.33л', 330), ('Beer "Mythos" 0․33л', 330), ('Beer "Faxe" 5l', 5000),
    ('Beer "Bentley" 246ml', 246), ('Beer "Heineken 0.0" 0.33l', 330), ('Beer "Mr. P"', None),
])
def test_volume_ml(title, ml):
    assert volume_ml(title) == ml


# --- fetch_sas ----------------------------------------------------------------

def list_url(section, page):
    return f"https://www.sas.am/en/catalog/{section}/" + (f"?offset={24 * (page - 1)}" if page > 1 else "")


def item_url(section, item_id):
    return f"https://www.sas.am/en/catalog/{section}/{item_id}/"


def renumber(html, prefix):
    """Prefix every product id in the page, so a copy of a real page acts as a page of other items."""
    return re.sub(r"(/en/catalog/[a-z_]+/)(\d+)/", lambda m: f"{m.group(1)}{prefix}{m.group(2)}/", html)


# the real catalog on 2026-10-03: 57 Armenian and 149 imported beers; middle pages are renumbered copies
ARMENIAN = (ARM_P1, renumber(ARM_P1, "2"), ARM_LAST)
IMPORTED = (IMP_P1, *(renumber(IMP_P1, str(n)) for n in range(2, 7)), IMP_LAST)


def catalog(armenian=ARMENIAN, imported=IMPORTED):
    pages = {"https://www.sas.am/robots.txt": ROBOTS_TXT}
    for section, walk in (("armyanskoe", armenian), ("importnoe_pivo", imported)):
        pages.update({list_url(section, n): html for n, html in enumerate(walk, 1)})
    return pages


class FakeHttp:
    """Serves pages by URL; an exception value is raised; a product page not listed falls back to `product`."""

    def __init__(self, pages, product=None):
        self.pages, self.product = pages, product
        self.urls = []

    def get(self, url, headers=None):
        self.urls.append(url)
        body = self.pages.get(url)
        if body is None and self.product is not None and re.fullmatch(r".*/en/catalog/[a-z_]+/\d+/", url):
            body = self.product
        assert body is not None, f"unexpected GET {url}"
        if isinstance(body, Exception):
            raise body
        return HttpResponse(200, {"content-type": "text/html; charset=UTF-8"}, body)

    def product_urls(self):
        return [u for u in self.urls if re.fullmatch(r".*/en/catalog/[a-z_]+/\d+/", u)]


def all_ids(pages=None):
    pages = pages or catalog()
    return {c.item_id for url, html in pages.items() if isinstance(html, str) and "/catalog/" in url
            for c in parse_listing(html, "x")}


def fetch(pages=None, known=None, product=None, aliases=None):
    """known: ids the state already knows (default: every listed item). Item 135534 and the sold-out 682628
    have real product pages; any other product page needs `product`."""
    pages = {item_url("armyanskoe", "135534"): PRODUCT_APRICOT, item_url("importnoe_pivo", "682628"): PRODUCT_SOLDOUT,
             **(pages or catalog())}
    http = FakeHttp(pages, product)
    result = fetch_sas(http, PLACE, all_ids(pages) if known is None else known, NOW, aliases or {})
    return result, http


def test_fetch_walks_both_sections_by_offset_until_a_short_page():
    result, http = fetch()
    assert result.ok and result.full and result.source == "sas" and result.key == "sas:sas"
    assert http.urls == ["https://www.sas.am/robots.txt"] + [list_url("armyanskoe", n) for n in (1, 2, 3)] + [
        list_url("importnoe_pivo", n) for n in range(1, 8)]
    assert len(result.sightings) == 57 + 150


def test_fetch_known_items_cost_no_product_page():
    _, http = fetch()
    assert http.product_urls() == []


def test_fetch_reads_the_product_page_of_each_new_item_once():
    known = all_ids() - {"135534", "682628"}
    result, http = fetch(known=known)
    assert sorted(http.product_urls()) == [item_url("armyanskoe", "135534"), item_url("importnoe_pivo", "682628")]
    by_id = {s.shop_item_id: s for s in result.sightings}
    apricot = by_id["135534"]
    assert (apricot.brewery, apricot.abv, apricot.country, apricot.country_checked) == ("Dargett", 6.0, "Armenia", True)
    assert by_id["135838"].brewery is None and by_id["135838"].country_checked is None   # known: not read again


def test_fetch_sighting_carries_the_listing_fields():
    result, _ = fetch()
    s = {s.shop_item_id: s for s in result.sightings}["135534"]
    assert (s.place_id, s.source, s.title, s.name) == ("sas", "sas", 'Beer "Dargett Apricot" 0.33l', "Dargett Apricot")
    assert (s.beer_key, s.seen_at, s.price_amd, s.volume_ml, s.in_stock) == ("n:dargett apricot", NOW, 730, 330, True)
    assert (s.category, s.url, s.shop_url) == ("armyanskoe", item_url("armyanskoe", "135534"), item_url("armyanskoe", "135534"))
    assert s.logo.startswith("https://www.sas.am/upload/Sh/imageCache/")
    assert s.kind == "shop"


def test_fetch_sold_out_item_is_reported_not_in_stock():
    result, _ = fetch()
    sold = {s.shop_item_id: s for s in result.sightings}["682628"]
    assert (sold.in_stock, sold.price_amd) == (False, None)


def test_fetch_new_item_whose_product_page_fails_is_left_out_this_run():
    pages = {**catalog(), item_url("armyanskoe", "135534"): FetchError("http", "503")}
    result, http = fetch(pages=pages, known=all_ids() - {"135534"})
    assert result.ok and "135534" not in {s.shop_item_id for s in result.sightings}
    assert len(result.sightings) == 206


def test_fetch_a_page_with_nothing_new_ends_the_walk():
    """A site that ignores the offset would answer page 1 again and again."""
    pages = catalog(armenian=(ARM_P1, ARM_P1, ARM_LAST))
    result, http = fetch(pages=pages)
    assert list_url("armyanskoe", 3) not in http.urls
    ids = [s.shop_item_id for s in result.sightings]
    assert len(ids) == len(set(ids)) == 24 + 150


def test_fetch_beer_key_leaves_out_the_strength_the_title_may_state():
    result, _ = fetch()
    keys = {s.title: s.beer_key for s in result.sightings}
    assert keys['Beer "Miller Genuine Draft" alc. 4,4% 0.47l'] == keys['Beer "Miller Genuine Draft" 0.33l'] == "n:miller genuine"


def test_fetch_beer_key_comes_from_the_quoted_name_not_the_flavour_words_around_it():
    result, _ = fetch()
    keys = {s.title: s.beer_key for s in result.sightings}
    assert keys['Cherry flavored beer "Dargett Cherry Ale" 330ml'] == "n:dargett cherry ale"


def test_fetch_uses_brewery_aliases_for_the_beer_key():
    result, _ = fetch(aliases={"dargett": "dargett craft"})
    assert {s.shop_item_id: s for s in result.sightings}["135534"].beer_key == "n:dargett craft apricot"


@pytest.mark.parametrize("failing", [list_url("armyanskoe", 2), list_url("importnoe_pivo", 1), "https://www.sas.am/robots.txt"])
def test_fetch_any_page_failure_fails_the_whole_run(failing):
    pages = {**catalog(), failing: FetchError("network", "boom")}
    result, _ = fetch(pages=pages)
    assert (result.ok, result.error, result.sightings, result.place_id) == (False, "network", [], "sas")


def test_fetch_too_few_cards_is_an_empty_run():
    result, _ = fetch(pages=catalog(armenian=(ARM_LAST,), imported=(IMP_LAST,)))
    assert (result.ok, result.error) == (False, "empty")
    assert MIN_CARDS > 15


def test_fetch_page_counter_is_bounded():
    endless = {list_url("armyanskoe", n): renumber(ARM_P1, str(n)) for n in range(1, MAX_PAGES + 5)}
    result, http = fetch(pages={**catalog(), **endless})
    assert len([u for u in http.urls if "/armyanskoe/" in u and "offset" in u or u.endswith("/armyanskoe/")]) == MAX_PAGES


def test_fetch_never_requests_a_url_robots_txt_disallows():
    result, http = fetch(known=set(), product=PRODUCT_VOLFAS)
    assert result.ok and len(http.product_urls()) > 200
    assert http.urls[0] == "https://www.sas.am/robots.txt"
    assert [u for u in http.urls if not ROBOTS.allowed(u)] == []


def test_fetch_stops_when_robots_txt_forbids_the_catalog():
    pages = {**catalog(), "https://www.sas.am/robots.txt": "User-agent: *\nDisallow: /en/catalog/\n"}
    result, http = fetch(pages=pages)
    assert (result.ok, result.error) == (False, "blocked")
    assert http.urls == ["https://www.sas.am/robots.txt"]


# --- through the merge: a new source starts silent ----------------------------

def test_first_run_is_a_silent_baseline_and_a_later_new_item_is_an_event():
    config = Config(places={"sas": PLACE}, breweries=(), settings=Settings())
    state = empty_state(NOW)
    first, _ = fetch()

    out = merge_results(state, [first], config, Corrections(), NOW)

    assert out.events == []
    assert state.pairs["sas"] and {p.notified_at for p in state.pairs["sas"].values()} == {"baseline"}
    assert state.source("sas:sas").baseline_done

    later = NOW + timedelta(hours=8)
    arrival = replace(first.sightings[0], shop_item_id="999999", beer_key="n:brand new ipa",
                      title='Beer "Brand New IPA" 0.33l', name="Brand New IPA", seen_at=later)
    out = merge_results(state, [replace(first, sightings=[*first.sightings, arrival])], config, Corrections(), later)
    assert out.events == [("sas", "n:brand new ipa")]


def test_collect_shops_runs_sas_with_the_items_the_state_already_knows():
    config = Config(places={"sas": PLACE}, breweries=(), settings=Settings())
    state = empty_state(NOW)
    state.shop_items["sas"] = {item_id: f"n:item {item_id}" for item_id in all_ids()}
    http = FakeHttp(catalog())
    results = collect_shops(state, config, Corrections(), NOW, http)
    assert [(r.key, r.ok, len(r.sightings)) for r in results] == [("sas:sas", True, 207)]
    assert http.product_urls() == []
