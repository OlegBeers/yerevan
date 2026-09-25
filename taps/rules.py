"""Merge source results into state: pairs, events and silent baselines (spec §6)."""
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime

from taps.breaker import evaluate, result_keys
from taps.config import Config, Place
from taps.corrections import Corrections
from taps.model import Sighting, SourceResult, strip_color, u_key, untappd_n_key
from taps.shop_filter import classify
from taps.state import BeerRec, BreweryNewRec, PairRec, SourceRec, State, resolve_alias
from taps.timeutil import age_days, iso, parse_iso, to_yerevan

CHECKIN_EVENT_DAYS = 7     # older check-ins are stored silently
CHECKIN_KEEP_DAYS = 21     # older check-ins are ignored
MANUAL_EVENT_DAYS = 3      # older manual entries are stored silently
TRIP_REASONS = ("shrink", "mass_new", "list_mass_new")   # breaker trips; any other discard is a failure
INFO_FIELDS = ("title", "name", "brewery", "style", "abv", "ibu", "rating", "price_amd", "volume_ml", "container",
               "serving", "url", "shop_url", "logo", "menu_id", "shop_item_id", "manual_id", "manual_by", "manual_date")
BREWERY_INFO_FIELDS = ("name", "brewery", "style", "abv", "url")
CHECKIN_BACKFILL_FIELDS = ("style", "abv", "ibu", "rating")   # filled from a menu pair when the check-in lacks them
# Who may rewrite a pair's display fields: menus and shops over check-ins over manual entries.
KIND_RANK = {"manual": 0, "checkin": 1}


@dataclass
class MergeOutcome:
    events: list[tuple[str, str]] = field(default_factory=list)       # (place_id, beer_key) new pending events
    brewery_events: list[str] = field(default_factory=list)           # beer_keys added to state.brewery_new
    failed: list[tuple[str, str]] = field(default_factory=list)       # (source_key, error)
    tripped: list[tuple[str, str]] = field(default_factory=list)      # (source_key, reason)
    accepted: list[str] = field(default_factory=list)                 # source keys accepted after repeated trips
    ok: list[str] = field(default_factory=list)                       # source keys merged (merge or baseline)


def merge_results(state: State, results: Sequence[SourceResult], config: Config, corrections: Corrections,
                  now: datetime) -> MergeOutcome:
    merger = _Merger(state, config, corrections, now)
    merger.apply_hide()
    for result in results:
        verdict = evaluate(result, state, now)
        if verdict.action == "discard":
            target = merger.out.tripped if verdict.reason in TRIP_REASONS else merger.out.failed
            target.append((result.key, verdict.reason))
            continue
        merger.merge(result, baseline=verdict.action == "baseline")
        if verdict.reason == "accepted":
            merger.out.accepted.append(result.key)
        merger.out.ok.append(result.key)
    merger.reconcile_manual()
    return merger.out


def _rank(kind: str | None) -> int:
    return KIND_RANK.get(kind, 2)


def _drop(rec: PairRec) -> None:
    """Hidden or filtered: not shown on the site and never an event, even after the hide is lifted."""
    rec.info["hidden"] = True
    if rec.notified_at is None:
        rec.notified_at = "suppressed"


class _Merger:
    def __init__(self, state: State, config: Config, corrections: Corrections, now: datetime):
        self.state, self.config, self.corrections, self.now = state, config, corrections, now
        self.stamp = iso(now)
        self.out = MergeOutcome()
        # ⭐ compares with the beers known before this call: their keys and the n-keys of Untappd beers
        self.known = set(state.beers) | {b.n_key for b in state.beers.values() if b.n_key}
        # a place stays new until each of its own sources had a first successful run
        self.new_places = {
            p.id for p in config.places.values()
            if any(k not in state.sources or not state.sources[k].baseline_done for k in p.source_keys())
        }
        self.hidden = {(p, resolve_alias(k, corrections.aliases)) for p, k in corrections.hide}
        self._manual_touched: set[tuple[str, str]] | None = None

    def reconcile_manual(self) -> None:
        """Manual entries cover every place at once: a pair not in this run's result is no longer reported."""
        if self._manual_touched is None:
            return
        touched = self._manual_touched
        for place_id, pairs in self.state.pairs.items():
            for key, pair in pairs.items():
                if pair.info.get("source") != "manual":
                    continue
                pair.last_in_result = (place_id, key) in touched
                if not pair.last_in_result and pair.event_at is not None and pair.notified_at is None:
                    pair.notified_at = "suppressed"

    def apply_hide(self) -> None:
        """A hide works at once, also for pairs whose source is not fetched in this run."""
        for place_id, key in self.hidden:
            rec = self.state.pair(place_id, key)
            if rec is not None:
                _drop(rec)

    def merge(self, result: SourceResult, baseline: bool) -> None:
        rec = self.state.source(result.key)
        if result.source == "untappd_brewery_list":
            self._brewery_list(result, rec, baseline)
        else:
            self._sightings(result, rec, baseline)
        rec.baseline_done, rec.last_ok, rec.fail_streak, rec.last_error = True, self.stamp, 0, None
        if result.full:
            rec.last_count = len(result_keys(result))
            rec.last_full = self.stamp
        rec.seen_menu_ids = sorted({s.menu_id for s in result.sightings if s.menu_id})
        rec.menu_updated_at = iso(result.menu_updated_at) if result.menu_updated_at else None

    def _sightings(self, result: SourceResult, rec: SourceRec, baseline: bool) -> None:
        tabs = set(rec.seen_menu_ids)   # menu tabs of the previous successful run
        touched: set[tuple[str, str]] = set()
        # a partial Beer City run only scouts for new arrivals: items already known keep their stored data
        partial_shop = result.source == "beercity" and not result.full
        known_items = set(self.state.shop_items.get(result.place_id, {})) if partial_shop else set()
        for s in result.sightings:
            place = self.config.places.get(s.place_id)
            if place is None or self._ignored_checkin(s, place):
                continue
            key = self._key(s)
            silent = (baseline
                      or (s.menu_id is not None and s.menu_id not in tabs)
                      or (place.id in self.new_places and s.source not in place.sources and s.kind != "manual"))
            refresh = not (partial_shop and s.shop_item_id in known_items)
            self._pair(s, place, key, silent, first=(place.id, key) not in touched, refresh=refresh)
            touched.add((place.id, key))
        if result.source == "manual":
            self._manual_touched = touched
        elif result.full and result.place_id is not None:
            for key, pair in self.state.pairs.get(result.place_id, {}).items():
                if pair.info.get("source") == result.source:
                    pair.last_in_result = (result.place_id, key) in touched

    def _ignored_checkin(self, s: Sighting, place: Place) -> bool:
        """A check-in counts only when poured at a place without a menu (spec §6). The serving no longer
        matters in any kind of place (v1.1): a bottle, can or unlabelled check-in is as good a sighting
        as a draft pour, in a bar or brewpub just like it already was in a shop."""
        if s.kind != "checkin":
            return False
        return place.has_menu or s.at_home or age_days(s.seen_at, self.now) > CHECKIN_KEEP_DAYS

    def _key(self, s: Sighting) -> str:
        key = resolve_alias(s.beer_key, self.corrections.aliases)
        if s.shop_item_id is None:
            return key
        items = self.state.shop_items.setdefault(s.place_id, {})
        if s.shop_item_id not in items:   # a shop item keeps the key it was first stored under
            if s.source == "parma" and strip_color(key) == key:
                at_place = self.state.pairs.get(s.place_id, {})
                colored = sorted(k for k in at_place if k != key and strip_color(k) == key)
                if colored and key not in at_place:
                    key = colored[0]      # "Dahook 0.5L" joins the known "Dahook light 0.5L"
            items[s.shop_item_id] = key
        return items[s.shop_item_id]

    def _pair(self, s: Sighting, place: Place, key: str, silent: bool, first: bool, refresh: bool = True) -> None:
        pairs = self.state.pairs.setdefault(place.id, {})
        rec = pairs.get(key)
        # a sighting that lacks brand (Beer City/Parma only fetch it for genuinely new items) falls back
        # to the brand already on file, so the shop filter keeps classifying the item the same way.
        brand = s.brewery if s.brewery is not None else (rec.info.get("brewery") if rec is not None else None)
        drop = (place.id, key) in self.hidden or (
            s.kind == "shop" and not classify(s.title, brand, self.corrections.not_craft, s.category)[0])
        nk = self._n_key(s, key)
        if rec is None:
            notified = "baseline" if silent else "suppressed" if drop or self._suppressed(s, place, nk) else None
            rec = pairs[key] = PairRec(first_seen=self.stamp, last_seen=self.stamp, star=self._star(key, nk),
                                       notified_at=notified, in_stock=s.in_stock)
        else:
            rec.last_seen = self.stamp
            if refresh and s.in_stock is not None and (first or s.in_stock):   # one item in stock is enough
                rec.in_stock = s.in_stock
        owner = rec.info.get("kind")
        if owner is None or _rank(s.kind) >= _rank(owner):
            self._update_info(rec, s, key, refresh)
        if drop:
            _drop(rec)
            return
        if rec.notified_at is None and rec.event_at is None:   # not an event yet (new, or never in stock)
            if silent:
                rec.notified_at = "baseline"
            elif rec.in_stock is not False:
                rec.event_at = self.stamp     # a new pair, or a shop item in stock for the first time
                self.out.events.append((place.id, key))
        self._remember(key, nk)

    def _update_info(self, rec: PairRec, s: Sighting, key: str, refresh: bool = True) -> None:
        """A field the sighting lacks keeps its old value: shop product pages are read only once."""
        rec.last_in_result = True
        if not refresh:   # a partial Beer City resighting of a known item: seen again, but not re-trusted
            return
        old_checkin = rec.info.get("checkin_at")
        rec.info.update({f: getattr(s, f) for f in INFO_FIELDS if getattr(s, f) is not None})
        rec.info.update(source=s.source, kind=s.kind)
        if s.servings:
            rec.info["servings"] = [asdict(serving) for serving in s.servings]
        else:
            rec.info.pop("servings", None)   # the list is the source's whole answer: a serving that left is gone
        if s.kind == "checkin":
            rec.info["checkin_at"] = iso(max(s.seen_at, parse_iso(old_checkin)) if old_checkin else s.seen_at)
            self._backfill_checkin(rec, key)
        rec.info.pop("hidden", None)      # set again by _drop while hidden or filtered

    def _backfill_checkin(self, rec: PairRec, key: str) -> None:
        """A check-in poured without menu details borrows style/abv/ibu/rating from a menu pair of the
        same beer, else (v1.1 §2) from the state.beers cache filled by fetch_beer_ratings for beers
        seen only in check-ins."""
        missing = [f for f in CHECKIN_BACKFILL_FIELDS if rec.info.get(f) is None]
        if not missing:
            return
        for pairs in self.state.pairs.values():
            menu_rec = pairs.get(key)
            if menu_rec is not None and menu_rec.info.get("kind") == "menu":
                for f in missing:
                    value = menu_rec.info.get(f)
                    if value is not None:
                        rec.info[f] = value
                return
        beer = self.state.beers.get(key)
        if beer is None:
            return
        for f in missing:
            value = getattr(beer, f, None)
            if value is not None:
                rec.info[f] = value

    def _suppressed(self, s: Sighting, place: Place, nk: str | None) -> bool:
        if s.kind == "manual":
            manual_date = date.fromisoformat(s.manual_date)
            if manual_date < to_yerevan(parse_iso(self.state.started_at)).date():
                return True   # predates this state generation: likely already announced before it was lost
            days = (to_yerevan(self.now).date() - manual_date).days
            if s.manual_id in self.state.announced_manual or days > MANUAL_EVENT_DAYS:
                return True
        if s.kind == "checkin" and age_days(s.seen_at, self.now) > CHECKIN_EVENT_DAYS:
            return True
        # the same beer is already here under another key (Untappd changed the id)
        return nk is not None and any(self._stored_n_key(k) == nk for k in self.state.pairs.get(place.id, {}))

    def _stored_n_key(self, key: str) -> str | None:
        if key.startswith("n:"):
            return key
        beer = self.state.beers.get(key)
        return beer.n_key if beer else None

    def _n_key(self, s: Sighting, key: str) -> str | None:
        """n-key for ⭐ and duplicates; a friend's spelling or an aliased shop title is not an Untappd name."""
        if key.startswith("n:"):
            return key
        if s.source == "manual" or s.untappd_beer_id is None:
            return None
        return self._untappd_n_key(s.brewery, s.name)

    def _untappd_n_key(self, brewery: str | None, name: str) -> str | None:
        nk = untappd_n_key(brewery, name, self.corrections.brewery_aliases)
        return None if nk == "n:" else nk

    def _star(self, key: str, nk: str | None) -> bool:
        return key not in self.known and nk not in self.known

    def _remember(self, key: str, nk: str | None) -> None:
        """beers: every accepted beer, with the n-key of an Untappd beer."""
        own_nk = nk if key.startswith("u:") else None
        beer = self.state.beers.get(key)
        if beer is None:
            self.state.beers[key] = BeerRec(first_seen_city=self.stamp, n_key=own_nk)
        elif beer.n_key is None:
            beer.n_key = own_nk

    def _brewery_list(self, result: SourceResult, rec: SourceRec, baseline: bool) -> None:
        """🏭: an id above the brewery's maximum is a new beer; a lower unknown id is remembered silently."""
        for b in result.brewery_beers:
            key = resolve_alias(u_key(b.untappd_beer_id), self.corrections.aliases)
            nk = self._untappd_n_key(b.brewery, b.name)
            if not baseline and b.untappd_beer_id > rec.max_beer_id and key not in self.state.brewery_new:
                info = {f: getattr(b, f) for f in BREWERY_INFO_FIELDS if getattr(b, f) is not None}
                self.state.brewery_new[key] = BreweryNewRec(brewery_id=b.brewery_id, found_at=self.stamp,
                                                            star=self._star(key, nk), info=info)
                self.out.brewery_events.append(key)
            self._remember(key, nk)
        rec.max_beer_id = max([rec.max_beer_id, *(b.untappd_beer_id for b in result.brewery_beers)])
