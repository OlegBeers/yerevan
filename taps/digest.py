"""Daily Telegram digest: when it is due, what it says, and the sent mark with rollback."""
import html
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta

from taps.config import Config, Settings
from taps.model import SOURCE_KINDS
from taps.state import DigestRec, PairRec, State
from taps.timeutil import YEREVAN, age_days, iso, parse_iso, to_yerevan, yerevan_date

STALE_EVENT_DAYS = 3
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
DAY_START, DAY_END = time(9, 0), time(23, 0)
MIN_GAP_HOURS = 20
CONTAINER_RU = {"can": "банка", "bottle": "бутылка", "keg": "кег", "draft": "розлив"}
SERVING_RU = {"Draft": "розлив", "Bottle": "бутылка", "Can": "банка", "Taster": "дегустационный",
             "Cask": "из бочки", None: "подача неизвестна"}
BREWERY_NEW_NOTE = "новый сорт в Untappd, где наливают — пока неизвестно"
MANUAL_LIST_MAX = 5   # more manual entries from one place: one "list updated" line instead of every beer

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


def _style_abv(info: dict) -> str | None:
    abv = info.get("abv")
    text = " ".join(filter(None, (info.get("style"), f"{abv:g}%" if abv is not None else None)))
    return esc(text) or None


def _line(info: dict, rating: str | None, details: list[str | None], also: list[str], place: str | None = None) -> str:
    """rating, name, brewery, then the details; a shop pair matched to Untappd shows Untappd's name/brewery.
    A brewery that is the place itself (Dors at Dors) is dropped: the line already ends with the place."""
    name = info.get("u_name") or info.get("name") or info.get("title")
    brewery = info.get("u_brewery") or info.get("brewery")
    if brewery and place and brewery.casefold() == place.casefold():
        brewery = None
    text = " · ".join(filter(None, (esc(name) if name else None, esc(brewery) if brewery else None, *details)))
    if rating:
        text = f"{rating} {text}"
    if also:
        text += " + ещё в " + ", ".join(esc(a) for a in also)
    return text


def _rating(info: dict, settings: Settings) -> str | None:
    r = info.get("rating")
    if r is None:
        return None
    return f"<b>{r:.2f}</b>" if r >= settings.hot_rating else f"{r:.2f}"


def _price(info: dict) -> str | None:
    return f"{info['price_amd']} ֏" if info.get("price_amd") is not None else None


def _pack(info: dict) -> str | None:
    vol, cont = info.get("volume_ml"), info.get("container")
    text = " ".join(filter(None, (f"{vol / 1000:g} л" if vol is not None else None,
                                  CONTAINER_RU.get(cont, cont) if cont else None)))
    return esc(text) or None


def _positions_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "позиция"
    if 2 <= n % 10 <= 4 and not 11 <= n % 100 <= 14:
        return "позиции"
    return "позиций"


def _days_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "день"
    if 2 <= n % 10 <= 4 and not 11 <= n % 100 <= 14:
        return "дня"
    return "дней"


def _seen(info: dict, rec: PairRec, now: datetime) -> str:
    seen = info.get("checkin_at") or rec.last_seen
    n = (to_yerevan(now).date() - to_yerevan(parse_iso(seen)).date()).days
    return "видели сегодня" if n <= 0 else f"видели {n} {_days_word(n)} назад"


def _section(rec: PairRec, place_kind: str) -> str:
    kind = rec.info.get("kind") or SOURCE_KINDS.get(rec.info.get("source"))
    if kind in ("checkin", "manual"):
        return kind
    return "shop" if place_kind == "shop" else "menu"


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
    listed = [pid for pid in config.places if per_place.get(pid, 0) > MANUAL_LIST_MAX]
    if listed:
        groups["manual"] = {k: kept for k, found in groups["manual"].items()
                            if (kept := [(p, r) for p, r in found if p.id not in listed])}

    def sort_key(item):   # config order of the first place, then ⭐ first, then oldest event
        found = item[1]
        place, rec = found[0]
        return (order[place.id], not any(r.star for _, r in found), rec.event_at, rec.info.get("name") or "")

    def details(section: str, place, rec: PairRec) -> list[str | None]:
        info = rec.info
        if section == "menu":
            return [_style_abv(info), _price(info)]
        if section == "shop":
            return [_style_abv(info), _pack(info), _price(info)]
        if section == "checkin":
            serving = info.get("serving")
            return [_style_abv(info), f"{esc(place.short)}, {esc(SERVING_RU.get(serving, serving))}, {_seen(info, rec, now)}"]
        return [_style_abv(info), f"{esc(place.short)} (от {esc(info.get('manual_by') or '?')})"]

    group_header = {"checkin": "<i>Похоже, появилось</i>", "manual": "<i>Со слов</i>"}
    entries: list[tuple[str, str, str]] = []   # (block, group header, line)
    for section in ("menu", "brewery", "checkin", "manual", "shop"):
        if section == "brewery":
            for key in sorted(brewery_keys, key=lambda k: (not state.brewery_new[k].star, state.brewery_new[k].found_at)):
                rec = state.brewery_new[key]
                line = _line(rec.info, None, [_style_abv(rec.info)], []) + f" ({BREWERY_NEW_NOTE})"
                entries.append(("bars", "<i>Новые сорта пивоварен</i>", line))
            continue
        if section == "manual":
            for pid in listed:
                by = sorted({r.info.get("manual_by") or "?" for p, k in pairs if p == pid
                             for r in [state.pairs[p][k]] if (r.info.get("kind") or r.info.get("source")) == "manual"})
                line = (f"<b>{esc(config.places[pid].short)}</b>: обновился список, "
                        f"{per_place[pid]} {_positions_word(per_place[pid])} (от {esc(', '.join(by))}) — на сайте")
                block = "shops" if config.places[pid].kind == "shop" else "bars"
                entries.append((block, group_header["manual"], line))
        for key, found in sorted(groups[section].items(), key=sort_key):
            place, rec = found[0]
            line = _line(rec.info, _rating(rec.info, settings), details(section, place, rec),
                         [p.short for p, _ in found[1:]], place.short)
            header = group_header.get(section) or f"<b>{esc(place.short)}</b>"
            # v1.1: a shop's own check-ins (e.g. Houl) belong in the shops block, not bars
            block = "shops" if section == "shop" or (section == "checkin" and place.kind == "shop") else "bars"
            entries.append((block, header, line))

    if not entries:
        return None
    # v1.1: bars and shops can interleave within a section (e.g. a shop's own check-ins, §I-2) --
    # stable-sort so all bars come first, then all shops, before capping and building headers.
    entries.sort(key=lambda e: e[0] != "bars")
    shown = entries[:settings.digest_max_lines]
    hidden = len(entries) - len(shown)

    local = to_yerevan(now)
    blocks: list[list[str]] = []
    last_block = last_group = None
    for block, group, line in shown:
        if block != last_block:
            blocks.append(["<b>Бары</b>" if block == "bars" else "<b>Магазины</b>"])
            last_block, last_group = block, None
        if group != last_group:
            blocks[-1].append(group)
            last_group = group
        blocks[-1].append(line)
    tail = f"…и ещё {hidden} — на сайте" if hidden else None
    header = f"🍺 <b>Новое в Ереване</b> · {WEEKDAYS[local.weekday()]}, {local.day} {MONTHS[local.month - 1]}"
    text = "\n\n".join(filter(None, [header, *("\n".join(b) for b in blocks), tail]))

    manual_ids = []
    for p, k in pairs:
        info = state.pairs[p][k].info
        for mid in info.get("manual_ids") or [info.get("manual_id")]:   # every entry merged into the pair
            if mid and mid not in manual_ids:
                manual_ids.append(mid)
    return Digest(html=text, lines_total=len(entries), lines_shown=len(shown), pairs=pairs,
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
