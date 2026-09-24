"""One run of the bot (spec §3): pull, collect, merge, site data, digest protocol (§7), push, send."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Mapping, Sequence

from taps.config import Config, ConfigError, load_config
from taps.corrections import Corrections, load_corrections
from taps.digest import build_digest, drop_stale_events, is_due, mark_sent, rollback
from taps.fetch import (
    FetchError, Http, PageFetcher, UntappdClient, dump_debug_html, playwright_fetcher, untappd_due,
)
from taps.gitsync import CheckoutError, GitError, commit_and_push, pull_ff
from taps.model import SourceResult
from taps.rules import CHECKIN_KEEP_DAYS, MergeOutcome, merge_results
from taps.site_data import build_site_data, write_site_data
from taps.sources.beercity import fetch_beercity
from taps.sources.buyam import fetch_buyam
from taps.sources.local_match import KnownBeer, clean_query, local_match
from taps.sources.manual import manual_result
from taps.sources.parma import fetch_parma
from taps.sources.untappd_beer import parse_beer_page
from taps.sources.untappd_brewery import fetch_brewery_checkins, fetch_brewery_list
from taps.sources.untappd_checkins import (
    fetch_venue_checkins, is_armenia_location, is_yerevan_city, parse_venue_location, parse_venue_meta,
)
from taps.sources.untappd_menu import fetch_menu
from taps.sources.untappd_search import matches, parse_search_results, search_url
from taps.sources.yerevan_city import fetch_yerevan_city
from taps.state import (
    VENUE_KEEP_DAYS, BeerRec, ShopMatchRec, State, apply_aliases, load_state, merge_places, prune, record_venues,
    save_state,
)
from taps.telegram import MAX_TEXT, Alerter, SendOutcome, send_message
from taps.timeutil import YEREVAN, age_days, iso, parse_iso, to_yerevan, utcnow, yerevan_date

STATE_FILE = "state.json"
FATAL_FILE = ".taps-fatal"   # dedup marker for fatal alerts when no state.json can be trusted; not committed
SITE_DATA = Path("site") / "data.json"
BUTTON_TEXT = "Открыть список"
LISTS_PER_RUN = 2          # brewery beer lists per Untappd collection (spec §10: 1-2)
ADMIN_ENV_KEYS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ADMIN_CHAT_ID")          # alerts only
ENV_KEYS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_ADMIN_CHAT_ID", "SITE_URL")   # a digest may go out
TRIP_RU = {
    "shrink": "позиций стало меньше половины",
    "mass_new": "больше половины позиций новые",
    "list_mass_new": "больше 5 новых сортов за раз",
}
CLOUDFLARE_ALERT = "Untappd показал проверку Cloudflare: сбор Untappd в этом прогоне остановлен, магазины работают"
DISCOVERY_MIN_CHECKINS = 3   # v1.1: untracked venues below this are not worth mentioning
DISCOVERY_HOUR = 9           # weekly report: first successful run on or after Monday 09:00 Yerevan
                              # (or, during the owner's temporary discovery_daily_until window, 09:00 daily)
DISCOVERY_MAX_VENUES = 20    # cap on lines in the weekly report, besides the MAX_TEXT length cap below
LOCATION_CANDIDATES_PER_RUN = 3   # v1.1 city check (§4): at most this many untracked venue pages per run
LOCATION_MIN_CHECKINS = 2         # below this, not worth spending a page on
LOCATION_RECHECK_DAYS = 90        # a failed/unknown check is retried after this many days
BEER_RATING_CANDIDATES_PER_RUN = 5   # v1.1 §2: beer pages fetched per run for check-in-only ratings
BEER_RATING_MAX_AGE_DAYS = 30        # a cached rating older than this is refreshed
SHOP_SEARCH_CANDIDATES_PER_RUN = 8   # v1.1 §3: shop beers searched on Untappd per run
SHOP_MATCH_RETRY_DAYS = 30           # a failed search ("no_match") is retried after this many days
SHOP_MATCH_REFRESH_PER_RUN = 3       # matched shop beers whose cached rating is refreshed per run
SHOP_MATCH_MAX_AGE_DAYS = 30         # a matched beer's cached rating is refreshed after this many days


@dataclass
class Deps:
    http: Http = field(default_factory=Http)
    untappd_fetcher: Callable[[], tuple[PageFetcher, Callable[[], None]]] = playwright_fetcher
    send: Callable[..., SendOutcome] = send_message
    pull: Callable[[Path], None] = pull_ff
    push: Callable[[Path, Sequence[str], str], bool] = commit_and_push
    sleep: Callable[[float], None] = time.sleep     # Untappd pauses; tests pass a no-op


# --- collect -----------------------------------------------------------------

def _guard(key: str, place_id: str | None, fetch: Callable[[], SourceResult]) -> SourceResult:
    """A crash in one source (e.g. changed markup) fails that source only (spec §10)."""
    try:
        return fetch()
    except Exception as e:
        return SourceResult(key=key, source=key.partition(":")[0], ok=False, place_id=place_id,
                            error=f"exception: {type(e).__name__}: {e}"[:300])


DEBUG_ERRORS = ("empty", "bad_response")   # TAPS_DEBUG_DIR: dump the page behind these Untappd failures


def _maybe_dump_debug(result: SourceResult, client: UntappdClient) -> None:
    if result.error in DEBUG_ERRORS or (result.error or "").startswith("exception:"):
        dump_debug_html(result.key, client)


def collect_untappd(state: State, config: Config, corrections: Corrections, now: datetime, deps: Deps,
                    alerter: Alerter) -> tuple[list[SourceResult], UntappdClient | None]:
    """Menus -> brewery check-ins -> venue check-ins -> 1-2 brewery lists -> city check for a few
    discovered venues (v1.1, §4); at most once per 20 h."""
    if not untappd_due(state.untappd, now):
        return [], None
    ba = corrections.brewery_aliases
    places = list(config.places.values())
    lists = [b for b in config.breweries if b.list_enabled]
    cursor = state.untappd.brewery_list_cursor
    picked = [lists[(cursor + i) % len(lists)] for i in range(min(LISTS_PER_RUN, len(lists)))]
    jobs: list[tuple[str, str | None, Callable[[UntappdClient], SourceResult]]] = [
        *[(f"untappd_menu:{p.id}", p.id, lambda c, p=p: fetch_menu(c, p, now, ba))
          for p in places if "untappd_menu" in p.sources],
        *[(f"untappd_brewery:{b.brewery_id}", None, lambda c, b=b: fetch_brewery_checkins(c, b, config, now, ba))
          for b in config.breweries],
        *[(f"untappd_checkins:{p.id}", p.id, lambda c, p=p: fetch_venue_checkins(c, p, config, now, ba))
          for p in places if "untappd_checkins" in p.sources],
        *[(f"untappd_brewery_list:{b.brewery_id}", None, lambda c, b=b: fetch_brewery_list(c, b, now))
          for b in picked],
    ]
    try:
        fetch_page, close = deps.untappd_fetcher()
    except Exception as e:
        alerter.alert("untappd:browser", f"не запустился браузер для Untappd: {type(e).__name__}: {e}",
                      dedupe_by_key=True)
        return [], None
    alerter.resolve("untappd:browser")
    try:
        daily_pages = config.settings.effective_daily_pages(yerevan_date(now))   # owner's temporary boost, if active
        client = UntappdClient(state.untappd, daily_pages, now, fetch_page, sleep=deps.sleep)
        # once blocked, the sources themselves return error "blocked" without spending pages
        results = []
        for key, place_id, job in jobs:
            result = _guard(key, place_id, lambda: job(client))
            _maybe_dump_debug(result, client)   # client.last_html/url still belong to this job
            results.append(result)
        discover_venue_locations(state, config, client, now)   # v1.1 city check, same client/budget
        fetch_beer_ratings(state, client, now)                 # v1.1 §2: check-in-only beer ratings
        search_limit = config.settings.effective_search_per_run(yerevan_date(now), SHOP_SEARCH_CANDIDATES_PER_RUN)
        match_shop_beers(state, client, now, search_limit)     # v1.1 §3: search shop beers on Untappd
        refresh_shop_matches(state, client, now)               # v1.1 §3: refresh matched shop ratings
    finally:
        close()
    if client.responded:
        state.untappd.last_attempt = iso(now)
    if lists:
        done = sum(1 for r in results if r.source == "untappd_brewery_list" and r.error != "budget")
        state.untappd.brewery_list_cursor = (cursor + done) % len(lists)
    # out of budget: skipped without a failure status (spec §10)
    return [r for r in results if r.error != "budget"], client


def _beercity_full(state: State, place_id: str, now: datetime) -> bool:
    rec = state.sources.get(f"beercity:{place_id}")
    return rec is None or rec.last_full is None or yerevan_date(parse_iso(rec.last_full)) != yerevan_date(now)


def collect_shops(state: State, config: Config, corrections: Corrections, now: datetime,
                  http: Http) -> list[SourceResult]:
    """Every run: Beer City, Yerevan City, Parma, then buy.am."""
    ba = corrections.brewery_aliases

    def known(p) -> set[str]:
        return set(state.shop_items.get(p.id, {}))

    fetchers = {
        "beercity": lambda p: fetch_beercity(http, p, known(p), _beercity_full(state, p.id, now), now, ba),
        "yerevan_city": lambda p: fetch_yerevan_city(http, p, now, ba),
        "parma": lambda p: fetch_parma(http, p, known(p), now, ba),
        "buyam": lambda p: fetch_buyam(http, p, now, ba),
    }
    return [_guard(f"{name}:{p.id}", p.id, lambda: fetch(p))
            for name, fetch in fetchers.items() for p in config.places.values() if name in p.sources]


# --- v1.1: city check for discovered venues (§4) ------------------------------

def _location_candidates(state: State, config: Config, now: datetime) -> list:
    """Untracked venues with >= LOCATION_MIN_CHECKINS check-ins in the last 30 days and no known
    location yet -- never checked, or checked long enough ago to deserve a retry (a location once
    known, Armenian or not, is never rechecked). Most check-ins first, capped per run.

    M-4: a location is "known" once either city or country is set -- a foreign venue whose address
    carried only addressCountry (no addressLocality) must not look unchecked. A venue pruned from
    state.venues (VENUE_KEEP_DAYS) and later reappearing starts as a fresh record with both fields
    None, so it is rechecked once more even if it was previously known to be foreign."""
    due = []
    for vid_str, rec in state.venues.items():
        if int(vid_str) in config.known_venue_ids or rec.city is not None or rec.country is not None:
            continue
        checked = rec.location_checked_at
        if checked is not None and age_days(parse_iso(checked), now) <= LOCATION_RECHECK_DAYS:
            continue
        recent = len([c for c in rec.checkins if age_days(parse_iso(c["at"]), now) <= VENUE_KEEP_DAYS])
        if recent >= LOCATION_MIN_CHECKINS:
            due.append((recent, rec))
    due.sort(key=lambda t: -t[0])
    return [rec for _, rec in due[:LOCATION_CANDIDATES_PER_RUN]]


def discover_venue_locations(state: State, config: Config, client: UntappdClient, now: datetime) -> None:
    """Fetch up to LOCATION_CANDIDATES_PER_RUN untracked venue pages to learn their city/country, so a
    foreign venue -- seen worldwide on an Armenian brewery's check-in page -- can be excluded from the
    weekly report and the site's "Все места" tab. Any fetch failure (network, Cloudflare, out of
    budget) stops the whole step for this run without marking the venue checked, so it is retried next
    run; a page that loads but carries no parseable location is marked checked and only retried after
    LOCATION_RECHECK_DAYS.

    I-1: an unexpected page (odd markup, a parser bug) must fail this one venue only, never the run --
    it is marked checked (so LOCATION_RECHECK_DAYS applies) and the loop moves on to the next candidate."""
    for rec in _location_candidates(state, config, now):
        try:
            html = client.get(rec.url)
        except FetchError:
            break
        try:
            loc = parse_venue_location(html)
            rec.city = loc["locality"] if loc else None
            rec.country = "Armenia" if is_armenia_location(loc) else (loc["country"] if loc else None)
            meta = parse_venue_meta(html)
            if meta is not None:
                rec.logo, rec.verified = meta["logo"], meta["verified"]
        except Exception:
            pass
        rec.location_checked_at = iso(now)


# --- v1.1 §2: beer ratings for beers seen only in check-ins ------------------

def _beer_rating_candidates(state: State, now: datetime) -> list[tuple[str, str]]:
    """(key, url) of check-in-only beers (no menu pair anywhere) visible on the site in the last
    CHECKIN_KEEP_DAYS days whose cached rating is missing or older than BEER_RATING_MAX_AGE_DAYS,
    most recently seen first, capped at BEER_RATING_CANDIDATES_PER_RUN. Candidates come from state as
    it stood before this run's merge, same as the venue-location candidates above."""
    menu_keys = {key for pairs in state.pairs.values() for key, rec in pairs.items()
                if rec.info.get("kind") == "menu"}
    best: dict[str, tuple[str, str]] = {}   # key -> (checkin_at, url)
    for pairs in state.pairs.values():
        for key, rec in pairs.items():
            if not key.startswith("u:") or key in menu_keys or rec.info.get("kind") != "checkin":
                continue
            checkin_at, url = rec.info.get("checkin_at"), rec.info.get("url")
            if not checkin_at or not url or age_days(parse_iso(checkin_at), now) > CHECKIN_KEEP_DAYS:
                continue
            beer = state.beers.get(key)
            if (beer and beer.rating is not None and beer.rating_at
                    and age_days(parse_iso(beer.rating_at), now) <= BEER_RATING_MAX_AGE_DAYS):
                continue
            if key not in best or checkin_at > best[key][0]:
                best[key] = (checkin_at, url)
    ordered = sorted(best.items(), key=lambda kv: kv[1][0], reverse=True)
    return [(key, url) for key, (_, url) in ordered[:BEER_RATING_CANDIDATES_PER_RUN]]


def fetch_beer_ratings(state: State, client: UntappdClient, now: datetime) -> None:
    """Fetch up to BEER_RATING_CANDIDATES_PER_RUN beer pages to fill state.beers rating/style/abv/ibu
    for beers seen only in check-ins (v1.1 §2), so rules._backfill_checkin can use them; a budget or
    Cloudflare error stops this step only, same client/pauses as the other Untappd sources. A page
    that doesn't come back parseable as a beer page is dumped for debugging (TAPS_DEBUG_DIR) --
    the cache is still stamped as checked, so it isn't refetched every run."""
    for key, url in _beer_rating_candidates(state, now):
        try:
            html = client.get(url)
        except FetchError:
            break
        try:
            data = parse_beer_page(html)   # I-1: an unexpected page must fail this one beer, not the run
        except Exception:
            data = None
        if not data or all(v is None for v in data.values()):
            dump_debug_html(f"untappd_beer_{key.removeprefix('u:')}", client)
        beer = state.beers.setdefault(key, BeerRec(first_seen_city=iso(now)))
        if data:
            for field in ("rating", "style", "abv", "ibu"):
                if data[field] is not None:
                    setattr(beer, field, data[field])
        beer.rating_at = iso(now)


# --- v1.2 beer identity: match shop/menu/manual beers to Untappd, zero pages or search --------

def apply_same_as(state: State, corrections: Corrections, now: datetime) -> None:
    """corrections.yaml same_as: a manual identity override, wins over local/search matches -- run
    before both so their own candidate selection skips whatever this already resolved. A key the
    owner removed from corrections.yaml drops its stale manual match, so local/search matching
    resumes for it. untappd_id: null ("не то же") records a manual block with no Untappd id/url,
    re-applied every run so it never ages into local/search's own retry window."""
    active_keys = {key for _, key in corrections.same_as}
    for key, match in list(state.shop_matches.items()):
        if match.via == "manual" and key not in active_keys:
            del state.shop_matches[key]
    if not corrections.same_as:
        return
    catalog = {b.untappd_id: b for b in known_untappd_beers(state)}
    for (place_id, key), untappd_id in corrections.same_as.items():
        if key not in state.pairs.get(place_id, {}):
            continue
        known = catalog.get(untappd_id) if untappd_id is not None else None
        state.shop_matches[key] = ShopMatchRec(
            untappd_beer_id=untappd_id,
            url=f"https://untappd.com/beer/{untappd_id}" if untappd_id is not None else None,
            name=known.name if known else None, brewery=known.brewery if known else None,
            matched_at=iso(now), via="manual")


def known_untappd_beers(state: State) -> list[KnownBeer]:
    """Every Untappd beer already known from a bar's own menu/check-ins/brewery page ("u:"-keyed
    pairs, wherever seen), deduped by id. A pair missing name or brewery is incomplete and skipped."""
    out: dict[int, KnownBeer] = {}
    for pairs in state.pairs.values():
        for key, rec in pairs.items():
            if not key.startswith("u:"):
                continue
            beer_id = int(key[2:])
            name, brewery = rec.info.get("name"), rec.info.get("brewery")
            if beer_id not in out and name and brewery:
                out[beer_id] = KnownBeer(untappd_id=beer_id, name=name, brewery=brewery,
                                         rating=rec.info.get("rating"), style=rec.info.get("style"),
                                         abv=rec.info.get("abv"), logo=rec.info.get("logo"))
    return list(out.values())


def match_shop_beers_locally(state: State, now: datetime) -> None:
    """Before Untappd search: match every shop/menu/manual candidate against beers already known
    from a bar's own menu (zero extra pages). A miss is not cached (unlike search's "no_match") --
    it costs nothing to retry every run, and a bar might reveal the match later. A hit is recorded
    exactly like a search match (state.shop_matches), marked via="local", so search skips it too.
    The matched beer's rating/style/abv/logo are already known too, copied straight in with
    checked_at stamped now, so refresh_shop_matches doesn't spend a page re-fetching them."""
    candidates = known_untappd_beers(state)
    for key, brand, name in _shop_match_candidates(state, now, limit=None):
        found = local_match(brand, name, candidates)
        if found is not None:
            state.shop_matches[key] = ShopMatchRec(
                untappd_beer_id=found.untappd_id, url=f"https://untappd.com/beer/{found.untappd_id}",
                rating=found.rating, style=found.style, abv=found.abv, logo=found.logo,
                name=found.name, brewery=found.brewery, matched_at=iso(now), checked_at=iso(now), via="local")


# --- v1.1 §3: match shop beers to Untappd via search --------------------------

def _shop_match_candidates(state: State, now: datetime,
                           limit: int | None = SHOP_SEARCH_CANDIDATES_PER_RUN) -> list[tuple[str, str, str]]:
    """(key, brand, name) of shop/menu/manual beers with no successful match yet -- never searched,
    or a failed search ("no_match") old enough to retry -- most recently seen first, capped at
    SHOP_SEARCH_CANDIDATES_PER_RUN. A key already resolved to an Untappd id via a corrections.yaml
    alias starts with "u:", not "n:", so it needs no search (apply_aliases runs before collect_untappd).
    "menu"/"manual" (v1.2 beer identity: Dargett Brewpub via buyam, a friend's manual sighting) are
    candidates too -- an Untappd-native "u:"-keyed menu pair is already excluded by the key check.
    limit=None (only match_shop_beers_locally) also ignores a search "no_match" record's retry-days
    wait: local matching is free, so it need not wait on search's own throttle -- but a corrections.yaml
    same_as untappd_id: null override (via="manual") still blocks it, same as search."""
    best: dict[str, tuple[str, str, str]] = {}   # key -> (last_seen, brand, name)
    for pairs in state.pairs.values():
        for key, rec in pairs.items():
            if not key.startswith("n:") or rec.info.get("kind") not in ("shop", "menu", "manual"):
                continue
            if rec.info.get("hidden") or rec.in_stock is False or not rec.last_in_result:
                continue   # not shown on the site (mass brand, hidden, out of stock): don't spend pages on it
            match = state.shop_matches.get(key)
            if match is not None and match.untappd_beer_id is not None:
                continue
            if match is not None:
                if limit is None:
                    if match.via == "manual":
                        continue
                elif age_days(parse_iso(match.matched_at), now) <= SHOP_MATCH_RETRY_DAYS:
                    continue
            brand, name = rec.info.get("brewery"), rec.info.get("name")
            if not brand or not name:
                continue
            if key not in best or rec.last_seen > best[key][0]:
                best[key] = (rec.last_seen, brand, name)
    ordered = sorted(best.items(), key=lambda kv: kv[1][0], reverse=True)
    return [(key, brand, name) for key, (_, brand, name) in ordered[:limit]]


def match_shop_beers(state: State, client: UntappdClient, now: datetime,
                     limit: int = SHOP_SEARCH_CANDIDATES_PER_RUN) -> None:
    """Search Untappd for up to `limit` shop beers per run (SHOP_SEARCH_CANDIDATES_PER_RUN by
    default, or the owner's temporary boost_search_per_run, v1.1 §3), same client/budget/pauses as
    the other Untappd sources; a budget or Cloudflare error stops this step only. An empty or
    unparseable results page is dumped for debugging (TAPS_DEBUG_DIR) and still counts as
    "no_match", so it is retried after SHOP_MATCH_RETRY_DAYS rather than every run."""
    for key, brand, name in _shop_match_candidates(state, now, limit):
        try:
            html = client.get(search_url(clean_query(brand, name)))
        except FetchError:
            break
        try:
            results = parse_search_results(html)   # I-1: an unexpected page must fail this one beer
        except Exception:
            results = []
        if not results:
            dump_debug_html(f"untappd_search_{key}", client)
        found = next((r for r in results if matches(brand, name, r)), None)
        if found is None:
            state.shop_matches[key] = ShopMatchRec(matched_at=iso(now))
        else:
            state.shop_matches[key] = ShopMatchRec(
                untappd_beer_id=found.beer_id, url=found.url, rating=found.rating, style=found.style,
                abv=found.abv, logo=found.logo, name=found.name, brewery=found.brewery,
                matched_at=iso(now), checked_at=iso(now), via="search")


def _shop_match_refresh_candidates(state: State, now: datetime) -> list[tuple[str, str]]:
    """(key, url) of matched shop beers whose cached rating is missing or older than
    SHOP_MATCH_MAX_AGE_DAYS, oldest checked first, capped at SHOP_MATCH_REFRESH_PER_RUN."""
    due = [(key, m) for key, m in state.shop_matches.items()
           if m.untappd_beer_id is not None and m.url
           and (m.checked_at is None or age_days(parse_iso(m.checked_at), now) > SHOP_MATCH_MAX_AGE_DAYS)]
    due.sort(key=lambda kv: kv[1].checked_at or "")
    return [(key, m.url) for key, m in due[:SHOP_MATCH_REFRESH_PER_RUN]]


def refresh_shop_matches(state: State, client: UntappdClient, now: datetime) -> None:
    """Refresh up to SHOP_MATCH_REFRESH_PER_RUN matched shop beers' ratings via the beer's own page
    (v1.1 §3, the existing untappd_beer parser), same client/budget. A page that doesn't parse leaves
    the cached fields as they were, but still stamps checked_at so it isn't retried every run."""
    for key, url in _shop_match_refresh_candidates(state, now):
        try:
            html = client.get(url)
        except FetchError:
            break
        try:
            data = parse_beer_page(html)   # I-1: an unexpected page must fail this one beer, not the run
        except Exception:
            data = None
        match = state.shop_matches[key]
        if data:
            for field in ("rating", "style", "abv"):
                if data[field] is not None:
                    setattr(match, field, data[field])
        match.checked_at = iso(now)


def apply_shop_matches(state: State) -> None:
    """Overlay a matched Untappd beer's name/brewery/rating/style/abv/logo/url onto every shop/menu/
    manual pair that shares its key (v1.1 §3, v1.2 beer identity), after this run's merge. The shop's
    own product link already reached info via Sighting.shop_url like any other field; only Untappd's
    data needs to move in here."""
    for pairs in state.pairs.values():
        for key, rec in pairs.items():
            if rec.info.get("kind") not in ("shop", "menu", "manual"):
                continue
            match = state.shop_matches.get(key)
            if match is None or match.untappd_beer_id is None:
                continue
            rec.info["url"] = match.url
            for field in ("logo", "rating", "style", "abv", "name", "brewery"):
                value = getattr(match, field)
                if value is not None:
                    rec.info[field] = value


# --- alerts ------------------------------------------------------------------

def update_alerts(alerter: Alerter, state: State, outcome: MergeOutcome, client: UntappdClient | None,
                  corrections_errors: Sequence[str]) -> None:
    """One admin alert per series: queued on the first failure, resolved when the source is ok again."""
    for key in outcome.ok:
        alerter.resolve(f"source:{key}")
    blocked = client is not None and client.blocked
    for key, error in outcome.failed:
        if blocked and key.startswith("untappd") and error in ("cloudflare", "blocked"):
            continue   # covered by the single Cloudflare alert
        alerter.alert(f"source:{key}", f"{key}: не удалось получить данные ({error})", dedupe_by_key=True)
    for key, reason in outcome.tripped:
        alerter.alert(f"source:{key}", f"{key}: сработал предохранитель ({TRIP_RU.get(reason, reason)}), "
                                       "результат отброшен")
    for key in outcome.accepted:
        alerter.alert(f"source:{key}", f"{key}: 3 прогона подряд одно и то же новое меню — принял его молча")
    if blocked:
        alerter.alert("untappd:cloudflare", CLOUDFLARE_ALERT)
    elif client is not None and client.responded:
        alerter.resolve("untappd:cloudflare")
    current = {f"corrections:{e}" for e in corrections_errors}
    for key in [k for k in state.alerts if k.startswith("corrections:") and k not in current]:
        alerter.resolve(key)
    for error in corrections_errors:
        alerter.alert(f"corrections:{error}", error)


def digest_alerts(alerter: Alerter, outcome: SendOutcome) -> None:
    if outcome.status == "sent":
        alerter.resolve("digest")
        alerter.resolve("digest:migrate")
    elif outcome.status == "rejected":
        alerter.alert("digest", f"Telegram не принял сводку ({outcome.description}): отметка отменена, "
                                "сводка уйдёт в следующий прогон")
        if outcome.migrate_to_chat_id is not None:
            alerter.alert("digest:migrate", f"чат переехал, новый id: {outcome.migrate_to_chat_id} — "
                                            "поменяйте TELEGRAM_CHAT_ID")
    else:
        alerter.alert("digest", f"сводка, возможно, не дошла ({outcome.description})")


# --- v1.1: weekly admin report of untracked venues seen in check-ins ---------

def _week_start(now: datetime) -> str:
    local = to_yerevan(now).date()
    return (local - timedelta(days=local.weekday())).isoformat()


def _discovery_period(config: Config, now: datetime) -> str:
    """The Yerevan date identifying the current report period: the day itself while the owner's
    temporary daily-cadence window (discovery_daily_until) is active, otherwise the Monday of the
    week -- compared against state.discovery.last_report_date to decide "already reported"."""
    today = yerevan_date(now)
    if config.settings.discovery_daily_active(today):
        return today
    return _week_start(now)


def _discovery_due(state: State, config: Config, now: datetime) -> bool:
    local = to_yerevan(now)
    if config.settings.discovery_daily_active(yerevan_date(now)):
        due_since = datetime(local.year, local.month, local.day, DISCOVERY_HOUR, tzinfo=YEREVAN)
    else:
        monday = local.date() - timedelta(days=local.weekday())
        due_since = datetime(monday.year, monday.month, monday.day, DISCOVERY_HOUR, tzinfo=YEREVAN)
    if local < due_since:
        return False
    return state.discovery.last_report_date != _discovery_period(config, now)


def _checkins_ru(n: int) -> str:
    """Russian plural of "чекин": 1 чекин, 2-4 чекина, else чекинов (11-14 also чекинов)."""
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        form = "чекин"
    elif 2 <= n10 <= 4 and not (12 <= n100 <= 14):
        form = "чекина"
    else:
        form = "чекинов"
    return f"{n} {form}"


def build_discovery_report(state: State, config: Config, now: datetime) -> tuple[str, list[int]] | None:
    """Untracked venues (no place in places.yaml, enabled or disabled) with >= 3 check-ins in the last
    30 days, not reported before. Capped at DISCOVERY_MAX_VENUES lines and MAX_TEXT chars (Telegram's
    limit): venues left out of a capped message are NOT marked reported, so they are reconsidered
    (and may rank higher) next week.

    I-3: the project's scope is Yerevan, not Armenia -- a venue in another Armenian city (Gyumri, ...)
    is excluded here just like a foreign one, even though its country is also "Armenia" (M-1)."""
    reported = set(state.discovery.reported)
    candidates = []
    for vid_str, rec in state.venues.items():
        vid = int(vid_str)
        if vid in config.known_venue_ids or vid in reported or not is_yerevan_city(rec.city):
            continue
        recent = [c for c in rec.checkins if age_days(parse_iso(c["at"]), now) <= VENUE_KEEP_DAYS]
        if len(recent) >= DISCOVERY_MIN_CHECKINS:
            candidates.append((vid, rec, recent))
    if not candidates:
        return None
    candidates.sort(key=lambda c: (-len(c[2]), c[1].name))
    header = "🍺 Новые места по чекинам (за 60 дней):"
    footer = "\nДобавить в список — напиши Claude."
    lines: list[str] = []
    included: list[int] = []
    for vid, rec, recent in candidates[:DISCOVERY_MAX_VENUES]:
        last = to_yerevan(parse_iso(max(c["at"] for c in recent))).strftime("%d.%m")
        line = f"• {html.escape(rec.name)} — {_checkins_ru(len(recent))}, последний {last} — {html.escape(rec.url)}"
        omitted = len(candidates) - len(lines) - 1
        tail = f"\n…и ещё {omitted}" if omitted else ""
        if len(header) + 1 + len("\n".join([*lines, line])) + len(tail) + len(footer) > MAX_TEXT:
            break
        lines.append(line)
        included.append(vid)
    omitted = len(candidates) - len(lines)
    tail = f"\n…и ещё {omitted}" if omitted else ""
    text = header + "\n" + "\n".join(lines) + tail + footer
    return text, included


def discovery_alerts(alerter: Alerter, outcome: SendOutcome) -> None:
    if outcome.status == "sent":
        alerter.resolve("discovery")
    else:
        alerter.alert("discovery", f"еженедельный отчёт о новых местах, возможно, не дошёл ({outcome.description})")


# --- run ---------------------------------------------------------------------

def _save_push(repo: Path, state: State, deps: Deps, message: str) -> bool:
    save_state(repo / STATE_FILE, state)
    try:
        return deps.push(repo, [STATE_FILE], message)
    except GitError:
        return False


def _fatal_untracked(repo: Path, text: str, send: Callable[[str], SendOutcome]) -> None:
    """Dedup a fatal alert when no state.json can be trusted (corrupt state.json, failed pull): the hash
    lives in an untracked marker file instead. Fine on a Mac; a fresh CI checkout starts without it."""
    path = repo / FATAL_FILE
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
    prev = path.read_text(encoding="utf-8").strip() if path.exists() else None
    if prev != digest:
        send(html.escape(text))   # sent with parse_mode HTML
    path.write_text(digest + "\n", encoding="utf-8")


def run(repo: Path, now: datetime, env: Mapping[str, str], deps: Deps, dry_run: bool = False,
        no_digest: bool = False) -> int:
    """0 done; 1 git pull/push failed (nothing sent); 2 cannot run (settings, config, state, corrections)."""
    needed = () if dry_run else ADMIN_ENV_KEYS if no_digest else ENV_KEYS
    missing = [k for k in needed if not env.get(k)]
    if missing:
        print(f"не заданы переменные окружения: {', '.join(missing)}", file=sys.stderr)
        return 2

    def to_admin(text: str) -> SendOutcome:
        return deps.send(env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_ADMIN_CHAT_ID"], text)

    def fatal(text: str, code: int, state: State | None = None) -> int:
        """Dedup once per series: with a loadable state use its own alerts (persisted, so it survives a
        fresh CI checkout); without one (corrupt state.json, or a failed pull) an untracked marker file."""
        print(text, file=sys.stderr)
        if not dry_run:
            if state is not None:
                alerter = Alerter(state)
                alerter.alert("fatal", text)
                alerter.flush(to_admin)
                _save_push(repo, state, deps, f"state {iso(now)}: авария")
            else:
                _fatal_untracked(repo, text, to_admin)
        return code

    if not dry_run:
        try:
            deps.pull(repo)
        except CheckoutError as e:   # not a clean main: nothing is fetched, saved or pushed
            return fatal(str(e), 2)
        except GitError as e:
            return fatal(f"git pull не прошёл: {e}", 1)
    try:   # first: a broken places.yaml or corrections.yaml then dedups its alert through state.alerts
        state = load_state(repo / STATE_FILE, now)
    except ValueError as e:
        return fatal(str(e), 2)
    try:
        config = load_config(repo / "places.yaml")
    except ConfigError as e:
        return fatal(str(e), 2, state)
    load = load_corrections(repo / "corrections.yaml", state.corrections_snapshot, set(config.places))
    if load.corrections is None:
        return fatal(load.errors[0], 2, state)
    corrections = load.corrections
    if load.raw is not None:
        state.corrections_snapshot = load.raw

    alerter = Alerter(state)
    alerter.resolve("fatal")   # reached the end of loading: the fatal series is over
    (repo / FATAL_FILE).unlink(missing_ok=True)
    apply_aliases(state, corrections.aliases)
    merge_places(state, {old: p.id for p in config.places.values() for old in p.merged_from})
    apply_same_as(state, corrections, now)   # v1.2 beer identity: manual override, wins over local/search
    match_shop_beers_locally(state, now)     # v1.2 beer identity: zero-page match, before search
    untappd_results, client = collect_untappd(state, config, corrections, now, deps, alerter)
    results = [*untappd_results, *collect_shops(state, config, corrections, now, deps.http),
               manual_result(corrections, config, now)]
    record_venues(state, results, now, config.known_venue_ids)
    outcome = merge_results(state, results, config, corrections, now)
    apply_shop_matches(state)   # v1.1 §3: overlay this run's (or an earlier) Untappd match onto shop rows
    prune(state, now)
    update_alerts(alerter, state, outcome, client, load.errors)
    site_data = build_site_data(state, config, now)
    write_site_data(repo / SITE_DATA, site_data)

    drop_stale_events(state, now)
    digest = None
    if not no_digest and (dry_run or is_due(state, config.settings, now)):
        digest = build_digest(state, config, config.settings, now)
    if dry_run:   # the digest that would go out now, ignoring the time gate; nothing saved or sent
        print(digest.html if digest else "нет сводки")
        text = alerter.pending_text()
        if text:
            print(text)
        print(json.dumps(site_data, ensure_ascii=False))
        return 0

    discovery_msg = None
    if _discovery_due(state, config, now):
        built = build_discovery_report(state, config, now)
        if built is not None:
            discovery_msg, venue_ids = built
            state.discovery.reported.extend(venue_ids)
        state.discovery.last_report_date = _discovery_period(config, now)

    # Spec §7: the sent mark is pushed before sending, so a failed push sends nothing.
    mark = mark_sent(state, digest, now) if digest else None
    if not _save_push(repo, state, deps, f"state {iso(now)}"):
        return 1
    pushed = state.to_dict()
    # The discovery report's own bookkeeping (reported ids, last_report_date) was pushed above already,
    # with no rollback on a failed send (unlike the digest mark below): the report is a small addition
    # to the admin, not a public announcement, so re-sending it next run if it did not arrive is fine,
    # and it must not depend on -- or be skipped by -- the digest branch below (its own rollback and
    # possible early "push failed" return must not silently drop the report).
    if discovery_msg:
        discovery_alerts(alerter, to_admin(discovery_msg))
    if digest:
        chat = env["TELEGRAM_ADMIN_CHAT_ID"] if digest.to_admin else env["TELEGRAM_CHAT_ID"]
        sent = deps.send(env["TELEGRAM_BOT_TOKEN"], chat, digest.html, button=(BUTTON_TEXT, env["SITE_URL"]))
        if sent.status == "rejected":   # surely not delivered: undo the mark (spec §7 step 4)
            rollback(state, mark)
            if not _save_push(repo, state, deps, f"state {iso(now)}: сводка не принята, отметка отменена"):
                return 1
            pushed = state.to_dict()
        digest_alerts(alerter, sent)
        # the marks are settled now: rebuild so the rows just announced carry 🆕/⭐
        write_site_data(repo / SITE_DATA, build_site_data(state, config, now))
    alert_outcome = alerter.flush(to_admin)
    # alert hashes changed after the last push: push them too
    if state.to_dict() != pushed and not _save_push(repo, state, deps, f"state {iso(now)}: предупреждения"):
        return 1
    # an admin alert that surely or possibly did not arrive: fail the job so GitHub emails
    return 1 if alert_outcome is not None and alert_outcome.status != "sent" else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m taps")
    commands = parser.add_subparsers(dest="command", required=True)
    cmd = commands.add_parser("run", help="один прогон: сбор, сайт, сводка")
    cmd.add_argument("--dry-run", action="store_true", help="ничего не отправлять и не коммитить")
    cmd.add_argument("--no-digest", action="store_true", help="не слать сводку")
    cmd.add_argument("--repo", type=Path, default=Path("."), help="папка репозитория")
    args = parser.parse_args(argv)
    return run(args.repo, utcnow(), os.environ, Deps(), dry_run=args.dry_run, no_digest=args.no_digest)
