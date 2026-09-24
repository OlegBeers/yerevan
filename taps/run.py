"""One run of the bot (spec §3): pull, collect, merge, site data, digest protocol (§7), push, send."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping, Sequence

from taps.config import Config, ConfigError, load_config
from taps.corrections import Corrections, load_corrections
from taps.digest import build_digest, drop_stale_events, is_due, mark_sent, rollback
from taps.fetch import Http, PageFetcher, UntappdClient, playwright_fetcher, untappd_due
from taps.gitsync import GitError, commit_and_push, pull_ff
from taps.model import SourceResult
from taps.rules import MergeOutcome, merge_results
from taps.site_data import build_site_data, write_site_data
from taps.sources.beercity import fetch_beercity
from taps.sources.buyam import fetch_buyam
from taps.sources.manual import manual_result
from taps.sources.parma import fetch_parma
from taps.sources.untappd_brewery import fetch_brewery_checkins, fetch_brewery_list
from taps.sources.untappd_checkins import fetch_venue_checkins
from taps.sources.untappd_menu import fetch_menu
from taps.sources.yerevan_city import fetch_yerevan_city
from taps.state import State, apply_aliases, load_state, prune, save_state
from taps.telegram import Alerter, SendOutcome, send_message
from taps.timeutil import iso, parse_iso, utcnow, yerevan_date

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
    """Menus -> brewery check-ins -> venue check-ins -> 1-2 brewery lists; at most once per 20 h."""
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
        send(text)
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

    # Spec §7: the sent mark is pushed before sending, so a failed push sends nothing.
    mark = mark_sent(state, digest, now) if digest else None
    if not _save_push(repo, state, deps, f"state {iso(now)}"):
        return 1
    pushed = state.to_dict()
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
