from datetime import datetime, timedelta, timezone

from taps.breaker import Verdict, evaluate, overlap, result_keys
from taps.model import BreweryBeer, Sighting, SourceResult
from taps.state import PairRec, empty_state
from taps.timeutil import iso

NOW = datetime(2026, 9, 23, 13, 0, tzinfo=timezone.utc)
PLACE = "gargoyle"
KEY = f"untappd_menu:{PLACE}"


def sighting(key, source="untappd_menu", place=PLACE, item_id=None):
    return Sighting(place_id=place, source=source, beer_key=key, title=key, name=key,
                    seen_at=NOW, shop_item_id=item_id)


def menu(keys, key=KEY, source="untappd_menu", place=PLACE, full=True):
    return SourceResult(key=key, source=source, ok=True, place_id=place, full=full,
                        sightings=[sighting(k, source, place) for k in keys])


def keys(prefix, n, start=0):
    return [f"n:{prefix} {i}" for i in range(start, start + n)]


def state_with(key=KEY, last_count=0, days_ago=1.0, pairs=(), place=PLACE, **rec_fields):
    st = empty_state(NOW - timedelta(days=60))
    rec = st.source(key)
    rec.baseline_done = True
    rec.last_ok = iso(NOW - timedelta(days=days_ago))
    rec.last_count = last_count
    for name, value in rec_fields.items():
        setattr(rec, name, value)
    t = iso(NOW - timedelta(days=10))
    st.pairs[place] = {k: PairRec(first_seen=t, last_seen=t) for k in pairs}
    return st


def brewery_list(ids, key="untappd_brewery_list:265165"):
    beers = [BreweryBeer(brewery_id=265165, untappd_beer_id=i, name=f"B{i}", brewery="Dargett") for i in ids]
    return SourceResult(key=key, source="untappd_brewery_list", ok=True, brewery_id=265165, brewery_beers=beers)


# --- failures ---------------------------------------------------------------

def test_failed_result_is_discarded_with_its_error_and_counts_a_failure():
    st = state_with(fail_streak=2, last_count=30, pairs=keys("a", 30))
    res = SourceResult(key=KEY, source="untappd_menu", ok=False, error="cloudflare", place_id=PLACE)
    assert evaluate(res, st, NOW) == Verdict("discard", "cloudflare")
    rec = st.sources[KEY]
    assert rec.fail_streak == 3 and rec.last_error == "cloudflare"
    assert rec.last_ok == iso(NOW - timedelta(days=1))   # not touched here
    assert rec.trip_streak == 0


def test_failed_result_on_a_new_key_creates_the_source_record():
    st = empty_state(NOW)
    res = SourceResult(key="parma:parma", source="parma", ok=False, error="network", place_id="parma")
    assert evaluate(res, st, NOW) == Verdict("discard", "network")
    assert st.sources["parma:parma"].fail_streak == 1


def test_ok_result_with_zero_items_is_empty_failure():
    st = state_with(last_count=30, pairs=keys("a", 30))
    assert evaluate(menu([]), st, NOW) == Verdict("discard", "empty")
    assert st.sources[KEY].fail_streak == 1 and st.sources[KEY].last_error == "empty"


def test_zero_checkins_is_not_a_failure():
    key = f"untappd_checkins:{PLACE}"
    st = state_with(key=key)
    res = SourceResult(key=key, source="untappd_checkins", ok=True, place_id=PLACE)
    assert evaluate(res, st, NOW) == Verdict("merge", None)


# --- baseline triggers ------------------------------------------------------

def test_first_run_is_baseline():
    st = empty_state(NOW)
    assert evaluate(menu(keys("a", 40)), st, NOW) == Verdict("baseline", "first_run")


def test_last_ok_four_days_old_is_stale_baseline():
    st = state_with(days_ago=4, last_count=5)
    assert evaluate(menu(keys("a", 5)), st, NOW) == Verdict("baseline", "stale")


def test_last_ok_under_three_days_merges():
    st = state_with(days_ago=2.9, last_count=5)
    assert evaluate(menu(keys("a", 5)), st, NOW) == Verdict("merge", None)


def test_last_ok_exactly_three_days_merges_one_minute_more_is_stale():
    st = state_with(days_ago=3.0, last_count=5)
    assert evaluate(menu(keys("a", 5)), st, NOW) == Verdict("merge", None)
    st = state_with(days_ago=3.0 + 1 / 1440, last_count=5)
    assert evaluate(menu(keys("a", 5)), st, NOW) == Verdict("baseline", "stale")


def test_brewery_list_uses_thirty_day_staleness():
    st = state_with(key="untappd_brewery_list:265165", days_ago=10, max_beer_id=100)
    assert evaluate(brewery_list([90, 95, 101]), st, NOW) == Verdict("merge", None)
    st = state_with(key="untappd_brewery_list:265165", days_ago=31, max_beer_id=100)
    assert evaluate(brewery_list([90, 95, 101]), st, NOW) == Verdict("baseline", "stale")


def test_brewery_list_staleness_boundary_at_exactly_thirty_days():
    key = "untappd_brewery_list:265165"
    st = state_with(key=key, days_ago=30.0, max_beer_id=100)
    assert evaluate(brewery_list([90, 95, 101]), st, NOW) == Verdict("merge", None)
    st = state_with(key=key, days_ago=30.0 + 1 / 1440, max_beer_id=100)
    assert evaluate(brewery_list([90, 95, 101]), st, NOW) == Verdict("baseline", "stale")


# --- breaker trips ----------------------------------------------------------

def test_shrink_below_half_discards_and_starts_trip_streak():
    old = keys("a", 100)
    st = state_with(last_count=100, pairs=old)
    assert evaluate(menu(old[:40]), st, NOW) == Verdict("discard", "shrink")
    rec = st.sources[KEY]
    assert rec.trip_streak == 1
    assert rec.last_trip_keys == sorted(old[:40])
    assert rec.fail_streak == 1 and rec.last_error == "shrink"


def test_breaker_min_count_boundary_nineteen_never_trips_twenty_does():
    st = state_with(last_count=19, pairs=keys("a", 19))
    assert evaluate(menu(keys("a", 5)), st, NOW) == Verdict("merge", None)
    st = state_with(last_count=20, pairs=keys("a", 20))
    assert evaluate(menu(keys("a", 5)), st, NOW) == Verdict("discard", "shrink")


def test_exactly_half_is_not_a_shrink():
    old = keys("a", 100)
    st = state_with(last_count=100, pairs=old)
    assert evaluate(menu(old[:50]), st, NOW) == Verdict("merge", None)


def test_mass_new_keys_discard():
    st = state_with(last_count=30, pairs=keys("a", 30))
    res = menu(keys("a", 14) + keys("b", 16))          # 16/30 unknown > 0.5
    assert evaluate(res, st, NOW) == Verdict("discard", "mass_new")
    st = state_with(last_count=30, pairs=keys("a", 30))
    res = menu(keys("a", 15) + keys("b", 15))          # exactly half unknown
    assert evaluate(res, st, NOW) == Verdict("merge", None)


def test_mass_new_counts_shop_items_already_stored_under_another_key_as_known():
    place, key = "parma", "parma:parma"
    old = keys("old", 30)
    st = state_with(key=key, place=place, last_count=30, pairs=old)
    st.shop_items[place] = {str(i): k for i, k in enumerate(old)}
    renamed = [Sighting(place_id=place, source="parma", beer_key=f"n:renamed {i}", title="x", name="x",
                        seen_at=NOW, shop_item_id=str(i)) for i in range(30)]
    res = SourceResult(key=key, source="parma", ok=True, place_id=place, sightings=renamed)
    assert evaluate(res, st, NOW) == Verdict("merge", None)


def test_small_source_never_trips():
    st = state_with(last_count=10, pairs=keys("a", 10))
    assert evaluate(menu(keys("b", 2)), st, NOW) == Verdict("merge", None)


def test_beercity_partial_run_never_trips():
    place, key = "beer-city", "beercity:beer-city"
    st = state_with(key=key, place=place, last_count=300, pairs=keys("a", 300))
    res = menu(keys("z", 12), key=key, source="beercity", place=place, full=False)
    assert evaluate(res, st, NOW) == Verdict("merge", None)
    assert st.sources[key].trip_streak == 0


def test_checkins_never_trip():
    key = f"untappd_checkins:{PLACE}"
    st = state_with(key=key, last_count=100)
    res = menu(keys("z", 3), key=key, source="untappd_checkins")
    assert evaluate(res, st, NOW) == Verdict("merge", None)


def test_three_similar_trips_are_accepted_as_baseline_and_reset():
    old = keys("a", 100)
    new = keys("a", 40)
    st = state_with(last_count=100, pairs=old)
    assert evaluate(menu(new), st, NOW) == Verdict("discard", "shrink")
    assert evaluate(menu(new[:36] + keys("c", 4)), st, NOW) == Verdict("discard", "shrink")   # 36/40 = 0.9
    assert st.sources[KEY].trip_streak == 2
    assert evaluate(menu(new[:35] + keys("d", 5)), st, NOW) == Verdict("baseline", "accepted")
    rec = st.sources[KEY]
    assert rec.trip_streak == 0 and rec.last_trip_keys == []


def test_overlap_exactly_point_eight_counts_as_similar():
    old = keys("a", 100)
    st = state_with(last_count=100, pairs=old)
    evaluate(menu(old[:40]), st, NOW)
    assert st.sources[KEY].trip_streak == 1
    second = old[:32] + keys("c", 8)          # 32 shared of 40: overlap == 0.8
    assert evaluate(menu(second), st, NOW) == Verdict("discard", "shrink")
    assert st.sources[KEY].trip_streak == 2


def test_dissimilar_trip_restarts_streak_at_one():
    old = keys("a", 100)
    st = state_with(last_count=100, pairs=old)
    evaluate(menu(old[:40]), st, NOW)
    evaluate(menu(old[:40]), st, NOW)
    assert st.sources[KEY].trip_streak == 2
    other = old[60:100]                                    # no overlap with the previous trip
    assert evaluate(menu(other), st, NOW) == Verdict("discard", "shrink")
    assert st.sources[KEY].trip_streak == 1
    assert st.sources[KEY].last_trip_keys == sorted(other)


def test_a_normal_run_between_trips_ends_the_series():
    old = keys("a", 100)
    st = state_with(last_count=100, pairs=old)
    evaluate(menu(old[:40]), st, NOW)
    evaluate(menu(old[:40]), st, NOW)
    assert evaluate(menu(old), st, NOW) == Verdict("merge", None)
    assert st.sources[KEY].trip_streak == 0 and st.sources[KEY].last_trip_keys == []
    assert evaluate(menu(old[:40]), st, NOW) == Verdict("discard", "shrink")
    assert st.sources[KEY].trip_streak == 1


def test_failed_run_does_not_break_trip_series():
    old = keys("a", 100)
    st = state_with(last_count=100, pairs=old)
    evaluate(menu(old[:40]), st, NOW)
    evaluate(menu(old[:40]), st, NOW)
    evaluate(SourceResult(key=KEY, source="untappd_menu", ok=False, error="network"), st, NOW)
    assert evaluate(menu(old[:40]), st, NOW) == Verdict("baseline", "accepted")


def test_four_consecutive_failures_never_accepted_and_leave_trip_streak_at_zero():
    st = state_with(last_count=30, pairs=keys("a", 30))
    for kind in ("cloudflare", "network", "cloudflare", "network"):
        res = SourceResult(key=KEY, source="untappd_menu", ok=False, error=kind, place_id=PLACE)
        assert evaluate(res, st, NOW) == Verdict("discard", kind)
    assert st.sources[KEY].trip_streak == 0 and st.sources[KEY].fail_streak == 4
    assert evaluate(menu([]), st, NOW) == Verdict("discard", "empty")
    assert st.sources[KEY].trip_streak == 0 and st.sources[KEY].fail_streak == 5


# --- brewery list -----------------------------------------------------------

def test_brewery_list_six_new_ids_discard_five_merge():
    lkey = "untappd_brewery_list:265165"
    st = state_with(key=lkey, max_beer_id=1000)
    assert evaluate(brewery_list([900, 1000] + list(range(1001, 1007))), st, NOW) == Verdict("discard", "list_mass_new")
    st = state_with(key=lkey, max_beer_id=1000)
    assert evaluate(brewery_list([900, 1000] + list(range(1001, 1006))), st, NOW) == Verdict("merge", None)


def test_brewery_list_first_run_is_baseline_not_trip():
    st = empty_state(NOW)
    assert evaluate(brewery_list(range(1, 40)), st, NOW) == Verdict("baseline", "first_run")


# --- manual -----------------------------------------------------------------

def test_manual_always_merges_even_on_first_run_and_old_last_ok():
    st = empty_state(NOW)
    res = SourceResult(key="manual", source="manual", ok=True,
                       sightings=[sighting("u:1", source="manual")])
    assert evaluate(res, st, NOW) == Verdict("merge", None)
    st = state_with(key="manual", days_ago=40)
    assert evaluate(SourceResult(key="manual", source="manual", ok=True), st, NOW) == Verdict("merge", None)


# --- helpers ----------------------------------------------------------------

def test_result_keys_and_overlap():
    assert result_keys(menu(["n:a", "u:2", "n:a"])) == {"n:a", "u:2"}
    assert result_keys(brewery_list([5, 7])) == {"u:5", "u:7"}
    assert overlap({"a", "b", "c", "d", "e"}, {"a", "b", "c", "d"}) == 0.8
    assert overlap(set(), {"a"}) == 0.0
