from tests.helpers import fixture_text
from taps.sources.untappd_search import (
    SearchResult, best_candidate, is_brand_only, matches, matches_russian_name, parse_search_results, search_url)

# v1.3 search fix: 18 real (anonymized) Untappd search pages were captured in production -- but
# Untappd's own Algolia widget reported "0 drink results" on all of them (a bad query, not a parser
# problem -- see docs/superpowers), so none is a POPULATED results page. The row below therefore
# stays a SYNTHETIC page modeled on Untappd's div.beer-item markup (same shape as the brewery beer
# list), with p.brewery added (a search hit needs its own brewery name, unlike a beer list row where
# the whole page is one brewery). Check parse_search_results against a real POPULATED page before
# trusting it in production -- only the zero-result case is verified below with real markup.
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


def test_no_results_on_a_real_untappd_zero_hit_search_page():
    """v1.3 search fix: a genuine captured Untappd search page (Algolia's own "0 drink results"
    state) has no div.beer-item at all -- parse_search_results must not choke on real markup, just
    correctly report no hits."""
    real_page = fixture_text("untappd/search_unfiltered_dargett.html")
    assert parse_search_results(real_page) == []


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
    result = SearchResult(beer_id=1, slug="s", name="Imperial Stout Brandy Aged", brewery="Dargett",
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


# --- matches(): shop-side noise (v1.3 search fix) must not block an otherwise-correct match --------

def test_matches_ignores_unfiltered_noise_in_the_remaining_name_tokens():
    """Real production search failure: shop name "Dargett non-filtered" -- "non"/"filtered" will
    never appear in Untappd's own beer name, so they must not count against the 50% overlap."""
    result = SearchResult(beer_id=1, slug="s", name="Dargett", brewery="Dargett Brewery", style=None,
                          abv=None, rating=None, logo=None)
    assert matches("Dargett", "Dargett non-filtered", result)


def test_matches_still_rejects_wrong_brewery_despite_noise_cleaning():
    """Noise-cleaning the shop side must not loosen the brewery check itself."""
    result = SearchResult(beer_id=1, slug="s", name="Hell", brewery="Gargoyle Brewpub", style=None,
                          abv=None, rating=None, logo=None)
    assert not matches("Dahook", "Dahook hell non-filtered", result)


def test_matches_russian_name_ignores_unfiltered_noise_in_the_remaining_words():
    result = SearchResult(beer_id=1, slug="s", name="Лагер", brewery="Дарджетт", style=None, abv=None,
                          rating=None, logo=None)
    assert matches_russian_name("Дарджетт нефильтрованное", result)


# --- best_candidate(): the best-scoring REJECTED result, for a one-tap owner suggestion (v1.4) -----

def test_best_candidate_picks_the_higher_brand_and_name_overlap():
    close = SearchResult(beer_id=1, slug="s1", name="Hell", brewery="Dahook", style=None, abv=None,
                         rating=None, logo=None)
    far = SearchResult(beer_id=2, slug="s2", name="Nothing Alike", brewery="Someone Else", style=None,
                       abv=None, rating=None, logo=None)
    assert best_candidate([far, close], "Dahook", "Hell") == close


def test_best_candidate_prefers_closer_abv_when_overlap_ties():
    near = SearchResult(beer_id=1, slug="s1", name="Tremens", brewery="Delirium", style=None, abv=8.4,
                        rating=None, logo=None)
    far = SearchResult(beer_id=2, slug="s2", name="Tremens", brewery="Delirium", style=None, abv=5.0,
                       rating=None, logo=None)
    assert best_candidate([far, near], "Delirium", "Delirium Tremens", shop_abv=8.5) == near


def test_best_candidate_is_none_for_an_empty_list():
    assert best_candidate([], "Dahook", "Hell") is None


# --- stricter acceptance: real production false matches ---------------------------------

def _r(name, brewery, abv=None, style=None, bid=1):
    return SearchResult(beer_id=bid, slug="s", name=name, brewery=brewery, style=style, abv=abv,
                        rating=None, logo=None)


def test_rejects_a_candidate_with_a_colour_qualifier_the_shop_name_lacks():
    black_ipa = _r("Black IPA (Milestones)", "Dargett Brewery", abv=7.5, bid=1547626)
    assert not matches("Dargett", "Dargett IPA", black_ipa)
    assert not matches("Dargett", "Dargett IPA g/b", black_ipa)
    assert matches("Dargett", "Dargett Black IPA", black_ipa)   # the shop names it too


def test_accepts_the_plain_candidate_over_the_qualified_one():
    assert matches("Dargett", "Dargett IPA", _r("IPA", "Dargett Brewery", abv=6.0, bid=6795069))


def test_qualifier_in_candidate_nickname_or_own_brewery_is_ignored():
    assert matches("Dargett", "Dargett Biere Blanche", _r("Dr. White (Bière Blanche)", "Dargett Brewery"))
    assert matches("Black Tie", "Black Tie IPA", _r("Black Tie IPA", "Black Tie Brewing"))


def test_rejects_a_non_alcoholic_shop_name_matched_to_an_alcoholic_beer():
    pils = _r("Krombacher Pils", "Krombacher Gruppe", abv=4.8)
    assert not matches("Krombacher", "Krombacher 0%", pils, shop_abv=None)
    assert not matches("Warsteiner", "Warsteiner 0%", _r("Premium Pilsener", "Warsteiner", abv=4.8))
    alk_free = _r("Krombacher Alkoholfrei", "Krombacher Gruppe", abv=0.0)
    assert matches("Krombacher", "Krombacher 0%", alk_free)


def test_rejects_a_non_alcoholic_candidate_for_an_ordinary_shop_beer():
    assert not matches("Krombacher", "Krombacher Pils", _r("Pils Alkoholfrei", "Krombacher Gruppe", abv=0.4))
    # abv alone counts only when the shop's own abv is known and says otherwise
    assert not matches("Krombacher", "Krombacher Pils", _r("Pils", "Krombacher Gruppe", abv=0.4), shop_abv=4.8)
    assert matches("Selfmade", "The Rogue", _r("The Rogue", "Selfmade Brewery", abv=0.5), shop_abv=0.5)


def test_rejects_a_candidate_brewery_that_is_a_meadery_the_shop_brand_is_not():
    mead = _r("Great Gamgam's Apple Pie Ice Cyser", "Mythos Meadery")
    assert not matches("Mythos", "Mythos Ice", mead)
    assert matches("Mythos", "Mythos Ice", _r("Ice", "Mythos Brewery"))


def test_brand_only_shop_name_rejects_a_brewery_with_extra_distinctive_words():
    assert not matches("Bentley", "Bentley", _r("March of the Gladiators", "The Bentley Brook Brewing Co."))
    assert matches("Krombacher", "Krombacher", _r("Krombacher Pils", "Krombacher Gruppe"))
    assert matches("Kellers", "Kellers", _r("Kellers Beer", "Kellers (Sevan Brewery)"))


def test_is_brand_only():
    assert is_brand_only("Krombacher", "Krombacher 0%")
    assert not is_brand_only("Krombacher", "Krombacher Pils")


def test_russian_name_also_rejects_alcoholic_for_non_alcoholic_shop_name():
    assert not matches_russian_name("Жигулевское безалкогольное", _r("Zhigulevskoe Жигулевское", "Moscow", abv=4.9))
