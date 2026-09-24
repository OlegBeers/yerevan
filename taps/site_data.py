"""Build site/data.json from state (spec §8)."""
import json
from datetime import date, datetime
from pathlib import Path

from taps.config import Config, Place
from taps.model import SOURCE_KINDS
from taps.sources.manual import MANUAL_KEEP_DAYS
from taps.sources.untappd_checkins import is_yerevan_city
from taps.state import MARKERS, VENUE_KEEP_DAYS, PairRec, State
from taps.timeutil import age_days, iso, parse_iso, to_yerevan, yerevan_date

CHECKIN_KEEP_DAYS = 21   # same window as rules.CHECKIN_KEEP_DAYS
NEW_DAYS = 7             # 🆕/⭐ badges live this long after the event was sent
INFO_FIELDS = ("brewery", "style", "abv", "ibu", "rating", "price_amd", "volume_ml", "container", "url")


def _section(place: Place) -> str:
    return "shops" if place.kind == "shop" else "bars"


def _latest(values) -> str | None:
    values = [v for v in values if v]
    return max(values, key=parse_iso) if values else None


def _place(place: Place, state: State, now: datetime) -> dict:
    recs = [state.sources[k] for k in place.source_keys() if k in state.sources]  # never create records
    last_ok = _latest(r.last_ok for r in recs)
    failing = any(r.fail_streak > 0 for r in recs)
    failing_days = 0
    if failing:
        failing_days = max(0, int(age_days(parse_iso(last_ok or state.started_at), now)))
    venue = state.venues.get(str(place.venue_id)) if place.venue_id is not None else None
    return {
        "id": place.id, "name": place.name, "kind": place.kind, "section": _section(place),
        "last_ok": last_ok, "menu_updated_at": _latest(r.menu_updated_at for r in recs),
        "failing": failing, "failing_days": failing_days,
        "logo": venue.logo if venue else None,
        "verified": venue.verified if venue else False,
        "untappd_url": venue.url if venue else None,
    }


def _venues(state: State, config: Config, now: datetime) -> list[dict]:
    """v1.1 "Все места" tab: every venue seen in check-ins with at least one in the last 30 days.
    A venue already in places.yaml (enabled or disabled) never appears as untracked. A place not in
    places.yaml is shown only once its city is known to be Yerevan (city check, §4; I-3): a foreign
    venue, one in another Armenian city, or one not yet checked stays hidden -- the project's scope
    is Yerevan, not Armenia."""
    tracked = {p.venue_id: p.id for p in config.places.values() if p.venue_id is not None}
    out = []
    for vid_str, rec in state.venues.items():
        vid = int(vid_str)
        if vid not in tracked:
            if vid in config.known_venue_ids or not is_yerevan_city(rec.city):
                continue
        recent = [c for c in rec.checkins if age_days(parse_iso(c["at"]), now) <= VENUE_KEEP_DAYS]
        if not recent:
            continue
        out.append({
            "venue_id": vid, "name": rec.name, "url": rec.url, "logo": rec.logo, "verified": rec.verified,
            "checkins_30d": len(recent), "last_checkin": max(c["at"] for c in recent),
            "tracked": vid in tracked, "place_id": tracked.get(vid),
        })
    out.sort(key=lambda v: (-v["checkins_30d"], v["name"]))
    return out


def _days_ago(day: str, now: datetime) -> int:
    return (to_yerevan(now).date() - date.fromisoformat(day)).days


def _kind(rec: PairRec) -> str | None:
    return rec.info.get("kind") or SOURCE_KINDS.get(rec.info.get("source"))


def _visible(place: Place, rec: PairRec, kind: str, state: State, now: datetime) -> bool:
    info = rec.info
    if info.get("hidden"):
        return False
    if kind == "menu":
        return rec.last_in_result
    if kind == "shop":
        in_result = rec.last_in_result
        if info.get("source") == "beercity":
            # Beer City partial runs add items without refreshing last_in_result
            last_full = state.sources.get(f"beercity:{place.id}")
            last_full = last_full.last_full if last_full else None
            if last_full and parse_iso(rec.first_seen) > parse_iso(last_full):
                in_result = True
        return in_result and rec.in_stock is not False
    if kind == "checkin":
        at = info.get("checkin_at")
        return bool(at) and age_days(parse_iso(at), now) <= CHECKIN_KEEP_DAYS
    if kind == "manual":
        day = info.get("manual_date")
        return rec.last_in_result and bool(day) and 0 <= _days_ago(day, now) <= MANUAL_KEEP_DAYS
    return False


def _is_new(rec: PairRec, now: datetime) -> bool:
    n = rec.notified_at
    return n is not None and n not in MARKERS and age_days(parse_iso(n), now) <= NEW_DAYS


def _row(place: Place, key: str, rec: PairRec, kind: str, now: datetime) -> dict:
    info = rec.info
    new = _is_new(rec, now)
    row = {"place_id": place.id, "section": _section(place), "beer_key": key,
           "name": info.get("name") or info.get("title") or key}
    row.update({f: info.get(f) for f in INFO_FIELDS})
    row.update({
        "badge": kind,
        "since": yerevan_date(parse_iso(rec.first_seen)),
        "seen_days_ago": _days_ago(yerevan_date(parse_iso(info["checkin_at"])), now) if kind == "checkin" else None,
        "new": new,
        "star": new and rec.star,
        "by": info.get("manual_by") if kind == "manual" else None,
    })
    return row


def build_site_data(state: State, config: Config, now: datetime) -> dict:
    rows = []
    for place in config.places.values():
        for key, rec in state.pairs.get(place.id, {}).items():
            kind = _kind(rec)
            if kind and _visible(place, rec, kind, state, now):
                rows.append(_row(place, key, rec, kind, now))
    return {
        "generated_at": iso(now),
        "started_at": state.started_at,
        "hot_rating": config.settings.hot_rating,
        "places": [_place(p, state, now) for p in config.places.values()],
        "rows": rows,
        "venues": _venues(state, config, now),
    }


def write_site_data(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=None, separators=(",", ":")), encoding="utf-8")
