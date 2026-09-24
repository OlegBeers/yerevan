"""state.json: the bot's memory. Load/save, alias merging, pruning."""
import copy
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from taps.model import SourceResult
from taps.timeutil import age_days, iso, parse_iso

PAIR_KEEP_DAYS = 180      # pairs not seen for longer are pruned...
SOURCE_OK_DAYS = 30       # ...but only if a source of their place succeeded this recently
ALIAS_MAX_HOPS = 5
VENUE_KEEP_DAYS = 60      # v1.1: venue check-in window ("Все места", weekly report); older check-ins are pruned
MARKERS = ("baseline", "suppressed")   # notified_at values that are not timestamps


@dataclass
class PairRec:
    first_seen: str
    last_seen: str
    event_at: str | None = None
    star: bool = False
    notified_at: str | None = None     # iso | "baseline" | "suppressed" | None (= pending if event_at set)
    last_in_result: bool = True
    in_stock: bool | None = None
    info: dict = field(default_factory=dict)


@dataclass
class BeerRec:
    first_seen_city: str
    n_key: str | None = None
    # v1.1 §2: cache for beers seen only in check-ins, filled from the beer's own Untappd page
    rating: float | None = None
    style: str | None = None
    abv: float | None = None
    ibu: int | None = None
    rating_at: str | None = None


@dataclass
class BreweryNewRec:
    brewery_id: int
    found_at: str
    star: bool = False
    notified_at: str | None = None
    info: dict = field(default_factory=dict)


@dataclass
class SourceRec:
    baseline_done: bool = False
    last_ok: str | None = None
    last_full: str | None = None
    last_count: int = 0
    fail_streak: int = 0
    last_error: str | None = None
    trip_streak: int = 0
    last_trip_keys: list[str] = field(default_factory=list)
    seen_menu_ids: list[str] = field(default_factory=list)
    max_beer_id: int = 0
    menu_updated_at: str | None = None


@dataclass
class UntappdRec:
    last_attempt: str | None = None
    pages_today: int = 0
    pages_date: str | None = None
    brewery_list_cursor: int = 0


@dataclass
class DigestRec:
    last_sent_date: str | None = None   # Yerevan date "YYYY-MM-DD"
    last_sent_at: str | None = None
    sent_count: int = 0


@dataclass
class VenueRec:
    """v1.1: every venue seen in Untappd check-ins, tracked or not (§4/§8 "venues" list)."""
    name: str
    url: str
    logo: str | None = None
    verified: bool = False
    checkins: list[dict] = field(default_factory=list)   # [{"id": int, "at": iso}], deduped by id
    has_meta: bool = False   # name/url came from the venue's own page (untappd_menu/checkins): checkins must not overwrite them
    city: str | None = None            # v1.1 city check (§4): raw addressLocality once the venue page was read
    country: str | None = None        # "Armenia" if the venue is there, else its country (or unknown but read)
    location_checked_at: str | None = None   # iso; set even on an unresolved read, to space out retries


@dataclass
class DiscoveryRec:
    """v1.1: the weekly admin DM about untracked venues with enough check-ins."""
    last_report_date: str | None = None   # Yerevan date of the last weekly check, sent or not
    reported: list[int] = field(default_factory=list)   # venue ids already mentioned once


@dataclass
class State:
    started_at: str
    pairs: dict[str, dict[str, PairRec]] = field(default_factory=dict)
    beers: dict[str, BeerRec] = field(default_factory=dict)
    brewery_new: dict[str, BreweryNewRec] = field(default_factory=dict)
    shop_items: dict[str, dict[str, str]] = field(default_factory=dict)
    sources: dict[str, SourceRec] = field(default_factory=dict)
    untappd: UntappdRec = field(default_factory=UntappdRec)
    digest: DigestRec = field(default_factory=DigestRec)
    alerts: dict[str, str] = field(default_factory=dict)
    corrections_snapshot: dict | None = None
    announced_manual: list[str] = field(default_factory=list)
    venues: dict[str, VenueRec] = field(default_factory=dict)
    discovery: DiscoveryRec = field(default_factory=DiscoveryRec)

    def source(self, key: str) -> SourceRec:
        return self.sources.setdefault(key, SourceRec())

    def pair(self, place_id: str, beer_key: str) -> PairRec | None:
        return self.pairs.get(place_id, {}).get(beer_key)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "State":
        d = copy.deepcopy(d)
        return cls(
            started_at=d["started_at"],
            pairs={p: {k: PairRec(**r) for k, r in recs.items()} for p, recs in d.get("pairs", {}).items()},
            beers={k: BeerRec(**r) for k, r in d.get("beers", {}).items()},
            brewery_new={k: BreweryNewRec(**r) for k, r in d.get("brewery_new", {}).items()},
            shop_items=d.get("shop_items", {}),
            sources={k: SourceRec(**r) for k, r in d.get("sources", {}).items()},
            untappd=UntappdRec(**d.get("untappd", {})),
            digest=DigestRec(**d.get("digest", {})),
            alerts=d.get("alerts", {}),
            corrections_snapshot=d.get("corrections_snapshot"),
            announced_manual=d.get("announced_manual", []),
            venues={k: VenueRec(**r) for k, r in d.get("venues", {}).items()},
            discovery=DiscoveryRec(**d.get("discovery", {})),
        )


def empty_state(now: datetime) -> State:
    return State(started_at=iso(now))


def load_state(path: Path, now: datetime) -> State:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if not text.strip():
        return empty_state(now)
    # A damaged file must stop the run: silently starting over would reset the digest marks.
    try:
        return State.from_dict(json.loads(text))
    except (ValueError, TypeError, KeyError, AttributeError) as e:
        raise ValueError(f"{path.name} повреждён: {type(e).__name__}: {e}") from e


def save_state(path: Path, state: State) -> None:
    text = json.dumps(state.to_dict(), ensure_ascii=False, indent=1, sort_keys=True)
    path.write_text(text + "\n", encoding="utf-8")


def resolve_alias(key: str, aliases: Mapping[str, str]) -> str:
    """Follow the alias chain. A chain that does not end within 5 hops (a cycle) leaves the key as is."""
    current = key
    for _ in range(ALIAS_MAX_HOPS + 1):
        nxt = aliases.get(current)
        if nxt is None:
            return current
        current = nxt
    return key


def _earliest(*values: str | None) -> str | None:
    present = [v for v in values if v is not None]
    return min(present, key=parse_iso) if present else None


def _merge_notified(a: str | None, b: str | None) -> str | None:
    """Earliest announcement, else a marker, else None: a merge never makes an event pending again."""
    stamps = [v for v in (a, b) if v is not None and v not in MARKERS]
    if stamps:
        return _earliest(*stamps)
    return a if a is not None else b


def _merge_pair(a: PairRec, b: PairRec) -> PairRec:
    newer = b if parse_iso(b.last_seen) > parse_iso(a.last_seen) else a
    return PairRec(
        first_seen=_earliest(a.first_seen, b.first_seen),
        last_seen=newer.last_seen,
        event_at=_earliest(a.event_at, b.event_at),
        star=a.star or b.star,
        notified_at=_merge_notified(a.notified_at, b.notified_at),
        last_in_result=a.last_in_result or b.last_in_result,
        in_stock=newer.in_stock,
        info=newer.info,
    )


def _merge_beer(a: BeerRec, b: BeerRec) -> BeerRec:
    # the freshest cached rating wins, so an alias merge never resurrects a stale one
    newer = b if (b.rating_at and (not a.rating_at or parse_iso(b.rating_at) > parse_iso(a.rating_at))) else a
    return BeerRec(first_seen_city=_earliest(a.first_seen_city, b.first_seen_city), n_key=a.n_key or b.n_key,
                  rating=newer.rating, style=newer.style, abv=newer.abv, ibu=newer.ibu, rating_at=newer.rating_at)


def _merge_brewery_new(a: BreweryNewRec, b: BreweryNewRec) -> BreweryNewRec:
    return BreweryNewRec(
        brewery_id=a.brewery_id,
        found_at=_earliest(a.found_at, b.found_at),
        star=a.star or b.star,
        notified_at=_merge_notified(a.notified_at, b.notified_at),
        info=a.info,
    )


def _rekey(recs: dict, aliases: Mapping[str, str], merge: Callable) -> dict:
    # Records already under their final key go first, so they are `a` in merge (their n_key/info win).
    out, moved = {}, []
    for key, rec in recs.items():
        new = resolve_alias(key, aliases)
        if new == key:
            out[key] = rec
        else:
            moved.append((new, rec))
    for new, rec in moved:
        out[new] = merge(out[new], rec) if new in out else rec
    return out


def apply_aliases(state: State, aliases: Mapping[str, str]) -> None:
    for place_id, recs in state.pairs.items():
        state.pairs[place_id] = _rekey(recs, aliases, _merge_pair)
    state.beers = _rekey(state.beers, aliases, _merge_beer)
    state.brewery_new = _rekey(state.brewery_new, aliases, _merge_brewery_new)
    for items in state.shop_items.values():
        for item_id, key in items.items():
            items[item_id] = resolve_alias(key, aliases)


def merge_places(state: State, merges: Mapping[str, str]) -> None:
    """v1.1: one-time merge of an old place id's pairs into its replacement (places.yaml `merged_from`,
    e.g. two Untappd venues folded into one place). Once an old id's pairs are moved, it is gone from
    state.pairs, so a later run's call is a no-op -- no flag needed. Colliding beer keys use the same
    never-re-announce merge as aliases (_merge_pair), so the merge itself never creates a fresh event."""
    for old, new in merges.items():
        if old == new or old not in state.pairs:
            continue
        new_pairs = state.pairs.setdefault(new, {})
        for key, rec in state.pairs.pop(old).items():
            new_pairs[key] = _merge_pair(new_pairs[key], rec) if key in new_pairs else rec


def prune(state: State, now: datetime) -> int:
    healthy = {
        key.partition(":")[2]
        for key, rec in state.sources.items()
        if rec.last_ok is not None and age_days(parse_iso(rec.last_ok), now) <= SOURCE_OK_DAYS
    }
    removed = 0
    for place_id, recs in state.pairs.items():
        if place_id not in healthy:
            continue
        for key in [k for k, r in recs.items() if age_days(parse_iso(r.last_seen), now) > PAIR_KEEP_DAYS]:
            del recs[key]
            removed += 1
    return removed


def record_venues(state: State, results: Sequence[SourceResult], now: datetime,
                  known_venue_ids: frozenset[int] = frozenset()) -> None:
    """v1.1: state.venues from every fetch's venue_meta (own venue: logo/verified) and venue_checkins
    (every venue seen in check-ins, tracked or not); check-ins deduped by id, pruned to VENUE_KEEP_DAYS (60) days.

    known_venue_ids: every place's venue id from places.yaml, enabled or disabled (Config.known_venue_ids).
    A venue record left with no check-in in the last VENUE_KEEP_DAYS days is dropped unless its id is known, so that
    worldwide venues surfacing on brewery pages don't accumulate in state.json forever, while a disabled
    place's own venue (kept for its logo/verified badge) survives a quiet spell."""
    seen_ids: dict[str, set[int]] = {}
    for result in results:
        if result.venue_meta:
            m = result.venue_meta
            rec = state.venues.setdefault(str(m["venue_id"]), VenueRec(name=m["name"], url=m["url"]))
            rec.name, rec.url, rec.logo, rec.verified, rec.has_meta = (
                m["name"], m["url"], m["logo"], m["verified"], True)
        for vc in result.venue_checkins:
            key = str(vc.venue_id)
            rec = state.venues.setdefault(key, VenueRec(name=vc.venue_name, url=vc.venue_url))
            if not rec.has_meta:
                rec.name, rec.url = vc.venue_name, vc.venue_url
            ids = seen_ids.setdefault(key, {c["id"] for c in rec.checkins})
            if vc.checkin_id not in ids:
                rec.checkins.append({"id": vc.checkin_id, "at": iso(vc.at)})
                ids.add(vc.checkin_id)
    for rec in state.venues.values():
        rec.checkins = [c for c in rec.checkins if age_days(parse_iso(c["at"]), now) <= VENUE_KEEP_DAYS]
    for vid in [v for v, rec in state.venues.items() if not rec.checkins and int(v) not in known_venue_ids]:
        del state.venues[vid]
