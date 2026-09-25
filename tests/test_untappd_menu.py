from collections import Counter
from datetime import datetime, timezone

import pytest
from bs4 import BeautifulSoup

from taps.config import Place
from taps.fetch import FetchError
from taps.model import Serving, Sighting
from taps.sources.untappd_menu import SKIP_TAB_RE, MenuItem, fetch_menu, parse_menu_page
from tests.helpers import fixture_text

NOW = datetime(2026, 9, 24, 6, 17, tzinfo=timezone.utc)
GARGOYLE = Place(id="gargoyle", name="Gargoyle Bar", kind="bar",
                 sources={"untappd_menu": {"slug": "gargoyle-bar", "venue_id": 12252462}})
BEATLES = Place(id="beatles", name="Beatles Pub", kind="bar",
                sources={"untappd_menu": {"slug": "beatles-pub-yerevan", "venue_id": 2162817}})
GARGOYLE_URL = "https://untappd.com/v/gargoyle-bar/12252462"
BEATLES_URL = "https://untappd.com/v/beatles-pub-yerevan/2162817"
TAP_LIST = fixture_text("untappd/gargoyle_menu.html")     # default venue page: "On Tap", 3 beers
FOOD_TAB = fixture_text("untappd/gargoyle_menu_tab.html")  # ?menu_id=234933: "Food by Kruzhok", 17 items
BEATLES_MENU = fixture_text("untappd/beatles_menu.html")   # single-menu venue, 141 rows


class FakeClient:
    """url -> html, or an exception to raise; records requested urls."""

    def __init__(self, pages):
        self.pages, self.urls = pages, []

    def get(self, url):
        self.urls.append(url)
        page = self.pages[url]
        if isinstance(page, Exception):
            raise page
        return page


def first_by_id(items):
    out = {}
    for it in items:
        out.setdefault(it.beer_id, it)
    return out


def with_prices(rows_html):
    """The Gargoyle page with a price block added to its first beer (no captured beer row has prices)."""
    soup = BeautifulSoup(TAP_LIST, "html.parser")
    soup.select_one("li.menu-item").append(BeautifulSoup(rows_html, "html.parser"))
    return str(soup)


def with_price_rows(rows_html):
    return parse_menu_page(with_prices(rows_html)).items[0]


def price_row(size, price):
    return f'<div class="beer-prices"><p><span class="size">{size}</span><span class="price">{price}</span></p></div>'


def test_gargoyle_default_page_tabs_and_updated_time():
    page = parse_menu_page(TAP_LIST)
    assert page.tabs == [("203569", "On Tap"), ("203568", "Bottles And Cans"),
                         ("234933", "Food by Kruzhok"), ("250574", "Wine")]
    assert page.active_menu_id == "203569"   # no option is selected: matched by p.menu-total
    assert page.updated_at == datetime(2026, 4, 19, 10, 49, 24, 755755, tzinfo=timezone.utc)


def test_gargoyle_default_page_items():
    assert parse_menu_page(TAP_LIST).items == [
        MenuItem(beer_id=5817007, name="Hell", brewery="Dahook", brewery_id=559009, style="Lager - Helles",
                 abv=4.8, ibu=20, rating=3.42226, price_amd=None, volume_ml=None, container=None,
                 section="Tap List", url="https://untappd.com/b/dahook-hell/5817007",
                 logo="https://labels.untappd.com/5817007"),
        MenuItem(beer_id=5817002, name="Red IPA", brewery="Dahook", brewery_id=559009, style="IPA - Red",
                 abv=6.0, ibu=45, rating=3.66676, price_amd=None, volume_ml=None, container=None,
                 section="Tap List", url="https://untappd.com/b/dahook-red-ipa/5817002",
                 logo="https://labels.untappd.com/5817002"),
        MenuItem(beer_id=4775604, name="American Wheat Ale", brewery="379 Torch & Brew", brewery_id=518994,
                 style="Wheat Beer - American Pale Wheat", abv=4.8, ibu=25, rating=3.72004,
                 price_amd=None, volume_ml=None, container=None, section="Tap List",
                 url="https://untappd.com/b/379-torch-and-brew-american-wheat-ale/4775604",
                 logo="https://labels.untappd.com/4775604"),
    ]


def test_gargoyle_food_tab_has_no_beers():
    page = parse_menu_page(FOOD_TAB)
    assert page.tabs == [("203569", "On Tap"), ("203568", "Bottles And Cans"), ("234933", "Food by Kruzhok")]
    assert page.active_menu_id == "234933"   # option selected="TRUE"
    assert page.updated_at == datetime(2025, 5, 2, 23, 25, 38, 645009, tzinfo=timezone.utc)
    assert len(BeautifulSoup(FOOD_TAB, "html.parser").select("li.menu-item")) == 17
    assert page.items == []   # 13 food and 4 non-alcoholic rows, none links an Untappd beer


def test_beatles_single_menu_page():
    page = parse_menu_page(BEATLES_MENU)
    assert (page.tabs, page.active_menu_id) == ([], None)
    assert page.updated_at == datetime(2025, 6, 17, 10, 24, 26, 39219, tzinfo=timezone.utc)
    assert len(page.items) == 141
    assert Counter(it.section for it in page.items) == {
        "On Tap": 21, "Belgian": 41, "Trappist": 16, "German": 19, "Armenian": 18, "Other": 26}


def test_beatles_item_fields():
    items = first_by_id(parse_menu_page(BEATLES_MENU).items)
    assert items[4473] == MenuItem(   # "1. Guinness Draught": numbering stripped
        beer_id=4473, name="Guinness Draught", brewery="Guinness", brewery_id=49, style="Stout - Irish Dry",
        abv=4.2, ibu=45, rating=3.76508, price_amd=None, volume_ml=None, container=None, section="On Tap",
        url="https://untappd.com/b/guinness-guinness-draught/4473", logo="https://labels.untappd.com/4473")
    warsteiner = items[4305756]   # "N/A ABV • N/A IBU"
    assert (warsteiner.name, warsteiner.style, warsteiner.abv, warsteiner.ibu) == \
        ("Warsteiner 0,0% Isotonisch", "Non-Alcoholic - Other", None, None)
    stille = items[1365]          # "12% ABV • N/A IBU"
    assert (stille.brewery, stille.brewery_id, stille.abv, stille.ibu) == \
        ("Brouwerij De Dolle Brouwers", 272, 12.0, None)
    assert (items[16851].name, items[16851].brewery, items[16851].brewery_id) == \
        ("Aventinus (TAP06)", "Schneider Weisse G. Schneider & Sohn", 1023)
    assert [items[i].name for i in (420671, 5939, 4775604)] == ["1664 Rosé", "1664", "379 American Wheat Ale"]
    assert items[1518439].brewery_id == 265165   # Dargett


def test_beatles_beer_listed_in_two_sections():
    rows = [it for it in parse_menu_page(BEATLES_MENU).items if it.beer_id == 1518439]
    assert [(it.name, it.section, it.ibu) for it in rows] == [
        ("Pilsner (La Rapsodia)", "On Tap", 42), ("Pilsner (La Rapsodia)", "Armenian", 30)]


def test_price_volume_and_container_from_real_price_row():
    # The kombucha row of the food tab is the only captured price row with a size.
    food = BeautifulSoup(FOOD_TAB, "html.parser")
    kombucha = next(li for li in food.select("li.menu-item") if "kombucha" in li.get_text())
    item = with_price_rows(str(kombucha.select_one("div.beer-prices")))
    assert (item.beer_id, item.price_amd, item.volume_ml, item.container) == (5817007, 1900, 500, "bottle")


@pytest.mark.parametrize("rows, expected", [
    ('<p><span class="price">2300.00 AMD</span></p>', (2300, None, None)),
    ('<p><span class="size">0.5L Draft</span><span class="price">1,800.00 AMD</span></p>', (1800, 500, "draft")),
    ('<p><span class="size">33cl Can</span><span class="price">$ 7.00</span></p>', (None, 330, "can")),
    ('<p><span class="size">12oz Draft</span><span class="price">$ 7.00</span></p>'
     '<p><span class="size">0.3L Draft</span><span class="price">֏ 1200</span></p>', (1200, 300, "draft")),
])
def test_price_rows(rows, expected):
    item = with_price_rows(f'<div class="beer-prices">{rows}</div>')
    assert (item.price_amd, item.volume_ml, item.container) == expected


@pytest.mark.parametrize("value", ["0", "N/A"])
def test_rating_zero_or_unreadable_is_none(value):
    html = TAP_LIST.replace('data-rating="3.42226"', f'data-rating="{value}"')
    assert parse_menu_page(html).items[0].rating is None


@pytest.mark.parametrize("value", ["soon", "2026-04-19T10:49:24"])   # garbage, naive time
def test_unreadable_updated_time_is_none(value):
    html = TAP_LIST.replace('data-time="2026-04-19T10:49:24.755755Z"', f'data-time="{value}"')
    assert parse_menu_page(html).updated_at is None


@pytest.mark.parametrize("change", ["unwrap", "vanity"])
def test_brewery_without_w_link(change):
    soup = BeautifulSoup(TAP_LIST, "html.parser")
    link = soup.select_one("li.menu-item h6 span a")
    if change == "unwrap":
        link.unwrap()
    else:
        link["href"] = "/Dahook"
    item = parse_menu_page(str(soup)).items[0]
    assert (item.brewery, item.brewery_id, item.abv, item.ibu) == ("Dahook", None, 4.8, 20)


def test_shown_tab_unknown_or_ambiguous():
    soup = BeautifulSoup(TAP_LIST, "html.parser")
    soup.select_one("p.menu-total").string = "Seasonal"
    assert parse_menu_page(str(soup)).active_menu_id is None
    soup.select_one("p.menu-total").string = "On Tap"
    soup.select("select.menu-selector option")[1].string = "On Tap"   # two tabs named "On Tap"
    assert parse_menu_page(str(soup)).active_menu_id is None


@pytest.mark.parametrize("name", ["Food by Kruzhok", "Wine", "Cocktails", "Spirits", "Kitchen", "Кухня", "Вино"])
def test_skipped_tab_names(name):
    assert SKIP_TAB_RE.search(name)


@pytest.mark.parametrize("name", ["On Tap", "Bottles And Cans", "Beer Menu"])
def test_beer_tab_names(name):
    assert not SKIP_TAB_RE.search(name)


def test_fetch_gargoyle_reads_every_beer_tab():
    client = FakeClient({
        GARGOYLE_URL: TAP_LIST,
        GARGOYLE_URL + "?menu_id=203568": BEATLES_MENU,   # stand-in for "Bottles And Cans"
    })
    result = fetch_menu(client, GARGOYLE, NOW, {})
    # On Tap is the venue page itself; Food by Kruzhok and Wine are skipped.
    assert client.urls == [GARGOYLE_URL, GARGOYLE_URL + "?menu_id=203568"]
    assert (result.key, result.source, result.ok, result.error, result.place_id, result.full) == \
        ("untappd_menu:gargoyle", "untappd_menu", True, None, "gargoyle", True)
    assert result.venue_meta == {
        "venue_id": 12252462, "name": "Gargoyle Bar", "url": GARGOYLE_URL,
        "logo": "https://images.untp.beer/resize?width=176&height=176&background=255,255,255&extend=white&"
                "stripmeta=true&url=https://utfb-images.untappd.com/5u4i6ms9fe4iuzdw1dy36fvhunp0?auto=compress",
        "verified": True,
    }
    assert result.menu_updated_at == datetime(2026, 4, 19, 10, 49, 24, 755755, tzinfo=timezone.utc)  # newest tab
    keys = [s.beer_key for s in result.sightings]
    assert len(keys) == len(set(keys)) == 133   # 8 rows repeated on the stand-in, 3 beers in both tabs
    assert Counter(s.menu_id for s in result.sightings) == {"203569": 3, "203568": 130}
    red_ipa = next(s for s in result.sightings if s.beer_key == "u:5817002")
    assert red_ipa == Sighting(   # the On Tap row wins over the second tab's row (rating 3.69186)
        place_id="gargoyle", source="untappd_menu", beer_key="u:5817002", title="Dahook Red IPA",
        name="Red IPA", seen_at=NOW, brewery="Dahook", brewery_id=559009, untappd_beer_id=5817002,
        style="IPA - Red", abv=6.0, ibu=45, rating=3.66676, menu_id="203569",
        url="https://untappd.com/b/dahook-red-ipa/5817002", logo="https://labels.untappd.com/5817002")


def test_fetch_single_menu_venue():
    client = FakeClient({BEATLES_URL: BEATLES_MENU})
    result = fetch_menu(client, BEATLES, NOW, {"dahook": "dahook brewery"})
    assert client.urls == [BEATLES_URL]
    assert (result.ok, result.key, len(result.sightings)) == (True, "untappd_menu:beatles", 133)
    assert result.venue_meta["verified"] is True and result.venue_meta["venue_id"] == 2162817
    assert result.menu_updated_at == datetime(2025, 6, 17, 10, 24, 26, 39219, tzinfo=timezone.utc)
    assert all(s.menu_id is None and s.kind == "menu" and s.place_id == "beatles" and s.seen_at == NOW
               and s.beer_key == f"u:{s.untappd_beer_id}" for s in result.sightings)
    pils = [s for s in result.sightings if s.untappd_beer_id == 1518439]
    assert [(s.title, s.ibu) for s in pils] == [("Dargett Brewery Pilsner (La Rapsodia)", 42)]   # first row wins


def test_fetch_unknown_shown_tab_fetches_every_beer_tab():
    soup = BeautifulSoup(TAP_LIST, "html.parser")
    soup.select_one("p.menu-total").string = "Seasonal"
    client = FakeClient({
        GARGOYLE_URL: str(soup),
        GARGOYLE_URL + "?menu_id=203569": TAP_LIST,
        GARGOYLE_URL + "?menu_id=203568": FOOD_TAB,
    })
    result = fetch_menu(client, GARGOYLE, NOW, {})
    assert client.urls == [GARGOYLE_URL, GARGOYLE_URL + "?menu_id=203569", GARGOYLE_URL + "?menu_id=203568"]
    assert [(s.beer_key, s.menu_id) for s in result.sightings] == [
        ("u:5817007", "203569"), ("u:5817002", "203569"), ("u:4775604", "203569")]


def test_fetch_zero_beers_is_empty():
    # The venue page shows a skipped tab (food), so both beer tabs are fetched; none lists a beer.
    client = FakeClient({
        GARGOYLE_URL: FOOD_TAB,
        GARGOYLE_URL + "?menu_id=203569": FOOD_TAB,
        GARGOYLE_URL + "?menu_id=203568": FOOD_TAB,
    })
    result = fetch_menu(client, GARGOYLE, NOW, {})
    assert client.urls == [GARGOYLE_URL, GARGOYLE_URL + "?menu_id=203569", GARGOYLE_URL + "?menu_id=203568"]
    assert (result.ok, result.error, result.sightings, result.key) == (False, "empty", [], "untappd_menu:gargoyle")


def test_fetch_ignores_rows_of_a_skipped_shown_tab():
    # The venue page shows the Wine tab, which may list Untappd ciders: its rows are not used.
    soup = BeautifulSoup(TAP_LIST, "html.parser")
    soup.select_one('select.menu-selector option[value="250574"]')["selected"] = "TRUE"
    client = FakeClient({
        GARGOYLE_URL: str(soup),
        GARGOYLE_URL + "?menu_id=203569": FOOD_TAB,
        GARGOYLE_URL + "?menu_id=203568": FOOD_TAB,
    })
    result = fetch_menu(client, GARGOYLE, NOW, {})
    assert client.urls == [GARGOYLE_URL, GARGOYLE_URL + "?menu_id=203569", GARGOYLE_URL + "?menu_id=203568"]
    assert (result.ok, result.error, result.sightings) == (False, "empty", [])


@pytest.mark.parametrize("pages, kind", [
    ({GARGOYLE_URL: FetchError("cloudflare")}, "cloudflare"),
    ({GARGOYLE_URL: FetchError("blocked")}, "blocked"),
    ({GARGOYLE_URL: TAP_LIST, GARGOYLE_URL + "?menu_id=203568": FetchError("network")}, "network"),
    ({GARGOYLE_URL: TAP_LIST, GARGOYLE_URL + "?menu_id=203568": FetchError("budget")}, "budget"),
])
def test_fetch_error_fails_whole_menu(pages, kind):
    result = fetch_menu(FakeClient(pages), GARGOYLE, NOW, {})
    assert (result.ok, result.error, result.sightings, result.place_id, result.key) == \
        (False, kind, [], "gargoyle", "untappd_menu:gargoyle")


def test_fetch_beer_on_tap_and_in_bottles_is_one_sighting_with_both_servings():
    client = FakeClient({
        GARGOYLE_URL: with_prices(price_row("0.5L Draft", "1,800.00 AMD")),
        GARGOYLE_URL + "?menu_id=203568": with_prices(price_row("0.33L Bottle", "1,200.00 AMD")),
    })
    result = fetch_menu(client, GARGOYLE, NOW, {})
    assert [s.beer_key for s in result.sightings] == ["u:5817007", "u:5817002", "u:4775604"]
    hell = result.sightings[0]
    assert hell.menu_id == "203569"   # the first row gives the beer, both give servings
    assert (hell.container, hell.price_amd, hell.volume_ml) == ("draft", 1800, 500)   # what single-serving readers see
    assert hell.servings == (Serving("draft", 1800, 500), Serving("bottle", 1200, 330))
    assert [s.servings for s in result.sightings[1:]] == [(), ()]


def test_fetch_beer_in_two_sections_without_serving_details_stays_one_plain_serving():
    # Beatles lists 8 beers twice ("On Tap" plus a country or style section); no row has a price or a size
    items = parse_menu_page(BEATLES_MENU).items
    twice = {beer_id for beer_id, n in Counter(it.beer_id for it in items).items() if n > 1}
    assert len(twice) == 8
    result = fetch_menu(FakeClient({BEATLES_URL: BEATLES_MENU}), BEATLES, NOW, {})
    assert {s.untappd_beer_id for s in result.sightings} >= twice
    assert all(s.servings == () and (s.container, s.price_amd, s.volume_ml) == (None, None, None)
               for s in result.sightings)


def test_fetch_beer_in_two_sections_with_the_same_serving_stays_single():
    row = price_row("0.5L Draft", "1,800.00 AMD")
    client = FakeClient({GARGOYLE_URL: with_prices(row), GARGOYLE_URL + "?menu_id=203568": with_prices(row)})
    hell = fetch_menu(client, GARGOYLE, NOW, {}).sightings[0]
    assert (hell.container, hell.price_amd, hell.volume_ml, hell.servings) == ("draft", 1800, 500, ())


def test_fetch_second_row_gives_the_serving_the_first_row_lacks():
    client = FakeClient({GARGOYLE_URL: TAP_LIST,
                         GARGOYLE_URL + "?menu_id=203568": with_prices(price_row("0.33L Bottle", "1,200.00 AMD"))})
    hell = fetch_menu(client, GARGOYLE, NOW, {}).sightings[0]
    assert (hell.container, hell.price_amd, hell.volume_ml, hell.servings) == ("bottle", 1200, 330, ())
