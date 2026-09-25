import pytest

from taps.style_guess import guess_style

# Names as the shops write them (Beer City, Parma, Yerevan City, buy.am), with the label a reader would expect.
NAMED = [
    ("Hard root Double IPA", "IPA"),
    ("Circus Session IPA", "IPA"),
    ("Hop flow IPA American", "IPA"),
    ("Gletcher Bowler Ipa non-filtered", "IPA"),
    ("India Pale Ale", "IPA"),                              # not a "Pale Ale" as well
    ("Волковская пиваварня Indian Pale Ale Ipa", "IPA"),
    ("American Pale Ale", "Pale Ale"),
    ("Shame Apa", "Pale Ale"),
    ("Primator APA, light", "Pale Ale"),
    ("Волковская пиваварня American Pale Ale Apa", "Pale Ale"),
    ("Volfas Engelman Australian Pale Ale", "Pale Ale"),
    ("Alpirsbacher pilsner", "Pilsner"),
    ("Ayinger Pils", "Pilsner"),
    ("Flenburger Pilsener", "Pilsner"),
    ("Krombacher Pils +termo bag", "Pilsner"),
    ("Bohemian Pilsner", "Pilsner"),
    ("379 Pilsner Lager light", "Pilsner"),                # the narrower style wins over "lager"
    ("Ayinger lager", "Lager"),
    ("Bever Vienna lager", "Lager"),
    ("Moritz 7 lager", "Lager"),
    ("Hofbrau helles lager", "Helles"),
    ("Starnberger helles", "Helles"),
    ("Dahook hell non-filtered", "Helles"),
    ("Franziskaner Hefe-Weissbier Hell", "Wheat Beer"),       # Hell / Dunkel modify a wheat beer
    ("Erdinger Weissbier Dunkel", "Wheat Beer"),
    ("Paulaner Weissbier Hell Dunkel", "Wheat Beer"),
    ("Hell Yeah IPA", "IPA"),                                 # a bare "hell" is no style
    ("Weizenbock", "Bock"),
    ("Ayinger Doppelbock", "Bock"),
    ("Bock", "Bock"),
    ("Gletcher Heidegger Hell lager", "Helles"),
    ("Alpirsbacher weizen", "Wheat Beer"),
    ("Weihenstephaner Weissbier", "Wheat Beer"),
    ("HB Weisse", "Wheat Beer"),
    ("Ayinger Urweisse non-filtered wheat", "Wheat Beer"),
    ("Schofferhofer hefewizen", "Wheat Beer"),
    ("weihenstephaner Hefe Weissbier light", "Wheat Beer"),
    ("379 American Wheat Ale Citrus", "Wheat Beer"),
    ("Bavik Super Wit wheat", "Witbier"),
    ("Bever blanche", "Witbier"),
    ("Biere Blanche", "Witbier"),
    ("Kwaremont Blond", "Blonde"),
    ("Duvel Blond", "Blonde"),
    ("Bever amber", "Amber"),
    ("Pauwel Kwak Amber wheat", "Amber"),                  # a named style beats the generic "wheat"
    ("Forged irish stout", "Stout"),
    ("Dargett Imperial Stout dark", "Stout"),
    ("Афанасий Porter темное", "Porter"),
    ("Baltic Porter", "Porter"),
    ("379 dunkel dark", "Dunkel"),
    ("Circus Herb Tripel", "Tripel"),
    ("La Trappe Dubbel dark", "Dubbel"),
    ("Konix tomato gose", "Gose"),
    ("Kriek Boon", "Kriek"),
    ("Sour Cherry", "Sour"),
    ("Saison Dupont", "Saison"),
    ("Krombacher Radler", "Radler"),
    ("Weihenstephaner Vitus Weizenbock", "Bock"),          # the wheat word is glued to "bock": one style, not two
    ("Ayinger Celebrator Doppelbock", "Bock"),
    ("Paulaner MÄRZEN", "Märzen"),
    ("Mahr's Kellerbier", "Kellerbier"),
    ("Apple Cider", "Cider"),
]

# Nothing to go on: colour words, brand names, other styles the table does not know.
UNNAMED = [
    "Bitburger", "Estrella Galicia", "Kellers non-filtered", "Gletcher Milk Of Amnesia", "Dargett Cherry ale",
    "Rewort Ale", "Chimay peres trappistes blue", "Жигулевское светлое", "Трехгорное Prem.Ale свет.",
    "Warsteiner light", "Dahook dark", "Petrus Red Cherry", "Paderborner Pilger, light", "Liebenweiss unfiltered, light",
    "Ayinger celebrator dunkles", "Hell's Kitchen", "Dark Roast",
    "Road to Hell", "Hell or High Watermelon", "Rebock", "", "Beer",
]

# Two different styles in one name: better nothing than a guess.
AMBIGUOUS = ["Stout Porter", "Tripel Blonde", "Weizen lager", "Amber Dubbel", "Pilsner Stout"]


@pytest.mark.parametrize("name, style", NAMED)
def test_a_style_word_in_the_name_gives_that_style(name, style):
    assert guess_style(name) == style


@pytest.mark.parametrize("name", UNNAMED)
def test_a_name_without_a_style_word_gives_none(name):
    assert guess_style(name) is None


@pytest.mark.parametrize("name", AMBIGUOUS)
def test_a_name_with_two_different_styles_gives_none(name):
    assert guess_style(name) is None


def test_case_and_accents_do_not_matter():
    assert guess_style("PILSNER") == guess_style("pilsner") == guess_style("Pilsnér") == "Pilsner"
