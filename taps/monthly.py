"""Monthly «итоги месяца» Telegram message: when it is due and what it says.

Built from the check-in log (data/checkins.json, Armenian venues only, hide_users already purged by
record_checkins) and from the pairs in state.json for the "new beers" number."""
import html
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from taps.checkin_log import CheckinLogEntry
from taps.config import Config
from taps.digest import DAY_END, DAY_START, _shorten_brewery, _shorten_name
from taps.state import MARKERS, State
from taps.stats_data import _beer_lookup
from taps.timeutil import YEREVAN, parse_iso, to_yerevan

MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня",
              "июля", "августа", "сентября", "октября", "ноября", "декабря"]
TOP_N = 3
BEER_MIN_CHECKINS = 3            # rated check-ins in the month for a beer to enter "best beer" ...
BEER_MIN_CHECKINS_FALLBACK = 2   # ... lowered to this when fewer than TOP_N beers qualify
BUTTON_TEXT = "Статистика"
NBSP = " "

esc = html.escape


def previous_month(now: datetime) -> str:
    """"YYYY-MM" of the calendar month before the Yerevan date of `now`."""
    last_day = to_yerevan(now).date().replace(day=1) - timedelta(days=1)
    return f"{last_day.year}-{last_day.month:02d}"


def month_pending(state: State, now: datetime) -> str | None:
    """The month to summarize, unless it (or a later one) already was. Not time-of-day gated."""
    month = previous_month(now)
    return month if (state.monthly_last or "") < month else None


def monthly_due(state: State, now: datetime) -> str | None:
    """month_pending, but never at night (same day window as the daily digest)."""
    if not DAY_START <= to_yerevan(now).time() < DAY_END:
        return None
    return month_pending(state, now)


def _bounds(month: str) -> tuple[datetime, datetime]:
    year, mon = map(int, month.split("-"))
    start = datetime(year, mon, 1, tzinfo=YEREVAN)
    nxt = date(year + (mon == 12), mon % 12 + 1, 1)
    return start, datetime(nxt.year, nxt.month, 1, tzinfo=YEREVAN)


def _place_key_name(venue_id: int, config: Config, state: State) -> tuple[tuple, str]:
    place = config.place_by_venue(venue_id)
    if place is not None:
        return ("p", place.id), place.short
    rec = state.venues.get(str(venue_id))
    return ("v", venue_id), rec.name if rec else str(venue_id)


def _best_beers(entries: list[CheckinLogEntry], lookup: dict) -> tuple[list[tuple[int, str, float]], int]:
    """Top TOP_N (beer_id, brewery, average rating) among named beers with enough rated check-ins,
    and the minimum used."""
    ratings: dict[int, list[float]] = defaultdict(list)
    brewery: dict[int, str] = {}
    for e in entries:
        if e.rating is not None and lookup.get(e.beer_id, {}).get("name"):
            ratings[e.beer_id].append(e.rating)
            brewery.setdefault(e.beer_id, e.brewery)
    def rank(minimum: int) -> list[tuple[int, list[float]]]:
        return sorted(((b, r) for b, r in ratings.items() if len(r) >= minimum),
                      key=lambda br: (-sum(br[1]) / len(br[1]), -len(br[1]), lookup[br[0]]["name"]))
    minimum = BEER_MIN_CHECKINS
    ranked = rank(minimum)
    if len(ranked) < TOP_N:
        minimum = BEER_MIN_CHECKINS_FALLBACK
        ranked = rank(minimum)
    return [(b, brewery[b], sum(r) / len(r)) for b, r in ranked[:TOP_N]], minimum


def _new_beers(state: State, config: Config, start: datetime, end: datetime) -> tuple[int, int]:
    """Distinct (bar, shop) beers that were real news in our places during the month. A silent first
    sighting (baseline, or suppressed) is not news; the digest's Бары/Магазины split is the place kind."""
    keys: dict[bool, set[str]] = {False: set(), True: set()}
    for pid, recs in state.pairs.items():
        if pid not in config.places:
            continue
        is_shop = config.places[pid].kind == "shop"
        keys[is_shop].update(key for key, rec in recs.items() if rec.event_at is not None
                             and rec.notified_at not in MARKERS and start <= parse_iso(rec.event_at) < end)
    return len(keys[False]), len(keys[True])


def build_monthly(state: State, config: Config, log: list[CheckinLogEntry], month: str) -> str | None:
    """The message HTML for "YYYY-MM", or None when the log has no check-in in that month."""
    start, end = _bounds(month)
    entries = [e for e in log if start <= parse_iso(e.time) < end]
    if not entries:
        return None
    lookup = _beer_lookup(state)
    by_place: Counter = Counter()
    names: dict[tuple, str] = {}
    for e in entries:
        key, name = _place_key_name(e.venue_id, config, state)
        by_place[key] += 1
        names[key] = name
    top_places = sorted(by_place, key=lambda k: (-by_place[k], names[k]))[:TOP_N]
    best, minimum = _best_beers(entries, lookup)
    new_bars, new_shops = _new_beers(state, config, start, end)

    blocks = [f"📊 <b>Итоги {MONTHS_GEN[start.month - 1]}</b>",
              f"Чекинов: {len(entries)} · людей: {len({e.username for e in entries})} · мест: {len(by_place)}",
              "<b>Больше всего чекинов</b>\n" + "\n".join(
                  f"{i}. {esc(names[k])} — {by_place[k]}" for i, k in enumerate(top_places, 1))]
    if best:
        lines = []
        for i, (beer_id, brewery, avg) in enumerate(best, 1):
            name = esc(_shorten_name(lookup[beer_id]["name"]))
            tail = f"{esc(_shorten_brewery(brewery))} · {avg:.2f}".replace(" ", NBSP)
            lines.append(f"{i}. {name} — {tail}")
        blocks.append(f"<b>Лучшее пиво</b> · от {minimum} оценок\n" + "\n".join(lines))
    blocks.append(f"Новых сортов: в барах {new_bars} · в магазинах {new_shops}")
    return "\n\n".join(blocks)
