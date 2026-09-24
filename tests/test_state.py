import json
from datetime import datetime, timedelta, timezone

import pytest

from taps.model import SourceResult, VenueCheckin
from taps.state import (
    BeerRec, BreweryNewRec, DigestRec, DiscoveryRec, PairRec, SourceRec, State, UntappdRec, VenueRec,
    apply_aliases, empty_state, load_state, prune, record_venues, resolve_alias, save_state,
)
from taps.timeutil import iso

NOW = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)


def ago(days: float) -> str:
    return iso(NOW - timedelta(days=days))


def full_state() -> State:
    s = empty_state(NOW - timedelta(days=3))
    s.pairs = {
        "gargoyle": {"u:3539672": PairRec(
            first_seen=ago(2), last_seen=ago(0), event_at=ago(2), star=True, notified_at=ago(1),
            last_in_result=False, in_stock=None,
            info={"source": "untappd_menu", "name": "Black Sails", "brewery": "Загов&р", "abv": 11.0, "rating": 4.12},
        )},
        "yerevan-city": {"n:kilikia": PairRec(
            first_seen=ago(3), last_seen=ago(0), notified_at="suppressed", in_stock=True,
            info={"title": "Գարեջուր «Կիլիկիա» 1լ", "hidden": True},
        )},
    }
    s.beers = {"u:3539672": BeerRec(first_seen_city=ago(2), n_key="n:zagovor black sails"),
               "n:kilikia": BeerRec(first_seen_city=ago(3))}
    s.brewery_new = {"u:6000001": BreweryNewRec(
        brewery_id=265165, found_at=ago(1), star=True, notified_at=None,
        info={"name": "DDH NEIPA", "brewery": "Dargett", "abv": 6.5})}
    s.shop_items = {"yerevan-city": {"8811": "n:kilikia"}}
    s.sources = {"untappd_menu:gargoyle": SourceRec(
        baseline_done=True, last_ok=ago(0), last_full=ago(0), last_count=42, fail_streak=1,
        last_error="network", trip_streak=2, last_trip_keys=["u:1", "u:2"], seen_menu_ids=["101", "102"],
        max_beer_id=6000001, menu_updated_at=ago(1))}
    s.untappd = UntappdRec(last_attempt=ago(1), pages_today=12, pages_date="2026-09-22", brewery_list_cursor=3)
    s.digest = DigestRec(last_sent_date="2026-09-22", last_sent_at=ago(1), sent_count=2)
    s.alerts = {"failed:parma:parma": "0123456789ab"}
    s.corrections_snapshot = {"sightings": [{"place": "tap-station", "by": "Аня"}], "not_craft": ["Kilikia"]}
    s.announced_manual = ["tap-station|2026-09-22|Аня"]
    s.venues = {"12252462": VenueRec(name="Gargoyle Bar", url="https://untappd.com/v/gargoyle-bar/12252462",
                                     logo="https://x/logo.jpg", verified=True,
                                     checkins=[{"id": 1, "at": ago(1)}])}
    s.discovery = DiscoveryRec(last_report_date="2026-09-22", reported=[13968261])
    return s


# --- empty state, records, round trip -------------------------------------------------

def test_empty_state():
    s = empty_state(NOW)
    assert s.started_at == "2026-09-23T10:00:00+00:00"
    assert (s.pairs, s.beers, s.brewery_new, s.shop_items, s.sources, s.alerts) == ({}, {}, {}, {}, {}, {})
    assert s.untappd == UntappdRec(last_attempt=None, pages_today=0, pages_date=None, brewery_list_cursor=0)
    assert s.digest == DigestRec(last_sent_date=None, last_sent_at=None, sent_count=0)
    assert s.corrections_snapshot is None
    assert s.announced_manual == []
    assert s.venues == {}
    assert s.discovery == DiscoveryRec(last_report_date=None, reported=[])
    other = empty_state(NOW)
    other.pairs["x"] = {}
    other.untappd.pages_today = 5
    other.venues["x"] = VenueRec(name="X", url="u")
    assert s.pairs == {} and s.untappd.pages_today == 0      # no shared mutable defaults
    assert s.venues == {}


def test_to_dict_is_plain_json_data():
    d = full_state().to_dict()
    assert d["pairs"]["gargoyle"]["u:3539672"]["notified_at"] == ago(1)
    assert d["pairs"]["yerevan-city"]["n:kilikia"]["info"]["hidden"] is True
    assert d["beers"]["u:3539672"] == {"first_seen_city": ago(2), "n_key": "n:zagovor black sails"}
    assert d["brewery_new"]["u:6000001"]["brewery_id"] == 265165
    assert d["sources"]["untappd_menu:gargoyle"]["last_trip_keys"] == ["u:1", "u:2"]
    assert d["untappd"]["pages_today"] == 12
    assert d["digest"]["sent_count"] == 2
    assert d["shop_items"] == {"yerevan-city": {"8811": "n:kilikia"}}
    assert d["venues"]["12252462"]["verified"] is True
    assert d["discovery"]["reported"] == [13968261]
    assert json.loads(json.dumps(d)) == d


def test_to_dict_from_dict_round_trip_every_record_type():
    s = full_state()
    back = State.from_dict(json.loads(json.dumps(s.to_dict())))
    assert back == s
    assert isinstance(back.pairs["gargoyle"]["u:3539672"], PairRec)
    assert isinstance(back.beers["n:kilikia"], BeerRec)
    assert isinstance(back.brewery_new["u:6000001"], BreweryNewRec)
    assert isinstance(back.sources["untappd_menu:gargoyle"], SourceRec)
    assert isinstance(back.untappd, UntappdRec) and isinstance(back.digest, DigestRec)
    assert isinstance(back.venues["12252462"], VenueRec) and isinstance(back.discovery, DiscoveryRec)


def test_from_dict_does_not_share_input_objects():
    d = full_state().to_dict()
    s = State.from_dict(d)
    s.pairs["gargoyle"]["u:3539672"].info["name"] = "changed"
    s.shop_items["yerevan-city"]["9999"] = "n:x"
    assert d["pairs"]["gargoyle"]["u:3539672"]["info"]["name"] == "Black Sails"
    assert "9999" not in d["shop_items"]["yerevan-city"]


def test_from_dict_fills_missing_sections_and_fields_with_defaults():
    s = State.from_dict({"started_at": ago(0), "sources": {"parma:parma": {"last_ok": ago(1)}}})
    assert s.sources["parma:parma"] == SourceRec(last_ok=ago(1))
    assert s.pairs == {} and s.untappd == UntappdRec() and s.announced_manual == []


# --- save / load -------------------------------------------------------------------------

def test_save_state_writes_sorted_readable_json_and_load_reads_it_back(tmp_path):
    path = tmp_path / "state.json"
    s = full_state()
    save_state(path, s)
    text = path.read_text(encoding="utf-8")
    assert "Аня" in text and "Գարեջուր «Կիլիկիա» 1լ" in text and "Загов&р" in text
    assert "\\u" not in text
    top = [line for line in text.splitlines() if line.startswith(' "')]
    assert [line.split('"')[1] for line in top] == sorted(State.__dataclass_fields__)
    assert text.endswith("}\n")
    assert load_state(path, NOW) == s


def test_save_state_output_does_not_depend_on_insertion_order(tmp_path):
    a, b = empty_state(NOW), empty_state(NOW)
    rec1 = PairRec(first_seen=ago(1), last_seen=ago(1), info={"z": 1, "a": 2})
    rec2 = PairRec(first_seen=ago(2), last_seen=ago(2))
    a.pairs = {"gargoyle": {"u:2": rec1, "u:10": rec2}, "beatles": {}}
    b.pairs = {"beatles": {}, "gargoyle": {"u:10": rec2, "u:2": rec1}}
    save_state(tmp_path / "a.json", a)
    save_state(tmp_path / "b.json", b)
    assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes()


def test_load_state_missing_file_returns_empty_state(tmp_path):
    assert load_state(tmp_path / "state.json", NOW) == empty_state(NOW)


@pytest.mark.parametrize("content", ["", "\n", "  \n"])
def test_load_state_empty_file_returns_empty_state(tmp_path, content):
    path = tmp_path / "state.json"
    path.write_text(content, encoding="utf-8")
    assert load_state(path, NOW) == empty_state(NOW)


@pytest.mark.parametrize("content", [
    "{not json",
    "[]",
    '{"pairs": {}}',                                                     # no started_at
    '{"started_at": "2026-09-23T10:00:00+00:00", "digest": {"oops": 1}}',  # unknown field
    '{"started_at": "2026-09-23T10:00:00+00:00", "pairs": []}',
])
def test_load_state_corrupt_file_raises_instead_of_resetting(tmp_path, content):
    path = tmp_path / "state.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="state.json"):
        load_state(path, NOW)


# --- accessors -------------------------------------------------------------------------

def test_source_creates_rec_once():
    s = empty_state(NOW)
    rec = s.source("parma:parma")
    assert rec == SourceRec()
    assert s.sources == {"parma:parma": rec}
    rec.fail_streak = 2
    assert s.source("parma:parma") is rec
    assert s.source("parma:parma").fail_streak == 2


def test_pair_lookup():
    s = full_state()
    assert s.pair("gargoyle", "u:3539672").info["name"] == "Black Sails"
    assert s.pair("gargoyle", "u:1") is None
    assert s.pair("nowhere", "u:3539672") is None
    assert "nowhere" not in s.pairs


# --- aliases -------------------------------------------------------------------------

def test_resolve_alias_chains():
    aliases = {"n:konix bronx": "u:3539672", "n:a": "n:b", "n:b": "n:c"}
    assert resolve_alias("u:1", aliases) == "u:1"
    assert resolve_alias("n:konix bronx", aliases) == "u:3539672"
    assert resolve_alias("n:a", aliases) == "n:c"
    five = {f"k{i}": f"k{i + 1}" for i in range(5)}          # k0 -> ... -> k5, 5 hops
    assert resolve_alias("k0", five) == "k5"


def test_resolve_alias_cycles_and_overlong_chains_leave_key_unchanged():
    assert resolve_alias("a", {"a": "b", "b": "a"}) == "a"
    assert resolve_alias("b", {"a": "b", "b": "a"}) == "b"
    assert resolve_alias("a", {"a": "a"}) == "a"
    assert resolve_alias("c", {"c": "a", "a": "b", "b": "a"}) == "c"
    six = {f"k{i}": f"k{i + 1}" for i in range(6)}            # 6 hops: more than 5
    assert resolve_alias("k0", six) == "k0"
    assert resolve_alias("k1", six) == "k6"


def test_apply_aliases_merges_colliding_pairs():
    s = empty_state(NOW)
    s.pairs = {
        "beer-city": {
            "n:konix bronx": PairRec(first_seen=ago(30), last_seen=ago(10), event_at=ago(30), star=False,
                                     notified_at="baseline", last_in_result=False, in_stock=False,
                                     info={"name": "old"}),
            "u:3539672": PairRec(first_seen=ago(5), last_seen=ago(1), event_at=ago(5), star=True,
                                 notified_at=ago(4), last_in_result=True, in_stock=True, info={"name": "new"}),
            "n:other": PairRec(first_seen=ago(1), last_seen=ago(1)),
        },
        "parma": {"n:konix bronx": PairRec(first_seen=ago(7), last_seen=ago(2), notified_at="baseline")},
    }
    apply_aliases(s, {"n:konix bronx": "u:3539672"})
    assert set(s.pairs["beer-city"]) == {"u:3539672", "n:other"}
    assert s.pairs["beer-city"]["u:3539672"] == PairRec(
        first_seen=ago(30), last_seen=ago(1), event_at=ago(30), star=True, notified_at=ago(4),
        last_in_result=True, in_stock=True, info={"name": "new"})
    assert s.pairs["parma"] == {"u:3539672": PairRec(first_seen=ago(7), last_seen=ago(2), notified_at="baseline")}


def test_apply_aliases_takes_display_fields_from_latest_seen_pair_and_ors_flags():
    s = empty_state(NOW)
    s.pairs = {"beer-city": {
        "n:a": PairRec(first_seen=ago(3), last_seen=ago(3), event_at=ago(3), star=False, notified_at=None,
                       last_in_result=False, in_stock=True, info={"price_amd": 2100}),
        "n:b": PairRec(first_seen=ago(9), last_seen=ago(1), event_at=None, star=True, notified_at=None,
                       last_in_result=True, in_stock=False, info={"price_amd": 1900}),
    }}
    apply_aliases(s, {"n:b": "n:a"})
    assert s.pairs["beer-city"] == {"n:a": PairRec(
        first_seen=ago(9), last_seen=ago(1), event_at=ago(3), star=True, notified_at=None,
        last_in_result=True, in_stock=False, info={"price_amd": 1900})}


@pytest.mark.parametrize("a, b, expected", [
    (ago(4), "baseline", ago(4)),
    ("suppressed", ago(4), ago(4)),
    (ago(2), ago(4), ago(4)),          # two announcements: the earliest
    ("suppressed", None, "suppressed"),
    (None, "baseline", "baseline"),
    (None, None, None),
])
def test_apply_aliases_notified_at_prefers_iso_then_marker_then_none(a, b, expected):
    s = empty_state(NOW)
    s.pairs = {"gargoyle": {"n:x": PairRec(first_seen=ago(6), last_seen=ago(6), notified_at=a),
                            "u:1": PairRec(first_seen=ago(6), last_seen=ago(6), notified_at=b)}}
    apply_aliases(s, {"n:x": "u:1"})
    assert s.pairs["gargoyle"]["u:1"].notified_at == expected


def test_apply_aliases_never_creates_a_pending_event():
    # a never-in-stock shop pair (no event yet) merged into an already known beer stays silent
    s = empty_state(NOW)
    s.pairs = {"parma": {"n:x": PairRec(first_seen=ago(1), last_seen=ago(1), event_at=None, notified_at=None,
                                        in_stock=False),
                         "u:1": PairRec(first_seen=ago(8), last_seen=ago(2), event_at=None,
                                        notified_at="baseline", in_stock=True)}}
    apply_aliases(s, {"n:x": "u:1"})
    rec = s.pairs["parma"]["u:1"]
    assert rec.notified_at == "baseline" and rec.event_at is None


def test_apply_aliases_merges_beers_keeping_earliest_first_seen_city():
    s = empty_state(NOW)
    s.beers = {"n:konix bronx": BeerRec(first_seen_city=ago(20), n_key="n:konix bronx"),
               "u:3539672": BeerRec(first_seen_city=ago(5), n_key="n:konix cassis ruby"),   # its own n_key wins
               "n:lonely": BeerRec(first_seen_city=ago(1))}
    apply_aliases(s, {"n:konix bronx": "u:3539672", "n:lonely": "u:77"})
    assert s.beers == {"u:3539672": BeerRec(first_seen_city=ago(20), n_key="n:konix cassis ruby"),
                       "u:77": BeerRec(first_seen_city=ago(1))}


def test_apply_aliases_merges_brewery_new():
    s = empty_state(NOW)
    s.brewery_new = {
        "u:6000001": BreweryNewRec(brewery_id=265165, found_at=ago(2), star=False, notified_at=None,
                                   info={"name": "DDH NEIPA"}),
        "n:dargett ddh neipa": BreweryNewRec(brewery_id=265165, found_at=ago(9), star=True,
                                             notified_at="baseline", info={"name": "DDH Neipa"}),
    }
    apply_aliases(s, {"n:dargett ddh neipa": "u:6000001"})
    assert s.brewery_new == {"u:6000001": BreweryNewRec(
        brewery_id=265165, found_at=ago(9), star=True, notified_at="baseline", info={"name": "DDH NEIPA"})}


def test_apply_aliases_rewrites_shop_item_values():
    s = empty_state(NOW)
    s.shop_items = {"beer-city": {"1204": "n:konix bronx", "1300": "n:other"},
                    "parma": {"55": "n:konix bronx"}}
    apply_aliases(s, {"n:konix bronx": "u:3539672"})
    assert s.shop_items == {"beer-city": {"1204": "u:3539672", "1300": "n:other"},
                            "parma": {"55": "u:3539672"}}


def test_apply_aliases_is_idempotent():
    s = full_state()
    s.pairs["gargoyle"]["n:zagovor black sails"] = PairRec(first_seen=ago(9), last_seen=ago(9))
    aliases = {"n:zagovor black sails": "u:3539672", "a": "b", "b": "a"}
    apply_aliases(s, aliases)
    once = s.to_dict()
    apply_aliases(s, aliases)
    assert s.to_dict() == once


# --- prune -------------------------------------------------------------------------

def pruning_state(last_ok: str | None, with_source: bool = True) -> State:
    s = empty_state(NOW - timedelta(days=400))
    s.pairs = {"gargoyle": {"u:old": PairRec(first_seen=ago(300), last_seen=ago(181)),
                            "u:keep": PairRec(first_seen=ago(300), last_seen=ago(179))}}
    if with_source:
        s.sources["untappd_menu:gargoyle"] = SourceRec(baseline_done=True, last_ok=last_ok)
    return s


def test_prune_removes_old_pair_when_place_source_is_healthy():
    s = pruning_state(last_ok=ago(1))
    assert prune(s, NOW) == 1
    assert set(s.pairs["gargoyle"]) == {"u:keep"}


def test_prune_keeps_pairs_when_source_last_ok_is_old():
    s = pruning_state(last_ok=ago(40))
    assert prune(s, NOW) == 0
    assert set(s.pairs["gargoyle"]) == {"u:old", "u:keep"}


@pytest.mark.parametrize("with_source", [True, False])
def test_prune_keeps_pairs_when_source_never_succeeded(with_source):
    s = pruning_state(last_ok=None, with_source=with_source)
    assert prune(s, NOW) == 0
    assert set(s.pairs["gargoyle"]) == {"u:old", "u:keep"}


def test_prune_only_counts_sources_of_the_same_place():
    s = pruning_state(last_ok=ago(40))
    s.sources["untappd_menu:beatles"] = SourceRec(last_ok=ago(0))
    s.sources["untappd_brewery:265165"] = SourceRec(last_ok=ago(0))
    s.sources["manual"] = SourceRec(last_ok=ago(0))
    assert prune(s, NOW) == 0
    s.sources["untappd_checkins:gargoyle"] = SourceRec(last_ok=ago(29))   # any source of the place is enough
    assert prune(s, NOW) == 1


def test_prune_returns_total_count_over_places():
    s = pruning_state(last_ok=ago(1))
    s.pairs["parma"] = {"n:a": PairRec(first_seen=ago(400), last_seen=ago(200)),
                        "n:b": PairRec(first_seen=ago(400), last_seen=ago(190))}
    s.sources["parma:parma"] = SourceRec(last_ok=ago(0))
    assert prune(s, NOW) == 3
    assert s.pairs["parma"] == {}


# --- record_venues (v1.1 discovery) ---------------------------------------------------

def test_record_venues_stores_meta_and_checkins():
    s = empty_state(NOW)
    result = SourceResult(
        key="untappd_checkins:gargoyle", source="untappd_checkins", ok=True, place_id="gargoyle",
        venue_meta={"venue_id": 12252462, "name": "Gargoyle Bar",
                   "url": "https://untappd.com/v/gargoyle-bar/12252462", "logo": "https://x/logo.jpg",
                   "verified": True},
        venue_checkins=[VenueCheckin(venue_id=12252462, venue_name="Gargoyle Bar",
                                     venue_url="https://untappd.com/v/gargoyle-bar/12252462",
                                     checkin_id=1, at=NOW - timedelta(days=1))],
    )
    record_venues(s, [result], NOW)
    rec = s.venues["12252462"]
    assert (rec.name, rec.url, rec.logo, rec.verified) == (
        "Gargoyle Bar", "https://untappd.com/v/gargoyle-bar/12252462", "https://x/logo.jpg", True)
    assert rec.checkins == [{"id": 1, "at": ago(1)}]


def test_record_venues_adds_untracked_venue_from_checkins_only():
    s = empty_state(NOW)
    result = SourceResult(
        key="untappd_brewery:265165", source="untappd_brewery", ok=True,
        venue_checkins=[VenueCheckin(venue_id=645961, venue_name="Caffe Napoli",
                                     venue_url="https://untappd.com/v/caffe-napoli/645961",
                                     checkin_id=99, at=NOW)],
    )
    record_venues(s, [result], NOW)
    rec = s.venues["645961"]
    assert (rec.name, rec.url, rec.logo, rec.verified) == ("Caffe Napoli", "https://untappd.com/v/caffe-napoli/645961", None, False)
    assert rec.checkins == [{"id": 99, "at": iso(NOW)}]


def test_record_venues_dedupes_by_checkin_id_and_prunes_old_ones():
    s = empty_state(NOW)
    s.venues["1"] = VenueRec(name="Bar", url="u", checkins=[
        {"id": 1, "at": ago(1)}, {"id": 2, "at": ago(70)}])   # 2 is older than 60 days
    result = SourceResult(
        key="untappd_checkins:bar", source="untappd_checkins", ok=True,
        venue_checkins=[VenueCheckin(venue_id=1, venue_name="Bar", venue_url="u", checkin_id=1, at=NOW - timedelta(days=1)),
                        VenueCheckin(venue_id=1, venue_name="Bar", venue_url="u", checkin_id=3, at=NOW)],
    )
    record_venues(s, [result], NOW)
    assert s.venues["1"].checkins == [{"id": 1, "at": ago(1)}, {"id": 3, "at": iso(NOW)}]


def test_record_venues_ignores_results_without_venue_data():
    s = empty_state(NOW)
    record_venues(s, [SourceResult(key="parma:parma", source="parma", ok=True)], NOW)
    assert s.venues == {}


def test_record_venues_drops_venues_with_no_recent_checkins_unless_known():
    s = empty_state(NOW)
    s.venues["1"] = VenueRec(name="Random Bar", url="u1", checkins=[{"id": 1, "at": ago(70)}])
    s.venues["2"] = VenueRec(name="Closed Bar", url="u2", checkins=[{"id": 2, "at": ago(70)}])
    record_venues(s, [], NOW, known_venue_ids=frozenset({2}))
    assert "1" not in s.venues
    assert "2" in s.venues


def test_record_venues_does_not_let_checkins_overwrite_meta_name_and_url():
    s = empty_state(NOW)
    meta_result = SourceResult(
        key="untappd_menu:gargoyle", source="untappd_menu", ok=True,
        venue_meta={"venue_id": 1, "name": "Real Name", "url": "https://untappd.com/v/real/1",
                   "logo": None, "verified": False})
    record_venues(s, [meta_result], NOW, known_venue_ids=frozenset({1}))
    checkin_result = SourceResult(
        key="untappd_brewery:265165", source="untappd_brewery", ok=True,
        venue_checkins=[VenueCheckin(venue_id=1, venue_name="Stale Name From Checkin",
                                     venue_url="https://untappd.com/v/stale/1", checkin_id=5, at=NOW)])
    record_venues(s, [checkin_result], NOW, known_venue_ids=frozenset({1}))
    assert (s.venues["1"].name, s.venues["1"].url) == ("Real Name", "https://untappd.com/v/real/1")
