from datetime import time
from pathlib import Path

import pytest
import yaml

from taps.config import Brewery, ConfigError, Settings, load_config

ROOT = Path(__file__).parent.parent
LIVE_PLACES_YAML = ROOT / "places.yaml"                              # hand-edited: invariants only
PLACES_YAML = Path(__file__).parent / "fixtures" / "config" / "places.yaml"   # frozen copy for exact asserts

MINIMAL = """
places:
  - id: gargoyle
    name: Gargoyle Bar
    kind: bar
    sources:
      untappd_menu: {slug: gargoyle-bar, venue_id: 12252462}
  - id: tuf
    name: Tuf
    kind: bar
    enabled: false
    sources:
      untappd_checkins: {slug: tuf, venue_id: 11284746}
"""


def write(tmp_path, text):
    path = tmp_path / "places.yaml"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def cfg():
    return load_config(PLACES_YAML)


def test_fixture_file_counts_and_order(cfg):
    assert len(cfg.places) == 17
    kinds = [p.kind for p in cfg.places.values()]
    assert (kinds.count("bar"), kinds.count("brewpub"), kinds.count("shop")) == (10, 4, 3)
    assert list(cfg.places)[:2] == ["gargoyle", "beatles"]
    assert list(cfg.places)[-3:] == ["beer-city", "yerevan-city", "parma"]
    assert len(cfg.breweries) == 8
    assert not any(b.list_enabled for b in cfg.breweries)


def test_live_places_file_invariants():
    """Whatever Oleg edits, these must always hold (no exact counts, names or ids)."""
    raw = yaml.safe_load(LIVE_PLACES_YAML.read_text(encoding="utf-8"))
    ids = [p["id"] for p in raw["places"]]
    assert len(ids) == len(set(ids))                              # unique, disabled ones included
    live = load_config(LIVE_PLACES_YAML)                          # no ConfigError
    assert live.places and set(live.places) <= set(ids)
    assert all(b.slug and b.brewery_id for b in live.breweries)
    assert len({b.brewery_id for b in live.breweries}) == len(live.breweries)
    venues = [p.venue_id for p in live.places.values() if p.venue_id]
    assert len(venues) == len(set(venues))
    assert live.settings.digest_time < time(18, 17)               # before the evening run (README)


def test_menu_places(cfg):
    gargoyle = cfg.places["gargoyle"]
    assert gargoyle.name == "Gargoyle Bar"
    assert gargoyle.has_menu
    assert gargoyle.venue_id == 12252462
    assert gargoyle.sources["untappd_menu"]["slug"] == "gargoyle-bar"
    dargett = cfg.places["dargett-brewpub"]
    assert dargett.kind == "brewpub"
    assert dargett.has_menu
    assert dargett.sources == {"buyam": {"url": "https://buy.am/en/restaurants/dargett"}}
    assert dargett.venue_id == 4640403
    assert (dargett.brewery_id, dargett.brewery_name) == (265165, "Dargett")


def test_checkin_brewpubs(cfg):
    dors = cfg.places["dors"]
    assert dors.brewery_name == "Dors"
    assert dors.brewery_id == 441775
    assert not dors.has_menu
    assert dors.venue_id == 9312556
    assert cfg.places["379-torch-brew"].brewery_name == "379"  # quoted, stays a string


def test_place_by_venue(cfg):
    assert cfg.place_by_venue(12252462).id == "gargoyle"
    assert cfg.place_by_venue(4640403).id == "dargett-brewpub"
    assert cfg.place_by_venue(12455977).id == "punk-photo"
    assert cfg.place_by_venue(1) is None


def test_known_venue_ids_includes_disabled_places(tmp_path):
    """I-2: a disabled place's venue must still count as known, so it is not reported as a new place."""
    cfg = load_config(write(tmp_path, MINIMAL))
    assert cfg.known_venue_ids == frozenset({12252462, 11284746})
    assert "tuf" not in cfg.places                     # disabled: excluded from places (tracked flag) as before


def test_source_keys(cfg):
    assert cfg.places["gargoyle"].source_keys() == ["untappd_menu:gargoyle"]
    assert cfg.places["dors"].source_keys() == ["untappd_checkins:dors"]
    assert cfg.places["dargett-brewpub"].source_keys() == ["buyam:dargett-brewpub"]
    assert cfg.places["beer-city"].source_keys() == ["beercity:beer-city"]
    assert cfg.places["beer-city"].venue_id is None


def test_breweries(cfg):
    first = cfg.breweries[0]
    assert first == Brewery(id="dargett", name="Dargett", brewery_id=265165, slug="dargett-brewery",
                            list_enabled=False)
    assert first.url == "https://untappd.com/brewery/265165"
    assert [b.brewery_id for b in cfg.breweries] == [
        265165, 441775, 573921, 518994, 559009, 143586, 520321, 321115]
    assert cfg.breweries[3].name == "379 Torch & Brew"
    assert [b.slug for b in cfg.breweries] == [
        "dargett-brewery", "dors-craft-beer", "pulpulak-craft-beer", "379-torch-and-brew",
        "dahook", "beer-academy", "bever-brewery", "tovmas-brewery"]


def test_settings(cfg):
    assert cfg.settings == Settings(preview_digests=2, digest_time=time(17, 0), digest_max_lines=15,
                                    hot_rating=3.75, untappd_daily_pages=30)
    assert cfg.settings.digest_time == time(17, 0)


def test_disabled_place_is_excluded(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL))
    assert list(cfg.places) == ["gargoyle"]
    assert cfg.place_by_venue(11284746) is None
    assert cfg.breweries == ()
    assert cfg.settings == Settings()


@pytest.mark.parametrize("old, new", [
    ("kind: bar", "kind: pub"),                                              # unknown kind
    ("untappd_menu:", "untappd_venue:"),                                     # unknown source name
    ("id: tuf", "id: gargoyle"),                                             # duplicate place id
    ("{slug: gargoyle-bar, venue_id: 12252462}", "{slug: gargoyle-bar}"),     # missing venue_id
    ("venue_id: 12252462", 'venue_id: "12252462"'),                           # wrong type
    ("enabled: false", "enabled: nope"),                                     # not a bool
    ("name: Tuf", "name: Tuf\n    untappd_slug: tuf"),                       # unknown place field
    ("id: tuf", "id: Tap Station"),                                          # bad id
    ("sources:\n      untappd_menu: {slug: gargoyle-bar, venue_id: 12252462}", "sources: {}"),  # no sources
])
def test_invalid_place_raises(tmp_path, old, new):
    assert old in MINIMAL
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, MINIMAL.replace(old, new, 1)))


@pytest.mark.parametrize("extra", [
    "settings:\n  digest_time: 17:00\n",            # unquoted: YAML reads it as 1020
    'settings:\n  digest_time: "25:00"\n',
    "settings:\n  digest_tme: \"18:00\"\n",          # typo
    "breweries:\n  - {id: dors, name: Dors}\n",      # missing brewery_id
    "breweries:\n  - {id: dors, name: Dors, brewery_id: 441775}\n",   # missing slug
])
def test_invalid_settings_or_breweries_raise(tmp_path, extra):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, MINIMAL + extra))


@pytest.mark.parametrize("text", ["", "places: [\n", "- just a list\n"])
def test_broken_file_raises(tmp_path, text):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, text))


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.yaml")


def test_multi_venue_checkin_place(tmp_path):
    """v1.1: one place may list several Untappd venues (e.g. two branches sharing one brand)."""
    extra = MINIMAL + """
  - id: beer-academy
    name: Beer Academy
    kind: brewpub
    brewery_id: 143586
    brewery_name: Beer Academy
    merged_from: [beer-academy-ethnograph]
    sources:
      untappd_checkins:
        venues:
          - {slug: beer-academy, venue_id: 368914, address: "Московян 8"}
          - {slug: beer-academy-ethnograph, venue_id: 10274653, address: "Абовяна 10"}
"""
    cfg = load_config(write(tmp_path, extra))
    ba = cfg.places["beer-academy"]
    assert ba.venue_ids == [368914, 10274653]
    assert ba.venue_id == 368914                                    # primary venue for logo/verified lookups
    assert ba.addresses == ["Московян 8", "Абовяна 10"]
    assert ba.merged_from == ("beer-academy-ethnograph",)
    assert cfg.place_by_venue(368914).id == cfg.place_by_venue(10274653).id == "beer-academy"
    assert {368914, 10274653} <= cfg.known_venue_ids


def test_multi_venue_missing_slug_raises(tmp_path):
    extra = MINIMAL + """
  - id: beer-academy
    name: Beer Academy
    kind: brewpub
    sources:
      untappd_checkins:
        venues:
          - {venue_id: 368914}
"""
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, extra))


def test_place_without_merged_from_has_empty_tuple(cfg):
    assert cfg.places["gargoyle"].merged_from == ()


def test_shop_with_untappd_checkins_source_is_valid(tmp_path):
    """v1.1: a craft beer shop (e.g. Houl) tracked by check-ins rather than a shop-list source."""
    extra = MINIMAL + """
  - id: houl
    name: Houl
    kind: shop
    sources:
      untappd_checkins: {slug: houl, venue_id: 9709804}
"""
    cfg = load_config(write(tmp_path, extra))
    houl = cfg.places["houl"]
    assert houl.kind == "shop"
    assert houl.venue_id == 9709804
    assert not houl.has_menu
