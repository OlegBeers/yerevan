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
from taps.fetch import FetchError, Http, PageFetcher, UntappdClient, playwright_fetcher, untappd_due
from taps.gitsync import CheckoutError, GitError, commit_and_push, pull_ff
from taps.model import SourceResult
from taps.rules import MergeOutcome, merge_results
from taps.site_data import build_site_data, write_site_data
from taps.sources.beercity import fetch_beercity
from taps.sources.buyam import fetch_buyam
from taps.sources.manual import manual_result
from taps.sources.parma import fetch_parma
from taps.sources.untappd_brewery import fetch_brewery_checkins, fetch_brewery_list
from taps.sources.untappd_checkins import (
    fetch_venue_checkins, is_armenia_location, is_yerevan_city, parse_venue_location, parse_venue_meta,
)
from taps.sources.untappd_menu import fetch_menu
from taps.sources.yerevan_city import fetch_yerevan_city
from taps.state import VENUE_KEEP_DAYS, State, apply_aliases, load_state, prune, record_venues, save_state
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
DISCOVERY_MAX_VENUES = 20    # cap on lines in the weekly report, besides the MAX_TEXT length cap below
LOCATION_CANDIDATES_PER_RUN = 3   # v1.1 city check (§4): at most this many untracked venue pages per run
LOCATION_MIN_CHECKINS = 2         # below this, not worth spending a page on
LOCATION_RECHECK_DAYS = 90        # a failed/unknown check is retried after this many days


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
        client = UntappdClient(state.untappd, config.settings.untappd_daily_pages, now, fetch_page, sleep=deps.sleep)
        # once blocked, the sources themselves return error "blocked" without spending pages
        results = [_guard(key, place_id, lambda: job(client)) for key, place_id, job in jobs]
        discover_venue_locations(state, config, client, now)   # v1.1 city check, same client/budget
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


def _discovery_due(state: State, now: datetime) -> bool:
    local = to_yerevan(now)
    monday = local.date() - timedelta(days=local.weekday())
    monday_9am = datetime(monday.year, monday.month, monday.day, DISCOVERY_HOUR, tzinfo=YEREVAN)
    if local < monday_9am:
        return False
    return state.discovery.last_report_date != _week_start(now)


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
    untappd_results, client = collect_untappd(state, config, corrections, now, deps, alerter)
    results = [*untappd_results, *collect_shops(state, config, corrections, now, deps.http),
               manual_result(corrections, config, now)]
    record_venues(state, results, now, config.known_venue_ids)
    outcome = merge_results(state, results, config, corrections, now)
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
    if _discovery_due(state, now):
        built = build_discovery_report(state, config, now)
        if built is not None:
            discovery_msg, venue_ids = built
            state.discovery.reported.extend(venue_ids)
        state.discovery.last_report_date = _week_start(now)

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
