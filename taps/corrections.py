"""corrections.yaml: manual sightings, hide, aliases, brewery aliases and not_craft brands."""
import re
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from taps.model import untappd_n_key

LIST_SECTIONS = ("sightings", "hide", "not_craft")
MAP_SECTIONS = ("aliases", "brewery_aliases")
SIGHTING_FIELDS = ("place", "brewery", "beer", "untappd", "by", "date")
HIDE_FIELDS = ("place", "beer")
BEER_KEY_RE = re.compile(r"u:[1-9]\d*|n:\S.*")


@dataclass(frozen=True)
class ManualEntry:
    id: str                 # f"{place}|{date.isoformat()}|{by}"
    place: str
    brewery: str | None
    beer: str | None
    untappd_id: int | None
    by: str
    date: date


@dataclass(frozen=True)
class Corrections:
    sightings: tuple[ManualEntry, ...] = ()
    hide: frozenset[tuple[str, str]] = frozenset()                   # (place_id, beer_key)
    aliases: Mapping[str, str] = field(default_factory=dict)          # beer_key -> beer_key
    brewery_aliases: Mapping[str, str] = field(default_factory=dict)  # text -> text
    not_craft: tuple[str, ...] = ()


@dataclass
class CorrectionsLoad:
    corrections: Corrections | None   # None => run must stop (no snapshot and file unreadable)
    raw: dict | None                  # JSON-safe dict to store as state.corrections_snapshot (None if snapshot used)
    errors: list[str]                 # human-readable, Russian
    used_snapshot: bool


class _Skip(Exception):
    """A bad entry: skipped and reported."""


def _jsonable(value: Any) -> Any:
    # raw goes into state.json: YAML dates become ISO strings, keys become strings
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, date):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _text(value: Any, name: str) -> str | None:
    """Stripped text or None; whole numbers count as text ("379")."""
    if value is None:
        return None
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str):
        raise _Skip(f"{name} {value!r}: ожидался текст")
    return value.strip() or None


def _describe(item: Any) -> str:
    if isinstance(item, tuple):
        item = dict([item])
    if isinstance(item, dict):
        return ", ".join(f"{k}: {v}" for k, v in item.items())
    return str(item)


def _fields(entry: Any, allowed: tuple[str, ...]) -> dict:
    if not isinstance(entry, dict):
        raise _Skip("запись должна быть вида «поле: значение»")
    extra = [str(k) for k in entry if k not in allowed]
    if extra:
        raise _Skip(f"неизвестные поля: {', '.join(extra)}")
    return entry


def _place(entry: dict, place_ids: Collection[str]) -> str:
    place = _text(entry.get("place"), "place")
    if place is None:
        raise _Skip("нет поля place")
    if place not in place_ids:
        raise _Skip(f"место {place!r} не найдено среди включённых мест places.yaml")
    return place


def _beer_key(value: Any) -> str:
    if isinstance(value, str) and BEER_KEY_RE.fullmatch(value):
        return value
    raise _Skip(f"{value!r} — не ключ пива, нужно u:<число из адреса на Untappd> или n:<название>")


def _sighting(entry: Any, place_ids: Collection[str], brewery_aliases: Mapping[str, str]) -> ManualEntry:
    entry = _fields(entry, SIGHTING_FIELDS)
    place = _place(entry, place_ids)
    by = _text(entry.get("by"), "by")
    if by is None:
        raise _Skip("нет поля by (кто рассказал)")
    raw_date = entry.get("date")
    if raw_date is None:
        raise _Skip("нет поля date")
    try:
        day = raw_date if type(raw_date) is date else date.fromisoformat(raw_date)
    except (TypeError, ValueError):
        raise _Skip(f"date {raw_date!r}: нужна дата ГГГГ-ММ-ДД, например 2026-09-24") from None
    untappd = entry.get("untappd")
    if untappd is not None and (type(untappd) is not int or untappd <= 0):
        raise _Skip(f"untappd {untappd!r}: нужно число из адреса пива на Untappd")
    brewery, beer = _text(entry.get("brewery"), "brewery"), _text(entry.get("beer"), "beer")
    if untappd is None:
        if beer is None:
            raise _Skip("нужно beer (название) или untappd (число из адреса пива на Untappd)")
        if untappd_n_key(brewery, beer, brewery_aliases) == "n:":
            raise _Skip(f"по названию {beer!r} не получается ключ пива, допишите название")
    return ManualEntry(f"{place}|{day.isoformat()}|{by}", place, brewery, beer, untappd, by, day)


def _hide(entry: Any, place_ids: Collection[str]) -> tuple[str, str]:
    entry = _fields(entry, HIDE_FIELDS)
    place = _place(entry, place_ids)
    if entry.get("beer") is None:
        raise _Skip("нет поля beer")
    return place, _beer_key(entry["beer"])


def _alias(item: tuple[str, Any]) -> tuple[str, str]:
    return _beer_key(item[0]), _beer_key(item[1])


def _brewery_alias(item: tuple[str, Any]) -> tuple[str, str]:
    text, correct = _text(item[0], "название"), _text(item[1], "название")
    if text is None or correct is None:
        raise _Skip('нужны оба написания: "как пишут": "как правильно"')
    return text, correct


def _brand(item: Any) -> str:
    brand = _text(item, "бренд")
    if brand is None:
        raise _Skip("пустое название бренда")
    return brand


def _parse_all(section: str, items: Iterable[Any], parse: Callable[[Any], Any], errors: list[str]) -> list:
    out = []
    for item in items:
        try:
            out.append(parse(item))
        except _Skip as e:
            errors.append(f"corrections.yaml, {section} ({_describe(item)}): {e}")
    return out


def parse_corrections(raw: dict, place_ids: Collection[str]) -> tuple[Corrections, list[str]]:
    """Skips bad entries and reports them. Raises ValueError when the file structure itself is broken."""
    if not isinstance(raw, dict):
        raise ValueError(f"ожидался словарь разделов {', '.join(LIST_SECTIONS + MAP_SECTIONS)}")
    unknown = [str(k) for k in raw if k not in LIST_SECTIONS + MAP_SECTIONS]
    if unknown:
        raise ValueError(f"неизвестные разделы: {', '.join(unknown)}")
    s = {}
    for name in LIST_SECTIONS + MAP_SECTIONS:
        typ = list if name in LIST_SECTIONS else dict
        s[name] = typ() if raw.get(name) is None else raw[name]
        if not isinstance(s[name], typ):
            raise ValueError(f"раздел {name} должен быть {'списком' if typ is list else 'словарём'}")
    errors: list[str] = []
    brewery_aliases = dict(_parse_all("brewery_aliases", s["brewery_aliases"].items(), _brewery_alias, errors))
    corrections = Corrections(
        sightings=tuple(_parse_all("sightings", s["sightings"], lambda e: _sighting(e, place_ids, brewery_aliases), errors)),
        hide=frozenset(_parse_all("hide", s["hide"], lambda e: _hide(e, place_ids), errors)),
        aliases=dict(_parse_all("aliases", s["aliases"].items(), _alias, errors)),
        brewery_aliases=brewery_aliases,
        not_craft=tuple(_parse_all("not_craft", s["not_craft"], _brand, errors)),
    )
    return corrections, errors


def _problem(e: Exception) -> str:
    mark = getattr(e, "problem_mark", None)
    if mark is not None:
        return f"ошибка YAML в строке {mark.line + 1}: {e.problem}"
    return str(e)


def load_corrections(path: Path, snapshot: dict | None, place_ids: Collection[str]) -> CorrectionsLoad:
    """A missing or empty file is empty corrections. An unreadable file falls back to the snapshot."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        raw = {} if raw is None else _jsonable(raw)
        corrections, errors = parse_corrections(raw, place_ids)
        return CorrectionsLoad(corrections=corrections, raw=raw, errors=errors, used_snapshot=False)
    except FileNotFoundError:
        return CorrectionsLoad(corrections=Corrections(), raw={}, errors=[], used_snapshot=False)
    except (OSError, yaml.YAMLError, ValueError) as e:  # ValueError includes UnicodeDecodeError
        problem = _problem(e)
    if snapshot is None:
        error = f"corrections.yaml не читается ({problem}), а удачной версии в state.json нет: прогон остановлен до исправления файла"
        return CorrectionsLoad(corrections=None, raw=None, errors=[error], used_snapshot=False)
    try:
        corrections, errors = parse_corrections(snapshot, place_ids)
    except ValueError as e:
        error = (f"corrections.yaml не читается ({problem}), снимок в state.json тоже не читается ({e}): "
                 "прогон остановлен до исправления файла")
        return CorrectionsLoad(corrections=None, raw=None, errors=[error], used_snapshot=False)
    error = f"corrections.yaml не читается ({problem}): работаю по последней удачной версии, пока файл не исправят"
    return CorrectionsLoad(corrections=corrections, raw=None, errors=[error, *errors], used_snapshot=True)
