import json
from datetime import date
from pathlib import Path

import pytest
import yaml

from taps.config import load_config
from taps.corrections import Corrections, CorrectionsLoad, ManualEntry, load_corrections, parse_corrections

ROOT = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures" / "config"
PLACE_IDS = {"tap-station", "gargoyle", "dors"}

SPEC_EXAMPLE = """
sightings:
  - place: tap-station
    brewery: "379"
    beer: "Hazy Pale"
    by: Аня
    date: 2026-09-24
  - place: dors
    untappd: 1234567
    by: Олег
    date: 2026-09-23
hide:
  - place: gargoyle
    beer: u:3539672
aliases:
  "n:konix bronx": u:3539672
brewery_aliases:
  "v engelman": "volfas engelman"
not_craft:
  - Kilikia
"""

HAZY = ManualEntry(
    id="tap-station|2026-09-24|Аня", place="tap-station", brewery="379", beer="Hazy Pale",
    untappd_id=None, by="Аня", date=date(2026, 9, 24),
)
BY_ID = ManualEntry(
    id="dors|2026-09-23|Олег", place="dors", brewery=None, beer=None,
    untappd_id=1234567, by="Олег", date=date(2026, 9, 23),
)

# spec §4.6, alternate spellings as separate entries
SPEC_NOT_CRAFT = {
    "Kotayk", "Kotayq", "Gyumri", "Kilikia", "Ararat", "Alexandrapol", "Aleksandrapol", "Erebuni",
    "Dilijan", "Debed", "Lincoln",
    "Baltika", "Zhigulevskoe", "Zhiguli", "Zolotaya Bochka", "Beliy Medved", "Motor", "Zatecky Gus",
    "Kozel", "Lvivske", "Vimpel", "Brander Bier", "Platina Latina",
    "Heineken", "Stella Artois", "Corona", "Bud", "Budweiser", "Miller", "Carlsberg", "Tuborg", "Efes",
    "Amstel", "Hoegaarden", "Estrella Damm", "Kuler", "Almaza", "Peroni", "Grolsch", "Kronenbourg",
    "Asahi", "Modelo", "Pilsner Urquell", "Birra Moretti", "Holsten", "Old Prague", "Dragon",
    "Krusovice", "Newcastle",
    "Natakhtari", "Kazbegi", "Zedazeni",
    "Pražečka", "Staročeské", "Santanos",
}


def write(tmp_path, text):
    path = tmp_path / "corrections.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def parse(text):
    return parse_corrections(yaml.safe_load(text), PLACE_IDS)


def good_snapshot(tmp_path):
    raw = load_corrections(write(tmp_path, SPEC_EXAMPLE), None, PLACE_IDS).raw
    return json.loads(json.dumps(raw, ensure_ascii=False))  # as stored in state.json


def test_parse_valid_file(tmp_path):
    load = load_corrections(write(tmp_path, SPEC_EXAMPLE), None, PLACE_IDS)
    assert load.errors == []
    assert load.used_snapshot is False
    assert load.corrections == Corrections(
        sightings=(HAZY, BY_ID),
        hide=frozenset({("gargoyle", "u:3539672")}),
        aliases={"n:konix bronx": "u:3539672"},
        brewery_aliases={"v engelman": "volfas engelman"},
        not_craft=("Kilikia",),
    )


def test_raw_is_json_safe_and_parses_back(tmp_path):
    load = load_corrections(write(tmp_path, SPEC_EXAMPLE), None, PLACE_IDS)
    assert load.raw["sightings"][0]["date"] == "2026-09-24"  # YAML date -> ISO string
    assert parse_corrections(json.loads(json.dumps(load.raw)), PLACE_IDS) == (load.corrections, [])


def test_parse_accepts_yaml_date_objects():
    corrections, errors = parse(SPEC_EXAMPLE)
    assert errors == []
    assert corrections.sightings == (HAZY, BY_ID)


def test_sighting_with_untappd_id_only():
    corrections, errors = parse("sightings:\n  - {place: dors, untappd: 1234567, by: Олег, date: 2026-09-23}\n")
    assert errors == []
    assert corrections.sightings == (BY_ID,)


def test_numbers_are_accepted_as_text():
    corrections, errors = parse(
        "sightings:\n  - {place: tap-station, brewery: 379, beer: Hazy Pale, by: Аня, date: 2026-09-24}\n"
        "not_craft: [1795]\n"
    )
    assert errors == []
    assert corrections.sightings == (HAZY,)
    assert corrections.not_craft == ("1795",)


def test_unknown_place_skips_entry_with_russian_error():
    corrections, errors = parse(
        "sightings:\n"
        "  - {place: nowhere, beer: X, by: Аня, date: 2026-09-24}\n"
        "  - {place: tap-station, brewery: '379', beer: Hazy Pale, by: Аня, date: 2026-09-24}\n"
        "hide:\n"
        "  - {place: nowhere, beer: 'u:1'}\n"
        "  - {place: gargoyle, beer: 'u:2'}\n"
    )
    assert corrections.sightings == (HAZY,)
    assert corrections.hide == frozenset({("gargoyle", "u:2")})
    assert len(errors) == 2
    assert errors[0].startswith("corrections.yaml, sightings (place: nowhere")
    assert errors[1].startswith("corrections.yaml, hide (place: nowhere")
    assert all("место 'nowhere' не найдено" in e for e in errors)


@pytest.mark.parametrize(
    "entry, fragment",
    [
        ("{place: tap-station, beer: X, by: Аня}", "нет поля date"),
        ("{place: tap-station, beer: X, date: 2026-09-24}", "нет поля by"),
        ("{place: tap-station, beer: X, by: '  ', date: 2026-09-24}", "нет поля by"),
        ("{beer: X, by: Аня, date: 2026-09-24}", "нет поля place"),
        ("{place: tap-station, beer: X, by: Аня, date: 24.09.2026}", "нужна дата ГГГГ-ММ-ДД"),
        ("{place: tap-station, beer: X, by: Аня, date: 2026-09-24 18:00:00}", "нужна дата ГГГГ-ММ-ДД"),
        ("{place: tap-station, brewery: '379', by: Аня, date: 2026-09-24}", "нужно beer"),
        ("{place: tap-station, untappd: abc, by: Аня, date: 2026-09-24}", "untappd 'abc'"),
        ("{place: tap-station, untappd: 0, by: Аня, date: 2026-09-24}", "untappd 0"),
        ("{place: tap-station, untappd: true, by: Аня, date: 2026-09-24}", "untappd True"),
        ("{place: tap-station, beer: [X], by: Аня, date: 2026-09-24}", "beer ['X']: ожидался текст"),
        ("{place: tap-station, bear: X, by: Аня, date: 2026-09-24}", "неизвестные поля: bear"),
        ("{place: tap-station, beer: Пиво, by: Аня, date: 2026-09-24}", "не получается ключ пива"),
        ("tap-station", "запись должна быть вида"),
    ],
)
def test_bad_sighting_is_skipped(entry, fragment):
    corrections, errors = parse(f"sightings:\n  - {entry}\n")
    assert corrections.sightings == ()
    assert len(errors) == 1
    assert errors[0].startswith("corrections.yaml, sightings (")
    assert fragment in errors[0]


def test_parse_same_as_entry():
    """v1.2 beer identity: a manual override -- wins over local/search matches (applied by run.py,
    not here) -- keyed by (place, beer_key), consistent with how `hide` already works."""
    corrections, errors = parse(
        "same_as:\n"
        "  - place: gargoyle\n"
        "    beer: 'n:chimay peres trappistes blue'\n"
        "    untappd_id: 34039\n"
    )
    assert errors == []
    assert corrections.same_as == {("gargoyle", "n:chimay peres trappistes blue"): 34039}


@pytest.mark.parametrize(
    "entry, fragment",
    [
        ("{place: gargoyle, untappd_id: 1}", "нет поля beer"),
        ("{place: gargoyle, beer: 'Chimay Blue', untappd_id: 1}", "не ключ пива"),
        ("{place: gargoyle, beer: 'n:x', untappd_id: 0}", "untappd_id 0"),
        ("{place: gargoyle, beer: 'n:x', untappd_id: abc}", "untappd_id 'abc'"),
        ("{place: gargoyle, beer: 'n:x'}", "untappd_id None"),
        ("{place: nowhere, beer: 'n:x', untappd_id: 1}", "место 'nowhere' не найдено"),
        ("{place: gargoyle, beer: 'n:x', untappd_id: 1, extra: 1}", "неизвестные поля: extra"),
    ],
)
def test_bad_same_as_entry_is_skipped(entry, fragment):
    corrections, errors = parse(f"same_as:\n  - {entry}\n")
    assert corrections.same_as == {}
    assert len(errors) == 1
    assert errors[0].startswith("corrections.yaml, same_as (")
    assert fragment in errors[0]


def test_bad_hide_alias_and_brand_entries_are_skipped():
    corrections, errors = parse(
        "hide:\n"
        "  - {place: gargoyle, beer: 3539672}\n"
        "  - {place: gargoyle}\n"
        "  - {place: gargoyle, beer: 'n:konix bronx'}\n"
        "aliases:\n"
        "  konix bronx: 'u:1'\n"
        "  'n:x': 5\n"
        "  'n:konix bronx': 'u:3539672'\n"
        "brewery_aliases:\n"
        "  'v engelman':\n"
        "  'bb': 'beer brothers'\n"
        "not_craft: [Kilikia, [Baltika], '  ']\n"
    )
    assert corrections.hide == frozenset({("gargoyle", "n:konix bronx")})
    assert corrections.aliases == {"n:konix bronx": "u:3539672"}
    assert corrections.brewery_aliases == {"bb": "beer brothers"}
    assert corrections.not_craft == ("Kilikia",)
    assert len(errors) == 7
    assert sum("не ключ пива" in e for e in errors) == 3
    assert any("нет поля beer" in e for e in errors)
    assert all(e.startswith("corrections.yaml, ") for e in errors)


def test_parse_rejects_broken_structure():
    for raw in (["x"], {"hides": []}, {"hide": {"place": "gargoyle"}}, {"aliases": ["n:a"]}):
        with pytest.raises(ValueError):
            parse_corrections(raw, PLACE_IDS)


@pytest.mark.parametrize(
    "text, fragment",
    [
        ("sightings:\n  - place: tap-station\n  beer: X\n", "ошибка YAML в строке"),
        ("hide:\n  place: gargoyle\n  beer: 'u:1'\n", "раздел hide должен быть списком"),
        ("hides: []\n", "неизвестные разделы: hides"),
        ("- Kilikia\n", "ожидался словарь разделов"),
    ],
)
def test_broken_file_uses_snapshot(tmp_path, text, fragment):
    snapshot = good_snapshot(tmp_path)
    load = load_corrections(write(tmp_path, text), snapshot, PLACE_IDS)
    assert load.used_snapshot is True
    assert load.raw is None
    assert load.corrections == Corrections(
        sightings=(HAZY, BY_ID),
        hide=frozenset({("gargoyle", "u:3539672")}),
        aliases={"n:konix bronx": "u:3539672"},
        brewery_aliases={"v engelman": "volfas engelman"},
        not_craft=("Kilikia",),
    )
    assert len(load.errors) == 1
    assert load.errors[0].startswith("corrections.yaml не читается")
    assert fragment in load.errors[0]
    assert "последней удачной версии" in load.errors[0]


def test_snapshot_entries_are_checked_against_current_places(tmp_path):
    snapshot = good_snapshot(tmp_path)
    load = load_corrections(write(tmp_path, "hides: []\n"), snapshot, {"tap-station", "gargoyle"})
    assert load.corrections.sightings == (HAZY,)
    assert len(load.errors) == 2
    assert "место 'dors' не найдено" in load.errors[1]


def test_broken_file_without_snapshot_stops_the_run(tmp_path):
    load = load_corrections(write(tmp_path, "sightings: [\n"), None, PLACE_IDS)
    assert load.corrections is None
    assert load.raw is None
    assert load.used_snapshot is False
    assert len(load.errors) == 1
    assert "прогон остановлен" in load.errors[0]


def test_broken_file_and_broken_snapshot_stop_the_run(tmp_path):
    load = load_corrections(write(tmp_path, "sightings: [\n"), {"hide": "oops"}, PLACE_IDS)
    assert load.corrections is None
    assert load.used_snapshot is False
    assert "снимок в state.json тоже не читается" in load.errors[0]
    assert "прогон остановлен" in load.errors[0]


def test_missing_or_empty_file_is_empty_corrections(tmp_path):
    empty = CorrectionsLoad(corrections=Corrections(), raw={}, errors=[], used_snapshot=False)
    assert load_corrections(tmp_path / "corrections.yaml", {"not_craft": ["X"]}, PLACE_IDS) == empty
    assert load_corrections(write(tmp_path, "# только комментарии\n"), None, PLACE_IDS) == empty


def test_fixture_corrections_file_loads_clean():
    """The frozen copy: exact asserts are safe here."""
    place_ids = set(load_config(FIXTURES / "places.yaml").places)
    load = load_corrections(FIXTURES / "corrections.yaml", None, place_ids)
    assert load.errors == []
    c = load.corrections
    assert (c.sightings, c.hide, c.aliases) == ((), frozenset(), {})
    assert c.brewery_aliases == {"v engelman": "volfas engelman"}
    assert set(c.not_craft) == SPEC_NOT_CRAFT
    assert len(c.not_craft) == len(SPEC_NOT_CRAFT)  # no duplicates


def test_live_corrections_file_invariants():
    """The hand-edited file: only what must hold whatever Oleg adds (sightings, hide, aliases are his)."""
    place_ids = set(load_config(ROOT / "places.yaml").places)
    load = load_corrections(ROOT / "corrections.yaml", None, place_ids)
    assert load.errors == []
    c = load.corrections
    assert SPEC_NOT_CRAFT <= set(c.not_craft)
    assert len(c.not_craft) == len(set(c.not_craft))
