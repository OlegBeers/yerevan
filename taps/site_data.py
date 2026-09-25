"""Build site/data.json from state (spec §8)."""
import json
import re
from datetime import date, datetime
from pathlib import Path

from taps.config import Config, Place
from taps.model import SOURCE_KINDS
from taps.sources.manual import MANUAL_KEEP_DAYS
from taps.sources.untappd_checkins import is_yerevan_city
from taps.state import MARKERS, VENUE_KEEP_DAYS, PairRec, ShopMatchRec, State
from taps.timeutil import age_days, iso, parse_iso, to_yerevan, yerevan_date

CHECKIN_KEEP_DAYS = 21   # same window as rules.CHECKIN_KEEP_DAYS
NEW_DAYS = 7             # 🆕/⭐ badges live this long after the event was sent
INFO_FIELDS = ("brewery", "style", "abv", "ibu", "rating", "price_amd", "volume_ml", "container", "url", "serving",
               "shop_url")


MATCH_VIA_ORDER = {"search": 0, "local": 1, "manual": 2}   # review page: least trustworthy first

UNTAPPD_BEER_RE = re.compile(r"untappd\.com/(?:b/[^/?#]+|beer)/(\d+)")
GLASS_BOTTLE_RE = re.compile(r" g b$")   # Yerevan City appends "G/B" to titles; the key normalizer leaves "g b"


def _group_key(key: str, info: dict) -> str:
    """Display-only identity: the same beer at a bar and a shop groups into one card.
    The pair key stays untouched (it carries notification state)."""
    m = UNTAPPD_BEER_RE.search(info.get("url") or "")
    return f"u:{m.group(1)}" if m else GLASS_BOTTLE_RE.sub("", key)


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
        "addresses": place.addresses,
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


def _active_match(state: State, key: str, kind: str) -> ShopMatchRec | None:
    """The Untappd match apply_shop_matches overlays onto this pair (a "не то же" block is no match)."""
    match = state.shop_matches.get(key)
    return match if kind in ("shop", "menu", "manual") and match and match.untappd_beer_id is not None else None


def _shop_name(info: dict, key: str) -> str:
    return info.get("name") or info.get("title") or key


def _row(place: Place, key: str, rec: PairRec, kind: str, now: datetime, match: ShopMatchRec | None) -> dict:
    info = rec.info
    new = _is_new(rec, now)
    # v1.2 beer identity: a shop/menu/manual pair matched to Untappd carries the canonical name/
    # brewery separately (info["u_name"]/["u_brewery"], set by run.py's apply_shop_matches) -- only
    # the site display prefers them; the digest and rules.py keep using the shop's own text.
    row = {"place_id": place.id, "section": _section(place), "beer_key": key,
           "group_key": _group_key(key, info),
           "name": info.get("u_name") or _shop_name(info, key)}
    row.update({f: info.get(f) for f in INFO_FIELDS})
    if info.get("servings"):   # only a beer with several servings: the fields above are its first one
        row["servings"] = info["servings"]
    row["brewery"] = info.get("u_brewery") or info.get("brewery")
    row["beer_logo"] = info.get("logo")
    row["match_weak"] = bool(info.get("match_weak"))   # a local match that rests on an unnamed parenthetical
    # the shop's own text next to the canonical one, so a wrong merge can be spotted on the site
    row["shop_name"] = _shop_name(info, key) if match else None
    row["shop_brewery"] = info.get("brewery") if match else None
    row["match_via"] = match.via if match else None
    row.update({
        "badge": kind,
        "since": yerevan_date(parse_iso(rec.first_seen)),
        "since_at": iso(parse_iso(rec.first_seen)),   # full UTC timestamp: the site shows the Yerevan clock time and sorts by it
        "seen_days_ago": _days_ago(yerevan_date(parse_iso(info["checkin_at"])), now) if kind == "checkin" else None,
        "new": new,
        "star": new and rec.star,
        "by": info.get("manual_by") if kind == "manual" else None,
    })
    return row


def _match_entry(place: Place, key: str, rec: PairRec, match: ShopMatchRec) -> dict:
    """One line of the review page (site/matches.html): the shop's beer and the Untappd beer it was merged with."""
    info = rec.info
    logo, overlaid = info.get("logo"), (info.get("u_overlay") or {}).get("logo")
    # only Yerevan City puts its own product photo in info["logo"]; a matched Untappd label replaces it
    photo = logo if info.get("source") == "yerevan_city" and logo != overlaid else None
    return {
        "place_id": place.id, "place": place.name, "key": key,
        "shop_name": _shop_name(info, key), "shop_brewery": info.get("brewery"),
        "shop_url": info.get("shop_url"), "shop_photo": photo,
        "untappd_name": match.name, "untappd_brewery": match.brewery, "untappd_url": match.url,
        "untappd_logo": match.logo, "rating": match.rating, "via": match.via, "weak": match.weak,
    }


def _matches_order(m: dict) -> tuple:
    return (not m["weak"], MATCH_VIA_ORDER.get(m["via"], len(MATCH_VIA_ORDER)), m["place_id"], m["shop_name"])


def build_site_data(state: State, config: Config, now: datetime) -> dict:
    rows, matches = [], []
    for place in config.places.values():
        for key, rec in state.pairs.get(place.id, {}).items():
            kind = _kind(rec)
            if kind and _visible(place, rec, kind, state, now):
                match = _active_match(state, key, kind)
                rows.append(_row(place, key, rec, kind, now, match))
                if match:
                    matches.append(_match_entry(place, key, rec, match))
    return {
        "generated_at": iso(now),
        "started_at": state.started_at,
        "hot_rating": config.settings.hot_rating,
        "places": [_place(p, state, now) for p in config.places.values()],
        "rows": rows,
        "venues": _venues(state, config, now),
        "matches": sorted(matches, key=_matches_order),
    }


def write_site_data(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=None, separators=(",", ":")), encoding="utf-8")
