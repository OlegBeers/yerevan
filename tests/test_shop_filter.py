import html

import pytest

from taps.shop_filter import brand_matches, classify
from tests.helpers import fixture_json, fixture_text

# Initial not_craft list from spec 4.6 ("A/B" spellings split into two entries).
NOT_CRAFT = (
    # Armenia
    "Kotayk", "Kotayq", "Gyumri", "Kilikia", "Ararat", "Alexandrapol", "Aleksandrapol",
    "Erebuni", "Dilijan", "Debed", "Lincoln",
    # Russia and CIS
    "Baltika", "Zhigulevskoe", "Zhiguli", "Zolotaya Bochka", "Beliy Medved", "Motor",
    "Zatecky Gus", "Kozel", "Lvivske", "Vimpel", "Brander Bier", "Platina Latina",
    # International lagers
    "Heineken", "Stella Artois", "Corona", "Bud", "Budweiser", "Miller", "Carlsberg", "Tuborg",
    "Efes", "Amstel", "Hoegaarden", "Estrella Damm", "Kuler", "Almaza", "Peroni", "Grolsch",
    "Kronenbourg", "Asahi", "Modelo", "Pilsner Urquell", "Birra Moretti", "Holsten",
    "Old Prague", "Dragon", "Krusovice", "Newcastle",
    # Georgia
    "Natakhtari", "Kazbegi", "Zedazeni",
    # Czechia
    "Pražečka", "Staročeské", "Santanos",
)

YC_CIDER = "Low alcohol cocktails and cider"  # real categoryName, yerevan_city/by_category.json

# (fixture or None for crafted, title, brand, category, expected)
CASES = [
    # Parma listing (a.item_name > span) and a product page title
    ("parma", 'Beer "Paulaner Original" light 330ml', None, None, (True, "keep")),
    ("parma", 'Beer "Heineken" light 500ml', None, None, (False, "not_craft")),
    ("parma", 'Beer "Miller Genuine Draft Lager" light 330ml', None, None, (False, "not_craft")),
    ("parma", 'Beer "Bud" 330ml', None, None, (False, "not_craft")),
    ("parma", 'Beer "Baltika №9" light 450ml', None, None, (False, "not_craft")),
    ("parma", 'Beer "Zhiguli Barnoe Export" light 450ml', None, None, (False, "not_craft")),
    ("parma", 'Beer "Kotayk Moskovyan" light 250ml', None, None, (False, "not_craft")),
    ("parma", 'Beer "Estrella Damm" light 500ml', None, None, (False, "not_craft")),
    ("parma", 'Beer "Volfas Engelman Ipa" light 568ml', None, None, (True, "style")),
    ("parma", 'Beer "Birrificio Estense Calle San Miguel 426 Apa" light 500ml', None, None, (True, "style")),
    ("parma", 'Beer "Chimay Tripel Ale Peres Trappistes" light 330ml', None, None, (True, "style")),
    ("parma", 'Beer "Dargett Imperial Stout" dark 330ml', None, None, (True, "style")),
    ("parma", 'Beer "Corona Cero" light 330ml', None, None, (False, "nonalc")),
    ("parma", 'Beer "Dahook" light 330ml', "Dahook", None, (True, "keep")),
    ("parma", 'Beer "379" cherry, dark 330ml', None, None, (True, "keep")),
    ("parma", 'Beer "Warsteiner Naturradler Lemon" light 500ml', None, None, (False, "radler")),
    # Yerevan City Search nameEn
    ("yc", 'Beer "Kilikia" 1l', None, "Armenian beer", (False, "not_craft")),
    ("yc", 'Draft beer "Dilijan" 1l', None, "Armenian beer", (False, "not_craft")),
    ("yc", 'Beer "Budweiser" g/b 0.5l', None, "Imported beer", (False, "not_craft")),
    ("yc", 'Beer "Staroceske" light (can) 0.5l', None, "Imported beer", (False, "not_craft")),
    ("yc", 'Beer "Lowenbrau" Original (can) 0.45l', None, "Imported beer", (True, "keep")),
    ("yc", 'Beer "Guinness" Draught, dark (can) 0.44l', None, "Imported beer", (True, "keep")),
    ("yc", 'Beer "Heineken" non-alcoholic g/b 0.33l', None, "Imported beer", (False, "nonalc")),
    ("yc", "Beer ''Corona'' Zero light (can) 330ml", None, "Imported beer", (False, "nonalc")),
    ("yc", 'Beer "Baltika" grapefruit (can) 0.33l', None, "Imported beer", (False, "radler")),
    ("yc", 'Beer "Volfas Engelman" Ipa (can) 0.568l', None, "Imported beer", (True, "style")),
    ("yc", 'Beer "Volkovskaya pivovarnya" IPA g/b 0.45l', None, "Imported beer", (True, "style")),
    ("yc", 'Beer "Athanasius" Porter, dark g/b 0.5l', None, "Imported beer", (True, "style")),
    ("yc", 'Beer "Dargett" Belgian Tripel g/b 0.33l', None, "Armenian beer", (True, "style")),
    # Yerevan City Armenian names (items missing from Search)
    ("yc", "Գարեջրային ըմպելիք «Պաուլաներ» կիտրոն թ/տ 0.5լ", None, "Imported beer", (False, "radler")),
    ("yc", "Գարեջրային ըմպ. «Պրիմատոր» Չիփ. թուրինջ ա/տ 0.5լ", None, "Imported beer", (False, "radler")),
    ("yc", "Գարեջրային ըմպելիք «Կրոմբախեր» Ռադլեր թ/տ 0.5լ", None, "Imported beer", (False, "radler")),
    # a Blanche (witbier) sold as "beer drink" is kept
    ("yc", "Գարեջրային ըմպ. «Տրյոխգորնոե»Բլանշ,բաց ա/տ 0.45լ", None, "Imported beer", (True, "keep")),
    ("yc", "Գարեջուր ոչ ալկոհոլային «Հեյնեկեն» ա/տ 0.33լ", None, "Imported beer", (False, "nonalc")),
    ("yc", "Գարեջուր «Կորոնա» զերո, բաց ա/տ 330մլ", None, "Imported beer", (False, "nonalc")),
    ("yc", "Սիդր «Դառգետ» խնձորի, չոր ա/տ 0.33լ", None, YC_CIDER, (False, "cider_cocktail")),
    ("yc", "Թույլ ալկ.ըմպելիք «Ջեք Դենիելս» Կոկա Կոլա 0.33լ", None, YC_CIDER, (False, "cider_cocktail")),
    ("yc", "Սիդր «Ֆիզ» Պրեմիում, խնձոր թ/տ 0.5լ", None, None, (False, "cider_cocktail")),
    ("yc", "Կոկտեյլ «Շեյք» մոխիտո թ/տ 0.5լ", None, None, (False, "cider_cocktail")),
    # Beer City listings; brand "Konix" from the product pages
    ("beercity", 'Beer "Pure wave" IPA non alco 0.45 l', "Konix", None, (False, "nonalc")),
    ("beercity", 'Beer "Hard root" Double IPA 0.45 l', "Konix", None, (True, "style")),
    ("beercity", 'Beer "Corona cero 0%" 0.33l', None, None, (False, "nonalc")),
    ("beercity", 'Draught beer "Kellers" non-filtered 1l', None, None, (True, "keep")),
    ("beercity", 'Draught beer "Gyumri" 1l', None, None, (False, "not_craft")),
    ("beercity", 'Beer "Bronx" 0.5L', None, None, (True, "keep")),
    ("beercity", 'Пиво "379 American Wheat Ale" Citrus 0,33л', None, None, (True, "keep")),
    # crafted
    (None, 'Beer "Budvar" Original 0.5l', None, None, (True, "keep")),
    (None, 'Cider "Dargett" apple dry 0.33l', None, None, (False, "cider_cocktail")),
    (None, 'Beer "Dargett" IPA 0.33l', None, YC_CIDER, (False, "cider_cocktail")),
    (None, 'Beer drink "Blanche de Namur" witbier 0.33l', None, None, (True, "keep")),
    (None, 'Beer "Bronx" Grapefruit IPA 0.5L', None, None, (False, "radler")),
    (None, 'Beer "Clausthaler" alcohol-free 0.5l', None, None, (False, "nonalc")),
    (None, 'Beer "Krombacher" Radler alcohol-free 0.5l', None, None, (False, "nonalc")),
    (None, 'Cider "Somersby" non-alcoholic 0.33l', None, None, (False, "cider_cocktail")),
    (None, 'Beer "Bitburger" 0,0% 0.33l', None, None, (False, "nonalc")),
    (None, 'Пиво "Жигулёвское" безалкогольное 0.5л', None, None, (False, "nonalc")),
    (None, 'Beer "Saison Dupont" 0.33l', None, None, (True, "style")),
    (None, 'Beer "Original" 0.5l', "Heineken N.V.", None, (False, "not_craft")),
    (None, 'Beer "Kilikia" 1l', "Some Importer", None, (False, "not_craft")),
    (None, 'Beer "Weissbier" 0.5l', "Paulaner Brauerei", None, (True, "keep")),
]


@pytest.mark.parametrize("fixture, title, brand, category, expected", CASES)
def test_classify(fixture, title, brand, category, expected):
    assert classify(title, brand, NOT_CRAFT, category) == expected


def test_real_titles_come_from_fixtures():
    texts = {
        "parma": "\n".join(
            html.unescape(fixture_text(f"parma/{name}.html"))
            for name in ("list_p1", "list_p4", "product_1645")
        ),
        "beercity": "\n".join(
            html.unescape(fixture_json(f"beercity/{name}.json")["products"])
            for name in ("list_bottles_p1", "list_bottles_last", "list_draft_last")
        ),
        "yc": "\n".join(
            [p["nameEn"] for p in fixture_json("yerevan_city/search.json")["data"]["products"]]
            + [p["name"] for p in fixture_json("yerevan_city/by_category.json")["data"]["list"]]
        ),
    }
    categories = {p["categoryName"] for p in fixture_json("yerevan_city/by_category.json")["data"]["list"]}
    for fixture, title, _brand, category, _expected in CASES:
        if fixture:
            assert title in texts[fixture], title
        if fixture == "yc" and category:
            assert category in categories, category


def test_style_keeps_mass_brand_even_if_listed():
    extended = NOT_CRAFT + ("Volfas Engelman",)
    assert classify('Beer "Volfas Engelman Ipa" light 568ml', None, extended) == (True, "style")
    assert classify('Beer "Volfas Engelman Apa" light 568ml', None, extended) == (True, "style")
    assert classify('Beer "Volfas Engelman Blanc" light 568ml', None, extended) == (False, "not_craft")
    assert classify('Beer "Volfas Engelman Blanc" light 568ml', None, NOT_CRAFT) == (True, "keep")


def test_not_craft_is_deaccented_both_ways():
    assert classify('Beer "Lowenbrau" Original (can) 0.45l', None, ["Löwenbräu"]) == (False, "not_craft")
    assert classify('Beer "Original" 0.5l', "Löwenbräu", ["Lowenbrau"]) == (False, "not_craft")


def test_empty_not_craft_keeps_mass_brands():
    assert classify('Beer "Heineken" light 500ml', "Heineken", ()) == (True, "keep")


def test_blank_not_craft_entries_match_nothing():
    assert classify('Beer "Bronx" 0.5L', "Konix", ["", "   ", "!!", "0.5l"]) == (True, "keep")


@pytest.mark.parametrize(
    "text, brand, expected",
    [
        ('Beer "Budvar" Original 0.5l', "Bud", False),
        ('Beer "Budweiser" g/b 0.5l', "Bud", False),
        ('Beer "Bud" 330ml', "Bud", True),
        ('BEER "KILIKIA" 1L', "kilikia", True),
        ('Beer "Lowenbrau" Original (can) 0.45l', "Löwenbräu", True),
        ("Löwenbräu Original", "lowenbrau", True),
        ('Beer "Staroceske" light (can) 0.5l', "Staročeské", True),
        ('Beer "Baltika №9" light 450ml', "Baltika", True),
        ('Beer "Stella Artois" light 500ml', "Stella Artois", True),
        ('Beer "Stella" 0.5l', "Stella Artois", False),
        ('Beer "Old Rasputin" 0.33l', "Old Prague", False),
        ('Beer "Beer Brothers" IPA', "Beer Brothers", True),
        ("anything at all", "", False),
        ("", "", False),
        ("anything at all", " !! ", False),
    ],
)
def test_brand_matches(text, brand, expected):
    assert brand_matches(text, brand) is expected
