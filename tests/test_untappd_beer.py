from taps.sources.untappd_beer import parse_beer_page
from tests.helpers import fixture_text

PAGE = fixture_text("untappd/beer_page.html")


def test_parse_beer_page_fields():
    assert parse_beer_page(PAGE) == {"style": "Fruit Beer", "abv": 6.2, "ibu": 18, "rating": 3.82}


def test_parse_beer_page_missing_abv_or_ibu_is_none():
    page = PAGE.replace('<p class="abv">6.2<span>% ABV</span></p>', "").replace(
        '<p class="ibu">18<span> IBU</span></p>', "")
    parsed = parse_beer_page(page)
    assert (parsed["abv"], parsed["ibu"]) == (None, None)


def test_parse_beer_page_zero_ratings_is_none():
    page = PAGE.replace('data-rating="3.82"', 'data-rating="0"')
    assert parse_beer_page(page)["rating"] is None


def test_parse_beer_page_no_style_is_none():
    page = PAGE.replace('<p class="style">Fruit Beer</p>', "")
    assert parse_beer_page(page)["style"] is None


def test_parse_beer_page_not_a_beer_page_is_none():
    assert parse_beer_page("<html><body>Not a beer page</body></html>") is None
    assert parse_beer_page("<<<not even html>>>") is None
