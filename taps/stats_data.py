"""Build site/stats.json from the check-in log (Phase 3): pulse of the scene + the people leaderboard."""
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from taps.checkin_log import CheckinLogEntry
from taps.config import Config
from taps.model import style_family
from taps.state import State
from taps.timeutil import iso, parse_iso

PERIODS = ("30d", "90d", "all")
PERIOD_DAYS = {"30d": 30, "90d": 90}
TOP_N = 20                        # cap on each "top" ranking (venues, beers, breweries, styles)
LEADERBOARD_MIN_CHECKINS = 3
LEADERBOARD_MAX = 100


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _venue_name(venue_id: int, config: Config, state: State) -> str:
    place = next((p for p in config.places.values() if p.venue_id == venue_id), None)
    if place is not None:
        return place.name
    rec = state.venues.get(str(venue_id))
    return rec.name if rec else str(venue_id)


def _beer_lookup(state: State) -> dict[int, dict]:
    """(name, style, country) per Untappd beer id, from state.beers and any pair carrying that key
    (spec: "style from the known beer data in state: pairs info / state.beers by Untappd id")."""
    info: dict[int, dict] = {}
    for key, beer in state.beers.items():
        if key.startswith("u:"):
            info[int(key[2:])] = {"name": beer.name, "style": beer.style, "country": beer.country}
    for pairs in state.pairs.values():
        for key, rec in pairs.items():
            if not key.startswith("u:"):
                continue
            beer_id = int(key[2:])
            got = info.setdefault(beer_id, {"name": None, "style": None, "country": None})
            got["name"] = got["name"] or rec.info.get("u_name") or rec.info.get("name")
            got["style"] = got["style"] or rec.info.get("style")
            got["country"] = got["country"] or rec.info.get("country")
    return info


def _period_entries(entries: list[CheckinLogEntry], period: str, now: datetime) -> list[CheckinLogEntry]:
    if period == "all":
        return entries
    cutoff = now - timedelta(days=PERIOD_DAYS[period])
    return [e for e in entries if parse_iso(e.time) >= cutoff]


def _summary(entries: list[CheckinLogEntry]) -> dict:
    return {
        "checkins": len(entries),
        "people": len({e.username for e in entries}),
        "venues": len({e.venue_id for e in entries}),
        "beers": len({e.beer_id for e in entries}),
        "breweries": len({e.brewery for e in entries if e.brewery}),
        "avg_rating": _avg([e.rating for e in entries if e.rating is not None]),
    }


def _venues_ranking(entries: list[CheckinLogEntry], config: Config, state: State, beer_lookup: dict) -> list[dict]:
    by_venue: dict[int, list[CheckinLogEntry]] = defaultdict(list)
    for e in entries:
        by_venue[e.venue_id].append(e)
    out = []
    for venue_id, es in by_venue.items():
        top_beer_id, _ = Counter(e.beer_id for e in es).most_common(1)[0]
        out.append({
            "venue_id": venue_id, "name": _venue_name(venue_id, config, state), "checkins": len(es),
            "unique_visitors": len({e.username for e in es}),
            "avg_rating": _avg([e.rating for e in es if e.rating is not None]),
            "top_beer": {"beer_id": top_beer_id, "name": beer_lookup.get(top_beer_id, {}).get("name")},
        })
    out.sort(key=lambda v: (-v["checkins"], v["name"]))
    return out[:TOP_N]


def _top_beers(entries: list[CheckinLogEntry], beer_lookup: dict) -> list[dict]:
    brewery_by_beer: dict[int, str | None] = {}
    for e in entries:
        brewery_by_beer.setdefault(e.beer_id, e.brewery)
    counts = Counter(e.beer_id for e in entries)
    return [{"beer_id": beer_id, "name": beer_lookup.get(beer_id, {}).get("name"),
            "brewery": brewery_by_beer.get(beer_id), "checkins": n}
           for beer_id, n in counts.most_common(TOP_N)]


def _top_breweries(entries: list[CheckinLogEntry]) -> list[dict]:
    counts = Counter(e.brewery for e in entries if e.brewery)
    return [{"brewery": brewery, "checkins": n} for brewery, n in counts.most_common(TOP_N)]


def _top_styles(entries: list[CheckinLogEntry], beer_lookup: dict) -> list[dict]:
    counts = Counter()
    for e in entries:
        style = beer_lookup.get(e.beer_id, {}).get("style")
        counts[style_family(style) if style else None] += 1
    return [{"style": style, "checkins": n} for style, n in counts.most_common(TOP_N)]


def _brewery_origin(entries: list[CheckinLogEntry], beer_lookup: dict) -> dict:
    """Share of distinct breweries whose known country (spec: "the existing brewery country data",
    i.e. the Untappd beer's own country) is Armenia vs. anything else; an unknown bucket for
    breweries with no beer of known country. A brewery's country is decided by majority vote across
    its own beers, in case two of its beers ever disagree."""
    breweries = {e.brewery for e in entries if e.brewery}
    countries_by_brewery: dict[str, Counter] = defaultdict(Counter)
    for e in entries:
        if not e.brewery:
            continue
        country = beer_lookup.get(e.beer_id, {}).get("country")
        if country:
            countries_by_brewery[e.brewery][country] += 1
    armenian = imported = 0
    for brewery in breweries:
        countries = countries_by_brewery.get(brewery)
        if not countries:
            continue
        if countries.most_common(1)[0][0] == "Armenia":
            armenian += 1
        else:
            imported += 1
    return {"armenian": armenian, "imported": imported, "unknown": len(breweries) - armenian - imported}


def _first_seen(entries: list[CheckinLogEntry], key) -> dict:
    """key -> the entry that is its earliest sighting across the WHOLE log (never period-filtered:
    "new" means first seen within the period, which needs the full history to decide)."""
    first: dict = {}
    for e in sorted(entries, key=lambda e: e.time):
        k = key(e)
        if k not in first:
            first[k] = e
    return first


def _new_in_period(all_entries: list[CheckinLogEntry], period_entries: list[CheckinLogEntry], period: str,
                   now: datetime, key) -> set:
    first = _first_seen(all_entries, key)
    period_keys = {key(e) for e in period_entries}
    if period == "all":
        return period_keys
    cutoff = now - timedelta(days=PERIOD_DAYS[period])
    return {k for k in period_keys if parse_iso(first[k].time) >= cutoff}


def _new_beers(all_entries: list[CheckinLogEntry], period_entries: list[CheckinLogEntry], period: str,
              now: datetime, beer_lookup: dict) -> list[dict]:
    brewery_by_beer: dict[int, str | None] = {}
    for e in all_entries:
        brewery_by_beer.setdefault(e.beer_id, e.brewery)
    keys = _new_in_period(all_entries, period_entries, period, now, lambda e: e.beer_id)
    out = [{"beer_id": beer_id, "name": beer_lookup.get(beer_id, {}).get("name"),
           "brewery": brewery_by_beer.get(beer_id)} for beer_id in keys]
    out.sort(key=lambda b: (b["name"] or "", b["beer_id"]))
    return out


def _new_breweries(all_entries: list[CheckinLogEntry], period_entries: list[CheckinLogEntry], period: str,
                   now: datetime) -> list[dict]:
    keys = _new_in_period(all_entries, period_entries, period, now, lambda e: e.brewery)
    return [{"brewery": brewery} for brewery in sorted(k for k in keys if k)]


def _leaderboard(entries: list[CheckinLogEntry]) -> list[dict]:
    by_user: dict[str, list[CheckinLogEntry]] = defaultdict(list)
    for e in entries:
        by_user[e.username].append(e)
    rows = [
        {"username": username, "checkins": len(es), "unique_beers": len({e.beer_id for e in es}),
         "venues": len({e.venue_id for e in es})}
        for username, es in by_user.items() if len(es) >= LEADERBOARD_MIN_CHECKINS
    ]
    rows.sort(key=lambda r: (-r["checkins"], r["username"]))
    return [
        {"rank": i, "username": r["username"], "profile_url": f"https://untappd.com/user/{r['username']}",
         "checkins": r["checkins"], "unique_beers": r["unique_beers"], "venues": r["venues"]}
        for i, r in enumerate(rows[:LEADERBOARD_MAX], 1)
    ]


def _period_data(all_entries: list[CheckinLogEntry], period: str, now: datetime, config: Config, state: State,
                 beer_lookup: dict) -> dict:
    entries = _period_entries(all_entries, period, now)
    return {
        "summary": _summary(entries),
        "venues": _venues_ranking(entries, config, state, beer_lookup),
        "top_beers": _top_beers(entries, beer_lookup),
        "top_breweries": _top_breweries(entries),
        "top_styles": _top_styles(entries, beer_lookup),
        "brewery_origin": _brewery_origin(entries, beer_lookup),
        "new_beers": _new_beers(all_entries, entries, period, now, beer_lookup),
        "new_breweries": _new_breweries(all_entries, entries, period, now),
        "people": _leaderboard(entries),
    }


def build_stats_data(state: State, config: Config, checkin_log: list[CheckinLogEntry], now: datetime) -> dict:
    beer_lookup = _beer_lookup(state)
    first_record_at = min((e.time for e in checkin_log), default=None)
    return {
        "generated_at": iso(now),
        "first_record_at": first_record_at,
        "periods": {p: _period_data(checkin_log, p, now, config, state, beer_lookup) for p in PERIODS},
    }


def write_stats_data(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=None, separators=(",", ":")), encoding="utf-8")
