"""Source breaker (spec §10) and silent-baseline triggers (spec §6)."""
from dataclasses import dataclass
from datetime import datetime

from taps.model import SOURCE_KINDS, SourceResult, u_key
from taps.state import SourceRec, State
from taps.timeutil import age_days, parse_iso

BREAKER_MIN_COUNT = 20
TRIP_ACCEPT_STREAK = 3
TRIP_OVERLAP = 0.8
STALE_DAYS = 3
LIST_STALE_DAYS = 30
LIST_MAX_NEW = 5
NONEMPTY_KINDS = ("menu", "shop", "brewery_list")   # an ok result with 0 items from these is "empty"


@dataclass
class Verdict:
    action: str          # "merge" | "baseline" | "discard"
    reason: str | None   # "first_run"|"stale"|"accepted"|"shrink"|"mass_new"|"list_mass_new"|<result.error>


def result_keys(result: SourceResult) -> set[str]:
    if result.brewery_beers:
        return {u_key(b.untappd_beer_id) for b in result.brewery_beers}
    return {s.beer_key for s in result.sightings}


def overlap(a: set[str], b: set[str]) -> float:
    """Share of common keys relative to the larger set; 0 when either is empty."""
    if not a or not b:
        return 0.0
    return len(a & b) / max(len(a), len(b))


def _trip_reason(result: SourceResult, rec: SourceRec, state: State, keys: set[str]) -> str | None:
    kind = SOURCE_KINDS[result.source]
    if kind == "brewery_list":
        if not rec.baseline_done:
            return None
        new_ids = {b.untappd_beer_id for b in result.brewery_beers if b.untappd_beer_id > rec.max_beer_id}
        return "list_mass_new" if len(new_ids) > LIST_MAX_NEW else None
    if kind not in ("menu", "shop") or not result.full or rec.last_count < BREAKER_MIN_COUNT:
        return None
    if len(keys) < rec.last_count / 2:
        return "shrink"
    known = state.pairs.get(result.place_id, {})
    items = state.shop_items.get(result.place_id, {})   # a shop item keeps the key it was first stored under
    unknown = {
        s.beer_key for s in result.sightings
        if s.beer_key not in known and items.get(s.shop_item_id) not in known
    }
    return "mass_new" if len(unknown) / len(keys) > 0.5 else None


def evaluate(result: SourceResult, state: State, now: datetime) -> Verdict:
    """Decide what rules.py does with one result.

    Mutates only fail_streak/last_error and the trip fields of the source rec;
    last_ok, last_count, baseline_done etc. are updated by rules.py after a merge.
    """
    rec = state.source(result.key)
    kind = SOURCE_KINDS[result.source]
    keys = result_keys(result)
    error = None
    if not result.ok:
        error = result.error or "error"
    elif not keys and kind in NONEMPTY_KINDS:
        error = "empty"
    if error:
        rec.fail_streak += 1
        rec.last_error = error
        return Verdict("discard", error)
    if kind == "manual":
        return Verdict("merge", None)

    reason = _trip_reason(result, rec, state, keys)
    if reason:
        similar = overlap(keys, set(rec.last_trip_keys)) >= TRIP_OVERLAP
        rec.trip_streak = rec.trip_streak + 1 if similar else 1
        rec.last_trip_keys = sorted(keys)
        if rec.trip_streak < TRIP_ACCEPT_STREAK:
            rec.fail_streak += 1
            rec.last_error = reason
            return Verdict("discard", reason)
    # accepted after repeated trips, or no trip at all: a trip series ends here
    rec.trip_streak = 0
    rec.last_trip_keys = []
    if reason:
        return Verdict("baseline", "accepted")
    if not rec.baseline_done:
        return Verdict("baseline", "first_run")
    limit = LIST_STALE_DAYS if kind == "brewery_list" else STALE_DAYS
    if rec.last_ok is None or age_days(parse_iso(rec.last_ok), now) > limit:
        return Verdict("baseline", "stale")
    return Verdict("merge", None)
