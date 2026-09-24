"""state.json: the bot's memory. Load/save, alias merging, pruning."""
import copy
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from taps.timeutil import age_days, iso, parse_iso

PAIR_KEEP_DAYS = 180      # pairs not seen for longer are pruned...
SOURCE_OK_DAYS = 30       # ...but only if a source of their place succeeded this recently
ALIAS_MAX_HOPS = 5
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
    return BeerRec(first_seen_city=_earliest(a.first_seen_city, b.first_seen_city), n_key=a.n_key or b.n_key)


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
