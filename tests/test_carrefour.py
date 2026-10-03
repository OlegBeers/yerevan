import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from taps.config import Config, Place, Settings
from taps.corrections import Corrections
from taps.fetch import FetchError, HttpResponse
from taps.rules import merge_results
from taps.run import collect_shops
from taps.sources import carrefour
from taps.sources.carrefour import (
    CarrefourCard, CarrefourProduct, abv, clean_name, container, fetch_carrefour, is_beer_candidate, is_due_for_refresh, parse_listing,
    parse_product, parse_sitemap, volume_ml,
)
from taps.sources.robots import Robots
from taps.state import PairRec, empty_state
from tests.helpers import fixture_text

NOW = datetime(2026, 10, 3, 6, 35, tzinfo=timezone.utc)
PLACE = Place(id="carrefour", name="Carrefour", kind="shop", sources={"carrefour": {}})
ROBOTS_TXT = fixture_text("carrefour/robots.txt")
ROBOTS = Robots.parse(ROBOTS_TXT)
SITEMAP = fixture_text("carrefour/sitemap_en.xml")
LIST_BEER = fixture_text("carrefour/list_beer.html")           # 6 cards
LIST_ARMENIAN = fixture_text("carrefour/list_armenian.html")   # 5 cards
LIST_IMPORTED = fixture_text("carrefour/list_imported.html")   # 5 cards
KOZEL = fixture_text("carrefour/product_kozel.html")
TRIPEL = fixture_text("carrefour/product_dargett_tripel.html")
BALTIKA = fixture_text("carrefour/product_baltika_can.html")
BASE = "https://carrefour.am"
CATEGORY = BASE + "/en/everyday-products/alcoholic-beverages/beer"


@pytest.fixture(autouse=True)
def small_catalog(monkeypatch):
    monkeypatch.setattr(carrefour, "MIN_ITEMS", 5)   # the fixtures hold ~20 items, the real catalog ~125


# --- parse_sitemap ------------------------------------------------------------

def test_parse_sitemap_maps_product_slugs_to_their_titles():
    titles = parse_sitemap(SITEMAP)
    assert titles["beer-kozel-premium-lager-500ml-4-8"] == "Beer Kozel premium lager 500ml 4.8%"
    assert titles["beer-super-dry-asahi-glass-330-ml"] == "Beer super dry Asahi 5% 330ml"   # inner spaces collapsed
    assert titles["carrefour-panache-beer-250-ml"] == "Քարֆուր Պանաչե Գարեջուր 1% 250մլ"
    assert len(titles) == 18


def test_parse_sitemap_skips_pages_that_are_not_products():
    assert not any(slug in ("", "everyday-products") or "/" in slug for slug in parse_sitemap(SITEMAP))


def test_parse_sitemap_that_is_not_xml_is_a_value_error():
    with pytest.raises(ValueError):
        parse_sitemap("<html><body>Just a moment</body>")


# --- is_beer_candidate --------------------------------------------------------

def test_candidates_among_the_fixture_products():
    titles = parse_sitemap(SITEMAP)
    assert sorted(slug for slug, title in titles.items() if is_beer_candidate(title)) == [
        "379-unfiltered-dark-cherry-beer-non-alcoholic-0-33l", "baltika-beer-9-can-0-9l",
        "beer-kozel-premium-lager-500ml-4-8", "beer-super-dry-asahi-glass-330-ml", "carrefour-panache-beer-250-ml",
        "dargett-beer-belgian-tripel-g-b-0-33l", "dargett-cider-apple-dry-g-b-330ml", "kilikia-light-beer-250-ml",
        "volkovskaya-pivovarnya-beer-ipa-g-b-0-45l", "wheat-beer-379-weizen-unfiltered-light-0-33l"]


@pytest.mark.parametrize("title, expected", [
    ("Beer Kozel premium lager 500ml 4.8%", True),
    ("Volkovskaya pivovarnya Beer IPA g/b 0.45l", True),
    ("BrewDog Punk IPA 330ml", True),
    ("Dargett Cider apple, dry g/b 330ml", True),
    ("Քարֆուր Պանաչե Գարեջուր 1% 250մլ", True),
    ("Beef sticks, beer snack 30g", False),
    ("Mini salami beer snack 45g", False),
    ("Whisky Beer Cask Togouchi 700ml 40%", False),
    ("Chips and Sauce El Sabor Beer Time 175g", False),
    ("Apple Cider Vinegar Carrefour Classic 750ml", False),
    ("Freken Bock bamboo toothpicks, 250 pcs", False),
    ("Rye lavash Fitness Cook cut 250g", False),
    ("Fresh milk 1l", False),
])
def test_is_beer_candidate(title, expected):
    assert is_beer_candidate(title) is expected


# --- parse_listing ------------------------------------------------------------

def test_parse_listing_reads_the_cards_of_a_category_page():
    cards = parse_listing(LIST_BEER)
    assert [c.slug for c in cards] == [
        "beer-super-dry-asahi-glass-330-ml", "carrefour-panache-beer-250-ml", "beer-light-holsten-pilsener-4-8-500ml-g-c",
        "wellefbrau-premium-beer-500ml", "beer-kronenbourg-m-c-1664-5-500ml",
        "beer-grolsch-premium-lager-light-4-7-500ml"]
    assert cards[0] == CarrefourCard(
        slug="beer-super-dry-asahi-glass-330-ml", title="Beer super dry Asahi 5% 330ml", brand="Asahi",
        country="Japan", price_amd=590,
        photo="https://carrefour.am/media/catalog/product/cache/b3462413f3908e81ee91087e97d8978a/9/8/98153_1.png")


def test_parse_listing_card_without_country_has_none():
    grolsch = parse_listing(LIST_BEER)[-1]
    assert (grolsch.brand, grolsch.country, grolsch.price_amd) == ("Grolsch", None, 680)


def test_parse_listing_skips_a_card_of_another_site_or_without_a_title():
    html = LIST_BEER.replace("https://carrefour.am/en/wellefbrau-premium-beer-500ml", "https://evil.example/x")
    assert "wellefbrau-premium-beer-500ml" not in {c.slug for c in parse_listing(html)}


def test_parse_listing_of_nothing_is_empty():
    assert parse_listing("<html><body>no products</body></html>") == []


# --- parse_product ------------------------------------------------------------

def test_parse_product_reads_the_page():
    assert parse_product(KOZEL) == CarrefourProduct(
        title="Beer Kozel premium lager 500ml 4.8%", brand="Kozel", country=None, price_amd=590, in_stock=True,
        photo="https://carrefour.am/media/catalog/product/cache/250942eb213b1ca8cb63a54e3ea01577/1/1/111101_1.png",
        is_beer=True)


def test_parse_product_country_from_the_manufacture_line():
    assert parse_product(TRIPEL).country == "Armenia"
    assert parse_product(BALTIKA).country == "Russia"


def test_parse_product_not_salable_is_out_of_stock():
    assert parse_product(KOZEL.replace('"is_salable":"1"', '"is_salable":"0"')).in_stock is False


def test_parse_product_without_the_salable_flag_has_no_stock_answer():
    assert parse_product(KOZEL.replace('"is_salable":"1"', '"x":"1"')).in_stock is None


def test_parse_product_beer_only_under_the_beer_category():
    assert parse_product(TRIPEL).is_beer and parse_product(BALTIKA).is_beer
    wine = KOZEL.replace("/alcoholic-beverages/beer", "/alcoholic-beverages/wine")
    assert parse_product(wine).is_beer is False


def test_parse_product_page_without_a_title_is_none():
    assert parse_product("<html><body>Page not found</body></html>") is None


# --- names, volume, strength, container ---------------------------------------

@pytest.mark.parametrize("title, name", [
    ("Beer Kozel premium lager 500ml 4.8%", "Kozel premium lager"),
    ("Dargett Beer Belgian Tripel g/b 0.33l", "Dargett Belgian Tripel"),
    ("Beer Kronenbourg M/C 1664 5% 330ml", "Kronenbourg 1664"),
    ("Baltika Beer 9 (can) 0.9l", "Baltika 9"),
    ("Volkovskaya pivovarnya Beer IPA g/b 0.45l", "Volkovskaya pivovarnya IPA"),
    ("Beer light Holsten Pilsener 4,8% 500ml", "light Holsten Pilsener"),
    ("Wheat beer 379 Weizen, unfiltered, light, 0.33L", "Wheat 379 Weizen, unfiltered, light"),
])
def test_clean_name(title, name):
    assert clean_name(title) == name


@pytest.mark.parametrize("title, ml", [
    ("Beer Kozel premium lager 500ml 4.8%", 500), ("Beer Gold 0.5l", 500), ("Beer Light 0.33 l", 330),
    ("Zatecky Gus Beer light 1.35l", 1350), ("Beer 1l", 1000), ("Wheat beer 379 Weizen, 0.33L", 330),
    ("Beer Estrella Galicia Especial 330 ml", 330), ("Քարֆուր Պանաչե Գարեջուր 1% 250մլ", None),
])
def test_volume_ml(title, ml):
    assert volume_ml(title) == ml


@pytest.mark.parametrize("title, value", [
    ("Beer Kozel premium lager 500ml 4.8%", 4.8), ("Beer Grolsch Premium Lager Light 4,7% 500ml", 4.7),
    ("Beer Gold 0.5l", None), ("Beer Baltika 0 non-alcoholic g/b 0.5l", None),
])
def test_abv(title, value):
    assert abv(title) == value


@pytest.mark.parametrize("title, expected", [
    ("Baltika Beer 9 (can) 0.9l", "can"), ("Beer Kronenbourg M/C 1664 5% 330ml", None),
    ("Dargett Beer Belgian Tripel g/b 0.33l", "bottle"), ("Beer Pet Gold Gyumri 1.5l", "bottle"),
    ("Kilikia draft Beer 1l", "draft"), ("Beer Kozel premium lager 500ml 4.8%", None),
])
def test_container(title, expected):
    assert container(title) == expected


# --- fetch_carrefour ----------------------------------------------------------

CATEGORIES = [CATEGORY, CATEGORY + "/armenian-beer", CATEGORY + "/beer-imported"]
CARDS = {c.slug: c for html in (LIST_BEER, LIST_ARMENIAN, LIST_IMPORTED) for c in parse_listing(html)}
SITEMAP_ONLY = sorted(set(slug for slug, title in parse_sitemap(SITEMAP).items() if is_beer_candidate(title)) - set(CARDS))
ALL_SLUGS = sorted(set(CARDS) | set(slug for slug, title in parse_sitemap(SITEMAP).items() if is_beer_candidate(title)))
RUN_TIMES = [NOW + timedelta(hours=12 * k) for k in range(14)]   # a week of the two daily runs


def due(now):
    return sorted(s for s in ALL_SLUGS if is_due_for_refresh(s, now))


QUIET = next(t for t in RUN_TIMES if not due(t))      # a run that refreshes none of the fixture items
BUSY = next(t for t in RUN_TIMES if len(due(t)) >= 2)


def product_url(slug):
    return f"{BASE}/en/{slug}"


def pages(**extra):
    out = {BASE + "/robots.txt": ROBOTS_TXT, BASE + "/sitemap_en.xml": SITEMAP,
           **dict(zip(CATEGORIES, (LIST_BEER, LIST_ARMENIAN, LIST_IMPORTED)))}
    out.update({product_url("beer-kozel-premium-lager-500ml-4-8"): KOZEL,
                product_url("dargett-beer-belgian-tripel-g-b-0-33l"): TRIPEL,
                product_url("baltika-beer-9-can-0-9l"): BALTIKA})
    out.update({product_url(slug): page for slug, page in extra.items()})
    return out


class FakeHttp:
    """Serves pages by URL; an exception value is raised; a product page not listed falls back to `product`."""

    def __init__(self, served, product=None):
        self.served, self.product = served, product
        self.urls = []

    def get(self, url, headers=None):
        self.urls.append(url)
        body = self.served.get(url)
        if body is None and self.product is not None and url.startswith(BASE + "/en/") and "/" not in url[len(BASE) + 4:]:
            body = self.product
        assert body is not None, f"unexpected GET {url}"
        if isinstance(body, Exception):
            raise body
        return HttpResponse(200, {"content-type": "text/html; charset=UTF-8"}, body)

    def product_urls(self):
        return [u for u in self.urls if u.startswith(BASE + "/en/") and u.count("/") == 4]


def fetch(served=None, known=None, now=QUIET, product=None, aliases=None):
    """known: slugs the state already knows (default: every item), or a {slug: stored brand} dict."""
    http = FakeHttp(served or pages(), product)
    known = known if isinstance(known, dict) else dict.fromkeys(ALL_SLUGS if known is None else known)
    result = fetch_carrefour(http, PLACE, known, now, aliases or {})
    return result, http


def by_slug(result):
    return {s.shop_item_id: s for s in result.sightings}


def test_fetch_reads_robots_sitemap_and_page_one_of_each_beer_category():
    result, http = fetch()
    assert result.ok and result.full and (result.key, result.source) == ("carrefour:carrefour", "carrefour")
    assert http.urls == [BASE + "/robots.txt", BASE + "/sitemap_en.xml", *CATEGORIES]
    assert sorted(by_slug(result)) == ALL_SLUGS


def test_fetch_known_items_cost_no_product_page_outside_their_refresh_day():
    _, http = fetch()
    assert http.product_urls() == []


def test_fetch_reads_every_new_item_that_no_listing_card_describes():
    result, http = fetch(known=set(), product=KOZEL)
    assert sorted(http.product_urls()) == sorted(product_url(s) for s in SITEMAP_ONLY)
    assert sorted(by_slug(result)) == ALL_SLUGS


def test_fetch_card_item_comes_from_the_card_without_a_page_read():
    result, http = fetch(known=set(), product=KOZEL)
    s = by_slug(result)["beer-super-dry-asahi-glass-330-ml"]
    assert (s.place_id, s.source, s.title, s.name) == ("carrefour", "carrefour", "Beer super dry Asahi 5% 330ml", "super dry Asahi")
    assert (s.brewery, s.country, s.price_amd, s.abv, s.volume_ml) == ("Asahi", "Japan", 590, 5.0, 330)
    assert (s.in_stock, s.country_checked, s.category, s.beer_key) == (None, None, "beer", "n:super dry asahi")
    assert s.url == s.shop_url == "https://carrefour.am/en/beer-super-dry-asahi-glass-330-ml"
    assert s.logo.startswith("https://carrefour.am/media/catalog/product/cache/") and s.kind == "shop"


def test_fetch_puts_the_shops_brand_in_front_of_a_title_that_omits_it():
    result, _ = fetch(known=set(), product=KOZEL)
    gold = by_slug(result)["beer-gold-250-ml"]
    assert (gold.title, gold.brewery, gold.name, gold.beer_key) == ("Beer Gold 250 ml", "Gyumri", "Gyumri Gold", "n:gyumri gold")
    assert by_slug(result)["beer-gold-1l"].beer_key == "n:gyumri gold"   # another size of the same beer


def test_fetch_unread_known_item_takes_its_brand_from_the_state():
    slug = "kilikia-light-beer-250-ml"
    result, _ = fetch(known={**dict.fromkeys(ALL_SLUGS), slug: "Acme"})
    s = by_slug(result)[slug]
    assert (s.brewery, s.name) == ("Acme", "Acme Kilikia Light")


def test_fetch_product_page_gives_title_brand_country_stock_and_photo():
    result, _ = fetch(known=set(), product=KOZEL)
    s = by_slug(result)["dargett-beer-belgian-tripel-g-b-0-33l"]
    assert (s.title, s.name, s.brewery, s.country, s.country_checked) == (
        "Dargett Beer Belgian Tripel g/b 0.33l", "Dargett Belgian Tripel", "Dargett", "Armenia", True)
    assert (s.price_amd, s.in_stock, s.volume_ml, s.container, s.abv) == (670, True, 330, "bottle", None)
    assert s.logo.endswith("/6/4/64704.png")
    can = by_slug(result)["baltika-beer-9-can-0-9l"]
    assert (can.container, can.volume_ml, can.price_amd) == ("can", 900, 1190)


def test_fetch_known_item_missing_from_every_listing_keeps_its_sitemap_title_only():
    result, http = fetch()
    s = by_slug(result)["kilikia-light-beer-250-ml"]
    assert (s.title, s.brewery, s.price_amd, s.in_stock, s.logo) == ("Kilikia Light Beer 250ml", None, None, None, None)
    assert (s.volume_ml, s.shop_url) == (250, "https://carrefour.am/en/kilikia-light-beer-250-ml")


def test_fetch_item_gone_from_sitemap_and_listings_is_not_reported():
    result, _ = fetch(known=[*ALL_SLUGS, "long-gone-beer"])
    assert "long-gone-beer" not in by_slug(result)


def test_fetch_new_item_outside_the_beer_category_is_left_out():
    wine = KOZEL.replace("/alcoholic-beverages/beer", "/alcoholic-beverages/wine")
    result, _ = fetch(served=pages(**{slug: wine for slug in SITEMAP_ONLY}), known=set())
    assert sorted(by_slug(result)) == sorted(CARDS)


def test_fetch_new_item_whose_page_fails_is_left_out_this_run():
    served = pages(**{"wheat-beer-379-weizen-unfiltered-light-0-33l": FetchError("http", "503")})
    result, _ = fetch(served=served, known=set(), product=KOZEL)
    assert "wheat-beer-379-weizen-unfiltered-light-0-33l" not in by_slug(result)
    assert len(result.sightings) == len(ALL_SLUGS) - 1


def test_fetch_new_pages_per_run_are_capped(monkeypatch):
    monkeypatch.setattr(carrefour, "MAX_PRODUCT_PAGES", 3)
    result, http = fetch(known=set(), product=KOZEL)
    assert len(http.product_urls()) == 3
    assert len(result.sightings) == len(CARDS) + 3


def test_fetch_failed_refresh_of_a_known_item_is_ignored():
    slug = due(BUSY)[0]
    result, http = fetch(served=pages(**{slug: FetchError("network", "boom")}), now=BUSY, product=KOZEL)
    assert result.ok and slug in by_slug(result)


# --- the rolling weekly refresh -----------------------------------------------

def test_a_week_of_runs_refreshes_every_item_exactly_once():
    seen = [slug for t in RUN_TIMES for slug in due(t)]
    assert sorted(seen) == ALL_SLUGS


def test_fetch_refreshes_the_items_due_today():
    slugs = due(BUSY)
    result, http = fetch(served=pages(), now=BUSY, product=KOZEL)
    assert sorted(http.product_urls()) == sorted(product_url(s) for s in slugs)
    refreshed = by_slug(result)[slugs[0]]
    assert refreshed.in_stock is True and refreshed.country_checked is True


def test_fetch_refresh_can_mark_an_item_out_of_stock():
    slug = due(BUSY)[0]
    sold_out = KOZEL.replace('"is_salable":"1"', '"is_salable":"0"')
    result, _ = fetch(served=pages(**{slug: sold_out}), now=BUSY, product=KOZEL)
    assert by_slug(result)[slug].in_stock is False


# --- failures and robots.txt --------------------------------------------------

@pytest.mark.parametrize("failing", [BASE + "/robots.txt", BASE + "/sitemap_en.xml", *CATEGORIES])
def test_fetch_any_listing_or_sitemap_failure_fails_the_whole_run(failing):
    result, _ = fetch(served={**pages(), failing: FetchError("network", "boom")})
    assert (result.ok, result.error, result.sightings, result.place_id) == (False, "network", [], "carrefour")


def test_fetch_sitemap_that_is_not_a_sitemap_is_a_bad_response():
    result, _ = fetch(served={**pages(), BASE + "/sitemap_en.xml": "<html><title>Just a moment</title></html>"})
    assert (result.ok, result.error) == (False, "bad_response")


def test_fetch_too_few_items_is_an_empty_run(monkeypatch):
    monkeypatch.setattr(carrefour, "MIN_ITEMS", 30)
    result, _ = fetch()
    assert (result.ok, result.error) == (False, "empty")


def test_fetch_never_requests_a_url_robots_txt_disallows():
    result, http = fetch(known=set(), product=KOZEL)
    assert result.ok and len(http.product_urls()) == len(SITEMAP_ONLY)
    assert http.urls[0] == BASE + "/robots.txt"
    assert [u for u in http.urls if not ROBOTS.allowed(u)] == []
    assert not any("?" in u for u in http.urls)   # robots.txt: "Disallow: /*?"


def test_fetch_stops_when_robots_txt_forbids_the_sitemap():
    served = {**pages(), BASE + "/robots.txt": "User-agent: *\nDisallow: /sitemap\n"}
    result, http = fetch(served=served)
    assert (result.ok, result.error) == (False, "blocked")
    assert http.urls == [BASE + "/robots.txt"]


def test_fetch_uses_brewery_aliases_for_the_beer_key():
    result, _ = fetch(aliases={"asahi": "asahi breweries"})
    assert by_slug(result)["beer-super-dry-asahi-glass-330-ml"].beer_key == "n:super dry asahi breweries"


# --- through the merge: a new source starts silent ----------------------------

def test_first_run_is_a_silent_baseline_and_a_later_new_item_is_an_event():
    config = Config(places={"carrefour": PLACE}, breweries=(), settings=Settings())
    state = empty_state(NOW)
    first, _ = fetch(known=set(), product=KOZEL)

    out = merge_results(state, [first], config, Corrections(), NOW)

    assert out.events == []
    assert state.pairs["carrefour"] and {p.notified_at for p in state.pairs["carrefour"].values()} == {"baseline"}
    assert state.source("carrefour:carrefour").baseline_done

    later = NOW + timedelta(hours=8)
    arrival = replace(first.sightings[0], shop_item_id="brewdog-punk-ipa-330ml", beer_key="n:brewdog punk ipa",
                      title="BrewDog Punk IPA 330ml", name="BrewDog Punk IPA", seen_at=later)
    out = merge_results(state, [replace(first, sightings=[*first.sightings, arrival])], config, Corrections(), later)
    assert out.events == [("carrefour", "n:brewdog punk ipa")]


def test_collect_shops_runs_carrefour_with_the_slugs_the_state_already_knows():
    config = Config(places={"carrefour": PLACE}, breweries=(), settings=Settings())
    state = empty_state(NOW)
    state.shop_items["carrefour"] = {slug: f"n:{slug}" for slug in ALL_SLUGS}
    http = FakeHttp(pages())
    results = collect_shops(state, config, Corrections(), QUIET, http)
    assert [(r.key, r.ok, len(r.sightings)) for r in results] == [("carrefour:carrefour", True, len(ALL_SLUGS))]
    assert http.product_urls() == []


def test_collect_shops_hands_carrefour_the_brand_stored_in_each_known_pair():
    config = Config(places={"carrefour": PLACE}, breweries=(), settings=Settings())
    state = empty_state(NOW)
    state.shop_items["carrefour"] = {slug: f"n:{slug}" for slug in ALL_SLUGS}
    state.pairs["carrefour"] = {"n:kilikia-light-beer-250-ml": PairRec(first_seen="x", last_seen="x", info={"brewery": "Acme"})}
    results = collect_shops(state, config, Corrections(), QUIET, FakeHttp(pages()))
    assert by_slug(results[0])["kilikia-light-beer-250-ml"].name == "Acme Kilikia Light"
