"""Daily Telegram digest: when it is due, what it says, and the sent mark with rollback."""
import html
import re
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta

from taps.config import Config, Settings
from taps.model import SOURCE_KINDS, style_family
from taps.state import DigestRec, PairRec, State
from taps.timeutil import YEREVAN, age_days, iso, parse_iso, to_yerevan, yerevan_date

STALE_EVENT_DAYS = 3
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
DAY_START, DAY_END = time(9, 0), time(23, 0)
MIN_GAP_HOURS = 20
BLOCK_RULE = "──────────"
MANUAL_LIST_MAX = 5   # more manual entries from one place: one "list updated" line instead of every beer
MAX_PER_PLACE = 5     # more beer lines in one group: the rest collapse into an inline "…и ещё N" tail
BREWERY_NEW_HEADER = "<b>Новые сорта пивоварен</b> · где наливают — пока неизвестно"

_PAREN_WITH_COMMA = re.compile(r"\s*\([^()]*,[^()]*\)")
_PAREN_ANY = re.compile(r"\s*\([^()]*\)")
_TRAILING_BREWERY_WORD = re.compile(r"\s+(?:Brewery|Пивоварня)$", re.IGNORECASE)

esc = html.escape


def pending_pairs(state: State) -> list[tuple[str, str]]:
    return [(p, k) for p, recs in state.pairs.items() for k, r in recs.items()
            if r.event_at is not None and r.notified_at is None]


def pending_brewery(state: State) -> list[str]:
    return [k for k, r in state.brewery_new.items() if r.notified_at is None]


def _is_stale(rec: PairRec, now: datetime) -> bool:
    manual_date = rec.info.get("manual_date")
    if manual_date:   # the calendar-day rule that records a manual entry as an event (rules.MANUAL_EVENT_DAYS)
        return (to_yerevan(now).date() - date.fromisoformat(manual_date)).days > STALE_EVENT_DAYS
    return age_days(parse_iso(rec.event_at), now) > STALE_EVENT_DAYS


def drop_stale_events(state: State, now: datetime) -> int:
    """Pending events older than STALE_EVENT_DAYS are marked notified without sending."""
    count = 0
    for p, k in pending_pairs(state):
        rec = state.pairs[p][k]
        if _is_stale(rec, now):
            rec.notified_at = iso(now)
            count += 1
    for k in pending_brewery(state):
        rec = state.brewery_new[k]
        if age_days(parse_iso(rec.found_at), now) > STALE_EVENT_DAYS:
            rec.notified_at = iso(now)
            count += 1
    return count


def is_due(state: State, settings: Settings, now: datetime) -> bool:
    local = to_yerevan(now)
    if not DAY_START <= local.time() < DAY_END:
        return False
    d = state.digest
    if d.last_sent_date == yerevan_date(now):
        return False
    if d.last_sent_at is not None and now - parse_iso(d.last_sent_at) < timedelta(hours=MIN_GAP_HOURS):
        return False
    # a hand-entered board counts from when it reached the bot: its own date is midnight, which would
    # look like a missed evening and trigger a daytime send
    times = [parse_iso(state.pairs[p][k].event_at) for p, k in pending_pairs(state)]
    times += [parse_iso(state.brewery_new[k].found_at) for k in pending_brewery(state)]
    if not times:
        return False
    if local.time() >= settings.digest_time:
        return True
    yesterday_cutoff = datetime.combine(local.date() - timedelta(days=1), settings.digest_time, tzinfo=YEREVAN)
    return min(times) < yesterday_cutoff


@dataclass
class Digest:
    html: str
    lines_total: int
    lines_shown: int
    pairs: list[tuple[str, str]]
    brewery_keys: list[str]
    manual_ids: list[str]
    to_admin: bool


def _shorten_name(name: str) -> str:
    """A parenthesised aside is dropped only when it lists several things (a comma inside) --
    a single-word aside like "(Seven Sins)" is part of the beer's name and stays."""
    return _PAREN_WITH_COMMA.sub("", name).strip()


def _shorten_brewery(brewery: str) -> str:
    """Drop any parenthesised aside, then a lone trailing "Brewery"/"Пивоварня" word once at least
    one other word remains ("Plan B Brewery" -> "Plan B"; "Moscow Brewing Company" unchanged)."""
    text = _PAREN_ANY.sub("", brewery).strip()
    return _TRAILING_BREWERY_WORD.sub("", text)


def _beer_line(info: dict, place_short: str | None, also: list[str]) -> str:
    """`name — brewery · style`; a shop pair matched to Untappd shows Untappd's own name/brewery.
    A brewery that is the place itself (Dors at Dors) is dropped, and the line becomes `name · style`.
    The tail after the dash (brewery + style) never wraps -- its spaces are bound with U+00A0."""
    name = info.get("u_name") or info.get("name") or info.get("title")
    name = esc(_shorten_name(name))
    brewery = info.get("u_brewery") or info.get("brewery")
    if brewery:
        brewery = _shorten_brewery(brewery)
        if place_short and brewery.casefold() == place_short.casefold():
            brewery = None
    style = info.get("style")
    family = esc(style_family(style)) if style else None
    if brewery:
        tail = " · ".join(filter(None, (esc(brewery), family))).replace(" ", " ")
        line = f"• {name} — {tail}"
    else:
        line = f"• {name}" + (f" · {family}" if family else "")
    if also:
        line += " · ещё в " + ", ".join(esc(a) for a in also)
    return line


def _positions_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "позиция"
    if 2 <= n % 10 <= 4 and not 11 <= n % 100 <= 14:
        return "позиции"
    return "позиций"


def _section(rec: PairRec, place_kind: str) -> str:
    kind = rec.info.get("kind") or SOURCE_KINDS.get(rec.info.get("source"))
    if kind in ("checkin", "manual"):
        return kind
    return "shop" if place_kind == "shop" else "menu"


def _cap_lines(lines: list[str]) -> list[str]:
    if len(lines) <= MAX_PER_PLACE:
        return lines
    hidden = len(lines) - MAX_PER_PLACE
    return lines[:MAX_PER_PLACE] + [f"…и ещё {hidden} — на сайте"]


def build_digest(state: State, config: Config, settings: Settings, now: datetime) -> Digest | None:
    pairs, brewery_keys = pending_pairs(state), pending_brewery(state)
    order = {pid: i for i, pid in enumerate(config.places)}
    # section -> beer_key -> [(place, rec)] in config order; unknown places are marked sent but not shown
    groups: dict[str, dict[str, list]] = {"menu": {}, "checkin": {}, "manual": {}, "shop": {}}
    for pid, key in sorted((pk for pk in pairs if pk[0] in order), key=lambda pk: order[pk[0]]):
        place, rec = config.places[pid], state.pairs[pid][key]
        groups[_section(rec, place.kind)].setdefault(key, []).append((place, rec))

    # a whole menu entered by hand (a photo of the tap board) is news about the place, not a batch of new beers
    per_place: dict[str, int] = {}
    for found in groups["manual"].values():
        for place, _ in found:
            per_place[place.id] = per_place.get(place.id, 0) + 1
    listed = {pid for pid in config.places if per_place.get(pid, 0) > MANUAL_LIST_MAX}
    manual_kept = {k: kept for k, found in groups["manual"].items()
                  if (kept := [(p, r) for p, r in found if p.id not in listed])}

    def sort_key(item):   # order within a group: rating descending (None last), then name
        _, found = item
        info = found[0][1].info
        rating = info.get("rating")
        name = info.get("u_name") or info.get("name") or info.get("title") or ""
        return ((0, -rating) if rating is not None else (1, 0), name)

    # (block, header, lines); header is None for a self-contained single line (the manual list line)
    groups_list: list[tuple[str, str | None, list[str]]] = []

    for section in ("menu", "shop", "checkin"):
        by_place: dict[str, list] = {}
        for key, found in groups[section].items():
            by_place.setdefault(found[0][0].id, []).append((key, found))
        for pid in sorted(by_place, key=lambda p: order[p]):
            place = config.places[pid]
            items = sorted(by_place[pid], key=sort_key)
            lines = [_beer_line(found[0][1].info, place.short, [p.short for p, _ in found[1:]])
                    for _, found in items]
            # v1.1: a shop's own check-ins (e.g. Houl) belong in the shops block, not bars
            block = "shops" if section == "shop" or (section == "checkin" and place.kind == "shop") else "bars"
            header = f"<b>{esc(place.short)}</b>" + (" · по чекинам" if section == "checkin" else "")
            groups_list.append((block, header, _cap_lines(lines)))

    manual_by_place: dict[str, list] = {}
    for key, found in manual_kept.items():
        manual_by_place.setdefault(found[0][0].id, []).append((key, found))
    for pid in sorted(set(manual_by_place) | listed, key=lambda p: order[p]):
        place = config.places[pid]
        block = "shops" if place.kind == "shop" else "bars"
        if pid in listed:
            n = per_place[pid]
            line = f"<b>{esc(place.short)}</b> · обновился список, {n} {_positions_word(n)} — на сайте"
            groups_list.append((block, None, [line]))
        else:
            items = sorted(manual_by_place[pid], key=sort_key)
            by = sorted({found[0][1].info.get("manual_by") or "?" for _, found in items})
            header = f"<b>{esc(place.short)}</b> · со слов: {esc(', '.join(by))}"
            lines = [_beer_line(found[0][1].info, place.short, [p.short for p, _ in found[1:]])
                    for _, found in items]
            groups_list.append((block, header, _cap_lines(lines)))

    if brewery_keys:
        ordered = sorted(brewery_keys, key=lambda k: (not state.brewery_new[k].star, state.brewery_new[k].found_at))
        lines = [_beer_line(state.brewery_new[k].info, None, []) for k in ordered]
        groups_list.append(("bars", BREWERY_NEW_HEADER, _cap_lines(lines)))

    if not groups_list:
        return None
    # v1.1: bars and shops can interleave within a section (e.g. a shop's own check-ins, §I-2) --
    # stable-sort so all bars come first, then all shops, before capping and building headers.
    groups_list.sort(key=lambda g: g[0] != "bars")

    lines_total = sum(len(lines) for _, _, lines in groups_list)
    budget = settings.digest_max_lines
    shown_groups: list[tuple[str, str | None, list[str]]] = []
    for block, header, lines in groups_list:
        if budget <= 0:
            break
        take = lines[:budget]
        if not take:
            continue
        shown_groups.append((block, header, take))
        budget -= len(take)
    lines_shown = sum(len(lines) for _, _, lines in shown_groups)
    hidden = lines_total - lines_shown

    block_order: list[str] = []
    block_chunks: dict[str, list[str]] = {}
    for block, header, lines in shown_groups:
        if block not in block_chunks:
            block_chunks[block] = ["🍻 <b>Бары</b>" if block == "bars" else "🛒 <b>Магазины</b>"]
            block_order.append(block)
        block_chunks[block].append(f"{header}\n" + "\n".join(lines) if header is not None else lines[0])
    body = f"\n\n{BLOCK_RULE}\n\n".join("\n\n".join(block_chunks[b]) for b in block_order)   # a rule between bars and shops

    local = to_yerevan(now)
    header = f"🍺 <b>Новое в Ереване</b> · {WEEKDAYS[local.weekday()]}, {local.day} {MONTHS[local.month - 1]}"
    tail = f"…и ещё {hidden} — на сайте" if hidden else None
    text = "\n\n".join(filter(None, [header, body, tail]))

    manual_ids = []
    for p, k in pairs:
        info = state.pairs[p][k].info
        for mid in info.get("manual_ids") or [info.get("manual_id")]:   # every entry merged into the pair
            if mid and mid not in manual_ids:
                manual_ids.append(mid)
    return Digest(html=text, lines_total=lines_total, lines_shown=lines_shown, pairs=pairs,
                  brewery_keys=brewery_keys, manual_ids=manual_ids,
                  to_admin=state.digest.sent_count < settings.preview_digests)


@dataclass
class SentMark:
    digest: DigestRec
    pairs: dict[tuple[str, str], str | None]
    brewery: dict[str, str | None]
    manual_before: list[str]


def mark_sent(state: State, digest: Digest, now: datetime) -> SentMark:
    mark = SentMark(
        digest=replace(state.digest),
        pairs={(p, k): state.pairs[p][k].notified_at for p, k in digest.pairs},
        brewery={k: state.brewery_new[k].notified_at for k in digest.brewery_keys},
        manual_before=list(state.announced_manual),
    )
    stamp = iso(now)
    state.digest.last_sent_date = yerevan_date(now)
    state.digest.last_sent_at = stamp
    state.digest.sent_count += 1
    for p, k in digest.pairs:
        state.pairs[p][k].notified_at = stamp
    for k in digest.brewery_keys:
        state.brewery_new[k].notified_at = stamp
    state.announced_manual.extend(m for m in digest.manual_ids if m not in mark.manual_before)
    return mark


def rollback(state: State, mark: SentMark) -> None:
    state.digest = replace(mark.digest)
    for (p, k), value in mark.pairs.items():
        state.pairs[p][k].notified_at = value
    for k, value in mark.brewery.items():
        state.brewery_new[k].notified_at = value
    state.announced_manual = list(mark.manual_before)
