"""places.yaml loader: places, breweries and settings."""
import re
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path
from typing import Any, Mapping

import yaml

KINDS = ("bar", "brewpub", "shop")
# Place source name -> required params and their types.
SOURCE_PARAMS: dict[str, dict[str, type]] = {
    "untappd_menu": {"slug": str, "venue_id": int},
    "untappd_checkins": {"slug": str, "venue_id": int},
    "buyam": {"url": str},
    "beercity": {},
    "yerevan_city": {},
    "parma": {},
}
ID_RE = re.compile(r"[a-z0-9][a-z0-9-]*")
CHECKIN_VENUE_FIELDS = ("slug", "venue_id", "address")
PLACE_FIELDS = ("id", "name", "kind", "sources", "enabled", "brewery_id", "brewery_name", "untappd_venue_id",
                "merged_from", "address")
BREWERY_FIELDS = ("id", "name", "brewery_id", "slug", "list_enabled")
SETTINGS_FIELDS = ("preview_digests", "digest_time", "digest_max_lines", "hot_rating", "untappd_daily_pages",
                   "boost_until", "boost_daily_pages", "boost_search_per_run", "discovery_daily_until")
_REQUIRED = object()


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Place:
    id: str
    name: str
    kind: str
    sources: Mapping[str, Mapping[str, Any]]
    enabled: bool = True
    brewery_id: int | None = None
    brewery_name: str | None = None
    untappd_venue_id: int | None = None
    merged_from: tuple[str, ...] = ()   # old place ids merged into this one (state.merge_places), v1.1
    address: str | None = None          # street address of a single-venue place, as people write it (v1.4)

    def source_keys(self) -> list[str]:
        return [f"{s}:{self.id}" for s in self.sources]

    @property
    def has_menu(self) -> bool:
        return "untappd_menu" in self.sources or "buyam" in self.sources

    def _checkin_venues(self) -> list[Mapping[str, Any]]:
        checkins = self.sources.get("untappd_checkins")
        if checkins is None:
            return []
        return checkins["venues"] if "venues" in checkins else [checkins]

    @property
    def venue_ids(self) -> list[int]:
        """Every Untappd venue id behind this place (v1.1: one place may list several, e.g. two branches
        of the same brewpub sharing a single Untappd brand)."""
        if "untappd_menu" in self.sources:
            return [self.sources["untappd_menu"]["venue_id"]]
        venues = self._checkin_venues()
        if venues:
            return [v["venue_id"] for v in venues]
        return [self.untappd_venue_id] if self.untappd_venue_id is not None else []

    @property
    def venue_id(self) -> int | None:
        """The place's own (first) venue id: used where a place is represented by a single id
        (logo/verified lookup, `known_venue_ids`'s uniqueness check)."""
        ids = self.venue_ids
        return ids[0] if ids else None

    @property
    def addresses(self) -> list[str]:
        """Per-venue addresses (v1.1 multi-venue places), in `places.yaml` order; they win over the place's
        own `address`, which stands in when there are none; empty for a place with no address on file."""
        venue_addresses = [v["address"] for v in self._checkin_venues() if v.get("address")]
        return venue_addresses or ([self.address] if self.address else [])


@dataclass(frozen=True)
class Brewery:
    id: str
    name: str
    brewery_id: int
    slug: str
    list_enabled: bool = False

    @property
    def url(self) -> str:
        return f"https://untappd.com/brewery/{self.brewery_id}"


@dataclass(frozen=True)
class Settings:
    preview_digests: int = 2
    digest_time: time = time(17, 0)
    digest_max_lines: int = 15
    hot_rating: float = 3.75
    untappd_daily_pages: int = 40
    # temporary one-off boost (owner decision): while the Yerevan date is <= boost_until, the daily
    # Untappd page budget and the shop-search-per-run cap are raised; unset or expired -> normal values.
    boost_until: str | None = None
    boost_daily_pages: int | None = None
    boost_search_per_run: int | None = None
    # temporary one-off cadence change (owner decision): while the Yerevan date is <= discovery_daily_until,
    # the new-places report (v1.1 weekly admin report) is due daily instead of weekly; unset or expired ->
    # the normal weekly (Monday) cadence.
    discovery_daily_until: str | None = None

    def _boost_active(self, today: str) -> bool:
        return self.boost_until is not None and today <= self.boost_until

    def effective_daily_pages(self, today: str) -> int:
        if self._boost_active(today) and self.boost_daily_pages is not None:
            return self.boost_daily_pages
        return self.untappd_daily_pages

    def effective_search_per_run(self, today: str, default: int) -> int:
        if self._boost_active(today) and self.boost_search_per_run is not None:
            return self.boost_search_per_run
        return default

    def discovery_daily_active(self, today: str) -> bool:
        return self.discovery_daily_until is not None and today <= self.discovery_daily_until


@dataclass(frozen=True)
class Config:
    places: Mapping[str, Place]  # enabled places only, in file order
    breweries: tuple[Brewery, ...]
    settings: Settings
    known_venue_ids: frozenset[int] = frozenset()   # every place's venue id, enabled or disabled (v1.1 discovery)

    def place_by_venue(self, venue_id: int) -> Place | None:
        return next((p for p in self.places.values() if venue_id in p.venue_ids), None)


def _fail(where: str, msg: str) -> ConfigError:
    return ConfigError(f"places.yaml, {where}: {msg}")


def _mapping(value: Any, where: str, allowed: tuple[str, ...] | None = None) -> dict:
    if not isinstance(value, dict):
        raise _fail(where, "ожидался словарь «поле: значение»")
    extra = [str(k) for k in value if allowed is not None and k not in allowed]
    if extra:
        raise _fail(where, f"неизвестные поля: {', '.join(extra)}")
    return value


def _get(d: Mapping, key: str, typ: type | tuple[type, ...], where: str, default: Any = _REQUIRED) -> Any:
    value = d.get(key)
    if value is None:
        if default is _REQUIRED:
            raise _fail(where, f"нет поля {key}")
        return default
    # bool is an int subclass: `venue_id: true` must not pass as a number
    if not isinstance(value, typ) or (isinstance(value, bool) and typ is not bool):
        raise _fail(where, f"неверное значение {key}: {value!r}")
    return value


def _checkin_venue(raw: Any, where: str) -> dict[str, Any]:
    d = _mapping(raw, where, CHECKIN_VENUE_FIELDS)
    return {
        "slug": _get(d, "slug", str, where),
        "venue_id": _get(d, "venue_id", int, where),
        "address": _get(d, "address", str, where, None),
    }


def _sources(raw: Any, where: str) -> dict[str, dict[str, Any]]:
    sources = {}
    for name, params in _mapping(raw, where).items():
        if name not in SOURCE_PARAMS:
            raise _fail(where, f"неизвестный источник {name!r}, есть: {', '.join(SOURCE_PARAMS)}")
        # v1.1: untappd_checkins may list several venues of one place instead of a single slug/venue_id
        if name == "untappd_checkins" and isinstance(params, dict) and "venues" in params:
            venues = params["venues"]
            if not isinstance(venues, list) or not venues:
                raise _fail(f"{where}, {name}", "venues: нужен непустой список")
            sources[name] = {"venues": [_checkin_venue(v, f"{where}, {name}, venues[{i + 1}]")
                                        for i, v in enumerate(venues)]}
            continue
        spec = SOURCE_PARAMS[name]
        params = _mapping(params or {}, f"{where}, {name}", tuple(spec))
        for key, typ in spec.items():
            _get(params, key, typ, f"{where}, {name}")
        sources[name] = params
    return sources


def _place(raw: Any, n: int) -> Place:
    where = f"places, запись {n + 1}"
    d = _mapping(raw, where, PLACE_FIELDS)
    pid = _get(d, "id", str, where)
    if not ID_RE.fullmatch(pid):
        raise _fail(where, f"id {pid!r}: только строчная латиница, цифры и дефис")
    where = f"место {pid}"
    kind = _get(d, "kind", str, where)
    if kind not in KINDS:
        raise _fail(where, f"kind {kind!r}, должно быть одно из: {', '.join(KINDS)}")
    sources = _sources(d.get("sources") or {}, where)
    if not sources:
        raise _fail(where, "нужен хотя бы один источник")
    merged_from = _get(d, "merged_from", list, where, [])
    if not all(isinstance(v, str) for v in merged_from):
        raise _fail(where, f"неверное значение merged_from: {merged_from!r}")
    return Place(
        id=pid,
        name=_get(d, "name", str, where),
        kind=kind,
        sources=sources,
        enabled=_get(d, "enabled", bool, where, True),
        brewery_id=_get(d, "brewery_id", int, where, None),
        brewery_name=_get(d, "brewery_name", str, where, None),
        untappd_venue_id=_get(d, "untappd_venue_id", int, where, None),
        merged_from=tuple(merged_from),
        address=_get(d, "address", str, where, None),
    )


def _brewery(raw: Any, n: int) -> Brewery:
    where = f"breweries, запись {n + 1}"
    d = _mapping(raw, where, BREWERY_FIELDS)
    return Brewery(
        id=_get(d, "id", str, where),
        name=_get(d, "name", str, where),
        brewery_id=_get(d, "brewery_id", int, where),
        slug=_get(d, "slug", str, where),
        list_enabled=_get(d, "list_enabled", bool, where, False),
    )


def _settings(raw: Any) -> Settings:
    d = _mapping(raw, "settings", SETTINGS_FIELDS)
    s = Settings()
    raw_time = d.get("digest_time")  # unquoted 17:00 is the int 1020 in YAML -> TypeError
    try:
        parsed_time = s.digest_time if raw_time is None else time.fromisoformat(raw_time)
    except (TypeError, ValueError):
        raise _fail("settings", f"digest_time {raw_time!r}: нужно время ЧЧ:ММ в кавычках, например \"17:00\"") from None
    raw_boost_until = d.get("boost_until")   # unquoted 2026-10-01 is a date object in YAML -> TypeError
    try:
        boost_until = None if raw_boost_until is None else date.fromisoformat(raw_boost_until).isoformat()
    except (TypeError, ValueError):
        raise _fail("settings",
                    f"boost_until {raw_boost_until!r}: нужна дата ГГГГ-ММ-ДД в кавычках, например \"2026-10-01\"") from None
    raw_discovery_daily_until = d.get("discovery_daily_until")   # unquoted date -> TypeError, same as boost_until
    try:
        discovery_daily_until = (None if raw_discovery_daily_until is None
                                 else date.fromisoformat(raw_discovery_daily_until).isoformat())
    except (TypeError, ValueError):
        raise _fail("settings",
                    f"discovery_daily_until {raw_discovery_daily_until!r}: нужна дата ГГГГ-ММ-ДД в кавычках, "
                    "например \"2026-10-01\"") from None
    return Settings(
        preview_digests=_get(d, "preview_digests", int, "settings", s.preview_digests),
        digest_time=parsed_time,
        digest_max_lines=_get(d, "digest_max_lines", int, "settings", s.digest_max_lines),
        hot_rating=float(_get(d, "hot_rating", (int, float), "settings", s.hot_rating)),
        untappd_daily_pages=_get(d, "untappd_daily_pages", int, "settings", s.untappd_daily_pages),
        boost_until=boost_until,
        boost_daily_pages=_get(d, "boost_daily_pages", int, "settings", s.boost_daily_pages),
        boost_search_per_run=_get(d, "boost_search_per_run", int, "settings", s.boost_search_per_run),
        discovery_daily_until=discovery_daily_until,
    )


def _list(root: Mapping, key: str, required: bool) -> list:
    value = root.get(key)
    if value is None and not required:
        return []
    if not isinstance(value, list):
        raise _fail(key, "ожидался список")
    return value


def _check_unique(ids: list[str], what: str) -> None:
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise _fail(what, f"повторяется id: {', '.join(dup)}")


def load_config(path: Path) -> Config:
    try:
        root = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        raise ConfigError(f"не удалось прочитать {path}: {e}") from e
    root = _mapping(root, "весь файл", ("places", "breweries", "settings"))
    places = [_place(raw, n) for n, raw in enumerate(_list(root, "places", required=True))]
    breweries = tuple(_brewery(raw, n) for n, raw in enumerate(_list(root, "breweries", required=False)))
    _check_unique([p.id for p in places], "places")
    _check_unique([b.id for b in breweries], "breweries")
    return Config(
        places={p.id: p for p in places if p.enabled},
        breweries=breweries,
        settings=_settings(root.get("settings") or {}),
        known_venue_ids=frozenset(vid for p in places for vid in p.venue_ids),
    )
