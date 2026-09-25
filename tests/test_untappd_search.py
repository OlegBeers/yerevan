from taps.sources.untappd_search import SearchResult, matches, matches_russian_name, parse_search_results, search_url

# No capture of the real search results page exists yet (https://untappd.com/search?q=...&type=beer).
# The row below is a SYNTHETIC page modeled on Untappd's div.beer-item markup (same shape as the
# brewery beer list), with p.brewery added (a search hit needs its own brewery name, unlike a beer
# list row where the whole page is one brewery). Check parse_search_results against a real page
# before trusting it in production.
ROW = """
<div class="beer-item" data-bid="{bid}">
 <a class="label" href="/b/{slug}/{bid}"><img src="https://assets.untappd.com/site/beer_logos/beer-{bid}.jpeg"></a>
 <div class="beer-details">
  <p class="name"><a href="/b/{slug}/{bid}">{name}</a></p>
  <p class="brewery">{brewery}</p>
  <p class="style">{style}</p>
 </div>
 <div class="details beer">
  <div class="abv">{abv}</div>
  <div class="caps" data-rating="{rating}"></div>
 </div>
</div>
"""
KILIKIA = dict(bid=1547626, slug="kilikia-brewery-kilikia", name="Kilikia", brewery="Kilikia Brewery",
               style="Pale Lager", abv="4.6% ABV", rating="3.21")
DAHOOK_HELL = dict(bid=5817007, slug="dahook-hell", name="Hell", brewery="Dahook",
                   style="Lager - Helles", abv="4.8% ABV", rating="3.42")


def page(*rows):
    return "<html><body>" + "".join(ROW.format(**r) for r in rows) + "</body></html>"


def test_search_url_encodes_query():
    assert search_url("Dahook Hell") == "https://untappd.com/search?q=Dahook%20Hell&type=beer"


def test_parse_search_results():
    assert parse_search_results(page(KILIKIA, DAHOOK_HELL)) == [
        SearchResult(beer_id=1547626, slug="kilikia-brewery-kilikia", name="Kilikia",
                    brewery="Kilikia Brewery", style="Pale Lager", abv=4.6, rating=3.21,
                    logo="https://assets.untappd.com/site/beer_logos/beer-1547626.jpeg"),
        SearchResult(beer_id=5817007, slug="dahook-hell", name="Hell", brewery="Dahook",
                    style="Lager - Helles", abv=4.8, rating=3.42,
                    logo="https://assets.untappd.com/site/beer_logos/beer-5817007.jpeg"),
    ]


def test_result_url_is_canonical_b_link():
    result = parse_search_results(page(DAHOOK_HELL))[0]
    assert result.url == "https://untappd.com/b/dahook-hell/5817007"


def test_no_results_is_empty_list():
    assert parse_search_results("<html><body>Nothing found.</body></html>") == []


def test_row_without_beer_link_is_skipped():
    broken = page(KILIKIA).replace('href="/b/kilikia-brewery-kilikia/1547626"', 'href="/beer/1547626"')
    assert parse_search_results(broken) == []


def test_rating_zero_or_unreadable_is_none():
    for value in ("0", "N/A"):
        result = parse_search_results(page({**KILIKIA, "rating": value}))[0]
        assert result.rating is None


def test_missing_abv_is_none():
    result = parse_search_results(page({**KILIKIA, "abv": "N/A"}))[0]
    assert result.abv is None


def test_missing_logo_is_none():
    broken = page(KILIKIA).replace(
        '<img src="https://assets.untappd.com/site/beer_logos/beer-1547626.jpeg">', "")
    result = parse_search_results(broken)[0]
    assert result.logo is None


# --- matches() ---------------------------------------------------------------------------

def test_matches_on_brand_and_full_name_overlap():
    result = SearchResult(beer_id=1, slug="s", name="Hell", brewery="Dahook", style=None, abv=None,
                          rating=None, logo=None)
    assert matches("Dahook", "Hell", result)


def test_matches_requires_brand_overlap_with_brewery():
    result = SearchResult(beer_id=1, slug="s", name="Hell", brewery="Gargoyle Brewpub", style=None,
                          abv=None, rating=None, logo=None)
    assert not matches("Dahook", "Hell", result)   # brand not in brewery -> reject even if name matches


def test_matches_accepts_half_or_more_remaining_name_tokens():
    result = SearchResult(beer_id=1, slug="s", name="Imperial Stout Brandy Barrel Aged", brewery="Dargett",
                          style=None, abv=None, rating=None, logo=None)
    # shop name has 4 tokens after removing the brand; 2 of them ("imperial", "stout") are in the result name
    assert matches("Dargett", "Dargett Imperial Stout Vanilla Coconut", result)


def test_matches_rejects_below_half_remaining_name_tokens():
    result = SearchResult(beer_id=1, slug="s", name="Pilsner", brewery="Dargett", style=None, abv=None,
                          rating=None, logo=None)
    assert not matches("Dargett", "Dargett Imperial Stout Vanilla Coconut", result)


def test_matches_true_when_shop_name_is_only_the_brand():
    result = SearchResult(beer_id=1, slug="s", name="Any Beer At All", brewery="Kilikia", style=None,
                          abv=None, rating=None, logo=None)
    assert matches("Kilikia", "Kilikia", result)


def test_matches_empty_brand_never_matches():
    result = SearchResult(beer_id=1, slug="s", name="Hell", brewery="Dahook", style=None, abv=None,
                          rating=None, logo=None)
    assert not matches("", "Hell", result)


def test_matches_russian_name_by_first_word_and_name_overlap():
    result = SearchResult(beer_id=1, slug="s", name="Жигулёвское", brewery="Очаково", style=None, abv=None,
                          rating=None, logo=None)
    assert matches_russian_name("Жигулевское светлое", result)   # ё equals е, the colour suffix is ignored


def test_matches_russian_name_rejects_another_beer():
    result = SearchResult(beer_id=1, slug="s", name="Баррель", brewery="Афанасий", style=None, abv=None,
                          rating=None, logo=None)
    assert not matches_russian_name("Жигулевское светлое", result)
    assert not matches_russian_name("Афанасий Тверское светлое", result)   # brand only, name tokens all differ
