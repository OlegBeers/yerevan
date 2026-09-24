from taps.sources.local_match import KnownBeer, clean_query, clean_text, local_match


def test_clean_text_strips_colour_suffix_case_preserved():
    assert clean_text("379 Dunkel dark") == "379 Dunkel"


def test_clean_text_strips_accented_generic_brewery_words():
    assert clean_text("Bières de Chimay") == "Chimay"


def test_clean_text_strips_glass_bottle_marker():
    assert clean_text('Draught "Dargett" Apricot Ale g/b 0.33l') == 'Draught "Dargett" Apricot Ale'


def test_clean_text_strips_abv_percent():
    assert clean_text("Dargett Wheat 5.2%") == "Dargett Wheat"


def test_clean_text_strips_volume_ml_and_cyrillic_litre():
    assert clean_text('Draught «Bavik Super pils» 0,33л') == 'Draught «Bavik Super pils»'
    assert clean_text("379 Dunkel 0.33l") == "379 Dunkel"


def test_clean_text_strips_the_generic_word_beer():
    assert clean_text('Beer "379" cherry 0,33l') == '"379" cherry'


def test_clean_text_preserves_meaningful_words():
    """Real style/variant words must survive: "dunkel" (German for dark, but the beer's own name),
    "cherry" (a flavour), "blue" (Chimay's own colour-coded product line) are not noise."""
    assert clean_text("Dunkel Cherry Blue") == "Dunkel Cherry Blue"


# --- clean_query: the same noise-cleaning applied to the Untappd search query ------------

def test_clean_query_joins_brand_and_name_and_cleans_noise():
    assert clean_query("379", "Dunkel dark") == "379 Dunkel"


def test_clean_query_without_brand_uses_name_alone():
    assert clean_query(None, "Dunkel dark") == "Dunkel"


# --- local_match(): the three worked examples from the beer-identity task ----------------

APRICOT = KnownBeer(untappd_id=1674726, name="Apricot Ale (Prunus Armeniaca)", brewery="Dargett Brewery")
BAVIK_PILS = KnownBeer(untappd_id=17265, name="Bavik Super Pils", brewery="Brouwerij De Brabandere")
BAVIK_ZERO = KnownBeer(untappd_id=6569311, name="Bavik 0.0%", brewery="Brouwerij De Brabandere")
CHIMAY_BLUE = KnownBeer(untappd_id=34039, name="Chimay Grande Réserve (Blue)", brewery="Bières de Chimay")
CHIMAY_RED = KnownBeer(untappd_id=4072, name="Chimay Première (Red)", brewery="Bières de Chimay")
CHIMAY_WHITE = KnownBeer(untappd_id=10049, name="Chimay Cinq Cents (White)", brewery="Bières de Chimay")
CHIMAY_GOLD = KnownBeer(untappd_id=4702, name="Chimay Dorée (Gold)", brewery="Bières de Chimay")


def test_local_match_finds_apricot_ale_by_brewery_and_name_overlap():
    """beer-city/yerevan-city/buyam all call it "Dargett"/"Apricot Ale"; Untappd (via a bar's own
    menu) has it as "Dargett Brewery"/"Apricot Ale (Prunus Armeniaca)"."""
    found = local_match("Dargett", "Dargett apricot ale", [APRICOT])
    assert found is APRICOT


def test_local_match_finds_bavik_via_untappd_name_when_brewery_differs():
    """The shop only knows the brand "Bavik"; Untappd's brewery is "Brouwerij De Brabandere" (no
    overlap), but the beer's own name "Bavik Super Pils" contains it."""
    found = local_match("Bavik", "Bavik Super pils", [BAVIK_PILS, BAVIK_ZERO])
    assert found is BAVIK_PILS


def test_local_match_rejects_chimay_as_too_different():
    """"Chimay peres trappistes blue" (beer-city) is too different from any of Chimay's own Untappd
    names for local matching -- brewery matches every one of the four, but "peres"/"trappistes"
    aren't in any of their names, so none passes and search is left to try instead."""
    found = local_match("Chimay", "Chimay peres trappistes blue",
                        [CHIMAY_BLUE, CHIMAY_RED, CHIMAY_WHITE, CHIMAY_GOLD])
    assert found is None


# --- local_match(): negative/edge cases ---------------------------------------------------

def test_local_match_ambiguous_colour_suffix_does_not_pick_either_beer():
    """"Dargett Pilsner light" strips to "Dargett Pilsner", but if Dargett has BOTH a "Pilsner Dark"
    and a "Pilsner Light" as distinct, already-known Untappd beers, we cannot tell which one the shop
    meant (was "light" the shop's own bottle-colour suffix, or the beer's real name?) -- ambiguous,
    so neither is picked, unlike a false merge onto the wrong one."""
    pilsner_dark = KnownBeer(untappd_id=100, name="Pilsner Dark", brewery="Dargett Brewery")
    pilsner_light = KnownBeer(untappd_id=200, name="Pilsner Light", brewery="Dargett Brewery")
    found = local_match("Dargett", "Dargett Pilsner light", [pilsner_dark, pilsner_light])
    assert found is None


def test_local_match_prefers_exact_name_over_a_superset_when_several_pass():
    """A plain, unsuffixed shop title ("Dargett Pilsner") that happens to also be a token subset of
    a differently-named sibling beer ("Pilsner Light") still resolves to the exact beer it named."""
    pilsner = KnownBeer(untappd_id=100, name="Pilsner", brewery="Dargett Brewery")
    pilsner_light = KnownBeer(untappd_id=200, name="Pilsner Light", brewery="Dargett Brewery")
    found = local_match("Dargett", "Dargett Pilsner", [pilsner, pilsner_light])
    assert found is pilsner


def test_local_match_falls_back_to_name_first_token_when_brewery_is_non_latin():
    """Parma's own "brewery" field is often the importer's Armenian legal-entity name, not the real
    brewery -- when it has no Latin letters at all, fall back to the shop name's own first word."""
    found = local_match("Բիթեր Ռիվեր ՍՊԸ", "Dargett Apricot Ale light", [APRICOT])
    assert found is APRICOT


def test_local_match_does_not_fall_back_when_brewery_is_latin_but_wrong():
    """Parma's "brewery" field is sometimes a real (Latin) importer/distributor name, unrelated to
    the actual brewery -- that must stay a miss, not trigger the non-latin fallback."""
    found = local_match("Haigen LLC", "379 American Wheat Ale light",
                        [KnownBeer(untappd_id=1, name="379 American Wheat Ale", brewery="379 Torch & Brew")])
    assert found is None


def test_local_match_with_no_known_beers_is_none():
    assert local_match("Dargett", "Apricot Ale", []) is None


def test_local_match_does_not_confuse_draught_with_a_different_stout():
    """Found via the real dry run: model.normalize_title's own stopwords (draught/can/bottle/... --
    meant for PAIR-KEY identity, where packaging never matters) must not feed fuzzy matching here,
    since "Draught" is part of Guinness's own distinguishing Untappd name, not shop packaging noise.
    Stripping it previously left just "stout", which wrongly satisfied containment against a
    completely different, stronger beer."""
    draught = KnownBeer(untappd_id=4473, name="Guinness Draught", brewery="Guinness")
    foreign_extra_stout = KnownBeer(untappd_id=1199, name="Guinness Foreign Extra Stout", brewery="Guinness")
    assert local_match("Guinness & Co.", "Guinness Draught Stout dark", [draught, foreign_extra_stout]) is None


def test_local_match_finds_plain_draught_by_name_alone():
    draught = KnownBeer(untappd_id=4473, name="Guinness Draught", brewery="Guinness")
    other = KnownBeer(untappd_id=1199, name="Guinness Foreign Extra Stout", brewery="Guinness")
    found = local_match("Guinness", "Draught, dark", [draught, other])
    assert found is draught


def test_local_match_rejects_variant_the_shop_does_not_name():
    """A candidate's alcohol-free or barrel-aged variant is a different beer unless the shop says so."""
    kromb_na = KnownBeer(untappd_id=84799, name="Krombacher Weizen Alkoholfrei", brewery="Krombacher Gruppe")
    stout_ba = KnownBeer(untappd_id=2508041, name="Armenian Imperial Stout (Brandy Barrel Aged)", brewery="Dargett Brewery")
    assert local_match("Krombacher", "Weizen", [kromb_na]) is None
    assert local_match("Բիթեր Ռիվեր ՍՊԸ", "Dargett Imperial Stout dark", [stout_ba]) is None
    assert local_match("Krombacher", "Weizen Alkoholfrei", [kromb_na]) == kromb_na


def test_local_match_variant_candidate_still_makes_a_plain_name_ambiguous():
    """'Dargett Stout' could be the oatmeal stout or the imperial one: a skipped variant still counts."""
    oatmeal = KnownBeer(untappd_id=1533350, name="Oatmeal Stout", brewery="Dargett Brewery")
    stout_ba = KnownBeer(untappd_id=2508041, name="Armenian Imperial Stout (Brandy Barrel Aged)", brewery="Dargett Brewery")
    assert local_match("Dargett", "Stout", [oatmeal, stout_ba]) is None
    assert local_match("Dargett", "Oatmeal Stout", [oatmeal, stout_ba]) == oatmeal


# --- local_match(): false-merge regressions found via the real dry run (code review) -----

def test_local_match_rejects_saint_prefixed_brewery_as_generic_evidence():
    """"St"/"Saint" is too generic a brewery-name prefix to count as evidence on its own -- two
    unrelated Trappist breweries both start with it."""
    found = local_match("St. Bernardus", "St. Bernardus Tripel",
                        [KnownBeer(untappd_id=1, name="St-Feuillien Tripel", brewery="Brasserie St-Feuillien")])
    assert found is None


def test_local_match_rejects_pivovar_prefixed_brewery_as_generic_evidence():
    """"Pivovar" (Czech for "brewery") is generic too -- it must not let one Czech brewery's beer
    match another's just because both are called "Pivovar X"."""
    found = local_match("Pivovar Svijany", "Svijany Pilsner",
                        [KnownBeer(untappd_id=1, name="Chotěboř Pilsner", brewery="Pivovar Chotěboř")])
    assert found is None


def test_local_match_rejects_double_edition_the_shop_does_not_name():
    """A "Double IPA" is a materially different, stronger beer than a plain "IPA"."""
    found = local_match("Dargett", "Dargett IPA",
                        [KnownBeer(untappd_id=1, name="Double IPA", brewery="Dargett Brewery")])
    assert found is None


def test_local_match_rejects_foreign_extra_stout_for_a_plain_stout():
    found = local_match("Guinness", "Guinness Stout",
                        [KnownBeer(untappd_id=1, name="Guinness Foreign Extra Stout", brewery="Guinness")])
    assert found is None


def test_local_match_rejects_a_flavoured_edition_the_shop_does_not_name():
    """"Beer Geek Vanilla Shake Breakfast" is a distinct, flavoured edition of "Beer Geek Breakfast"."""
    found = local_match("Mikkeller", "Mikkeller Beer Geek Breakfast",
                        [KnownBeer(untappd_id=1, name="Beer Geek Vanilla Shake Breakfast", brewery="Mikkeller")])
    assert found is None


# --- local_match(): the "loose" allowances the stricter rules above must not break -------

def test_local_match_accepts_a_parenthetical_regional_aside():
    found = local_match("Dargett", "Dargett Pilsner",
                        [KnownBeer(untappd_id=1, name="Pilsner (La Rapsodia)", brewery="Dargett Brewery")])
    assert found is not None


def test_local_match_accepts_extra_tokens_from_another_slash_alternative():
    """Paulaner's own Untappd name lists three alternative spellings; the shop naming only one of
    them is not "extra", unexplained evidence."""
    found = local_match("Paulaner", "Weissbier",
                        [KnownBeer(untappd_id=1, name="Hefe-Weißbier / Hefe-Weizen / Weissbier",
                                  brewery="Paulaner Brauerei")])
    assert found is not None


def test_local_match_accepts_brand_repeated_in_the_candidates_own_name():
    """The candidate's own Untappd name redundantly repeats the brand (common for German beers);
    a shop title that only names the style, not the brand again, still matches."""
    found = local_match("Weihenstephaner", "Hefeweissbier",
                        [KnownBeer(untappd_id=1, name="Weihenstephaner Hefeweissbier", brewery="Weihenstephaner")])
    assert found is not None
