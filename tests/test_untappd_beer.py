from taps.sources.untappd_beer import parse_beer_page
from tests.helpers import fixture_text

PAGE = fixture_text("untappd/beer_page.html")


def test_parse_beer_page_fields():
    assert parse_beer_page(PAGE) == {"style": "Fruit Beer", "abv": 6.2, "ibu": 18, "rating": 3.82,
                                     "logo": None, "country": None}


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


REAL = fixture_text("untappd/beer_page_real.html")   # a real page saved from a browser (Rodenbach Fruitage)


def test_parse_real_beer_page_fields_label_and_country():
    assert parse_beer_page(REAL) == {
        "style": "Fruit Beer", "abv": 3.4, "ibu": 7, "rating": 3.48902,
        "logo": "https://assets.untappd.com/site/beer_logos/beer-1715344_b8fec_sm.jpeg",
        "country": "Belgium",
    }


def test_parse_beer_page_logo_must_be_an_untappd_assets_url():
    page = REAL.replace("https://assets.untappd.com/site/beer_logos/beer-1715344_b8fec_sm.jpeg",
                        "https://evil.example/x.jpg")
    assert parse_beer_page(page)["logo"] is None


def test_parse_beer_page_country_follows_the_style_in_the_keywords():
    page = REAL.replace("Rodenbach Fruitage, Brouwerij Rodenbach, Fruit Beer, Belgium, Vlaams Gewest",
                        "Ice Cream | Strawberry, Banana, Konix Brewery, Fruit Beer, Armenia")
    assert parse_beer_page(page)["country"] == "Armenia"
    no_country = REAL.replace("Fruit Beer, Belgium, Vlaams Gewest", "Fruit Beer")
    assert parse_beer_page(no_country)["country"] is None
