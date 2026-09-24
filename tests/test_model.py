import dataclasses
from datetime import datetime, timezone

import pytest

from taps.model import (
    SOURCE_KINDS,
    BreweryBeer,
    Sighting,
    SourceResult,
    n_key,
    normalize_base,
    normalize_title,
    strip_color,
    u_key,
    untappd_n_key,
)

NOW = datetime(2026, 9, 23, 14, 17, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "title, expected",
    [
        ('Beer "Kilikia" 1l', "kilikia"),
        ("Ayinger Privatbrauerei Celebrator", "ayinger celebrator"),
        ('Beer "Volfas Engelman" Ipa (can) 0.568l', "volfas engelman ipa"),
        ("Dahook light 0.5L", "dahook light"),
        ("379 American Wheat", "379 american wheat"),
    ],
)
def test_normalize_title_contract_examples(title, expected):
    assert normalize_title(title) == expected


def test_normalize_title_with_brewery_alias():
    assert normalize_title("V.Engelman IPA", {"v engelman": "volfas engelman"}) == "volfas engelman ipa"


def test_normalize_title_real_beer_city_titles():
    # titles as they appear in tests/fixtures/beercity listings
    assert normalize_title('Beer "Starnberger helles" 0,45l') == "starnberger helles"
    assert normalize_title('Beer "Pure wave" IPA non alco 0.45 l') == "pure wave ipa non alco"
    assert normalize_title('Beer "StaroPeazske" 0.43L') == "staropeazske"


def test_normalize_title_strips_diacritics():
    assert normalize_title("Budějovický Märzen Rosé") == "budejovicky marzen rose"


def test_normalize_title_removes_volumes_and_multipacks():
    assert normalize_title("Hoegaarden 4x0.33L") == "hoegaarden"
    assert normalize_title("Guinness Draught 44 cl") == "guinness"
    assert normalize_title("Пиво разливное Жигулёвское 0,5 л") == "жигулевское"


def test_normalize_title_keeps_armenian_letters():
    assert normalize_title("Կիլիկիա 0.5լ") == "կիլիկիա"


def test_normalize_title_drops_legal_and_container_tokens():
    assert normalize_title("Dargett Brewery Co. Hazy IPA (bottle)") == "dargett hazy ipa"
    assert normalize_title("Brasserie Dupont GmbH Saison Unfiltered") == "dupont saison"


def test_normalize_base_is_steps_1_to_3_only():
    # keeps stop tokens; used by shop_filter.brand_matches
    assert normalize_base('Beer "Volfas Engelman" (can) 0.5L') == "beer volfas engelman can"


def test_brewery_aliases_longest_key_first_single_pass():
    aliases = {"engelman": "volfas engelman", "V. Engelman": "Volfas Engelman"}
    assert normalize_title("V.Engelman IPA", aliases) == "volfas engelman ipa"
    assert normalize_title("Engelman IPA", aliases) == "volfas engelman ipa"


def test_brewery_aliases_match_whole_tokens_only():
    aliases = {"v engelman": "volfas engelman"}
    assert normalize_title("DV Engelman IPA", aliases) == "dv engelman ipa"


def test_brewery_alias_with_empty_normalized_key_is_ignored():
    assert normalize_title("Kilikia", {"!!!": "x"}) == "kilikia"


def test_keys():
    assert u_key(12345) == "u:12345"
    assert n_key('Beer "Kilikia" 1l') == "n:kilikia"
    assert n_key("V.Engelman IPA", {"v engelman": "volfas engelman"}) == "n:volfas engelman ipa"
    assert untappd_n_key("Dargett", "Hazy IPA") == "n:dargett hazy ipa"
    assert untappd_n_key(None, "Hazy IPA") == "n:hazy ipa"
    assert untappd_n_key("V.Engelman", "IPA", {"v engelman": "volfas engelman"}) == "n:volfas engelman ipa"


def test_strip_color():
    assert strip_color("n:dahook light") == "n:dahook"
    assert strip_color("n:dark side light lager") == "n:side lager"
    assert strip_color("n:dahook") == "n:dahook"
    assert strip_color("u:12345") == "u:12345"


def _sighting(source: str) -> Sighting:
    return Sighting(place_id="gargoyle", source=source, beer_key="u:1", title="t", name="n", seen_at=NOW)


def test_sighting_kind_for_every_source():
    assert {s: _sighting(s).kind for s in SOURCE_KINDS} == {
        "untappd_menu": "menu",
        "buyam": "menu",
        "untappd_checkins": "checkin",
        "untappd_brewery": "checkin",
        "untappd_brewery_list": "brewery_list",
        "beercity": "shop",
        "yerevan_city": "shop",
        "parma": "shop",
        "manual": "manual",
    }


def test_sighting_is_frozen_with_defaults():
    s = _sighting("manual")
    assert s.brewery is None and s.rating is None and s.in_stock is None
    assert s.at_home is False
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.name = "x"


def test_brewery_beer_defaults():
    b = BreweryBeer(brewery_id=1, untappd_beer_id=2, name="Hazy IPA", brewery="Dargett")
    assert (b.style, b.abv, b.url) == (None, None, None)


def test_source_result_defaults():
    r = SourceResult(key="manual", source="manual", ok=True)
    assert r.sightings == []
    assert r.error is None
    assert r.full is True
    assert r.place_id is None and r.brewery_id is None
    assert r.menu_updated_at is None
    assert r.brewery_beers == []
    other = SourceResult(key="parma:parma", source="parma", ok=False, error="network")
    other.sightings.append(_sighting("parma"))
    assert r.sightings == []
