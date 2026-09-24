# Ереван на кранах — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Бесплатный трекер крафтового пива в барах и магазинах Еревана: статичная страница на GitHub Pages и одна тихая сводка новинок в день в Telegram-чат друзей, без спама.

**Architecture:** Python-пакет `taps` запускается GitHub Actions дважды в день (`python -m taps run`). Источники (меню Untappd через Playwright, чекины, страницы пивоварен, buy.am, Beer City, Yerevan City, Parma, правки «со слов») отдают наблюдения единого вида; `rules` сливает их с ограниченным `state.json` и порождает события с тихим первым прогоном и предохранителем; `digest` решает, пора ли слать, и собирает HTML-сводку; отправка идёт только после удачного `git push` отметки «отправлено». `site/index.html` читает сгенерированный `site/data.json`.

**Tech Stack:** Python ≥ 3.12, Playwright (Chromium), requests, beautifulsoup4 (html.parser), PyYAML, pytest; GitHub Actions + GitHub Pages; Telegram Bot API.

**Спецификация:** `docs/superpowers/specs/2026-09-23-yerevan-taps-design.md` (одобрена Олегом). Реальные страницы источников для тестов уже лежат в `tests/fixtures/**` (коммит `7821afc`, ники Untappd обезличены) — их не трогать.

**Как написан этот план:** весь код ниже написан и прогнан заранее на эталонной копии проекта: 680 тестов, 0 падений, после каждой волны задач — полный прогон всех тестов. Код в шагах — финальный, вставлять как есть. Если что-то не сходится с кодом соседней задачи, прав код, а не описание интерфейсов.

## Global Constraints

- Python ≥ 3.12; локально venv на python3.13: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`.
- Зависимости только из `requirements.txt`: `playwright>=1.45`, `requests>=2.31`, `beautifulsoup4>=4.12`, `PyYAML>=6.0`, `pytest>=8.0`. Новых не добавлять без согласования с Олегом.
- Тесты всегда так: `timeout 120 .venv/bin/pytest <файлы> -v`; один процесс pytest за раз; в тестах нет сети, время и паузы подставляются (`now`, `sleep`).
- Время: везде Asia/Yerevan (UTC+4, без летнего времени) через `zoneinfo`; «сегодня» — дата по Еревану; в `state.json` время хранится как ISO UTC.
- Главное требование — не спамить: в чат не больше одного сообщения в сутки и не чаще раза в 20 часов; отправка только после удачного push отметки; откат только при явном отказе Telegram; первые 2 сводки — в личку Олегу; служебное — только в личку, одно на серию сбоев.
- Untappd: не больше 30 страниц в сутки, паузы 4–6 с, первая проверка Cloudflare останавливает весь сбор Untappd в прогоне.
- Секреты только в GitHub Secrets (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_ADMIN_CHAT_ID`), переменная `SITE_URL = https://olegbeers.github.io/yerevan/`. Репозиторий `OlegBeers/yerevan`, публичный; локальная папка `~/Claude/Projects/yerevan-taps`.
- Строки для людей (сводка, сайт, предупреждения) — по-русски; идентификаторы и комментарии в коде — по-английски.
- Коммиты в конце каждой задачи, сообщения в стиле `feat: …` / `test: …` / `chore: …`.

## Порядок задач

1–6 — фундамент (модель, конфигурация, правки, фильтр, состояние, сеть); 7–13 — источники; 14–15 — предохранитель и правила; 16–17 — сводка и Telegram; 18 — синхронизация через git; 19–20 — данные и страница сайта; 21 — оркестрация прогона; 22 — GitHub Actions, README и ручные шаги запуска. Задачи выполняются по порядку: каждая опирается только на предыдущие.

---

### Task 1: Каркас проекта, время и модель данных

**Files:**
- Create: `requirements.txt`, `pyproject.toml`, `.gitignore`, `taps/__init__.py`, `taps/sources/__init__.py`, `tests/__init__.py`, `tests/helpers.py`
- Create: `taps/timeutil.py`
- Create: `taps/model.py`
- Test: `tests/test_timeutil.py`
- Test: `tests/test_model.py`

**Interfaces:**
- Consumes: ничего (первая задача). Фикстуры `tests/fixtures/**` уже лежат в репозитории.
- Produces:
  - `taps.timeutil`: `YEREVAN`, `utcnow() -> datetime`, `iso(dt) -> str`, `parse_iso(s) -> datetime`, `to_yerevan(dt) -> datetime`, `yerevan_date(dt) -> str`, `age_days(then, now) -> float`. Функции `iso`, `parse_iso`, `to_yerevan`, `yerevan_date` бросают `ValueError` на naive-времени.
  - `taps.model`: `SOURCE_KINDS`, `Sighting` (frozen, свойство `kind`), `BreweryBeer` (frozen), `SourceResult`, `u_key(beer_id)`, `COLOR_WORDS`, `normalize_base(text)` (шаги 1–3 нормализации, стоп-слова не удаляются), `normalize_title(text, brewery_aliases=None)`, `n_key(text, brewery_aliases=None)`, `untappd_n_key(brewery, name, brewery_aliases=None)`, `strip_color(key)`.
  - `tests.helpers`: `FIXTURES`, `fixture_text(rel)`, `fixture_json(rel)`.

Создаём каркас пакета и два базовых модуля. От них зависят все остальные задачи. `timeutil` держит правило спеки §2: внутри всё в aware UTC, «сегодня» считается по Еревану. `model` задаёт «Замечено» (Sighting), результат источника и ключи пива `u:`/`n:` с нормализацией названий (спека §5 «Ключи пива», алгоритм и примеры из контракта).

- [ ] **Step 0: Каркас проекта и виртуальное окружение**

Создай `requirements.txt`:
```text
playwright>=1.45
requests>=2.31
beautifulsoup4>=4.12
PyYAML>=6.0
pytest>=8.0
```

Создай `pyproject.toml`:
```toml
[project]
name = "yerevan-taps"
version = "0.1.0"
requires-python = ">=3.12"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

Создай `.gitignore`:
```text
.venv/
__pycache__/
.pytest_cache/
site/data.json
.taps-fatal
```

Создай пустые файлы пакетов `taps/__init__.py`, `taps/sources/__init__.py`, `tests/__init__.py`:
```python

```
```python

```
```python

```

Создай `tests/helpers.py`:
```python
import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_text(rel: str) -> str:
    return (FIXTURES / rel).read_text(encoding="utf-8")


def fixture_json(rel: str):
    return json.loads(fixture_text(rel))
```

Создай окружение: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
Проверка: `.venv/bin/pytest --version` печатает `pytest 8.x`.

- [ ] **Step 1: Write the failing test (timeutil)**

Создай `tests/test_timeutil.py`:
```python
from datetime import datetime, timedelta, timezone

import pytest

from taps.timeutil import YEREVAN, age_days, iso, parse_iso, to_yerevan, utcnow, yerevan_date


def test_utcnow_is_aware_utc():
    assert utcnow().utcoffset() == timedelta(0)


def test_iso_formats_utc_to_seconds():
    dt = datetime(2026, 9, 23, 14, 17, 5, 123456, tzinfo=timezone.utc)
    assert iso(dt) == "2026-09-23T14:17:05+00:00"


def test_iso_converts_other_zones_to_utc():
    assert iso(datetime(2026, 9, 23, 18, 17, tzinfo=YEREVAN)) == "2026-09-23T14:17:00+00:00"


def test_iso_parse_iso_round_trip():
    dt = datetime(2026, 9, 23, 14, 17, tzinfo=timezone.utc)
    back = parse_iso(iso(dt))
    assert back == dt
    assert back.tzinfo == timezone.utc


def test_parse_iso_normalizes_offset_and_z_to_utc():
    assert parse_iso("2026-09-23T18:17:00+04:00") == datetime(2026, 9, 23, 14, 17, tzinfo=timezone.utc)
    assert parse_iso("2026-09-23T18:17:00+04:00").tzinfo == timezone.utc
    assert parse_iso("2026-09-23T20:30:00Z") == datetime(2026, 9, 23, 20, 30, tzinfo=timezone.utc)


def test_naive_datetimes_are_rejected():
    with pytest.raises(ValueError):
        parse_iso("2026-09-23T14:17:00")
    with pytest.raises(ValueError):
        iso(datetime(2026, 9, 23, 14, 17))
    with pytest.raises(ValueError):
        yerevan_date(datetime(2026, 9, 23, 14, 17))


def test_to_yerevan_is_utc_plus_4():
    local = to_yerevan(parse_iso("2026-09-23T20:30:00Z"))
    assert local.utcoffset() == timedelta(hours=4)
    assert (local.day, local.hour, local.minute) == (24, 0, 30)


def test_yerevan_date_across_midnight():
    assert yerevan_date(parse_iso("2026-09-23T20:30:00Z")) == "2026-09-24"
    assert yerevan_date(parse_iso("2026-09-23T19:59:59Z")) == "2026-09-23"


def test_age_days():
    then = parse_iso("2026-09-20T12:00:00+00:00")
    now = parse_iso("2026-09-23T00:00:00+00:00")
    assert age_days(then, now) == 2.5
    assert age_days(now, then) == -2.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 120 .venv/bin/pytest tests/test_timeutil.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taps.timeutil'`

- [ ] **Step 3: Write minimal implementation (timeutil)**

Создай `taps/timeutil.py`:
```python
"""Time helpers: everything is aware UTC internally, Asia/Yerevan for "today"."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

YEREVAN = ZoneInfo("Asia/Yerevan")


def _aware(dt: datetime) -> datetime:
    if dt.utcoffset() is None:
        raise ValueError(f"naive datetime: {dt!r}")
    return dt


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return _aware(dt).astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(s: str) -> datetime:
    return _aware(datetime.fromisoformat(s)).astimezone(timezone.utc)


def to_yerevan(dt: datetime) -> datetime:
    return _aware(dt).astimezone(YEREVAN)


def yerevan_date(dt: datetime) -> str:
    return to_yerevan(dt).date().isoformat()


def age_days(then: datetime, now: datetime) -> float:
    return (now - then).total_seconds() / 86400
```

- [ ] **Step 4: Run test to verify it passes**

Run: `timeout 120 .venv/bin/pytest tests/test_timeutil.py -v`
Expected: `9 passed`

- [ ] **Step 5: Commit**

```bash
git add requirements.txt pyproject.toml .gitignore taps/__init__.py taps/sources/__init__.py tests/__init__.py tests/helpers.py tests/test_timeutil.py taps/timeutil.py
git commit -m "feat: project scaffold and Yerevan time helpers"
```

- [ ] **Step 6: Write the failing test (model)**

Создай `tests/test_model.py`:
```python
import dataclasses
from datetime import datetime, timezone

import pytest

from taps.model import (
    SOURCE_KINDS,
    BreweryBeer,
    Sighting,
    SourceResult,
    n_key,
    normalize_base,
    normalize_title,
    strip_color,
    u_key,
    untappd_n_key,
)

NOW = datetime(2026, 9, 23, 14, 17, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "title, expected",
    [
        ('Beer "Kilikia" 1l', "kilikia"),
        ("Ayinger Privatbrauerei Celebrator", "ayinger celebrator"),
        ('Beer "Volfas Engelman" Ipa (can) 0.568l', "volfas engelman ipa"),
        ("Dahook light 0.5L", "dahook light"),
        ("379 American Wheat", "379 american wheat"),
    ],
)
def test_normalize_title_contract_examples(title, expected):
    assert normalize_title(title) == expected


def test_normalize_title_with_brewery_alias():
    assert normalize_title("V.Engelman IPA", {"v engelman": "volfas engelman"}) == "volfas engelman ipa"


def test_normalize_title_real_beer_city_titles():
    # titles as they appear in tests/fixtures/beercity listings
    assert normalize_title('Beer "Starnberger helles" 0,45l') == "starnberger helles"
    assert normalize_title('Beer "Pure wave" IPA non alco 0.45 l') == "pure wave ipa non alco"
    assert normalize_title('Beer "StaroPeazske" 0.43L') == "staropeazske"


def test_normalize_title_strips_diacritics():
    assert normalize_title("Budějovický Märzen Rosé") == "budejovicky marzen rose"


def test_normalize_title_removes_volumes_and_multipacks():
    assert normalize_title("Hoegaarden 4x0.33L") == "hoegaarden"
    assert normalize_title("Guinness Draught 44 cl") == "guinness"
    assert normalize_title("Пиво разливное Жигулёвское 0,5 л") == "жигулевское"


def test_normalize_title_keeps_armenian_letters():
    assert normalize_title("Կիլիկիա 0.5լ") == "կիլիկիա"


def test_normalize_title_drops_legal_and_container_tokens():
    assert normalize_title("Dargett Brewery Co. Hazy IPA (bottle)") == "dargett hazy ipa"
    assert normalize_title("Brasserie Dupont GmbH Saison Unfiltered") == "dupont saison"


def test_normalize_base_is_steps_1_to_3_only():
    # keeps stop tokens; used by shop_filter.brand_matches
    assert normalize_base('Beer "Volfas Engelman" (can) 0.5L') == "beer volfas engelman can"


def test_brewery_aliases_longest_key_first_single_pass():
    aliases = {"engelman": "volfas engelman", "V. Engelman": "Volfas Engelman"}
    assert normalize_title("V.Engelman IPA", aliases) == "volfas engelman ipa"
    assert normalize_title("Engelman IPA", aliases) == "volfas engelman ipa"


def test_brewery_aliases_match_whole_tokens_only():
    aliases = {"v engelman": "volfas engelman"}
    assert normalize_title("DV Engelman IPA", aliases) == "dv engelman ipa"


def test_brewery_alias_with_empty_normalized_key_is_ignored():
    assert normalize_title("Kilikia", {"!!!": "x"}) == "kilikia"


def test_keys():
    assert u_key(12345) == "u:12345"
    assert n_key('Beer "Kilikia" 1l') == "n:kilikia"
    assert n_key("V.Engelman IPA", {"v engelman": "volfas engelman"}) == "n:volfas engelman ipa"
    assert untappd_n_key("Dargett", "Hazy IPA") == "n:dargett hazy ipa"
    assert untappd_n_key(None, "Hazy IPA") == "n:hazy ipa"
    assert untappd_n_key("V.Engelman", "IPA", {"v engelman": "volfas engelman"}) == "n:volfas engelman ipa"


def test_strip_color():
    assert strip_color("n:dahook light") == "n:dahook"
    assert strip_color("n:dark side light lager") == "n:side lager"
    assert strip_color("n:dahook") == "n:dahook"
    assert strip_color("u:12345") == "u:12345"


def _sighting(source: str) -> Sighting:
    return Sighting(place_id="gargoyle", source=source, beer_key="u:1", title="t", name="n", seen_at=NOW)


def test_sighting_kind_for_every_source():
    assert {s: _sighting(s).kind for s in SOURCE_KINDS} == {
        "untappd_menu": "menu",
        "buyam": "menu",
        "untappd_checkins": "checkin",
        "untappd_brewery": "checkin",
        "untappd_brewery_list": "brewery_list",
        "beercity": "shop",
        "yerevan_city": "shop",
        "parma": "shop",
        "manual": "manual",
    }


def test_sighting_is_frozen_with_defaults():
    s = _sighting("manual")
    assert s.brewery is None and s.rating is None and s.in_stock is None
    assert s.at_home is False
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.name = "x"


def test_brewery_beer_defaults():
    b = BreweryBeer(brewery_id=1, untappd_beer_id=2, name="Hazy IPA", brewery="Dargett")
    assert (b.style, b.abv, b.url) == (None, None, None)


def test_source_result_defaults():
    r = SourceResult(key="manual", source="manual", ok=True)
    assert r.sightings == []
    assert r.error is None
    assert r.full is True
    assert r.place_id is None and r.brewery_id is None
    assert r.menu_updated_at is None
    assert r.brewery_beers == []
    other = SourceResult(key="parma:parma", source="parma", ok=False, error="network")
    other.sightings.append(_sighting("parma"))
    assert r.sightings == []
```

- [ ] **Step 7: Run test to verify it fails**

Run: `timeout 120 .venv/bin/pytest tests/test_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taps.model'`

- [ ] **Step 8: Write minimal implementation (model)**

Создай `taps/model.py`:
```python
"""Core data model and beer keys."""
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

SOURCE_KINDS: dict[str, str] = {
    "untappd_menu": "menu", "buyam": "menu",
    "untappd_checkins": "checkin", "untappd_brewery": "checkin",
    "untappd_brewery_list": "brewery_list",
    "beercity": "shop", "yerevan_city": "shop", "parma": "shop",
    "manual": "manual",
}


@dataclass(frozen=True)
class Sighting:
    place_id: str
    source: str                  # key of SOURCE_KINDS
    beer_key: str                # "u:<id>" or "n:<normalized>"
    title: str                   # raw title as in source
    name: str                    # human-readable beer name
    seen_at: datetime            # aware UTC; check-in time for check-ins, else fetch time
    brewery: str | None = None
    brewery_id: int | None = None
    untappd_beer_id: int | None = None
    shop_item_id: str | None = None
    style: str | None = None
    abv: float | None = None
    ibu: int | None = None
    rating: float | None = None  # Untappd average rating, never a personal check-in rating
    price_amd: int | None = None
    volume_ml: int | None = None
    container: str | None = None  # "can" | "bottle" | "keg" | "draft"
    serving: str | None = None    # check-ins: "Draft" | "Bottle" | "Can" | "Taster" | ...
    in_stock: bool | None = None  # shops only
    menu_id: str | None = None
    category: str | None = None   # shops: source category name
    url: str | None = None
    checkin_id: int | None = None
    at_home: bool = False
    manual_id: str | None = None
    manual_by: str | None = None
    manual_date: str | None = None  # "YYYY-MM-DD"

    @property
    def kind(self) -> str:
        return SOURCE_KINDS[self.source]


@dataclass(frozen=True)
class BreweryBeer:
    brewery_id: int
    untappd_beer_id: int
    name: str
    brewery: str
    style: str | None = None
    abv: float | None = None
    url: str | None = None


@dataclass
class SourceResult:
    key: str                     # state.sources key
    source: str                  # key of SOURCE_KINDS
    ok: bool
    sightings: list[Sighting] = field(default_factory=list)
    error: str | None = None     # "network"|"cloudflare"|"http"|"blocked"|"bad_response"|"empty"|"incomplete"
    full: bool = True            # False only for Beer City partial runs
    place_id: str | None = None
    brewery_id: int | None = None
    menu_updated_at: datetime | None = None
    brewery_beers: list[BreweryBeer] = field(default_factory=list)


def u_key(beer_id: int) -> str:
    return f"u:{beer_id}"


COLOR_WORDS = frozenset({"light", "dark"})

_VOLUME_RE = re.compile(r"\d+(?:[.,]\d+)?\s*(?:ml|cl|l|мл|л|լ)\b")
_MULTIPACK_RE = re.compile(r"\b\d+\s*x\b")
_NON_TOKEN_RE = re.compile(r"[^a-z0-9а-яёԱ-և]")
_STOP_TOKENS = frozenset(
    "beer draught draft drink пиво разливное can bottle keg pet tin glass brewery brewing brewers brewpub"
    " brauerei privatbrauerei brasserie birrificio llc gmbh co ltd unfiltered filtered нефильтрованное".split()
)


def normalize_base(text: str) -> str:
    """Steps 1-3 of normalize_title, spaces collapsed."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _MULTIPACK_RE.sub(" ", _VOLUME_RE.sub(" ", text))
    return " ".join(_NON_TOKEN_RE.sub(" ", text).split())


def normalize_title(text: str, brewery_aliases: Mapping[str, str] | None = None) -> str:
    text = normalize_base(text)
    if brewery_aliases:
        table = {normalize_base(k): normalize_base(v) for k, v in brewery_aliases.items()}
        table.pop("", None)
        if table:
            keys = sorted(table, key=len, reverse=True)
            pattern = r"(?<!\S)(?:" + "|".join(map(re.escape, keys)) + r")(?!\S)"
            text = re.sub(pattern, lambda m: table[m.group(0)], text)
    return " ".join(t for t in text.split() if t not in _STOP_TOKENS)


def n_key(text: str, brewery_aliases: Mapping[str, str] | None = None) -> str:
    return "n:" + normalize_title(text, brewery_aliases)


def untappd_n_key(brewery: str | None, name: str, brewery_aliases: Mapping[str, str] | None = None) -> str:
    return n_key(f"{brewery or ''} {name}", brewery_aliases)


def strip_color(key: str) -> str:
    prefix, sep, body = key.partition(":")
    return prefix + sep + " ".join(t for t in body.split() if t not in COLOR_WORDS)
```

- [ ] **Step 9: Run test to verify it passes**

Run: `timeout 120 .venv/bin/pytest tests/test_model.py -v`
Expected: `21 passed`

- [ ] **Step 10: Commit**

```bash
git add tests/test_model.py taps/model.py
git commit -m "feat: data model, beer keys and title normalization"
```

---

### Task 2: Конфигурация мест: places.yaml и загрузчик

**Files:**
- Create: `places.yaml`
- Create: `taps/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: только `yaml.safe_load` (PyYAML); модули проекта не импортирует.
- Produces: `ConfigError(Exception)`; `Place(id, name, kind, sources, enabled=True, brewery_id=None, brewery_name=None, untappd_venue_id=None)` с `source_keys() -> list[str]`, свойствами `has_menu -> bool` и `venue_id -> int | None`; `Brewery(id, name, brewery_id, list_enabled=False)` со свойством `url -> str`; `Settings(preview_digests=2, digest_time=time(17, 0), digest_max_lines=15, hot_rating=3.75, untappd_daily_pages=30)`; `Config(places, breweries, settings)` с `place_by_venue(venue_id: int) -> Place | None`; `load_config(path: Path) -> Config`; константы `KINDS`, `SOURCE_PARAMS` (имена источников мест и их обязательные параметры).

Загрузчик `places.yaml` читает места, их источники, пивоварни из §4.2 и настройки сводки (спека §4, §5). Остальные модули берут отсюда список мест в порядке файла, ключи источников `"<source>:<place_id>"` и `place_by_venue` для привязки чекинов к местам. Файл Олег правит руками на GitHub, поэтому загрузчик строго проверяет типы, неизвестные поля и повторы id. При ошибке он падает с понятным русским `ConfigError`, а не с `KeyError` посреди прогона.

- [ ] **Step 1: Write the failing test**

Create `tests/test_config.py`:
```python
from datetime import time
from pathlib import Path

import pytest

from taps.config import Brewery, ConfigError, Settings, load_config

PLACES_YAML = Path(__file__).parent.parent / "places.yaml"

MINIMAL = """
places:
  - id: gargoyle
    name: Gargoyle Bar
    kind: bar
    sources:
      untappd_menu: {slug: gargoyle-bar, venue_id: 12252462}
  - id: tuf
    name: Tuf
    kind: bar
    enabled: false
    sources:
      untappd_checkins: {slug: tuf, venue_id: 11284746}
"""


def write(tmp_path, text):
    path = tmp_path / "places.yaml"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def cfg():
    return load_config(PLACES_YAML)


def test_real_file_counts_and_order(cfg):
    assert len(cfg.places) == 17
    kinds = [p.kind for p in cfg.places.values()]
    assert (kinds.count("bar"), kinds.count("brewpub"), kinds.count("shop")) == (10, 4, 3)
    assert list(cfg.places)[:2] == ["gargoyle", "beatles"]
    assert list(cfg.places)[-3:] == ["beer-city", "yerevan-city", "parma"]
    assert len(cfg.breweries) == 8
    assert not any(b.list_enabled for b in cfg.breweries)


def test_menu_places(cfg):
    gargoyle = cfg.places["gargoyle"]
    assert gargoyle.name == "Gargoyle Bar"
    assert gargoyle.has_menu
    assert gargoyle.venue_id == 12252462
    assert gargoyle.sources["untappd_menu"]["slug"] == "gargoyle-bar"
    dargett = cfg.places["dargett-brewpub"]
    assert dargett.kind == "brewpub"
    assert dargett.has_menu
    assert dargett.sources == {"buyam": {"url": "https://buy.am/en/restaurants/dargett"}}
    assert dargett.venue_id == 4640403
    assert (dargett.brewery_id, dargett.brewery_name) == (265165, "Dargett")


def test_checkin_brewpubs(cfg):
    dors = cfg.places["dors"]
    assert dors.brewery_name == "Dors"
    assert dors.brewery_id == 441775
    assert not dors.has_menu
    assert dors.venue_id == 9312556
    assert cfg.places["379-torch-brew"].brewery_name == "379"  # quoted, stays a string


def test_place_by_venue(cfg):
    assert cfg.place_by_venue(12252462).id == "gargoyle"
    assert cfg.place_by_venue(4640403).id == "dargett-brewpub"
    assert cfg.place_by_venue(12455977).id == "punk-photo"
    assert cfg.place_by_venue(1) is None


def test_source_keys(cfg):
    assert cfg.places["gargoyle"].source_keys() == ["untappd_menu:gargoyle"]
    assert cfg.places["dors"].source_keys() == ["untappd_checkins:dors"]
    assert cfg.places["dargett-brewpub"].source_keys() == ["buyam:dargett-brewpub"]
    assert cfg.places["beer-city"].source_keys() == ["beercity:beer-city"]
    assert cfg.places["beer-city"].venue_id is None


def test_breweries(cfg):
    first = cfg.breweries[0]
    assert first == Brewery(id="dargett", name="Dargett", brewery_id=265165, slug="dargett-brewery",
                            list_enabled=False)
    assert first.url == "https://untappd.com/brewery/265165"
    assert [b.brewery_id for b in cfg.breweries] == [
        265165, 441775, 573921, 518994, 559009, 143586, 520321, 321115]
    assert cfg.breweries[3].name == "379 Torch & Brew"
    assert [b.slug for b in cfg.breweries] == [
        "dargett-brewery", "dors-craft-beer", "pulpulak-craft-beer", "379-torch-and-brew",
        "dahook", "beer-academy", "bever-brewery", "tovmas-brewery"]


def test_settings(cfg):
    assert cfg.settings == Settings(preview_digests=2, digest_time=time(17, 0), digest_max_lines=15,
                                    hot_rating=3.75, untappd_daily_pages=30)
    assert cfg.settings.digest_time == time(17, 0)


def test_disabled_place_is_excluded(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL))
    assert list(cfg.places) == ["gargoyle"]
    assert cfg.place_by_venue(11284746) is None
    assert cfg.breweries == ()
    assert cfg.settings == Settings()


@pytest.mark.parametrize("old, new", [
    ("kind: bar", "kind: pub"),                                              # unknown kind
    ("untappd_menu:", "untappd_venue:"),                                     # unknown source name
    ("id: tuf", "id: gargoyle"),                                             # duplicate place id
    ("{slug: gargoyle-bar, venue_id: 12252462}", "{slug: gargoyle-bar}"),     # missing venue_id
    ("venue_id: 12252462", 'venue_id: "12252462"'),                           # wrong type
    ("enabled: false", "enabled: nope"),                                     # not a bool
    ("name: Tuf", "name: Tuf\n    untappd_slug: tuf"),                       # unknown place field
    ("id: tuf", "id: Tap Station"),                                          # bad id
    ("sources:\n      untappd_menu: {slug: gargoyle-bar, venue_id: 12252462}", "sources: {}"),  # no sources
])
def test_invalid_place_raises(tmp_path, old, new):
    assert old in MINIMAL
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, MINIMAL.replace(old, new, 1)))


@pytest.mark.parametrize("extra", [
    "settings:\n  digest_time: 17:00\n",            # unquoted: YAML reads it as 1020
    'settings:\n  digest_time: "25:00"\n',
    "settings:\n  digest_tme: \"18:00\"\n",          # typo
    "breweries:\n  - {id: dors, name: Dors}\n",      # missing brewery_id
    "breweries:\n  - {id: dors, name: Dors, brewery_id: 441775}\n",   # missing slug
])
def test_invalid_settings_or_breweries_raise(tmp_path, extra):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, MINIMAL + extra))


@pytest.mark.parametrize("text", ["", "places: [\n", "- just a list\n"])
def test_broken_file_raises(tmp_path, text):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, text))


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.yaml")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 120 .venv/bin/pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taps.config'`

- [ ] **Step 3: Write places.yaml**

Create `places.yaml` (17 мест, 8 пивоварен с `list_enabled: false`, настройки; в шапке комментарий для Олега о том, как править файл):
```yaml
# Места, пивоварни и настройки «Ереван на кранах».
# Править можно прямо на GitHub: значок карандаша → правка → «Commit changes».
# Следующий прогон подхватит изменения. Если файл сломан, прогон остановится с ошибкой.
#
# Выключить место: enabled: false (блок не удаляйте). Включить обратно: enabled: true.
# Новое место: скопируйте блок похожего места, поменяйте id, name, slug и venue_id
#   (они видны в адресе untappd.com/v/<slug>/<venue_id>). Первый прогон по нему пройдёт молча.
# id — строчная латиница, цифры и дефис. id существующего места не меняйте: бот примет его за новое.
# Источники: untappd_menu и untappd_checkins {slug, venue_id}, buyam {url}, beercity / yerevan_city / parma {}.
# Пивоварни: list_enabled: true (список сортов, 🏭) — только после проверки на первом ручном прогоне.
# Числа-названия ("379") и время ("17:00") пишите в кавычках.

places:
  - id: gargoyle
    name: Gargoyle Bar
    kind: bar
    enabled: true
    sources:
      untappd_menu: {slug: gargoyle-bar, venue_id: 12252462}

  - id: beatles
    name: Beatles Pub
    kind: bar
    enabled: true
    sources:
      untappd_menu: {slug: beatles-pub-yerevan, venue_id: 2162817}

  - id: dargett-brewpub
    name: Dargett Brewpub
    kind: brewpub
    enabled: true
    brewery_id: 265165
    brewery_name: Dargett
    untappd_venue_id: 4640403
    sources:
      buyam: {url: "https://buy.am/en/restaurants/dargett"}

  - id: dors
    name: Dors Craft Beer & Kitchen
    kind: brewpub
    enabled: true
    brewery_id: 441775
    brewery_name: Dors
    sources:
      untappd_checkins: {slug: dors-craft-beer-kitchen, venue_id: 9312556}

  - id: pulpulak
    name: Pulpulak Craft Beer & Kitchen
    kind: brewpub
    enabled: true
    brewery_id: 573921
    brewery_name: Pulpulak
    sources:
      untappd_checkins: {slug: pulpulak-craft-beer-kitchen, venue_id: 12734409}

  - id: 379-torch-brew
    name: "379 Torch & Brew"
    kind: brewpub
    enabled: true
    brewery_id: 518994
    brewery_name: "379"
    sources:
      untappd_checkins: {slug: 379-torch-and-brew, venue_id: 11198162}

  - id: tap-station
    name: Tap Station
    kind: bar
    enabled: true
    sources:
      untappd_checkins: {slug: tap-station, venue_id: 8234456}

  - id: tuf
    name: Tuf
    kind: bar
    enabled: true
    sources:
      untappd_checkins: {slug: tuf, venue_id: 11284746}

  - id: izh
    name: Izh
    kind: bar
    enabled: true
    sources:
      untappd_checkins: {slug: izh, venue_id: 12246294}

  - id: beer-point
    name: Beer Point Pub
    kind: bar
    enabled: true
    sources:
      untappd_checkins: {slug: beer-point-pub, venue_id: 12348951}

  - id: ker-u-sus
    name: KER U SUS
    kind: bar
    enabled: true
    sources:
      untappd_checkins: {slug: ker-u-sus, venue_id: 13968261}

  - id: tavern
    name: Tavern Yerevan
    kind: bar
    enabled: true
    sources:
      untappd_checkins: {slug: tavern-yerevan-khorenatsi, venue_id: 13298511}

  - id: shame
    name: Shame Bar
    kind: bar
    enabled: true
    sources:
      untappd_checkins: {slug: shame-bar, venue_id: 9324104}

  - id: punk-photo
    name: Punk Photo Bar
    kind: bar
    enabled: true
    sources:
      untappd_checkins: {slug: punk-photo-bar, venue_id: 12455977}

  - id: beer-city
    name: Beer City
    kind: shop
    enabled: true
    sources:
      beercity: {}

  - id: yerevan-city
    name: Yerevan City
    kind: shop
    enabled: true
    sources:
      yerevan_city: {}

  - id: parma
    name: Parma
    kind: shop
    enabled: true
    sources:
      parma: {}

# slug: из адреса списка сортов https://untappd.com/w/<slug>/<brewery_id>/beer — проверить при запуске
breweries:
  - {id: dargett, name: Dargett, brewery_id: 265165, slug: dargett-brewery, list_enabled: false}
  - {id: dors, name: Dors, brewery_id: 441775, slug: dors-craft-beer, list_enabled: false}
  - {id: pulpulak, name: Pulpulak, brewery_id: 573921, slug: pulpulak-craft-beer, list_enabled: false}
  - {id: 379-torch-brew, name: "379 Torch & Brew", brewery_id: 518994, slug: 379-torch-and-brew, list_enabled: false}
  - {id: dahook, name: Dahook, brewery_id: 559009, slug: dahook, list_enabled: false}
  - {id: beer-academy, name: Beer Academy, brewery_id: 143586, slug: beer-academy, list_enabled: false}
  - {id: bever, name: Bever, brewery_id: 520321, slug: bever-brewery, list_enabled: false}
  - {id: tovmas, name: Tovmas, brewery_id: 321115, slug: tovmas-brewery, list_enabled: false}

settings:
  preview_digests: 2
  digest_time: "17:00"        # раньше вечернего запуска (18:17 по Еревану)
  digest_max_lines: 15
  hot_rating: 3.75
  untappd_daily_pages: 30
```

- [ ] **Step 4: Write minimal implementation**

Create `taps/config.py`:
```python
"""places.yaml loader: places, breweries and settings."""
import re
from dataclasses import dataclass
from datetime import time
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
PLACE_FIELDS = ("id", "name", "kind", "sources", "enabled", "brewery_id", "brewery_name", "untappd_venue_id")
BREWERY_FIELDS = ("id", "name", "brewery_id", "slug", "list_enabled")
SETTINGS_FIELDS = ("preview_digests", "digest_time", "digest_max_lines", "hot_rating", "untappd_daily_pages")
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

    def source_keys(self) -> list[str]:
        return [f"{s}:{self.id}" for s in self.sources]

    @property
    def has_menu(self) -> bool:
        return "untappd_menu" in self.sources or "buyam" in self.sources

    @property
    def venue_id(self) -> int | None:
        for name in ("untappd_menu", "untappd_checkins"):
            if name in self.sources:
                return self.sources[name]["venue_id"]
        return self.untappd_venue_id


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
    untappd_daily_pages: int = 30


@dataclass(frozen=True)
class Config:
    places: Mapping[str, Place]  # enabled places only, in file order
    breweries: tuple[Brewery, ...]
    settings: Settings

    def place_by_venue(self, venue_id: int) -> Place | None:
        return next((p for p in self.places.values() if p.venue_id == venue_id), None)


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


def _sources(raw: Any, where: str) -> dict[str, dict[str, Any]]:
    sources = {}
    for name, params in _mapping(raw, where).items():
        if name not in SOURCE_PARAMS:
            raise _fail(where, f"неизвестный источник {name!r}, есть: {', '.join(SOURCE_PARAMS)}")
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
    return Place(
        id=pid,
        name=_get(d, "name", str, where),
        kind=kind,
        sources=sources,
        enabled=_get(d, "enabled", bool, where, True),
        brewery_id=_get(d, "brewery_id", int, where, None),
        brewery_name=_get(d, "brewery_name", str, where, None),
        untappd_venue_id=_get(d, "untappd_venue_id", int, where, None),
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
    return Settings(
        preview_digests=_get(d, "preview_digests", int, "settings", s.preview_digests),
        digest_time=parsed_time,
        digest_max_lines=_get(d, "digest_max_lines", int, "settings", s.digest_max_lines),
        hot_rating=float(_get(d, "hot_rating", (int, float), "settings", s.hot_rating)),
        untappd_daily_pages=_get(d, "untappd_daily_pages", int, "settings", s.untappd_daily_pages),
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
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `timeout 120 .venv/bin/pytest tests/test_config.py -v`
Expected: `26 passed`

- [ ] **Step 6: Commit**

```bash
git add places.yaml taps/config.py tests/test_config.py
git commit -m "feat: add places.yaml and strict config loader"
```

---

### Task 3: Файл правок и источник «со слов»

**Files:**
- Create: `taps/corrections.py`
- Create: `corrections.yaml`
- Create: `taps/sources/manual.py`
- Test: `tests/test_corrections.py`
- Test: `tests/test_manual.py`

**Interfaces:**
- Consumes: `taps.model.Sighting`, `taps.model.SourceResult`, `taps.model.u_key(beer_id) -> str`, `taps.model.untappd_n_key(brewery, name, brewery_aliases) -> str`; `taps.timeutil.to_yerevan(dt) -> datetime`; `taps.config.Config` (`places`: только включённые места), `taps.config.Place`, `taps.config.Settings`, `taps.config.load_config(path)` (только в тесте настоящего файла).
- Produces: `ManualEntry(id, place, brewery, beer, untappd_id, by, date)`; `Corrections(sightings=(), hide=frozenset(), aliases={}, brewery_aliases={}, not_craft=())`; `CorrectionsLoad(corrections, raw, errors, used_snapshot)`; `parse_corrections(raw: dict, place_ids) -> tuple[Corrections, list[str]]` (при сломанной структуре бросает `ValueError`); `load_corrections(path: Path, snapshot: dict | None, place_ids) -> CorrectionsLoad`; `taps.sources.manual.MANUAL_KEEP_DAYS = 14`; `manual_result(corrections: Corrections, config: Config, now: datetime) -> SourceResult` (key `"manual"`, source `"manual"`).

Модуль читает `corrections.yaml` (спека §9): записи «со слов», `hide`, склейки ключей и написаний пивоварен, стоп-лист `not_craft` (§4.6). Ошибочная отдельная запись пропускается, и по ней пишется ошибка на русском. Если файл не читается целиком или сломана его структура, используется снимок из `state.json`, а без снимка прогон останавливается. `raw` приводится к виду, пригодному для JSON: даты становятся строками ISO, поэтому его можно сохранить как `corrections_snapshot`. Источник «со слов» (§4.7, §6) превращает записи за последние 14 дней по Еревану в `Sighting` и никогда не падает.

- [ ] **Step 1: Write the failing test for corrections**

Create `tests/test_corrections.py`:
```python
import json
from datetime import date
from pathlib import Path

import pytest
import yaml

from taps.config import load_config
from taps.corrections import Corrections, CorrectionsLoad, ManualEntry, load_corrections, parse_corrections

ROOT = Path(__file__).parent.parent
PLACE_IDS = {"tap-station", "gargoyle", "dors"}

SPEC_EXAMPLE = """
sightings:
  - place: tap-station
    brewery: "379"
    beer: "Hazy Pale"
    by: Аня
    date: 2026-09-24
  - place: dors
    untappd: 1234567
    by: Олег
    date: 2026-09-23
hide:
  - place: gargoyle
    beer: u:3539672
aliases:
  "n:konix bronx": u:3539672
brewery_aliases:
  "v engelman": "volfas engelman"
not_craft:
  - Kilikia
"""

HAZY = ManualEntry(
    id="tap-station|2026-09-24|Аня", place="tap-station", brewery="379", beer="Hazy Pale",
    untappd_id=None, by="Аня", date=date(2026, 9, 24),
)
BY_ID = ManualEntry(
    id="dors|2026-09-23|Олег", place="dors", brewery=None, beer=None,
    untappd_id=1234567, by="Олег", date=date(2026, 9, 23),
)

# spec §4.6, alternate spellings as separate entries
SPEC_NOT_CRAFT = {
    "Kotayk", "Kotayq", "Gyumri", "Kilikia", "Ararat", "Alexandrapol", "Aleksandrapol", "Erebuni",
    "Dilijan", "Debed", "Lincoln",
    "Baltika", "Zhigulevskoe", "Zhiguli", "Zolotaya Bochka", "Beliy Medved", "Motor", "Zatecky Gus",
    "Kozel", "Lvivske", "Vimpel", "Brander Bier", "Platina Latina",
    "Heineken", "Stella Artois", "Corona", "Bud", "Budweiser", "Miller", "Carlsberg", "Tuborg", "Efes",
    "Amstel", "Hoegaarden", "Estrella Damm", "Kuler", "Almaza", "Peroni", "Grolsch", "Kronenbourg",
    "Asahi", "Modelo", "Pilsner Urquell", "Birra Moretti", "Holsten", "Old Prague", "Dragon",
    "Krusovice", "Newcastle",
    "Natakhtari", "Kazbegi", "Zedazeni",
    "Pražečka", "Staročeské", "Santanos",
}


def write(tmp_path, text):
    path = tmp_path / "corrections.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def parse(text):
    return parse_corrections(yaml.safe_load(text), PLACE_IDS)


def good_snapshot(tmp_path):
    raw = load_corrections(write(tmp_path, SPEC_EXAMPLE), None, PLACE_IDS).raw
    return json.loads(json.dumps(raw, ensure_ascii=False))  # as stored in state.json


def test_parse_valid_file(tmp_path):
    load = load_corrections(write(tmp_path, SPEC_EXAMPLE), None, PLACE_IDS)
    assert load.errors == []
    assert load.used_snapshot is False
    assert load.corrections == Corrections(
        sightings=(HAZY, BY_ID),
        hide=frozenset({("gargoyle", "u:3539672")}),
        aliases={"n:konix bronx": "u:3539672"},
        brewery_aliases={"v engelman": "volfas engelman"},
        not_craft=("Kilikia",),
    )


def test_raw_is_json_safe_and_parses_back(tmp_path):
    load = load_corrections(write(tmp_path, SPEC_EXAMPLE), None, PLACE_IDS)
    assert load.raw["sightings"][0]["date"] == "2026-09-24"  # YAML date -> ISO string
    assert parse_corrections(json.loads(json.dumps(load.raw)), PLACE_IDS) == (load.corrections, [])


def test_parse_accepts_yaml_date_objects():
    corrections, errors = parse(SPEC_EXAMPLE)
    assert errors == []
    assert corrections.sightings == (HAZY, BY_ID)


def test_sighting_with_untappd_id_only():
    corrections, errors = parse("sightings:\n  - {place: dors, untappd: 1234567, by: Олег, date: 2026-09-23}\n")
    assert errors == []
    assert corrections.sightings == (BY_ID,)


def test_numbers_are_accepted_as_text():
    corrections, errors = parse(
        "sightings:\n  - {place: tap-station, brewery: 379, beer: Hazy Pale, by: Аня, date: 2026-09-24}\n"
        "not_craft: [1795]\n"
    )
    assert errors == []
    assert corrections.sightings == (HAZY,)
    assert corrections.not_craft == ("1795",)


def test_unknown_place_skips_entry_with_russian_error():
    corrections, errors = parse(
        "sightings:\n"
        "  - {place: nowhere, beer: X, by: Аня, date: 2026-09-24}\n"
        "  - {place: tap-station, brewery: '379', beer: Hazy Pale, by: Аня, date: 2026-09-24}\n"
        "hide:\n"
        "  - {place: nowhere, beer: 'u:1'}\n"
        "  - {place: gargoyle, beer: 'u:2'}\n"
    )
    assert corrections.sightings == (HAZY,)
    assert corrections.hide == frozenset({("gargoyle", "u:2")})
    assert len(errors) == 2
    assert errors[0].startswith("corrections.yaml, sightings (place: nowhere")
    assert errors[1].startswith("corrections.yaml, hide (place: nowhere")
    assert all("место 'nowhere' не найдено" in e for e in errors)


@pytest.mark.parametrize(
    "entry, fragment",
    [
        ("{place: tap-station, beer: X, by: Аня}", "нет поля date"),
        ("{place: tap-station, beer: X, date: 2026-09-24}", "нет поля by"),
        ("{place: tap-station, beer: X, by: '  ', date: 2026-09-24}", "нет поля by"),
        ("{beer: X, by: Аня, date: 2026-09-24}", "нет поля place"),
        ("{place: tap-station, beer: X, by: Аня, date: 24.09.2026}", "нужна дата ГГГГ-ММ-ДД"),
        ("{place: tap-station, beer: X, by: Аня, date: 2026-09-24 18:00:00}", "нужна дата ГГГГ-ММ-ДД"),
        ("{place: tap-station, brewery: '379', by: Аня, date: 2026-09-24}", "нужно beer"),
        ("{place: tap-station, untappd: abc, by: Аня, date: 2026-09-24}", "untappd 'abc'"),
        ("{place: tap-station, untappd: 0, by: Аня, date: 2026-09-24}", "untappd 0"),
        ("{place: tap-station, untappd: true, by: Аня, date: 2026-09-24}", "untappd True"),
        ("{place: tap-station, beer: [X], by: Аня, date: 2026-09-24}", "beer ['X']: ожидался текст"),
        ("{place: tap-station, bear: X, by: Аня, date: 2026-09-24}", "неизвестные поля: bear"),
        ("{place: tap-station, beer: Пиво, by: Аня, date: 2026-09-24}", "не получается ключ пива"),
        ("tap-station", "запись должна быть вида"),
    ],
)
def test_bad_sighting_is_skipped(entry, fragment):
    corrections, errors = parse(f"sightings:\n  - {entry}\n")
    assert corrections.sightings == ()
    assert len(errors) == 1
    assert errors[0].startswith("corrections.yaml, sightings (")
    assert fragment in errors[0]


def test_bad_hide_alias_and_brand_entries_are_skipped():
    corrections, errors = parse(
        "hide:\n"
        "  - {place: gargoyle, beer: 3539672}\n"
        "  - {place: gargoyle}\n"
        "  - {place: gargoyle, beer: 'n:konix bronx'}\n"
        "aliases:\n"
        "  konix bronx: 'u:1'\n"
        "  'n:x': 5\n"
        "  'n:konix bronx': 'u:3539672'\n"
        "brewery_aliases:\n"
        "  'v engelman':\n"
        "  'bb': 'beer brothers'\n"
        "not_craft: [Kilikia, [Baltika], '  ']\n"
    )
    assert corrections.hide == frozenset({("gargoyle", "n:konix bronx")})
    assert corrections.aliases == {"n:konix bronx": "u:3539672"}
    assert corrections.brewery_aliases == {"bb": "beer brothers"}
    assert corrections.not_craft == ("Kilikia",)
    assert len(errors) == 7
    assert sum("не ключ пива" in e for e in errors) == 3
    assert any("нет поля beer" in e for e in errors)
    assert all(e.startswith("corrections.yaml, ") for e in errors)


def test_parse_rejects_broken_structure():
    for raw in (["x"], {"hides": []}, {"hide": {"place": "gargoyle"}}, {"aliases": ["n:a"]}):
        with pytest.raises(ValueError):
            parse_corrections(raw, PLACE_IDS)


@pytest.mark.parametrize(
    "text, fragment",
    [
        ("sightings:\n  - place: tap-station\n  beer: X\n", "ошибка YAML в строке"),
        ("hide:\n  place: gargoyle\n  beer: 'u:1'\n", "раздел hide должен быть списком"),
        ("hides: []\n", "неизвестные разделы: hides"),
        ("- Kilikia\n", "ожидался словарь разделов"),
    ],
)
def test_broken_file_uses_snapshot(tmp_path, text, fragment):
    snapshot = good_snapshot(tmp_path)
    load = load_corrections(write(tmp_path, text), snapshot, PLACE_IDS)
    assert load.used_snapshot is True
    assert load.raw is None
    assert load.corrections == Corrections(
        sightings=(HAZY, BY_ID),
        hide=frozenset({("gargoyle", "u:3539672")}),
        aliases={"n:konix bronx": "u:3539672"},
        brewery_aliases={"v engelman": "volfas engelman"},
        not_craft=("Kilikia",),
    )
    assert len(load.errors) == 1
    assert load.errors[0].startswith("corrections.yaml не читается")
    assert fragment in load.errors[0]
    assert "последней удачной версии" in load.errors[0]


def test_snapshot_entries_are_checked_against_current_places(tmp_path):
    snapshot = good_snapshot(tmp_path)
    load = load_corrections(write(tmp_path, "hides: []\n"), snapshot, {"tap-station", "gargoyle"})
    assert load.corrections.sightings == (HAZY,)
    assert len(load.errors) == 2
    assert "место 'dors' не найдено" in load.errors[1]


def test_broken_file_without_snapshot_stops_the_run(tmp_path):
    load = load_corrections(write(tmp_path, "sightings: [\n"), None, PLACE_IDS)
    assert load.corrections is None
    assert load.raw is None
    assert load.used_snapshot is False
    assert len(load.errors) == 1
    assert "прогон остановлен" in load.errors[0]


def test_broken_file_and_broken_snapshot_stop_the_run(tmp_path):
    load = load_corrections(write(tmp_path, "sightings: [\n"), {"hide": "oops"}, PLACE_IDS)
    assert load.corrections is None
    assert load.used_snapshot is False
    assert "снимок в state.json тоже не читается" in load.errors[0]
    assert "прогон остановлен" in load.errors[0]


def test_missing_or_empty_file_is_empty_corrections(tmp_path):
    empty = CorrectionsLoad(corrections=Corrections(), raw={}, errors=[], used_snapshot=False)
    assert load_corrections(tmp_path / "corrections.yaml", {"not_craft": ["X"]}, PLACE_IDS) == empty
    assert load_corrections(write(tmp_path, "# только комментарии\n"), None, PLACE_IDS) == empty


def test_real_corrections_file_loads_clean():
    place_ids = set(load_config(ROOT / "places.yaml").places)
    load = load_corrections(ROOT / "corrections.yaml", None, place_ids)
    assert load.errors == []
    c = load.corrections
    assert (c.sightings, c.hide, c.aliases) == ((), frozenset(), {})
    assert c.brewery_aliases == {"v engelman": "volfas engelman"}
    assert "Kilikia" in c.not_craft and "Baltika" in c.not_craft
    assert set(c.not_craft) == SPEC_NOT_CRAFT
    assert len(c.not_craft) == len(SPEC_NOT_CRAFT)  # no duplicates
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 120 .venv/bin/pytest tests/test_corrections.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taps.corrections'`

- [ ] **Step 3: Write the corrections loader**

Create `taps/corrections.py`:
```python
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
```

- [ ] **Step 4: Create the real corrections file**

Create `corrections.yaml` (шапка с примерами из §9 в комментариях, пустые `sightings`/`hide`/`aliases`, одна склейка пивоварни, полный стоп-лист из §4.6):
```yaml
# Правки «Ереван на кранах»: друзья пишут Олегу, Олег правит этот файл.
# Править можно прямо на GitHub: значок карандаша → правка → «Commit changes». Следующий прогон подхватит изменения.
# Если файл сломан (ошибка YAML), бот работает по последней удачной версии и пишет Олегу.
# Ошибочная запись (неизвестное место, нет даты или автора) пропускается, Олегу приходит предупреждение.
# Примеры ниже: скопируйте строки под нужный раздел и уберите # в начале.
# place — id места из places.yaml. Ключ пива: u:<число из адреса пива на Untappd> или n:<название> (n:-ключ спросите у Claude).
#
# sightings — ✍️ «со слов»: друг видел пиво в месте. В сводку попадает, если date не старше 3 дней; на сайте держится 14 дней.
#   Вместо brewery и beer можно написать untappd: 1234567.
#   Несколько сортов из одного похода — несколько записей с одинаковыми place, date и by.
#   Исправить название в уже объявленной записи можно, повторно она не объявится.
#   - place: tap-station
#     brewery: "379"
#     beer: "Hazy Pale"
#     by: Аня
#     date: 2026-09-24
#
# hide — «этого уже нет» или мусор: строка пропадает с сайта.
#   - place: gargoyle
#     beer: u:3539672
#
# aliases — одно и то же пиво из разных источников, "ключ из магазина": ключ Untappd. Новинок в чат не даёт.
#   "n:konix bronx": u:3539672
#
# brewery_aliases — разные написания одной пивоварни, "как пишут": "как правильно".
# not_craft — бренды, которые не показывать в магазинах. Сравнивается целым словом: Bud не прячет Budvar.
#   Если в названии есть стиль (IPA, Stout, Porter…), пиво показывается всё равно.

sightings:

hide:

aliases:

brewery_aliases:
  "v engelman": "volfas engelman"

not_craft:
  # Армения
  - Kotayk
  - Kotayq
  - Gyumri
  - Kilikia
  - Ararat
  - Alexandrapol
  - Aleksandrapol
  - Erebuni
  - Dilijan
  - Debed
  - Lincoln
  # Россия и СНГ
  - Baltika
  - Zhigulevskoe
  - Zhiguli
  - Zolotaya Bochka
  - Beliy Medved
  - Motor
  - Zatecky Gus
  - Kozel
  - Lvivske
  - Vimpel
  - Brander Bier
  - Platina Latina
  # Международные лагеры
  - Heineken
  - Stella Artois
  - Corona
  - Bud
  - Budweiser
  - Miller
  - Carlsberg
  - Tuborg
  - Efes
  - Amstel
  - Hoegaarden
  - Estrella Damm
  - Kuler
  - Almaza
  - Peroni
  - Grolsch
  - Kronenbourg
  - Asahi
  - Modelo
  - Pilsner Urquell
  - Birra Moretti
  - Holsten
  - Old Prague
  - Dragon
  - Krusovice
  - Newcastle
  # Грузия
  - Natakhtari
  - Kazbegi
  - Zedazeni
  # Чехия
  - Pražečka
  - Staročeské
  - Santanos
```

- [ ] **Step 5: Run test to verify it passes**

Run: `timeout 120 .venv/bin/pytest tests/test_corrections.py -v`
Expected: `31 passed`

- [ ] **Step 6: Write the failing test for the manual source**

Create `tests/test_manual.py`:
```python
from datetime import date, datetime, timezone

from taps.config import Config, Place, Settings
from taps.corrections import Corrections, ManualEntry
from taps.sources.manual import MANUAL_KEEP_DAYS, manual_result

NOW = datetime(2026, 9, 23, 21, 30, tzinfo=timezone.utc)  # 2026-09-24 01:30 in Yerevan


def place(pid, kind="bar"):
    return Place(id=pid, name=pid.title(), kind=kind, sources={"untappd_checkins": {"slug": pid, "venue_id": 1}})


CONFIG = Config(
    places={p.id: p for p in (place("tap-station"), place("dors", "brewpub"))},
    breweries=(),
    settings=Settings(),
)


def entry(place="tap-station", day=date(2026, 9, 24), by="Аня", brewery="379", beer="Hazy Pale", untappd=None):
    return ManualEntry(
        id=f"{place}|{day.isoformat()}|{by}", place=place, brewery=brewery, beer=beer,
        untappd_id=untappd, by=by, date=day,
    )


def run(*entries, **kwargs):
    return manual_result(Corrections(sightings=entries, **kwargs), CONFIG, NOW)


def test_no_entries_is_ok():
    result = run()
    assert (result.key, result.source, result.ok, result.error) == ("manual", "manual", True, None)
    assert result.sightings == []
    assert result.place_id is None
    assert result.full is True


def test_named_entry():
    [s] = run(entry()).sightings
    assert s.place_id == "tap-station"
    assert (s.source, s.kind) == ("manual", "manual")
    assert s.beer_key == "n:379 hazy pale"
    assert (s.title, s.name, s.brewery) == ("379 Hazy Pale", "Hazy Pale", "379")
    assert s.seen_at == NOW
    assert (s.manual_id, s.manual_by, s.manual_date) == ("tap-station|2026-09-24|Аня", "Аня", "2026-09-24")
    assert (s.untappd_beer_id, s.url) == (None, None)


def test_entry_without_brewery():
    [s] = run(entry(brewery=None, beer="Hazy Pale")).sightings
    assert s.beer_key == "n:hazy pale"
    assert s.title == "Hazy Pale"


def test_untappd_entry_uses_u_key():
    [by_id, named] = run(
        entry(place="dors", by="Олег", brewery=None, beer=None, untappd=1234567),
        entry(untappd=7654321),
    ).sightings
    assert by_id.beer_key == "u:1234567"
    assert by_id.untappd_beer_id == 1234567
    assert by_id.url == "https://untappd.com/beer/1234567"
    assert by_id.name == "Untappd #1234567"
    assert by_id.manual_id == "dors|2026-09-24|Олег"
    assert (named.beer_key, named.name) == ("u:7654321", "Hazy Pale")


def test_brewery_aliases_apply_to_n_key():
    [s] = run(entry(brewery="V.Engelman", beer="IPA"), brewery_aliases={"v engelman": "volfas engelman"}).sightings
    assert s.beer_key == "n:volfas engelman ipa"
    assert s.brewery == "V.Engelman"


def test_only_last_14_yerevan_days():
    assert MANUAL_KEEP_DAYS == 14
    days = [date(2026, 9, 24), date(2026, 9, 10), date(2026, 9, 9), date(2026, 9, 25)]
    result = run(*(entry(day=d) for d in days))
    # today in Yerevan is 2026-09-24 (UTC is still 09-23); 09-10 is 14 days ago; 09-09 is too old; 09-25 is future
    assert [s.manual_date for s in result.sightings] == ["2026-09-24", "2026-09-10"]
    assert result.ok is True


def test_all_entries_outside_window_is_still_ok():
    result = run(entry(day=date(2026, 8, 1)))
    assert result.ok is True
    assert result.sightings == []


def test_place_not_in_config_is_skipped():
    result = run(entry(place="tuf"), entry(place="dors"))
    assert [s.place_id for s in result.sightings] == ["dors"]
```

- [ ] **Step 7: Run test to verify it fails**

Run: `timeout 120 .venv/bin/pytest tests/test_manual.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taps.sources.manual'`

- [ ] **Step 8: Write the manual source**

Create `taps/sources/manual.py`:
```python
"""Source ✍️ "со слов": sightings entered by hand in corrections.yaml."""
from datetime import datetime

from taps.config import Config
from taps.corrections import Corrections
from taps.model import Sighting, SourceResult, u_key, untappd_n_key
from taps.timeutil import to_yerevan

MANUAL_KEEP_DAYS = 14


def manual_result(corrections: Corrections, config: Config, now: datetime) -> SourceResult:
    """Entries at enabled places dated 0..MANUAL_KEEP_DAYS Yerevan days ago; future dates wait for their day."""
    today = to_yerevan(now).date()
    sightings = []
    for e in corrections.sightings:
        if e.place not in config.places or not 0 <= (today - e.date).days <= MANUAL_KEEP_DAYS:
            continue
        if e.untappd_id is not None:
            key, url = u_key(e.untappd_id), f"https://untappd.com/beer/{e.untappd_id}"
        else:
            key, url = untappd_n_key(e.brewery, e.beer, corrections.brewery_aliases), None
        name = e.beer or f"Untappd #{e.untappd_id}"
        sightings.append(Sighting(
            place_id=e.place, source="manual", beer_key=key,
            title=" ".join(filter(None, (e.brewery, name))), name=name, seen_at=now,
            brewery=e.brewery, untappd_beer_id=e.untappd_id, url=url,
            manual_id=e.id, manual_by=e.by, manual_date=e.date.isoformat(),
        ))
    return SourceResult(key="manual", source="manual", ok=True, sightings=sightings)
```

- [ ] **Step 9: Run both tests to verify they pass**

Run: `timeout 120 .venv/bin/pytest tests/test_corrections.py tests/test_manual.py -v`
Expected: `39 passed`

- [ ] **Step 10: Commit**

```bash
git add taps/corrections.py corrections.yaml taps/sources/manual.py tests/test_corrections.py tests/test_manual.py
git commit -m "feat: load corrections.yaml with snapshot fallback and add manual sightings source"
```

---

### Task 4: Фильтр «интересное пиво» для магазинов

**Files:**
- Create: `taps/shop_filter.py`
- Test: `tests/test_shop_filter.py`

**Interfaces:**
- Consumes: `taps.model.normalize_base(text: str) -> str` (Task 1: шаги 1–3 `normalize_title`, пробелы схлопнуты)
- Produces: `STYLE_RE`, `NONALC_RE`, `RADLER_RE`, `CIDER_COCKTAIL_RE` (скомпилированные `re.Pattern`, все с `re.I`); `brand_matches(text: str, brand: str) -> bool`; `classify(title: str, brand: str | None, not_craft: Sequence[str], category: str | None = None) -> tuple[bool, str]`, где reason — одно из `"cider_cocktail" | "nonalc" | "radler" | "style" | "not_craft" | "keep"`

Модуль решает, показывать ли товар магазина (Beer City, Yerevan City, Parma), по правилам фильтра из спеки §4.6. Проверки идут в таком порядке: сидр или коктейль по категории или названию, безалкогольное, радлер по словам вкуса, стиль в названии (оставляем даже массовый бренд), бренд из `not_craft` (целым словом, без учёта регистра и диакритики, проверяются и бренд, и название). Всё остальное оставляем. Таблица тестов построена на настоящих названиях из фикстур Parma, Yerevan City (nameEn и армянские имена) и Beer City. Отдельный тест проверяет, что каждое такое название действительно есть в фикстуре.

- [ ] **Step 1: Write the failing test**

Create `tests/test_shop_filter.py`:
```python
import html

import pytest

from taps.shop_filter import brand_matches, classify
from tests.helpers import fixture_json, fixture_text

# Initial not_craft list from spec 4.6 ("A/B" spellings split into two entries).
NOT_CRAFT = (
    # Armenia
    "Kotayk", "Kotayq", "Gyumri", "Kilikia", "Ararat", "Alexandrapol", "Aleksandrapol",
    "Erebuni", "Dilijan", "Debed", "Lincoln",
    # Russia and CIS
    "Baltika", "Zhigulevskoe", "Zhiguli", "Zolotaya Bochka", "Beliy Medved", "Motor",
    "Zatecky Gus", "Kozel", "Lvivske", "Vimpel", "Brander Bier", "Platina Latina",
    # International lagers
    "Heineken", "Stella Artois", "Corona", "Bud", "Budweiser", "Miller", "Carlsberg", "Tuborg",
    "Efes", "Amstel", "Hoegaarden", "Estrella Damm", "Kuler", "Almaza", "Peroni", "Grolsch",
    "Kronenbourg", "Asahi", "Modelo", "Pilsner Urquell", "Birra Moretti", "Holsten",
    "Old Prague", "Dragon", "Krusovice", "Newcastle",
    # Georgia
    "Natakhtari", "Kazbegi", "Zedazeni",
    # Czechia
    "Pražečka", "Staročeské", "Santanos",
)

YC_CIDER = "Low alcohol cocktails and cider"  # real categoryName, yerevan_city/by_category.json

# (fixture or None for crafted, title, brand, category, expected)
CASES = [
    # Parma listing (a.item_name > span) and a product page title
    ("parma", 'Beer "Paulaner Original" light 330ml', None, None, (True, "keep")),
    ("parma", 'Beer "Heineken" light 500ml', None, None, (False, "not_craft")),
    ("parma", 'Beer "Miller Genuine Draft Lager" light 330ml', None, None, (False, "not_craft")),
    ("parma", 'Beer "Bud" 330ml', None, None, (False, "not_craft")),
    ("parma", 'Beer "Baltika №9" light 450ml', None, None, (False, "not_craft")),
    ("parma", 'Beer "Zhiguli Barnoe Export" light 450ml', None, None, (False, "not_craft")),
    ("parma", 'Beer "Kotayk Moskovyan" light 250ml', None, None, (False, "not_craft")),
    ("parma", 'Beer "Estrella Damm" light 500ml', None, None, (False, "not_craft")),
    ("parma", 'Beer "Volfas Engelman Ipa" light 568ml', None, None, (True, "style")),
    ("parma", 'Beer "Birrificio Estense Calle San Miguel 426 Apa" light 500ml', None, None, (True, "style")),
    ("parma", 'Beer "Chimay Tripel Ale Peres Trappistes" light 330ml', None, None, (True, "style")),
    ("parma", 'Beer "Dargett Imperial Stout" dark 330ml', None, None, (True, "style")),
    ("parma", 'Beer "Corona Cero" light 330ml', None, None, (False, "nonalc")),
    ("parma", 'Beer "Dahook" light 330ml', "Dahook", None, (True, "keep")),
    ("parma", 'Beer "379" cherry, dark 330ml', None, None, (True, "keep")),
    ("parma", 'Beer "Warsteiner Naturradler Lemon" light 500ml', None, None, (False, "radler")),
    # Yerevan City Search nameEn
    ("yc", 'Beer "Kilikia" 1l', None, "Armenian beer", (False, "not_craft")),
    ("yc", 'Draft beer "Dilijan" 1l', None, "Armenian beer", (False, "not_craft")),
    ("yc", 'Beer "Budweiser" g/b 0.5l', None, "Imported beer", (False, "not_craft")),
    ("yc", 'Beer "Staroceske" light (can) 0.5l', None, "Imported beer", (False, "not_craft")),
    ("yc", 'Beer "Lowenbrau" Original (can) 0.45l', None, "Imported beer", (True, "keep")),
    ("yc", 'Beer "Guinness" Draught, dark (can) 0.44l', None, "Imported beer", (True, "keep")),
    ("yc", 'Beer "Heineken" non-alcoholic g/b 0.33l', None, "Imported beer", (False, "nonalc")),
    ("yc", "Beer ''Corona'' Zero light (can) 330ml", None, "Imported beer", (False, "nonalc")),
    ("yc", 'Beer "Baltika" grapefruit (can) 0.33l', None, "Imported beer", (False, "radler")),
    ("yc", 'Beer "Volfas Engelman" Ipa (can) 0.568l', None, "Imported beer", (True, "style")),
    ("yc", 'Beer "Volkovskaya pivovarnya" IPA g/b 0.45l', None, "Imported beer", (True, "style")),
    ("yc", 'Beer "Athanasius" Porter, dark g/b 0.5l', None, "Imported beer", (True, "style")),
    ("yc", 'Beer "Dargett" Belgian Tripel g/b 0.33l', None, "Armenian beer", (True, "style")),
    # Yerevan City Armenian names (items missing from Search)
    ("yc", "Գարեջրային ըմպելիք «Պաուլաներ» կիտրոն թ/տ 0.5լ", None, "Imported beer", (False, "radler")),
    ("yc", "Գարեջրային ըմպ. «Պրիմատոր» Չիփ. թուրինջ ա/տ 0.5լ", None, "Imported beer", (False, "radler")),
    ("yc", "Գարեջրային ըմպելիք «Կրոմբախեր» Ռադլեր թ/տ 0.5լ", None, "Imported beer", (False, "radler")),
    # a Blanche (witbier) sold as "beer drink" is kept
    ("yc", "Գարեջրային ըմպ. «Տրյոխգորնոե»Բլանշ,բաց ա/տ 0.45լ", None, "Imported beer", (True, "keep")),
    ("yc", "Գարեջուր ոչ ալկոհոլային «Հեյնեկեն» ա/տ 0.33լ", None, "Imported beer", (False, "nonalc")),
    ("yc", "Գարեջուր «Կորոնա» զերո, բաց ա/տ 330մլ", None, "Imported beer", (False, "nonalc")),
    ("yc", "Սիդր «Դառգետ» խնձորի, չոր ա/տ 0.33լ", None, YC_CIDER, (False, "cider_cocktail")),
    ("yc", "Թույլ ալկ.ըմպելիք «Ջեք Դենիելս» Կոկա Կոլա 0.33լ", None, YC_CIDER, (False, "cider_cocktail")),
    ("yc", "Սիդր «Ֆիզ» Պրեմիում, խնձոր թ/տ 0.5լ", None, None, (False, "cider_cocktail")),
    ("yc", "Կոկտեյլ «Շեյք» մոխիտո թ/տ 0.5լ", None, None, (False, "cider_cocktail")),
    # Beer City listings; brand "Konix" from the product pages
    ("beercity", 'Beer "Pure wave" IPA non alco 0.45 l', "Konix", None, (False, "nonalc")),
    ("beercity", 'Beer "Hard root" Double IPA 0.45 l', "Konix", None, (True, "style")),
    ("beercity", 'Beer "Corona cero 0%" 0.33l', None, None, (False, "nonalc")),
    ("beercity", 'Draught beer "Kellers" non-filtered 1l', None, None, (True, "keep")),
    ("beercity", 'Draught beer "Gyumri" 1l', None, None, (False, "not_craft")),
    ("beercity", 'Beer "Bronx" 0.5L', None, None, (True, "keep")),
    ("beercity", 'Пиво "379 American Wheat Ale" Citrus 0,33л', None, None, (True, "keep")),
    # crafted
    (None, 'Beer "Budvar" Original 0.5l', None, None, (True, "keep")),
    (None, 'Cider "Dargett" apple dry 0.33l', None, None, (False, "cider_cocktail")),
    (None, 'Beer "Dargett" IPA 0.33l', None, YC_CIDER, (False, "cider_cocktail")),
    (None, 'Beer drink "Blanche de Namur" witbier 0.33l', None, None, (True, "keep")),
    (None, 'Beer "Bronx" Grapefruit IPA 0.5L', None, None, (False, "radler")),
    (None, 'Beer "Clausthaler" alcohol-free 0.5l', None, None, (False, "nonalc")),
    (None, 'Beer "Krombacher" Radler alcohol-free 0.5l', None, None, (False, "nonalc")),
    (None, 'Cider "Somersby" non-alcoholic 0.33l', None, None, (False, "cider_cocktail")),
    (None, 'Beer "Bitburger" 0,0% 0.33l', None, None, (False, "nonalc")),
    (None, 'Пиво "Жигулёвское" безалкогольное 0.5л', None, None, (False, "nonalc")),
    (None, 'Beer "Saison Dupont" 0.33l', None, None, (True, "style")),
    (None, 'Beer "Original" 0.5l', "Heineken N.V.", None, (False, "not_craft")),
    (None, 'Beer "Kilikia" 1l', "Some Importer", None, (False, "not_craft")),
    (None, 'Beer "Weissbier" 0.5l', "Paulaner Brauerei", None, (True, "keep")),
]


@pytest.mark.parametrize("fixture, title, brand, category, expected", CASES)
def test_classify(fixture, title, brand, category, expected):
    assert classify(title, brand, NOT_CRAFT, category) == expected


def test_real_titles_come_from_fixtures():
    texts = {
        "parma": "\n".join(
            html.unescape(fixture_text(f"parma/{name}.html"))
            for name in ("list_p1", "list_p4", "product_1645")
        ),
        "beercity": "\n".join(
            html.unescape(fixture_json(f"beercity/{name}.json")["products"])
            for name in ("list_bottles_p1", "list_bottles_last", "list_draft_last")
        ),
        "yc": "\n".join(
            [p["nameEn"] for p in fixture_json("yerevan_city/search.json")["data"]["products"]]
            + [p["name"] for p in fixture_json("yerevan_city/by_category.json")["data"]["list"]]
        ),
    }
    categories = {p["categoryName"] for p in fixture_json("yerevan_city/by_category.json")["data"]["list"]}
    for fixture, title, _brand, category, _expected in CASES:
        if fixture:
            assert title in texts[fixture], title
        if fixture == "yc" and category:
            assert category in categories, category


def test_style_keeps_mass_brand_even_if_listed():
    extended = NOT_CRAFT + ("Volfas Engelman",)
    assert classify('Beer "Volfas Engelman Ipa" light 568ml', None, extended) == (True, "style")
    assert classify('Beer "Volfas Engelman Apa" light 568ml', None, extended) == (True, "style")
    assert classify('Beer "Volfas Engelman Blanc" light 568ml', None, extended) == (False, "not_craft")
    assert classify('Beer "Volfas Engelman Blanc" light 568ml', None, NOT_CRAFT) == (True, "keep")


def test_not_craft_is_deaccented_both_ways():
    assert classify('Beer "Lowenbrau" Original (can) 0.45l', None, ["Löwenbräu"]) == (False, "not_craft")
    assert classify('Beer "Original" 0.5l', "Löwenbräu", ["Lowenbrau"]) == (False, "not_craft")


def test_empty_not_craft_keeps_mass_brands():
    assert classify('Beer "Heineken" light 500ml', "Heineken", ()) == (True, "keep")


def test_blank_not_craft_entries_match_nothing():
    assert classify('Beer "Bronx" 0.5L', "Konix", ["", "   ", "!!", "0.5l"]) == (True, "keep")


@pytest.mark.parametrize(
    "text, brand, expected",
    [
        ('Beer "Budvar" Original 0.5l', "Bud", False),
        ('Beer "Budweiser" g/b 0.5l', "Bud", False),
        ('Beer "Bud" 330ml', "Bud", True),
        ('BEER "KILIKIA" 1L', "kilikia", True),
        ('Beer "Lowenbrau" Original (can) 0.45l', "Löwenbräu", True),
        ("Löwenbräu Original", "lowenbrau", True),
        ('Beer "Staroceske" light (can) 0.5l', "Staročeské", True),
        ('Beer "Baltika №9" light 450ml', "Baltika", True),
        ('Beer "Stella Artois" light 500ml', "Stella Artois", True),
        ('Beer "Stella" 0.5l', "Stella Artois", False),
        ('Beer "Old Rasputin" 0.33l', "Old Prague", False),
        ('Beer "Beer Brothers" IPA', "Beer Brothers", True),
        ("anything at all", "", False),
        ("", "", False),
        ("anything at all", " !! ", False),
    ],
)
def test_brand_matches(text, brand, expected):
    assert brand_matches(text, brand) is expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 120 .venv/bin/pytest tests/test_shop_filter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taps.shop_filter'`

- [ ] **Step 3: Write minimal implementation**

Create `taps/shop_filter.py`:
```python
"""Shop filter "interesting beer" (spec 4.6)."""
import re
from collections.abc import Sequence

from taps.model import normalize_base

STYLE_RE = re.compile(
    r"\b(ipa|apa|neipa|stout|porter|tripel|dubbel|quad|sour|gose|kriek|saison|imperial|barley ?wine|trappist)\b",
    re.I,
)
# "non alco" with a space: real Beer City title 'Beer "Pure wave" IPA non alco 0.45 l'
NONALC_RE = re.compile(r"(\bcero\b|\bzero\b|\b0[.,]0\b|non[- ]?alc|alcohol[- ]free|безалког|ոչ ալկ|զերո)", re.I)
# "Ռադլեր": real Yerevan City name "Գարեջրային ըմպելիք «Կրոմբախեր» Ռադլեր թ/տ 0.5լ"
RADLER_RE = re.compile(r"(radler|радлер|ռադլեր|lemon|grapefruit|лимон|грейпфрут|կիտրոն|թուրինջ)", re.I)
CIDER_COCKTAIL_RE = re.compile(r"(cider|cidre|сидр|cocktail|коктейл|սիդր|կոկտեյլ)", re.I)


def brand_matches(text: str, brand: str) -> bool:
    """True if brand occurs in text as whole tokens, ignoring case and accents."""
    needle = normalize_base(brand)
    return bool(needle) and f" {needle} " in f" {normalize_base(text)} "


def classify(title: str, brand: str | None, not_craft: Sequence[str], category: str | None = None) -> tuple[bool, str]:
    """(keep, reason); reason is cider_cocktail | nonalc | radler | style | not_craft | keep."""
    if CIDER_COCKTAIL_RE.search(category or "") or CIDER_COCKTAIL_RE.search(title):
        return False, "cider_cocktail"
    if NONALC_RE.search(title):
        return False, "nonalc"
    if RADLER_RE.search(title):
        return False, "radler"
    if STYLE_RE.search(title):
        return True, "style"
    for name in not_craft:
        if brand_matches(title, name) or (brand is not None and brand_matches(brand, name)):
            return False, "not_craft"
    return True, "keep"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `timeout 120 .venv/bin/pytest tests/test_shop_filter.py -v`
Expected: `80 passed`

- [ ] **Step 5: Commit**

```bash
git add tests/test_shop_filter.py taps/shop_filter.py
git commit -m "feat: add shop filter for interesting beer"
```

---

### Task 5: Состояние: state.json

**Files:**
- Create: `taps/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: `taps.timeutil.iso(dt) -> str`, `taps.timeutil.parse_iso(s) -> datetime`, `taps.timeutil.age_days(then, now) -> float` (Task 1).
- Produces: `PairRec`, `BeerRec`, `BreweryNewRec`, `SourceRec`, `UntappdRec`, `DigestRec`, `State` (with `State.source(key) -> SourceRec`, `State.pair(place_id, beer_key) -> PairRec | None`, `State.to_dict() -> dict`, `State.from_dict(d) -> State`), `empty_state(now) -> State`, `load_state(path, now) -> State`, `save_state(path, state) -> None`, `resolve_alias(key, aliases) -> str`, `apply_aliases(state, aliases) -> None`, `prune(state, now) -> int`. Additional constants: `MARKERS = ("baseline", "suppressed")`, `PAIR_KEEP_DAYS = 180`, `SOURCE_OK_DAYS = 30`, `ALIAS_MAX_HOPS = 5`.

Это память бота (спецификация §5 «state.json» и «Размер», §9 «Склейки применяются везде»). Здесь описаны записи состояния и детерминированная запись JSON: ключи отсортированы, армянский и русский текст хранится читаемым. Здесь же склейка ключей по `aliases`, которая никогда не создаёт событий, и чистка пар старше 180 дней, которая не трогает долго молчащий источник. Повреждённый `state.json` останавливает прогон и не сбрасывается молча, потому что сброс стёр бы отметки сводки и сломал бы гарантию «одна сводка в сутки».

- [ ] **Step 1: Write the failing test**

Create `tests/test_state.py`:
```python
import json
from datetime import datetime, timedelta, timezone

import pytest

from taps.state import (
    BeerRec, BreweryNewRec, DigestRec, PairRec, SourceRec, State, UntappdRec,
    apply_aliases, empty_state, load_state, prune, resolve_alias, save_state,
)
from taps.timeutil import iso

NOW = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)


def ago(days: float) -> str:
    return iso(NOW - timedelta(days=days))


def full_state() -> State:
    s = empty_state(NOW - timedelta(days=3))
    s.pairs = {
        "gargoyle": {"u:3539672": PairRec(
            first_seen=ago(2), last_seen=ago(0), event_at=ago(2), star=True, notified_at=ago(1),
            last_in_result=False, in_stock=None,
            info={"source": "untappd_menu", "name": "Black Sails", "brewery": "Загов&р", "abv": 11.0, "rating": 4.12},
        )},
        "yerevan-city": {"n:kilikia": PairRec(
            first_seen=ago(3), last_seen=ago(0), notified_at="suppressed", in_stock=True,
            info={"title": "Գարեջուր «Կիլիկիա» 1լ", "hidden": True},
        )},
    }
    s.beers = {"u:3539672": BeerRec(first_seen_city=ago(2), n_key="n:zagovor black sails"),
               "n:kilikia": BeerRec(first_seen_city=ago(3))}
    s.brewery_new = {"u:6000001": BreweryNewRec(
        brewery_id=265165, found_at=ago(1), star=True, notified_at=None,
        info={"name": "DDH NEIPA", "brewery": "Dargett", "abv": 6.5})}
    s.shop_items = {"yerevan-city": {"8811": "n:kilikia"}}
    s.sources = {"untappd_menu:gargoyle": SourceRec(
        baseline_done=True, last_ok=ago(0), last_full=ago(0), last_count=42, fail_streak=1,
        last_error="network", trip_streak=2, last_trip_keys=["u:1", "u:2"], seen_menu_ids=["101", "102"],
        max_beer_id=6000001, menu_updated_at=ago(1))}
    s.untappd = UntappdRec(last_attempt=ago(1), pages_today=12, pages_date="2026-09-22", brewery_list_cursor=3)
    s.digest = DigestRec(last_sent_date="2026-09-22", last_sent_at=ago(1), sent_count=2)
    s.alerts = {"failed:parma:parma": "0123456789ab"}
    s.corrections_snapshot = {"sightings": [{"place": "tap-station", "by": "Аня"}], "not_craft": ["Kilikia"]}
    s.announced_manual = ["tap-station|2026-09-22|Аня"]
    return s


# --- empty state, records, round trip -------------------------------------------------

def test_empty_state():
    s = empty_state(NOW)
    assert s.started_at == "2026-09-23T10:00:00+00:00"
    assert (s.pairs, s.beers, s.brewery_new, s.shop_items, s.sources, s.alerts) == ({}, {}, {}, {}, {}, {})
    assert s.untappd == UntappdRec(last_attempt=None, pages_today=0, pages_date=None, brewery_list_cursor=0)
    assert s.digest == DigestRec(last_sent_date=None, last_sent_at=None, sent_count=0)
    assert s.corrections_snapshot is None
    assert s.announced_manual == []
    other = empty_state(NOW)
    other.pairs["x"] = {}
    other.untappd.pages_today = 5
    assert s.pairs == {} and s.untappd.pages_today == 0      # no shared mutable defaults


def test_to_dict_is_plain_json_data():
    d = full_state().to_dict()
    assert d["pairs"]["gargoyle"]["u:3539672"]["notified_at"] == ago(1)
    assert d["pairs"]["yerevan-city"]["n:kilikia"]["info"]["hidden"] is True
    assert d["beers"]["u:3539672"] == {"first_seen_city": ago(2), "n_key": "n:zagovor black sails"}
    assert d["brewery_new"]["u:6000001"]["brewery_id"] == 265165
    assert d["sources"]["untappd_menu:gargoyle"]["last_trip_keys"] == ["u:1", "u:2"]
    assert d["untappd"]["pages_today"] == 12
    assert d["digest"]["sent_count"] == 2
    assert d["shop_items"] == {"yerevan-city": {"8811": "n:kilikia"}}
    assert json.loads(json.dumps(d)) == d


def test_to_dict_from_dict_round_trip_every_record_type():
    s = full_state()
    back = State.from_dict(json.loads(json.dumps(s.to_dict())))
    assert back == s
    assert isinstance(back.pairs["gargoyle"]["u:3539672"], PairRec)
    assert isinstance(back.beers["n:kilikia"], BeerRec)
    assert isinstance(back.brewery_new["u:6000001"], BreweryNewRec)
    assert isinstance(back.sources["untappd_menu:gargoyle"], SourceRec)
    assert isinstance(back.untappd, UntappdRec) and isinstance(back.digest, DigestRec)


def test_from_dict_does_not_share_input_objects():
    d = full_state().to_dict()
    s = State.from_dict(d)
    s.pairs["gargoyle"]["u:3539672"].info["name"] = "changed"
    s.shop_items["yerevan-city"]["9999"] = "n:x"
    assert d["pairs"]["gargoyle"]["u:3539672"]["info"]["name"] == "Black Sails"
    assert "9999" not in d["shop_items"]["yerevan-city"]


def test_from_dict_fills_missing_sections_and_fields_with_defaults():
    s = State.from_dict({"started_at": ago(0), "sources": {"parma:parma": {"last_ok": ago(1)}}})
    assert s.sources["parma:parma"] == SourceRec(last_ok=ago(1))
    assert s.pairs == {} and s.untappd == UntappdRec() and s.announced_manual == []


# --- save / load -------------------------------------------------------------------------

def test_save_state_writes_sorted_readable_json_and_load_reads_it_back(tmp_path):
    path = tmp_path / "state.json"
    s = full_state()
    save_state(path, s)
    text = path.read_text(encoding="utf-8")
    assert "Аня" in text and "Գարեջուր «Կիլիկիա» 1լ" in text and "Загов&р" in text
    assert "\\u" not in text
    top = [line for line in text.splitlines() if line.startswith(' "')]
    assert [line.split('"')[1] for line in top] == sorted(State.__dataclass_fields__)
    assert text.endswith("}\n")
    assert load_state(path, NOW) == s


def test_save_state_output_does_not_depend_on_insertion_order(tmp_path):
    a, b = empty_state(NOW), empty_state(NOW)
    rec1 = PairRec(first_seen=ago(1), last_seen=ago(1), info={"z": 1, "a": 2})
    rec2 = PairRec(first_seen=ago(2), last_seen=ago(2))
    a.pairs = {"gargoyle": {"u:2": rec1, "u:10": rec2}, "beatles": {}}
    b.pairs = {"beatles": {}, "gargoyle": {"u:10": rec2, "u:2": rec1}}
    save_state(tmp_path / "a.json", a)
    save_state(tmp_path / "b.json", b)
    assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes()


def test_load_state_missing_file_returns_empty_state(tmp_path):
    assert load_state(tmp_path / "state.json", NOW) == empty_state(NOW)


@pytest.mark.parametrize("content", ["", "\n", "  \n"])
def test_load_state_empty_file_returns_empty_state(tmp_path, content):
    path = tmp_path / "state.json"
    path.write_text(content, encoding="utf-8")
    assert load_state(path, NOW) == empty_state(NOW)


@pytest.mark.parametrize("content", [
    "{not json",
    "[]",
    '{"pairs": {}}',                                                     # no started_at
    '{"started_at": "2026-09-23T10:00:00+00:00", "digest": {"oops": 1}}',  # unknown field
    '{"started_at": "2026-09-23T10:00:00+00:00", "pairs": []}',
])
def test_load_state_corrupt_file_raises_instead_of_resetting(tmp_path, content):
    path = tmp_path / "state.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="state.json"):
        load_state(path, NOW)


# --- accessors -------------------------------------------------------------------------

def test_source_creates_rec_once():
    s = empty_state(NOW)
    rec = s.source("parma:parma")
    assert rec == SourceRec()
    assert s.sources == {"parma:parma": rec}
    rec.fail_streak = 2
    assert s.source("parma:parma") is rec
    assert s.source("parma:parma").fail_streak == 2


def test_pair_lookup():
    s = full_state()
    assert s.pair("gargoyle", "u:3539672").info["name"] == "Black Sails"
    assert s.pair("gargoyle", "u:1") is None
    assert s.pair("nowhere", "u:3539672") is None
    assert "nowhere" not in s.pairs


# --- aliases -------------------------------------------------------------------------

def test_resolve_alias_chains():
    aliases = {"n:konix bronx": "u:3539672", "n:a": "n:b", "n:b": "n:c"}
    assert resolve_alias("u:1", aliases) == "u:1"
    assert resolve_alias("n:konix bronx", aliases) == "u:3539672"
    assert resolve_alias("n:a", aliases) == "n:c"
    five = {f"k{i}": f"k{i + 1}" for i in range(5)}          # k0 -> ... -> k5, 5 hops
    assert resolve_alias("k0", five) == "k5"


def test_resolve_alias_cycles_and_overlong_chains_leave_key_unchanged():
    assert resolve_alias("a", {"a": "b", "b": "a"}) == "a"
    assert resolve_alias("b", {"a": "b", "b": "a"}) == "b"
    assert resolve_alias("a", {"a": "a"}) == "a"
    assert resolve_alias("c", {"c": "a", "a": "b", "b": "a"}) == "c"
    six = {f"k{i}": f"k{i + 1}" for i in range(6)}            # 6 hops: more than 5
    assert resolve_alias("k0", six) == "k0"
    assert resolve_alias("k1", six) == "k6"


def test_apply_aliases_merges_colliding_pairs():
    s = empty_state(NOW)
    s.pairs = {
        "beer-city": {
            "n:konix bronx": PairRec(first_seen=ago(30), last_seen=ago(10), event_at=ago(30), star=False,
                                     notified_at="baseline", last_in_result=False, in_stock=False,
                                     info={"name": "old"}),
            "u:3539672": PairRec(first_seen=ago(5), last_seen=ago(1), event_at=ago(5), star=True,
                                 notified_at=ago(4), last_in_result=True, in_stock=True, info={"name": "new"}),
            "n:other": PairRec(first_seen=ago(1), last_seen=ago(1)),
        },
        "parma": {"n:konix bronx": PairRec(first_seen=ago(7), last_seen=ago(2), notified_at="baseline")},
    }
    apply_aliases(s, {"n:konix bronx": "u:3539672"})
    assert set(s.pairs["beer-city"]) == {"u:3539672", "n:other"}
    assert s.pairs["beer-city"]["u:3539672"] == PairRec(
        first_seen=ago(30), last_seen=ago(1), event_at=ago(30), star=True, notified_at=ago(4),
        last_in_result=True, in_stock=True, info={"name": "new"})
    assert s.pairs["parma"] == {"u:3539672": PairRec(first_seen=ago(7), last_seen=ago(2), notified_at="baseline")}


def test_apply_aliases_takes_display_fields_from_latest_seen_pair_and_ors_flags():
    s = empty_state(NOW)
    s.pairs = {"beer-city": {
        "n:a": PairRec(first_seen=ago(3), last_seen=ago(3), event_at=ago(3), star=False, notified_at=None,
                       last_in_result=False, in_stock=True, info={"price_amd": 2100}),
        "n:b": PairRec(first_seen=ago(9), last_seen=ago(1), event_at=None, star=True, notified_at=None,
                       last_in_result=True, in_stock=False, info={"price_amd": 1900}),
    }}
    apply_aliases(s, {"n:b": "n:a"})
    assert s.pairs["beer-city"] == {"n:a": PairRec(
        first_seen=ago(9), last_seen=ago(1), event_at=ago(3), star=True, notified_at=None,
        last_in_result=True, in_stock=False, info={"price_amd": 1900})}


@pytest.mark.parametrize("a, b, expected", [
    (ago(4), "baseline", ago(4)),
    ("suppressed", ago(4), ago(4)),
    (ago(2), ago(4), ago(4)),          # two announcements: the earliest
    ("suppressed", None, "suppressed"),
    (None, "baseline", "baseline"),
    (None, None, None),
])
def test_apply_aliases_notified_at_prefers_iso_then_marker_then_none(a, b, expected):
    s = empty_state(NOW)
    s.pairs = {"gargoyle": {"n:x": PairRec(first_seen=ago(6), last_seen=ago(6), notified_at=a),
                            "u:1": PairRec(first_seen=ago(6), last_seen=ago(6), notified_at=b)}}
    apply_aliases(s, {"n:x": "u:1"})
    assert s.pairs["gargoyle"]["u:1"].notified_at == expected


def test_apply_aliases_never_creates_a_pending_event():
    # a never-in-stock shop pair (no event yet) merged into an already known beer stays silent
    s = empty_state(NOW)
    s.pairs = {"parma": {"n:x": PairRec(first_seen=ago(1), last_seen=ago(1), event_at=None, notified_at=None,
                                        in_stock=False),
                         "u:1": PairRec(first_seen=ago(8), last_seen=ago(2), event_at=None,
                                        notified_at="baseline", in_stock=True)}}
    apply_aliases(s, {"n:x": "u:1"})
    rec = s.pairs["parma"]["u:1"]
    assert rec.notified_at == "baseline" and rec.event_at is None


def test_apply_aliases_merges_beers_keeping_earliest_first_seen_city():
    s = empty_state(NOW)
    s.beers = {"n:konix bronx": BeerRec(first_seen_city=ago(20), n_key="n:konix bronx"),
               "u:3539672": BeerRec(first_seen_city=ago(5), n_key="n:konix cassis ruby"),   # its own n_key wins
               "n:lonely": BeerRec(first_seen_city=ago(1))}
    apply_aliases(s, {"n:konix bronx": "u:3539672", "n:lonely": "u:77"})
    assert s.beers == {"u:3539672": BeerRec(first_seen_city=ago(20), n_key="n:konix cassis ruby"),
                       "u:77": BeerRec(first_seen_city=ago(1))}


def test_apply_aliases_merges_brewery_new():
    s = empty_state(NOW)
    s.brewery_new = {
        "u:6000001": BreweryNewRec(brewery_id=265165, found_at=ago(2), star=False, notified_at=None,
                                   info={"name": "DDH NEIPA"}),
        "n:dargett ddh neipa": BreweryNewRec(brewery_id=265165, found_at=ago(9), star=True,
                                             notified_at="baseline", info={"name": "DDH Neipa"}),
    }
    apply_aliases(s, {"n:dargett ddh neipa": "u:6000001"})
    assert s.brewery_new == {"u:6000001": BreweryNewRec(
        brewery_id=265165, found_at=ago(9), star=True, notified_at="baseline", info={"name": "DDH NEIPA"})}


def test_apply_aliases_rewrites_shop_item_values():
    s = empty_state(NOW)
    s.shop_items = {"beer-city": {"1204": "n:konix bronx", "1300": "n:other"},
                    "parma": {"55": "n:konix bronx"}}
    apply_aliases(s, {"n:konix bronx": "u:3539672"})
    assert s.shop_items == {"beer-city": {"1204": "u:3539672", "1300": "n:other"},
                            "parma": {"55": "u:3539672"}}


def test_apply_aliases_is_idempotent():
    s = full_state()
    s.pairs["gargoyle"]["n:zagovor black sails"] = PairRec(first_seen=ago(9), last_seen=ago(9))
    aliases = {"n:zagovor black sails": "u:3539672", "a": "b", "b": "a"}
    apply_aliases(s, aliases)
    once = s.to_dict()
    apply_aliases(s, aliases)
    assert s.to_dict() == once


# --- prune -------------------------------------------------------------------------

def pruning_state(last_ok: str | None, with_source: bool = True) -> State:
    s = empty_state(NOW - timedelta(days=400))
    s.pairs = {"gargoyle": {"u:old": PairRec(first_seen=ago(300), last_seen=ago(181)),
                            "u:keep": PairRec(first_seen=ago(300), last_seen=ago(179))}}
    if with_source:
        s.sources["untappd_menu:gargoyle"] = SourceRec(baseline_done=True, last_ok=last_ok)
    return s


def test_prune_removes_old_pair_when_place_source_is_healthy():
    s = pruning_state(last_ok=ago(1))
    assert prune(s, NOW) == 1
    assert set(s.pairs["gargoyle"]) == {"u:keep"}


def test_prune_keeps_pairs_when_source_last_ok_is_old():
    s = pruning_state(last_ok=ago(40))
    assert prune(s, NOW) == 0
    assert set(s.pairs["gargoyle"]) == {"u:old", "u:keep"}


@pytest.mark.parametrize("with_source", [True, False])
def test_prune_keeps_pairs_when_source_never_succeeded(with_source):
    s = pruning_state(last_ok=None, with_source=with_source)
    assert prune(s, NOW) == 0
    assert set(s.pairs["gargoyle"]) == {"u:old", "u:keep"}


def test_prune_only_counts_sources_of_the_same_place():
    s = pruning_state(last_ok=ago(40))
    s.sources["untappd_menu:beatles"] = SourceRec(last_ok=ago(0))
    s.sources["untappd_brewery:265165"] = SourceRec(last_ok=ago(0))
    s.sources["manual"] = SourceRec(last_ok=ago(0))
    assert prune(s, NOW) == 0
    s.sources["untappd_checkins:gargoyle"] = SourceRec(last_ok=ago(29))   # any source of the place is enough
    assert prune(s, NOW) == 1


def test_prune_returns_total_count_over_places():
    s = pruning_state(last_ok=ago(1))
    s.pairs["parma"] = {"n:a": PairRec(first_seen=ago(400), last_seen=ago(200)),
                        "n:b": PairRec(first_seen=ago(400), last_seen=ago(190))}
    s.sources["parma:parma"] = SourceRec(last_ok=ago(0))
    assert prune(s, NOW) == 3
    assert s.pairs["parma"] == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 120 .venv/bin/pytest tests/test_state.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taps.state'`

- [ ] **Step 3: Write minimal implementation**

Create `taps/state.py`:
```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `timeout 120 .venv/bin/pytest tests/test_state.py -v`
Expected: `39 passed`

- [ ] **Step 5: Commit**

```bash
git add tests/test_state.py taps/state.py
git commit -m "feat: add state.json model with alias merging and pruning"
```

---

### Task 6: Сетевой слой: HTTP, Untappd, Cloudflare

**Files:**
- Create: `taps/fetch.py`
- Test: `tests/test_fetch.py`

**Interfaces:**
- Consumes: `taps.state.UntappdRec(last_attempt, pages_today, pages_date, brewery_list_cursor)`; `taps.timeutil.parse_iso`, `taps.timeutil.yerevan_date`, `taps.timeutil.iso` (в тестах)
- Produces: `UA`; `FetchError(kind, message="")` с полем `.kind` ∈ `"network"|"cloudflare"|"http"|"budget"|"blocked"`; `HttpResponse(status, headers, text)`; `PageFetcher = Callable[[str], HttpResponse]`; `is_cloudflare_challenge(status, headers, body) -> bool`; `Http(delay=(1.5, 2.0), session=None, sleep=time.sleep)` с `.get(url, headers=None) -> HttpResponse` и `.post_json(url, payload, headers=None) -> Any`; `untappd_due(untappd, now) -> bool`; `UntappdClient(untappd, daily_pages, now, fetch_page, delay=(4.0, 6.0), sleep=time.sleep)` с `.get(url) -> str`, `.blocked`, `.responded`; `playwright_fetcher() -> (fetch_page, close)`

Общий сетевой слой для всех источников (спека §2, §10 «Вежливость к Untappd»). `Http` нужен магазинам: паузы между запросами, заголовок UA, все сбои приводятся к `FetchError`. `UntappdClient` держит дневной бюджет страниц по дате Еревана, делает один повтор после сетевой ошибки и после первой же проверки Cloudflare блокирует весь дальнейший сбор с Untappd. `playwright` импортируется лениво внутри `playwright_fetcher`, поэтому тесты идут без него.

- [ ] **Step 1: Write the failing test**

Create `tests/test_fetch.py`:
```python
from datetime import datetime, timedelta, timezone

import pytest
import requests

from taps.fetch import (UA, FetchError, Http, HttpResponse, UntappdClient,
                        is_cloudflare_challenge, untappd_due)
from taps.state import UntappdRec
from taps.timeutil import iso
from tests.helpers import fixture_text

NOW = datetime(2026, 9, 23, 14, 0, tzinfo=timezone.utc)   # 18:00 in Yerevan, date 2026-09-23


def challenge_headers() -> dict[str, str]:
    lines = fixture_text("untappd/cloudflare_challenge.headers").splitlines()[1:]   # skip "HTTP/2 403"
    return {k.strip().lower(): v.strip() for k, v in (l.split(":", 1) for l in lines if ":" in l)}


CF_HTML = fixture_text("untappd/cloudflare_challenge.html")
CF = HttpResponse(403, challenge_headers(), CF_HTML)
OK = HttpResponse(200, {"content-type": "text/html"}, "<html>menu</html>")


# --- Cloudflare detection -------------------------------------------------

def test_challenge_fixture_is_detected():
    assert CF.headers["cf-mitigated"] == "challenge"
    assert is_cloudflare_challenge(403, CF.headers, CF_HTML)


def test_each_challenge_signal_alone_is_enough():
    assert is_cloudflare_challenge(200, {"cf-mitigated": "challenge"}, "")
    assert is_cloudflare_challenge(200, {}, CF_HTML)   # body markers only, any page language
    assert is_cloudflare_challenge(200, {}, "<script src='/cdn-cgi/challenge-platform/x'>")
    assert is_cloudflare_challenge(403, {"server": "Cloudflare"}, "")
    assert is_cloudflare_challenge(403, {"server": "nginx"}, "forbidden")   # any 403 is treated as a challenge
    assert is_cloudflare_challenge(200, {"cf-mitigated": "static"}, "")     # any presence of the header counts
    assert not is_cloudflare_challenge(200, {}, "plain 200 page")


def test_real_menu_page_is_not_a_challenge():
    html = fixture_text("untappd/gargoyle_menu.html")
    assert "Gargoyle Bar" in html
    assert not is_cloudflare_challenge(200, {"server": "cloudflare"}, html)


# --- Http -------------------------------------------------------------------

class FakeRaw:
    def __init__(self, status=200, text="ok", headers=None, json_data=None):
        self.status_code, self.text = status, text
        self.headers = headers or {"Content-Type": "text/html"}
        self._json = json_data

    def json(self):
        if self._json is None:
            raise requests.JSONDecodeError("Expecting value", self.text, 0)
        return self._json


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def _next(self, method, url, **kw):
        self.calls.append((method, url, kw))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def get(self, url, **kw):
        return self._next("get", url, **kw)

    def post(self, url, **kw):
        return self._next("post", url, **kw)


def make_http(*responses):
    sleeps = []
    session = FakeSession(*responses)
    return Http(session=session, sleep=sleeps.append), session, sleeps


def test_http_get_sends_ua_and_delays_only_between_requests():
    http, session, sleeps = make_http(FakeRaw(text="a", headers={"X-Thing": "1"}), FakeRaw(text="b"))
    first = http.get("https://shop.example/1", headers={"Accept": "application/json"})
    assert sleeps == []
    assert first == HttpResponse(200, {"x-thing": "1"}, "a")
    assert http.get("https://shop.example/2").text == "b"
    assert len(sleeps) == 1 and 1.5 <= sleeps[0] <= 2.0
    _, url, kw = session.calls[0]
    assert url == "https://shop.example/1"
    assert kw["headers"] == {"User-Agent": UA, "Accept": "application/json"}
    assert kw["timeout"] > 0
    assert session.calls[1][2]["headers"]["User-Agent"] == UA


def test_http_request_exception_is_network_error():
    http, _, _ = make_http(requests.ConnectionError("dns failed"))
    with pytest.raises(FetchError) as e:
        http.get("https://shop.example/")
    assert e.value.kind == "network"


def test_http_challenge_is_cloudflare_error():
    http, _, _ = make_http(FakeRaw(403, CF_HTML, {"Cf-Mitigated": "challenge", "Server": "cloudflare"}))
    with pytest.raises(FetchError) as e:
        http.get("https://shop.example/")
    assert e.value.kind == "cloudflare"


def test_http_404_is_http_error():
    http, _, _ = make_http(FakeRaw(404, "not found"))
    with pytest.raises(FetchError) as e:
        http.get("https://shop.example/missing")
    assert e.value.kind == "http"


def test_post_json_sends_payload_and_returns_parsed_json():
    http, session, sleeps = make_http(FakeRaw(json_data={"items": [1, 2]}))
    assert http.post_json("https://shop.example/api", {"page": 1}) == {"items": [1, 2]}
    method, _, kw = session.calls[0]
    assert method == "post" and kw["json"] == {"page": 1}
    assert kw["headers"]["User-Agent"] == UA
    assert sleeps == []


def test_post_json_invalid_json_is_fetch_error():
    http, _, _ = make_http(FakeRaw(text="<html>maintenance</html>"))
    with pytest.raises(FetchError) as e:
        http.post_json("https://shop.example/api", {})
    assert e.value.kind == "http"


def test_http_delay_also_follows_a_failed_request():
    http, _, sleeps = make_http(requests.Timeout("slow"), FakeRaw())
    with pytest.raises(FetchError):
        http.get("https://shop.example/1")
    http.get("https://shop.example/2")
    assert len(sleeps) == 1


# --- untappd_due --------------------------------------------------------------

def test_untappd_due():
    assert untappd_due(UntappdRec(), NOW)
    assert not untappd_due(UntappdRec(last_attempt=iso(NOW - timedelta(hours=19))), NOW)
    assert untappd_due(UntappdRec(last_attempt=iso(NOW - timedelta(hours=20))), NOW)


# --- UntappdClient ------------------------------------------------------------

class FakePages:
    def __init__(self, *results):
        self.results = list(results)
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def make_client(*results, rec=None, daily_pages=30):
    rec = rec if rec is not None else UntappdRec(pages_date="2026-09-23")
    pages, sleeps = FakePages(*results), []
    client = UntappdClient(rec, daily_pages, NOW, pages, sleep=sleeps.append)
    return client, rec, pages, sleeps


def test_client_returns_html_counts_pages_and_delays_between_pages():
    client, rec, pages, sleeps = make_client(OK, OK)
    assert not client.responded
    assert client.get("https://untappd.com/v/gargoyle-bar/12252462") == "<html>menu</html>"
    assert client.responded and rec.pages_today == 1 and sleeps == []
    client.get("https://untappd.com/v/beatles-pub-yerevan/2162817")
    assert rec.pages_today == 2
    assert len(sleeps) == 1 and 4.0 <= sleeps[0] <= 6.0


def test_client_resets_budget_on_new_yerevan_date():
    rec = UntappdRec(pages_today=30, pages_date="2026-09-22")
    client, rec, _, _ = make_client(OK, rec=rec)
    assert (rec.pages_date, rec.pages_today) == ("2026-09-23", 0)
    client.get("https://untappd.com/v/x/1")
    assert rec.pages_today == 1


def test_client_keeps_counter_on_same_date():
    client, rec, _, _ = make_client(OK, rec=UntappdRec(pages_today=7, pages_date="2026-09-23"))
    client.get("https://untappd.com/v/x/1")
    assert rec.pages_today == 8


def test_client_budget_exhausted():
    client, rec, pages, _ = make_client(OK, rec=UntappdRec(pages_today=2, pages_date="2026-09-23"), daily_pages=3)
    client.get("https://untappd.com/v/x/1")
    with pytest.raises(FetchError) as e:
        client.get("https://untappd.com/v/x/2")
    assert e.value.kind == "budget"
    assert rec.pages_today == 3 and len(pages.urls) == 1
    assert not client.blocked


def test_client_challenge_blocks_the_rest_of_the_run():
    client, rec, pages, _ = make_client(CF, OK)
    with pytest.raises(FetchError) as e:
        client.get("https://untappd.com/v/x/1")
    assert e.value.kind == "cloudflare"
    assert client.blocked and client.responded
    with pytest.raises(FetchError) as e:
        client.get("https://untappd.com/v/x/2")
    assert e.value.kind == "blocked"
    assert len(pages.urls) == 1 and rec.pages_today == 1


def test_client_http_error_is_not_a_block():
    client, _, _, _ = make_client(HttpResponse(404, {}, "not found"), OK)
    with pytest.raises(FetchError) as e:
        client.get("https://untappd.com/v/x/1")
    assert e.value.kind == "http"
    assert not client.blocked and client.responded
    assert client.get("https://untappd.com/v/x/2") == "<html>menu</html>"


def test_client_retries_network_error_once_after_10s():
    client, rec, pages, sleeps = make_client(FetchError("network", "reset"), OK)
    assert client.get("https://untappd.com/v/x/1") == "<html>menu</html>"
    assert sleeps == [10.0]
    assert pages.urls == ["https://untappd.com/v/x/1"] * 2
    assert rec.pages_today == 2 and client.responded


def test_client_second_network_error_fails_without_response():
    client, rec, _, sleeps = make_client(FetchError("network", "a"), FetchError("network", "b"))
    with pytest.raises(FetchError) as e:
        client.get("https://untappd.com/v/x/1")
    assert e.value.kind == "network"
    assert sleeps == [10.0] and rec.pages_today == 2
    assert not client.responded and not client.blocked


def test_client_retry_respects_budget():
    client, rec, pages, _ = make_client(FetchError("network"), OK,
                                        rec=UntappdRec(pages_today=0, pages_date="2026-09-23"), daily_pages=1)
    with pytest.raises(FetchError) as e:
        client.get("https://untappd.com/v/x/1")
    assert e.value.kind == "budget"
    assert rec.pages_today == 1 and len(pages.urls) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 120 .venv/bin/pytest tests/test_fetch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taps.fetch'`

- [ ] **Step 3: Write minimal implementation**

Create `taps/fetch.py`:
```python
"""Network layer: plain HTTP for shops, a budgeted polite client for Untappd, Cloudflare detection."""
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping

import requests

from taps.state import UntappdRec
from taps.timeutil import parse_iso, yerevan_date

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
TIMEOUT = 30            # seconds per plain HTTP request
UNTAPPD_EVERY = timedelta(hours=20)
RETRY_SLEEP = 10.0      # seconds before the single retry after a network error


class FetchError(Exception):
    def __init__(self, kind: str, message: str = ""):
        super().__init__(f"{kind}: {message}" if message else kind)
        self.kind = kind   # "network" | "cloudflare" | "http" | "budget" | "blocked"


@dataclass
class HttpResponse:
    status: int
    headers: dict[str, str]    # lower-cased keys
    text: str


PageFetcher = Callable[[str], HttpResponse]


def is_cloudflare_challenge(status: int, headers: Mapping[str, str], body: str) -> bool:
    return ("cf-mitigated" in headers
            or "_cf_chl_opt" in body or "challenge-platform" in body
            or status == 403)


def _check(resp: HttpResponse, url: str) -> None:
    if is_cloudflare_challenge(resp.status, resp.headers, resp.text):
        raise FetchError("cloudflare", url)
    if resp.status >= 400:
        raise FetchError("http", f"{resp.status} {url}")


class Http:
    def __init__(self, delay: tuple[float, float] = (1.5, 2.0), session: Any = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.delay = delay
        self.session = session if session is not None else requests.Session()
        self.sleep = sleep
        self._first = True

    def _request(self, method: str, url: str, headers: Mapping[str, str] | None, **kw) -> tuple[Any, HttpResponse]:
        if not self._first:
            self.sleep(random.uniform(*self.delay))
        self._first = False
        try:
            raw = getattr(self.session, method)(url, headers={"User-Agent": UA, **(headers or {})},
                                                timeout=TIMEOUT, **kw)
            resp = HttpResponse(raw.status_code, {k.lower(): v for k, v in raw.headers.items()}, raw.text)
        except requests.RequestException as e:
            raise FetchError("network", f"{url}: {e}") from e
        _check(resp, url)
        return raw, resp

    def get(self, url: str, headers: Mapping[str, str] | None = None) -> HttpResponse:
        return self._request("get", url, headers)[1]

    def post_json(self, url: str, payload: Any, headers: Mapping[str, str] | None = None) -> Any:
        raw, _ = self._request("post", url, headers, json=payload)
        try:
            return raw.json()
        except ValueError as e:   # requests' JSONDecodeError is a ValueError
            raise FetchError("http", f"invalid JSON from {url}") from e


def untappd_due(untappd: UntappdRec, now: datetime) -> bool:
    return untappd.last_attempt is None or now - parse_iso(untappd.last_attempt) >= UNTAPPD_EVERY


class UntappdClient:
    def __init__(self, untappd: UntappdRec, daily_pages: int, now: datetime,
                 fetch_page: PageFetcher, delay: tuple[float, float] = (4.0, 6.0),
                 sleep: Callable[[float], None] = time.sleep):
        self.untappd = untappd
        self.daily_pages = daily_pages
        self.fetch_page = fetch_page
        self.delay = delay
        self.sleep = sleep
        self.blocked = False
        self.responded = False
        self._first = True
        today = yerevan_date(now)
        if untappd.pages_date != today:
            untappd.pages_date = today
            untappd.pages_today = 0

    def _attempt(self, url: str) -> HttpResponse:
        if self.untappd.pages_today >= self.daily_pages:
            raise FetchError("budget", url)
        self.untappd.pages_today += 1
        return self.fetch_page(url)

    def get(self, url: str) -> str:
        if self.blocked:
            raise FetchError("blocked", url)
        if not self._first:
            self.sleep(random.uniform(*self.delay))
        self._first = False
        try:
            resp = self._attempt(url)
        except FetchError as e:
            if e.kind != "network":
                raise
            self.sleep(RETRY_SLEEP)
            resp = self._attempt(url)
        self.responded = True
        if is_cloudflare_challenge(resp.status, resp.headers, resp.text):
            self.blocked = True
        _check(resp, url)
        return resp.text


# Ported from hopandshot/hopsandshot scraper.py (languages switched to en-US).
STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = window.chrome || { runtime: {} };
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
"""


def playwright_fetcher() -> tuple[PageFetcher, Callable[[], None]]:
    from playwright.sync_api import Error as PlaywrightError, sync_playwright

    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        context = browser.new_context(
            user_agent=UA,
            locale="en-US",
            viewport={"width": 1366, "height": 768},
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        context.add_init_script(STEALTH_INIT_SCRIPT)
        page = context.new_page()
    except BaseException:
        pw.stop()
        raise

    def fetch_page(url: str) -> HttpResponse:
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(1500)
            if resp is None:
                raise FetchError("network", f"no response: {url}")
            return HttpResponse(resp.status, {k.lower(): v for k, v in resp.headers.items()}, page.content())
        except PlaywrightError as e:
            raise FetchError("network", f"{url}: {e}") from e

    def close() -> None:
        try:
            context.close()
            browser.close()
        finally:
            pw.stop()

    return fetch_page, close
```

- [ ] **Step 4: Run test to verify it passes**

Run: `timeout 120 .venv/bin/pytest tests/test_fetch.py -v`
Expected: `20 passed`

- [ ] **Step 5: Commit**

```bash
git add tests/test_fetch.py taps/fetch.py
git commit -m "feat: add HTTP client, budgeted Untappd client and Cloudflare detection"
```

---

### Task 7: Источник: меню Untappd

**Files:**
- Create: `taps/sources/untappd_menu.py`
- Test: `tests/test_untappd_menu.py`

**Interfaces:**
- Consumes: `taps.model.Sighting`, `taps.model.SourceResult`, `taps.model.u_key(beer_id) -> str`, `taps.timeutil.parse_iso(s) -> datetime` (Task 1); `taps.config.Place` с `sources["untappd_menu"] == {"slug": str, "venue_id": int}` (Task 2); `taps.fetch.FetchError` (поле `.kind`), `taps.fetch.UntappdClient.get(url) -> str` (Task 6).
- Produces: `SKIP_TAB_RE`; `MenuItem(beer_id: int, name: str, brewery: str, brewery_id: int | None, style: str | None, abv: float | None, ibu: int | None, rating: float | None, price_amd: int | None, volume_ml: int | None, container: str | None, section: str)`; `MenuPage(tabs: list[tuple[str, str]], active_menu_id: str | None, updated_at: datetime | None, items: list[MenuItem])`, где `tabs` — пары `(menu_id, name)` в порядке селектора; `parse_menu_page(html: str) -> MenuPage`; `fetch_menu(client: UntappdClient, place: Place, now: datetime, brewery_aliases: Mapping[str, str]) -> SourceResult` (key `untappd_menu:<place.id>`, source `untappd_menu`, sightings с ключами `u:<id>` и `menu_id`, заполнен `menu_updated_at`; при сбое `ok=False`, `error` = `FetchError.kind` или `"empty"`).

Источник 4.1: меню заведения на Untappd (спека §4.1, поведение при сбоях — §10). Парсер читает вкладки из `select.menu-selector`, время обновления из `span.updated-time[data-time]` и строки меню: id пива из `/b/<slug>/<id>`, пивоварня и её id, стиль, крепость и IBU, рейтинг `div.caps[data-rating]`, цена в AMD, объём и тара. `fetch_menu` берёт страницу заведения и каждую другую пивную вкладку через `?menu_id=`, еду и вино пропускает; сбой любой страницы или 0 сортов дают `ok=False`, чтобы предохранитель не затёр меню частичным результатом.

- [ ] **Step 1: Write the failing test**

Create `tests/test_untappd_menu.py`:
```python
from collections import Counter
from datetime import datetime, timezone

import pytest
from bs4 import BeautifulSoup

from taps.config import Place
from taps.fetch import FetchError
from taps.model import Sighting
from taps.sources.untappd_menu import SKIP_TAB_RE, MenuItem, fetch_menu, parse_menu_page
from tests.helpers import fixture_text

NOW = datetime(2026, 9, 24, 6, 17, tzinfo=timezone.utc)
GARGOYLE = Place(id="gargoyle", name="Gargoyle Bar", kind="bar",
                 sources={"untappd_menu": {"slug": "gargoyle-bar", "venue_id": 12252462}})
BEATLES = Place(id="beatles", name="Beatles Pub", kind="bar",
                sources={"untappd_menu": {"slug": "beatles-pub-yerevan", "venue_id": 2162817}})
GARGOYLE_URL = "https://untappd.com/v/gargoyle-bar/12252462"
BEATLES_URL = "https://untappd.com/v/beatles-pub-yerevan/2162817"
TAP_LIST = fixture_text("untappd/gargoyle_menu.html")     # default venue page: "On Tap", 3 beers
FOOD_TAB = fixture_text("untappd/gargoyle_menu_tab.html")  # ?menu_id=234933: "Food by Kruzhok", 17 items
BEATLES_MENU = fixture_text("untappd/beatles_menu.html")   # single-menu venue, 141 rows


class FakeClient:
    """url -> html, or an exception to raise; records requested urls."""

    def __init__(self, pages):
        self.pages, self.urls = pages, []

    def get(self, url):
        self.urls.append(url)
        page = self.pages[url]
        if isinstance(page, Exception):
            raise page
        return page


def first_by_id(items):
    out = {}
    for it in items:
        out.setdefault(it.beer_id, it)
    return out


def with_price_rows(rows_html):
    """Gargoyle's first beer with a price block added (no captured beer row has prices)."""
    soup = BeautifulSoup(TAP_LIST, "html.parser")
    soup.select_one("li.menu-item").append(BeautifulSoup(rows_html, "html.parser"))
    return parse_menu_page(str(soup)).items[0]


def test_gargoyle_default_page_tabs_and_updated_time():
    page = parse_menu_page(TAP_LIST)
    assert page.tabs == [("203569", "On Tap"), ("203568", "Bottles And Cans"),
                         ("234933", "Food by Kruzhok"), ("250574", "Wine")]
    assert page.active_menu_id == "203569"   # no option is selected: matched by p.menu-total
    assert page.updated_at == datetime(2026, 4, 19, 10, 49, 24, 755755, tzinfo=timezone.utc)


def test_gargoyle_default_page_items():
    assert parse_menu_page(TAP_LIST).items == [
        MenuItem(beer_id=5817007, name="Hell", brewery="Dahook", brewery_id=559009, style="Lager - Helles",
                 abv=4.8, ibu=20, rating=3.42226, price_amd=None, volume_ml=None, container=None,
                 section="Tap List"),
        MenuItem(beer_id=5817002, name="Red IPA", brewery="Dahook", brewery_id=559009, style="IPA - Red",
                 abv=6.0, ibu=45, rating=3.66676, price_amd=None, volume_ml=None, container=None,
                 section="Tap List"),
        MenuItem(beer_id=4775604, name="American Wheat Ale", brewery="379 Torch & Brew", brewery_id=518994,
                 style="Wheat Beer - American Pale Wheat", abv=4.8, ibu=25, rating=3.72004,
                 price_amd=None, volume_ml=None, container=None, section="Tap List"),
    ]


def test_gargoyle_food_tab_has_no_beers():
    page = parse_menu_page(FOOD_TAB)
    assert page.tabs == [("203569", "On Tap"), ("203568", "Bottles And Cans"), ("234933", "Food by Kruzhok")]
    assert page.active_menu_id == "234933"   # option selected="TRUE"
    assert page.updated_at == datetime(2025, 5, 2, 23, 25, 38, 645009, tzinfo=timezone.utc)
    assert len(BeautifulSoup(FOOD_TAB, "html.parser").select("li.menu-item")) == 17
    assert page.items == []   # 13 food and 4 non-alcoholic rows, none links an Untappd beer


def test_beatles_single_menu_page():
    page = parse_menu_page(BEATLES_MENU)
    assert (page.tabs, page.active_menu_id) == ([], None)
    assert page.updated_at == datetime(2025, 6, 17, 10, 24, 26, 39219, tzinfo=timezone.utc)
    assert len(page.items) == 141
    assert Counter(it.section for it in page.items) == {
        "On Tap": 21, "Belgian": 41, "Trappist": 16, "German": 19, "Armenian": 18, "Other": 26}


def test_beatles_item_fields():
    items = first_by_id(parse_menu_page(BEATLES_MENU).items)
    assert items[4473] == MenuItem(   # "1. Guinness Draught": numbering stripped
        beer_id=4473, name="Guinness Draught", brewery="Guinness", brewery_id=49, style="Stout - Irish Dry",
        abv=4.2, ibu=45, rating=3.76508, price_amd=None, volume_ml=None, container=None, section="On Tap")
    warsteiner = items[4305756]   # "N/A ABV • N/A IBU"
    assert (warsteiner.name, warsteiner.style, warsteiner.abv, warsteiner.ibu) == \
        ("Warsteiner 0,0% Isotonisch", "Non-Alcoholic - Other", None, None)
    stille = items[1365]          # "12% ABV • N/A IBU"
    assert (stille.brewery, stille.brewery_id, stille.abv, stille.ibu) == \
        ("Brouwerij De Dolle Brouwers", 272, 12.0, None)
    assert (items[16851].name, items[16851].brewery, items[16851].brewery_id) == \
        ("Aventinus (TAP06)", "Schneider Weisse G. Schneider & Sohn", 1023)
    assert [items[i].name for i in (420671, 5939, 4775604)] == ["1664 Rosé", "1664", "379 American Wheat Ale"]
    assert items[1518439].brewery_id == 265165   # Dargett


def test_beatles_beer_listed_in_two_sections():
    rows = [it for it in parse_menu_page(BEATLES_MENU).items if it.beer_id == 1518439]
    assert [(it.name, it.section, it.ibu) for it in rows] == [
        ("Pilsner (La Rapsodia)", "On Tap", 42), ("Pilsner (La Rapsodia)", "Armenian", 30)]


def test_price_volume_and_container_from_real_price_row():
    # The kombucha row of the food tab is the only captured price row with a size.
    food = BeautifulSoup(FOOD_TAB, "html.parser")
    kombucha = next(li for li in food.select("li.menu-item") if "kombucha" in li.get_text())
    item = with_price_rows(str(kombucha.select_one("div.beer-prices")))
    assert (item.beer_id, item.price_amd, item.volume_ml, item.container) == (5817007, 1900, 500, "bottle")


@pytest.mark.parametrize("rows, expected", [
    ('<p><span class="price">2300.00 AMD</span></p>', (2300, None, None)),
    ('<p><span class="size">0.5L Draft</span><span class="price">1,800.00 AMD</span></p>', (1800, 500, "draft")),
    ('<p><span class="size">33cl Can</span><span class="price">$ 7.00</span></p>', (None, 330, "can")),
    ('<p><span class="size">12oz Draft</span><span class="price">$ 7.00</span></p>'
     '<p><span class="size">0.3L Draft</span><span class="price">֏ 1200</span></p>', (1200, 300, "draft")),
])
def test_price_rows(rows, expected):
    item = with_price_rows(f'<div class="beer-prices">{rows}</div>')
    assert (item.price_amd, item.volume_ml, item.container) == expected


@pytest.mark.parametrize("value", ["0", "N/A"])
def test_rating_zero_or_unreadable_is_none(value):
    html = TAP_LIST.replace('data-rating="3.42226"', f'data-rating="{value}"')
    assert parse_menu_page(html).items[0].rating is None


@pytest.mark.parametrize("value", ["soon", "2026-04-19T10:49:24"])   # garbage, naive time
def test_unreadable_updated_time_is_none(value):
    html = TAP_LIST.replace('data-time="2026-04-19T10:49:24.755755Z"', f'data-time="{value}"')
    assert parse_menu_page(html).updated_at is None


@pytest.mark.parametrize("change", ["unwrap", "vanity"])
def test_brewery_without_w_link(change):
    soup = BeautifulSoup(TAP_LIST, "html.parser")
    link = soup.select_one("li.menu-item h6 span a")
    if change == "unwrap":
        link.unwrap()
    else:
        link["href"] = "/Dahook"
    item = parse_menu_page(str(soup)).items[0]
    assert (item.brewery, item.brewery_id, item.abv, item.ibu) == ("Dahook", None, 4.8, 20)


def test_shown_tab_unknown_or_ambiguous():
    soup = BeautifulSoup(TAP_LIST, "html.parser")
    soup.select_one("p.menu-total").string = "Seasonal"
    assert parse_menu_page(str(soup)).active_menu_id is None
    soup.select_one("p.menu-total").string = "On Tap"
    soup.select("select.menu-selector option")[1].string = "On Tap"   # two tabs named "On Tap"
    assert parse_menu_page(str(soup)).active_menu_id is None


@pytest.mark.parametrize("name", ["Food by Kruzhok", "Wine", "Cocktails", "Spirits", "Kitchen", "Кухня", "Вино"])
def test_skipped_tab_names(name):
    assert SKIP_TAB_RE.search(name)


@pytest.mark.parametrize("name", ["On Tap", "Bottles And Cans", "Beer Menu"])
def test_beer_tab_names(name):
    assert not SKIP_TAB_RE.search(name)


def test_fetch_gargoyle_reads_every_beer_tab():
    client = FakeClient({
        GARGOYLE_URL: TAP_LIST,
        GARGOYLE_URL + "?menu_id=203568": BEATLES_MENU,   # stand-in for "Bottles And Cans"
    })
    result = fetch_menu(client, GARGOYLE, NOW, {})
    # On Tap is the venue page itself; Food by Kruzhok and Wine are skipped.
    assert client.urls == [GARGOYLE_URL, GARGOYLE_URL + "?menu_id=203568"]
    assert (result.key, result.source, result.ok, result.error, result.place_id, result.full) == \
        ("untappd_menu:gargoyle", "untappd_menu", True, None, "gargoyle", True)
    assert result.menu_updated_at == datetime(2026, 4, 19, 10, 49, 24, 755755, tzinfo=timezone.utc)  # newest tab
    keys = [s.beer_key for s in result.sightings]
    assert len(keys) == len(set(keys)) == 133   # 8 rows repeated on the stand-in, 3 beers in both tabs
    assert Counter(s.menu_id for s in result.sightings) == {"203569": 3, "203568": 130}
    red_ipa = next(s for s in result.sightings if s.beer_key == "u:5817002")
    assert red_ipa == Sighting(   # the On Tap row wins over the second tab's row (rating 3.69186)
        place_id="gargoyle", source="untappd_menu", beer_key="u:5817002", title="Dahook Red IPA",
        name="Red IPA", seen_at=NOW, brewery="Dahook", brewery_id=559009, untappd_beer_id=5817002,
        style="IPA - Red", abv=6.0, ibu=45, rating=3.66676, menu_id="203569",
        url="https://untappd.com/beer/5817002")


def test_fetch_single_menu_venue():
    client = FakeClient({BEATLES_URL: BEATLES_MENU})
    result = fetch_menu(client, BEATLES, NOW, {"dahook": "dahook brewery"})
    assert client.urls == [BEATLES_URL]
    assert (result.ok, result.key, len(result.sightings)) == (True, "untappd_menu:beatles", 133)
    assert result.menu_updated_at == datetime(2025, 6, 17, 10, 24, 26, 39219, tzinfo=timezone.utc)
    assert all(s.menu_id is None and s.kind == "menu" and s.place_id == "beatles" and s.seen_at == NOW
               and s.beer_key == f"u:{s.untappd_beer_id}" for s in result.sightings)
    pils = [s for s in result.sightings if s.untappd_beer_id == 1518439]
    assert [(s.title, s.ibu) for s in pils] == [("Dargett Brewery Pilsner (La Rapsodia)", 42)]   # first row wins


def test_fetch_unknown_shown_tab_fetches_every_beer_tab():
    soup = BeautifulSoup(TAP_LIST, "html.parser")
    soup.select_one("p.menu-total").string = "Seasonal"
    client = FakeClient({
        GARGOYLE_URL: str(soup),
        GARGOYLE_URL + "?menu_id=203569": TAP_LIST,
        GARGOYLE_URL + "?menu_id=203568": FOOD_TAB,
    })
    result = fetch_menu(client, GARGOYLE, NOW, {})
    assert client.urls == [GARGOYLE_URL, GARGOYLE_URL + "?menu_id=203569", GARGOYLE_URL + "?menu_id=203568"]
    assert [(s.beer_key, s.menu_id) for s in result.sightings] == [
        ("u:5817007", "203569"), ("u:5817002", "203569"), ("u:4775604", "203569")]


def test_fetch_zero_beers_is_empty():
    # The venue page shows a skipped tab (food), so both beer tabs are fetched; none lists a beer.
    client = FakeClient({
        GARGOYLE_URL: FOOD_TAB,
        GARGOYLE_URL + "?menu_id=203569": FOOD_TAB,
        GARGOYLE_URL + "?menu_id=203568": FOOD_TAB,
    })
    result = fetch_menu(client, GARGOYLE, NOW, {})
    assert client.urls == [GARGOYLE_URL, GARGOYLE_URL + "?menu_id=203569", GARGOYLE_URL + "?menu_id=203568"]
    assert (result.ok, result.error, result.sightings, result.key) == (False, "empty", [], "untappd_menu:gargoyle")


def test_fetch_ignores_rows_of_a_skipped_shown_tab():
    # The venue page shows the Wine tab, which may list Untappd ciders: its rows are not used.
    soup = BeautifulSoup(TAP_LIST, "html.parser")
    soup.select_one('select.menu-selector option[value="250574"]')["selected"] = "TRUE"
    client = FakeClient({
        GARGOYLE_URL: str(soup),
        GARGOYLE_URL + "?menu_id=203569": FOOD_TAB,
        GARGOYLE_URL + "?menu_id=203568": FOOD_TAB,
    })
    result = fetch_menu(client, GARGOYLE, NOW, {})
    assert client.urls == [GARGOYLE_URL, GARGOYLE_URL + "?menu_id=203569", GARGOYLE_URL + "?menu_id=203568"]
    assert (result.ok, result.error, result.sightings) == (False, "empty", [])


@pytest.mark.parametrize("pages, kind", [
    ({GARGOYLE_URL: FetchError("cloudflare")}, "cloudflare"),
    ({GARGOYLE_URL: FetchError("blocked")}, "blocked"),
    ({GARGOYLE_URL: TAP_LIST, GARGOYLE_URL + "?menu_id=203568": FetchError("network")}, "network"),
    ({GARGOYLE_URL: TAP_LIST, GARGOYLE_URL + "?menu_id=203568": FetchError("budget")}, "budget"),
])
def test_fetch_error_fails_whole_menu(pages, kind):
    result = fetch_menu(FakeClient(pages), GARGOYLE, NOW, {})
    assert (result.ok, result.error, result.sightings, result.place_id, result.key) == \
        (False, kind, [], "gargoyle", "untappd_menu:gargoyle")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 120 .venv/bin/pytest tests/test_untappd_menu.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taps.sources.untappd_menu'`

- [ ] **Step 3: Write minimal implementation**

Create `taps/sources/untappd_menu.py`:
```python
"""Source 4.1: Untappd venue menu (all beer tabs)."""
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from bs4 import BeautifulSoup, Tag

from taps.config import Place
from taps.fetch import FetchError, UntappdClient
from taps.model import Sighting, SourceResult, u_key
from taps.timeutil import parse_iso

SKIP_TAB_RE = re.compile(r"(food|wine|cocktail|spirit|kitchen|кухн|вино)", re.I)
BEER_HREF_RE = re.compile(r"^/b/[^/]+/(\d+)/?$")
BREWERY_HREF_RE = re.compile(r"^/w/[^/]+/(\d+)/?$")
ABV_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*ABV", re.I)
IBU_RE = re.compile(r"(\d+(?:\.\d+)?)\s*IBU", re.I)
NUMBERING_RE = re.compile(r"^\d+\.\s+")   # "1. Hell" in numbered sections
SECTION_COUNT_RE = re.compile(r"\s*\(\s*\d+\s*items?\s*\)$", re.I)
AMD_RE = re.compile(r"AMD|֏|դր", re.I)
PRICE_RE = re.compile(r"\d[\d\s,]*(?:\.\d+)?")
VOLUME_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(ml|cl|l)\b")
CONTAINER_RE = re.compile(r"\b(draft|can|bottle|keg)\b")
ML_PER_UNIT = {"ml": 1, "cl": 10, "l": 1000}


@dataclass(frozen=True)
class MenuItem:
    beer_id: int
    name: str
    brewery: str
    brewery_id: int | None
    style: str | None
    abv: float | None
    ibu: int | None
    rating: float | None
    price_amd: int | None
    volume_ml: int | None
    container: str | None
    section: str


@dataclass(frozen=True)
class MenuPage:
    tabs: list[tuple[str, str]]   # (menu_id, tab name) in selector order
    active_menu_id: str | None    # tab shown on this page; None if unknown or single-menu venue
    updated_at: datetime | None
    items: list[MenuItem]


def _text(tag: Tag | None) -> str:
    return " ".join(tag.get_text(" ").split()) if tag else ""


def _updated_at(soup: BeautifulSoup) -> datetime | None:
    span = soup.select_one("span.updated-time[data-time]")
    try:
        return parse_iso(span["data-time"]) if span else None
    except ValueError:
        return None


def _tabs(soup: BeautifulSoup) -> tuple[list[tuple[str, str]], str | None]:
    options = [o for o in soup.select("select.menu-selector option[value]") if o["value"].strip()]
    tabs = [(o["value"].strip(), _text(o)) for o in options]
    selected = [o["value"].strip() for o in options if o.has_attr("selected")]
    if selected:
        return tabs, selected[0]
    # The default venue page selects no option; p.menu-total holds the shown tab's name.
    shown = _text(soup.select_one("p.menu-total"))
    matches = [menu_id for menu_id, name in tabs if name == shown]
    return tabs, matches[0] if len(matches) == 1 else None


def _rating(li: Tag) -> float | None:
    caps = li.select_one("div.caps[data-rating]")
    try:
        rating = float(caps["data-rating"]) if caps else None
    except ValueError:
        return None
    return rating or None   # 0 means no ratings yet


def _price(li: Tag) -> tuple[int | None, int | None, str | None]:
    """(price_amd, volume_ml, container) from the first AMD price row, else from the first row."""
    rows = li.select("div.beer-prices p")
    if not rows:
        return None, None, None
    row = next((p for p in rows if AMD_RE.search(_text(p.select_one("span.price")))), rows[0])
    price_text = _text(row.select_one("span.price"))
    m = PRICE_RE.search(price_text) if AMD_RE.search(price_text) else None
    price = round(float(re.sub(r"[\s,]", "", m.group()))) if m else None
    size = _text(row.select_one("span.size")).lower()
    v = VOLUME_RE.search(size)
    volume = round(float(v.group(1).replace(",", ".")) * ML_PER_UNIT[v.group(2)]) if v else None
    c = CONTAINER_RE.search(size)
    return price, volume, c.group(1) if c else None


def _item(li: Tag, section: str) -> MenuItem | None:
    link = li.select_one("h5 a[href]")
    m = BEER_HREF_RE.match(link["href"]) if link else None
    if m is None:
        return None   # food and other items without an Untappd beer link
    stats = li.select_one("h6 span")   # "4.8% ABV • 20 IBU • <a>Dahook</a> •"
    stats_text = _text(stats)
    brewery_link = stats.find("a", href=True) if stats else None
    if brewery_link:
        brewery = _text(brewery_link)
    else:   # unlinked brewery: the last "•" part that is not ABV or IBU
        parts = [p.strip() for p in stats_text.split("•")]
        brewery = next((p for p in reversed(parts) if p and "ABV" not in p and "IBU" not in p), "")
    bm = BREWERY_HREF_RE.match(brewery_link["href"]) if brewery_link else None
    abv, ibu = ABV_RE.search(stats_text), IBU_RE.search(stats_text)
    price, volume, container = _price(li)
    return MenuItem(
        beer_id=int(m.group(1)),
        name=NUMBERING_RE.sub("", _text(link)),
        brewery=brewery,
        brewery_id=int(bm.group(1)) if bm else None,
        style=_text(li.select_one("h5 em")) or None,
        abv=float(abv.group(1)) if abv else None,
        ibu=round(float(ibu.group(1))) if ibu else None,
        rating=_rating(li),
        price_amd=price,
        volume_ml=volume,
        container=container,
        section=section,
    )


def parse_menu_page(html: str) -> MenuPage:
    soup = BeautifulSoup(html, "html.parser")
    tabs, active = _tabs(soup)
    items = []
    for sec in soup.select("div.menu-section"):
        section = SECTION_COUNT_RE.sub("", _text(sec.select_one(".menu-section-header h4")))
        items += [item for li in sec.select("li.menu-item") if (item := _item(li, section))]
    return MenuPage(tabs=tabs, active_menu_id=active, updated_at=_updated_at(soup), items=items)


def fetch_menu(client: UntappdClient, place: Place, now: datetime,
               brewery_aliases: Mapping[str, str]) -> SourceResult:
    """Venue page plus every other beer tab via ?menu_id=. One failed page fails the whole menu.

    brewery_aliases is unused: menu keys are u:<beer_id>.
    """
    key = f"untappd_menu:{place.id}"
    params = place.sources["untappd_menu"]
    url = f"https://untappd.com/v/{params['slug']}/{params['venue_id']}"
    try:
        first = parse_menu_page(client.get(url))
        # A single-menu venue page is the menu. Otherwise read every beer tab in selector order;
        # the venue page stands for the tab it shows and is not used when that tab is unknown.
        pages = [] if first.tabs else [(first.active_menu_id, first)]
        for menu_id, name in first.tabs:
            if SKIP_TAB_RE.search(name):
                continue
            if menu_id == first.active_menu_id:
                pages.append((menu_id, first))
            else:
                pages.append((menu_id, parse_menu_page(client.get(f"{url}?menu_id={menu_id}"))))
    except FetchError as e:
        return SourceResult(key=key, source="untappd_menu", ok=False, error=e.kind, place_id=place.id)

    sightings, seen = [], set()
    for menu_id, page in pages:
        for it in page.items:
            if it.beer_id in seen:
                continue   # listed twice (on tap and in bottles, or in two sections): first row wins
            seen.add(it.beer_id)
            sightings.append(Sighting(
                place_id=place.id, source="untappd_menu", beer_key=u_key(it.beer_id),
                title=f"{it.brewery} {it.name}".strip(), name=it.name, seen_at=now,
                brewery=it.brewery or None, brewery_id=it.brewery_id, untappd_beer_id=it.beer_id,
                style=it.style, abv=it.abv, ibu=it.ibu, rating=it.rating, price_amd=it.price_amd,
                volume_ml=it.volume_ml, container=it.container, menu_id=menu_id,
                url=f"https://untappd.com/beer/{it.beer_id}",
            ))
    return SourceResult(
        key=key, source="untappd_menu", ok=bool(sightings), sightings=sightings,
        error=None if sightings else "empty", place_id=place.id,
        menu_updated_at=max((p.updated_at for _, p in pages if p.updated_at), default=None),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `timeout 120 .venv/bin/pytest tests/test_untappd_menu.py -v`
Expected: `37 passed`

- [ ] **Step 5: Commit**

```bash
git add tests/test_untappd_menu.py taps/sources/untappd_menu.py
git commit -m "feat: parse Untappd venue menus across beer tabs"
```

---

### Task 8: Источник: чекины в барах без меню

**Files:**
- Create: `taps/sources/untappd_checkins.py`
- Test: `tests/test_untappd_checkins.py`

**Interfaces:**
- Consumes: `taps.config.Config` (`place_by_venue(venue_id) -> Place | None`), `taps.config.Place` (`id`, `sources["untappd_checkins"] = {"slug": str, "venue_id": int}`), `taps.fetch.FetchError` (`.kind`), `taps.fetch.UntappdClient.get(url) -> str`, `taps.model.Sighting`, `taps.model.SourceResult`, `taps.model.u_key`.
- Produces: `Checkin(checkin_id: int, beer_id: int, beer_name: str, brewery: str, venue_id: int | None, venue_name: str | None, serving: str | None, at_home: bool, created_at: datetime)`; `parse_checkins(html: str) -> list[Checkin]`; `checkins_to_sightings(checkins, config: Config, source: str, now: datetime, brewery_aliases) -> list[Sighting]`; `fetch_venue_checkins(client, place, config, now, brewery_aliases) -> SourceResult` (key `untappd_checkins:<place_id>`). `untappd_brewery.py` (задача 9) использует `parse_checkins` и `checkins_to_sightings` для страниц пивоварен.

Источник 👀 из спеки §4.3: последние чекины со страницы заведения Untappd `/v/<slug>/<venue_id>` для баров без меню. Каждый чекин превращается в «замечено» с ключом `u:<id пива>` и временем чекина в `seen_at`. Фильтры по подаче, давности, «Untappd at Home» и месту с меню применяет rules (§6). Место берётся только из ссылок в `p.text`: в `p.purchased` тоже бывает ссылка `/v/`, но это место покупки. В живом браузере скрипт страницы `refreshTime(".timezoner", "D MMM YY")` заменяет время на дату вида `31 Oct 24`, поэтому парсер понимает и её.

- [ ] **Step 1: Write the failing test**

Create `tests/test_untappd_checkins.py`:
```python
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from taps.config import Config, Place, Settings
from taps.fetch import FetchError
from taps.sources.untappd_checkins import (
    Checkin, checkins_to_sightings, fetch_venue_checkins, parse_checkins,
)
from tests.helpers import fixture_text

NOW = datetime(2024, 11, 1, 10, 0, tzinfo=timezone.utc)
CRAFT_STORY_URL = "https://untappd.com/v/craft-story/12281551"


def place(pid, venue_id, source="untappd_checkins"):
    return Place(id=pid, name=pid.title(), kind="bar", sources={source: {"slug": pid, "venue_id": venue_id}})


CRAFT_STORY = place("craft-story", 12281551)
CONFIG = Config(
    places={p.id: p for p in (CRAFT_STORY, place("vertigo", 11856429))},
    breweries=(),
    settings=Settings(),
)

# Real markup from dargett_brewery.html; the purchase venue differs from the check-in venue.
AT_HOME_HTML = """
<div class="item" id="checkin_1528967315" data-checkin-id="1528967315"><div class="checkin"><div class="top">
<p class="text"><a href="/user/user1" class="user">User</a> is drinking a
<a href="/b/dargett-brewery-pilsner-la-rapsodia/1518439">Pilsner (La Rapsodia)</a> by
<a href="/Dargett">Dargett Brewery</a> at <a href="/v/at-home/9917985">Untappd at Home</a></p>
<div class="checkin-comment"><p class="purchased">Purchased at <a href="/v/total-wine-more/115678">Total Wine</a></p>
<div class="rating-serving"><p class="serving"><img alt="Bottle"><span>Bottle</span></p></div></div>
</div><div class="feedback"><div class="bottom">
<a href="/user/user1/checkin/1528967315" class="time timezoner">Sun, 16 Nov 2025 00:44:33 +0000</a>
</div></div></div></div>
"""


class FakeClient:
    def __init__(self, html=None, error=None):
        self.html, self.error, self.urls = html, error, []

    def get(self, url):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.html


def by_id(checkins):
    return {c.checkin_id: c for c in checkins}


def test_craft_story_count_and_order():
    checkins = parse_checkins(fixture_text("untappd/craftstory_checkins.html"))
    assert len(checkins) == 20                 # 31 data-checkin-id attributes, 20 div.item blocks
    assert len({c.checkin_id for c in checkins}) == 20
    assert (checkins[0].checkin_id, checkins[-1].checkin_id) == (1429774602, 1418981193)
    assert {c.venue_id for c in checkins} == {12281551}


def test_craft_story_checkin_fields():
    c = by_id(parse_checkins(fixture_text("untappd/craftstory_checkins.html")))[1429774602]
    assert c == Checkin(
        checkin_id=1429774602, beer_id=5698328, beer_name="Black Is Beautiful Volume 2",
        brewery="Omnipollo", venue_id=12281551, venue_name="Craft Story", serving="Can",
        at_home=False, created_at=datetime(2024, 10, 31, 15, 50, 9, tzinfo=timezone.utc),
    )


def test_craft_story_servings_and_brewery_links():
    checkins = by_id(parse_checkins(fixture_text("untappd/craftstory_checkins.html")))
    assert checkins[1426811517].serving == "Draft"
    assert checkins[1419725361].serving == "Bottle"
    assert checkins[1426387350].serving is None           # no p.serving in this check-in
    assert checkins[1426387350].brewery == "Brauhaus Bevog"
    assert checkins[1419750295].brewery == "BlakStoc"     # brewery link in /w/<slug>/<id> form
    assert checkins[1426799004].beer_name == "NORTHERN STAR™ // CHOCOLATE, CARAMEL & BISCUIT PORTER"
    assert checkins[1419438214].brewery == "Steppe & Wind Meadery (Степь и Ветер)"


def test_vertigo_markup():
    # Items are "item " / "item fade-out-gradient" with an extra inner div; same fields otherwise.
    checkins = parse_checkins(fixture_text("untappd/vertigo_checkins.html"))
    assert [c.checkin_id for c in checkins] == [1515988927, 1505021426, 1505016023, 1502696702, 1497179096]
    first = checkins[0]
    assert (first.beer_id, first.beer_name, first.brewery) == (326032, "O'Hara's Double IPA", "O'Hara's Brewery")
    assert (first.venue_id, first.venue_name, first.serving) == (11856429, "Vertigo Bar & Bottleshop", None)
    assert first.created_at == datetime(2025, 9, 24, 20, 46, 45, tzinfo=timezone.utc)
    assert (checkins[-1].beer_name, checkins[-1].serving) == ("Leshy / Леший", "Can")


def test_vertigo_compact_list_adds_nothing():
    # The left-column div.venue-activity list repeats the stream's check-ins without venue and serving.
    html = fixture_text("untappd/vertigo_checkins.html")
    soup = BeautifulSoup(html, "html.parser")
    compact = [int(a["href"].rsplit("/", 1)[1]) for a in soup.select("div.venue-activity span.time a")]
    assert compact == [c.checkin_id for c in parse_checkins(html)]


def test_at_home_and_purchase_venue():
    [c] = parse_checkins(AT_HOME_HTML)
    assert c.at_home is True
    assert (c.venue_id, c.venue_name, c.beer_id, c.serving) == (9917985, "Untappd at Home", 1518439, "Bottle")


def test_brewery_page_at_home_and_no_venue():
    checkins = by_id(parse_checkins(fixture_text("untappd/dargett_brewery.html")))
    assert len(checkins) == 20
    assert (checkins[1528967315].at_home, checkins[1528967315].venue_id) == (True, 9917985)
    assert (checkins[1528921986].at_home, checkins[1528921986].venue_id) == (False, 645961)
    no_venue = checkins[1528687768]
    assert (no_venue.venue_id, no_venue.venue_name, no_venue.at_home) == (None, None, False)
    assert checkins[1528689976].serving == "Taster"


def test_browser_rewritten_time():
    # In a browser, refreshTime(".timezoner", "D MMM YY") turns the time into a date only.
    html = AT_HOME_HTML.replace("Sun, 16 Nov 2025 00:44:33 +0000", "6 Nov 25")
    [c] = parse_checkins(html)
    assert c.created_at == datetime(2025, 11, 6, tzinfo=timezone.utc)


def test_skips_duplicates_and_broken_items():
    no_beer = '<div class="item" data-checkin-id="5"><p class="text">no links</p><a class="time">6 Nov 25</a></div>'
    bad_time = AT_HOME_HTML.replace("1528967315", "6").replace("Sun, 16 Nov 2025 00:44:33 +0000", "2 hours ago")
    no_time = AT_HOME_HTML.replace("1528967315", "7").replace('class="time timezoner"', 'class="other"')
    html = AT_HOME_HTML + AT_HOME_HTML + no_beer + bad_time + no_time
    assert [c.checkin_id for c in parse_checkins(html)] == [1528967315]
    assert parse_checkins("<html><body>Nothing here</body></html>") == []


def test_checkins_to_sightings():
    checkins = parse_checkins(fixture_text("untappd/craftstory_checkins.html"))
    checkins += parse_checkins(fixture_text("untappd/vertigo_checkins.html"))
    checkins += parse_checkins(AT_HOME_HTML)
    sightings = checkins_to_sightings(checkins, CONFIG, "untappd_checkins", NOW, {})
    assert len(sightings) == 25                                   # at-home venue is not in config
    assert {s.place_id for s in sightings} == {"craft-story", "vertigo"}
    s = next(s for s in sightings if s.checkin_id == 1429774602)
    assert (s.place_id, s.source, s.kind, s.beer_key) == ("craft-story", "untappd_checkins", "checkin", "u:5698328")
    assert (s.name, s.title, s.brewery, s.untappd_beer_id) == (
        "Black Is Beautiful Volume 2", "Omnipollo Black Is Beautiful Volume 2", "Omnipollo", 5698328)
    assert s.seen_at == datetime(2024, 10, 31, 15, 50, 9, tzinfo=timezone.utc)   # check-in time, not now
    assert (s.serving, s.at_home, s.url) == ("Can", False, "https://untappd.com/beer/5698328")
    assert (s.rating, s.style, s.abv, s.brewery_id) == (None, None, None, None)  # personal ratings never used


def test_checkins_to_sightings_source_and_at_home_passthrough():
    home = place("home", 9917985)
    config = Config(places={"home": home}, breweries=(), settings=Settings())
    no_venue = Checkin(1, 2, "X", "Y", None, None, None, False, NOW)
    [s] = checkins_to_sightings(parse_checkins(AT_HOME_HTML) + [no_venue], config, "untappd_brewery", NOW, {})
    assert (s.source, s.kind, s.at_home, s.place_id, s.serving) == ("untappd_brewery", "checkin", True, "home", "Bottle")


def test_checkins_to_sightings_maps_venue_of_menu_place():
    # Brewery-page check-ins at a place with a menu still map; rules drop them later.
    config = Config(places={"cs": place("cs", 12281551, "untappd_menu")}, breweries=(), settings=Settings())
    sightings = checkins_to_sightings(
        parse_checkins(fixture_text("untappd/craftstory_checkins.html")), config, "untappd_brewery", NOW, {})
    assert len(sightings) == 20 and {s.place_id for s in sightings} == {"cs"}


def test_fetch_ok():
    client = FakeClient(fixture_text("untappd/craftstory_checkins.html"))
    result = fetch_venue_checkins(client, CRAFT_STORY, CONFIG, NOW, {})
    assert client.urls == [CRAFT_STORY_URL]
    assert (result.key, result.source, result.ok, result.error, result.place_id) == (
        "untappd_checkins:craft-story", "untappd_checkins", True, None, "craft-story")
    assert len(result.sightings) == 20
    assert {s.place_id for s in result.sightings} == {"craft-story"}
    assert result.sightings[0].checkin_id == 1429774602


def test_fetch_error():
    for kind in ("cloudflare", "network", "blocked"):
        result = fetch_venue_checkins(FakeClient(error=FetchError(kind, CRAFT_STORY_URL)), CRAFT_STORY, CONFIG, NOW, {})
        assert (result.key, result.ok, result.error, result.sightings) == (
            "untappd_checkins:craft-story", False, kind, [])


def test_fetch_empty_page():
    result = fetch_venue_checkins(FakeClient("<html><body></body></html>"), CRAFT_STORY, CONFIG, NOW, {})
    assert (result.key, result.source, result.ok, result.error, result.sightings) == (
        "untappd_checkins:craft-story", "untappd_checkins", False, "empty", [])


def test_fetch_other_venue_is_bad_response():
    # All check-ins name another venue id (e.g. Untappd merged the venue): fail loudly, not "ok with 0".
    moved = place("craft-story", 99)
    config = Config(places={"craft-story": moved}, breweries=(), settings=Settings())
    result = fetch_venue_checkins(FakeClient(fixture_text("untappd/craftstory_checkins.html")), moved, config, NOW, {})
    assert (result.ok, result.error, result.sightings) == (False, "bad_response", [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 120 .venv/bin/pytest tests/test_untappd_checkins.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taps.sources.untappd_checkins'`

- [ ] **Step 3: Write minimal implementation**

Create `taps/sources/untappd_checkins.py`:
```python
"""Source 4.3 (👀): recent check-ins on an Untappd venue page; the parser also serves brewery pages (4.2)."""
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup, Tag

from taps.config import Config, Place
from taps.fetch import FetchError, UntappdClient
from taps.model import Sighting, SourceResult, u_key

BEER_HREF_RE = re.compile(r"^/b/[^/]+/(\d+)/?$")
VENUE_HREF_RE = re.compile(r"^/v/[^/]+/(\d+)/?$")
AT_HOME = "untappd at home"


@dataclass(frozen=True)
class Checkin:
    checkin_id: int
    beer_id: int
    beer_name: str
    brewery: str
    venue_id: int | None
    venue_name: str | None
    serving: str | None
    at_home: bool
    created_at: datetime


def _text(tag: Tag | None) -> str:
    return " ".join(tag.get_text(" ").split()) if tag else ""


def _created_at(text: str) -> datetime | None:
    """Server HTML has 'Thu, 31 Oct 2024 15:50:09 +0000'. In a real browser the page script
    refreshTime(".timezoner", "D MMM YY") rewrites it to '31 Oct 24', a browser-local date:
    that is read as 00:00 UTC of that day."""
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            return datetime.strptime(text, "%d %b %y").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    if dt.tzinfo is None:   # RFC 2822 "-0000": UTC, source zone unknown
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_item(item: Tag) -> Checkin | None:
    """One div.item; None when the beer link or a readable time is missing.

    Only links inside p.text count: p.purchased also holds a /v/ link (where it was bought)."""
    text = item.select_one("p.text")
    created_at = _created_at(_text(item.select_one("a.time")))
    if text is None or created_at is None:
        return None
    beer = venue = brewery = None
    for a in text.find_all("a", href=True):   # user, beer, brewery, venue - in this order
        if beer is None:
            if m := BEER_HREF_RE.match(a["href"]):
                beer = (int(m.group(1)), _text(a))
        elif m := VENUE_HREF_RE.match(a["href"]):
            venue = (int(m.group(1)), _text(a))
        elif brewery is None:
            brewery = _text(a)
    if beer is None:
        return None
    serving = _text(item.select_one("p.serving span")) or None
    return Checkin(
        checkin_id=int(item["data-checkin-id"]),
        beer_id=beer[0],
        beer_name=beer[1],
        brewery=brewery or "",
        venue_id=venue[0] if venue else None,
        venue_name=venue[1] if venue else None,
        serving=serving,
        at_home=venue is not None and AT_HOME in venue[1].lower(),
        created_at=created_at,
    )


def parse_checkins(html: str) -> list[Checkin]:
    """Check-ins of a venue or brewery page in page order, each id once.

    Venue pages may also have a compact div.venue-activity list: it repeats the same
    check-ins without venue and serving, so only div.item is read."""
    soup = BeautifulSoup(html, "html.parser")
    seen, out = set(), []
    for item in soup.select("div.item[data-checkin-id]"):
        cid = item["data-checkin-id"]
        if not cid.isdigit() or cid in seen:
            continue
        if checkin := _parse_item(item):
            seen.add(cid)
            out.append(checkin)
    return out


def checkins_to_sightings(checkins: list[Checkin], config: Config, source: str, now: datetime,
                          brewery_aliases: Mapping[str, str]) -> list[Sighting]:
    """Sightings for check-ins at enabled places; other venues and check-ins without a venue are dropped.

    now and brewery_aliases are unused: seen_at is the check-in time and keys are u:<beer_id>.
    Rating/style/ABV stay empty: a check-in only carries one person's rating."""
    out = []
    for c in checkins:
        place = config.place_by_venue(c.venue_id) if c.venue_id is not None else None
        if place is None:
            continue
        out.append(Sighting(
            place_id=place.id, source=source, beer_key=u_key(c.beer_id),
            title=f"{c.brewery} {c.beer_name}".strip(), name=c.beer_name, seen_at=c.created_at,
            brewery=c.brewery or None, untappd_beer_id=c.beer_id, serving=c.serving,
            url=f"https://untappd.com/beer/{c.beer_id}", checkin_id=c.checkin_id, at_home=c.at_home,
        ))
    return out


def fetch_venue_checkins(client: UntappdClient, place: Place, config: Config, now: datetime,
                         brewery_aliases: Mapping[str, str]) -> SourceResult:
    params = place.sources["untappd_checkins"]
    result = SourceResult(key=f"untappd_checkins:{place.id}", source="untappd_checkins", ok=False,
                          place_id=place.id)
    try:
        html = client.get(f"https://untappd.com/v/{params['slug']}/{params['venue_id']}")
    except FetchError as e:
        result.error = e.kind
        return result
    checkins = parse_checkins(html)
    if not checkins:
        result.error = "empty"
        return result
    if all(c.venue_id != params["venue_id"] for c in checkins):
        result.error = "bad_response"   # e.g. Untappd merged the venue and redirected to a new id
        return result
    result.sightings = checkins_to_sightings(checkins, config, "untappd_checkins", now, brewery_aliases)
    result.ok = True
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `timeout 120 .venv/bin/pytest tests/test_untappd_checkins.py -v`
Expected: `16 passed`

- [ ] **Step 5: Commit**

```bash
git add tests/test_untappd_checkins.py taps/sources/untappd_checkins.py
git commit -m "feat: parse Untappd venue check-ins into sightings"
```

---

### Task 9: Источник: страницы пивоварен Untappd (чекины и список сортов)

**Files:**
- Create: `taps/sources/untappd_brewery.py`
- Test: `tests/test_untappd_brewery.py`

**Interfaces:**
- Consumes: `taps.sources.untappd_checkins.parse_checkins(html: str) -> list[Checkin]`, `taps.sources.untappd_checkins.checkins_to_sightings(checkins, config: Config, source: str, now: datetime, brewery_aliases: Mapping[str, str]) -> list[Sighting]`; `taps.fetch.UntappdClient.get(url: str) -> str`, `taps.fetch.FetchError(kind, message)` (`.kind`); `taps.config.Brewery` (`.brewery_id`, `.name`, `.url` = `https://untappd.com/brewery/<id>`), `Config` (`place_by_venue`), `Place`, `Settings`; `taps.model.BreweryBeer`, `SourceResult`; `tests.helpers.fixture_text`.
- Produces: `fetch_brewery_checkins(client, brewery: Brewery, config: Config, now: datetime, brewery_aliases: Mapping[str, str]) -> SourceResult` (key `untappd_brewery:<brewery_id>`, source `untappd_brewery`, `brewery_id` set on the result and on every sighting, `error="empty"` when the page has no check-ins); `BeerList(total: int | None, beers: list[BreweryBeer])`; `parse_beer_list(html: str) -> BeerList`; `fetch_brewery_list(client, brewery: Brewery, now: datetime) -> SourceResult` (key `untappd_brewery_list:<brewery_id>`, source `untappd_brewery_list`, `ok=False, error="incomplete"` unless `total == len(beers)`); constant `BEER_LIST_URL = "https://untappd.com/w/brewery/{brewery_id}/beer"`.

Источник 4.2 спецификации. Страница пивоварни (`Brewery.url`) при каждом сборе Untappd даёт последние чекины её пива; общий парсер из `untappd_checkins` оставляет только заведения из `places.yaml`, и каждое наблюдение получает `brewery_id` пивоварни (👀, §4.2, §6). Страница списка сортов даёт все сорта пивоварни для события 🏭. Ошибка загрузки, страница без чекинов и неполный список (число сортов не равно счётчику «N Beers») дают `ok=False`, чтобы сломанная страница не выглядела как «новинок нет» (§10).

> **Список сортов проверяется на настоящей странице во время запуска.** Сохранённой страницы списка сортов нет, поэтому `parse_beer_list` в этой задаче проверен только на синтетическом HTML из теста. Шапка в нём скопирована с настоящей страницы пивоварни Dargett (тот же счётчик «39 Beers»), строки повторяют разметку Untappd `div.beer-item`. Адрес `BEER_LIST_URL` тоже не проверен. Настоящая ссылка — `/w/dargett-brewery/265165/beer`, но slug пивоварни в `places.yaml` не хранится, и вместо него стоит условное `brewery`. Поэтому у всех пивоварен `list_enabled: false`. На первом ручном прогоне (§4.2, §11) нужно открыть `https://untappd.com/w/brewery/<brewery_id>/beer` и проверить три вещи:
> - адрес открывает список;
> - без входа видны все сорта, и их число совпадает со счётчиком «N Beers»;
> - `parse_beer_list` читает эту страницу.
>
> Затем страницу сохраняем в `tests/fixtures/untappd/` и добавляем тест на неё. Только после этого ставим `list_enabled: true`. Если что-то не сходится, источник возвращает `http` или `incomplete` и событий не создаёт. Тогда 🏭 в v1 не используется.

- [ ] **Step 1: Write the failing test**

Create `tests/test_untappd_brewery.py`:
```python
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from taps.config import Brewery, Config, Place, Settings
from taps.fetch import FetchError
from taps.model import BreweryBeer
from taps.sources.untappd_brewery import BeerList, fetch_brewery_checkins, fetch_brewery_list, parse_beer_list
from taps.sources.untappd_checkins import checkins_to_sightings, parse_checkins
from tests.helpers import fixture_text

NOW = datetime(2025, 11, 16, 12, 0, tzinfo=timezone.utc)
DARGETT = Brewery(id="dargett", name="Dargett", brewery_id=265165, slug="dargett-brewery")
LIST_URL = "https://untappd.com/w/dargett-brewery/265165/beer"

# As in places.yaml: menu from buy.am, Untappd venue 4640403 ("Dargett Craft Brewery").
DARGETT_PUB = Place(
    id="dargett-brewpub", name="Dargett Brewpub", kind="brewpub",
    sources={"buyam": {"url": "https://buy.am/en/restaurants/dargett"}},
    brewery_id=265165, brewery_name="Dargett", untappd_venue_id=4640403)
# A Yerevan bar from the page's check-ins, added the way Oleg would add one.
FERMENT = Place(id="ferment", name="Ferment", kind="bar",
                sources={"untappd_checkins": {"slug": "ferment", "venue_id": 13510969}})
CONFIG = Config(places={p.id: p for p in (DARGETT_PUB, FERMENT)}, breweries=(DARGETT,), settings=Settings())


class FakeClient:
    """Stands in for UntappdClient: returns one page or raises one FetchError."""

    def __init__(self, html=None, error=None):
        self.html, self.error, self.urls = html, error, []

    def get(self, url):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.html


def brewery_page():
    return fixture_text("untappd/dargett_brewery.html")


# --- brewery page check-ins (real page) ---------------------------------------------------

def test_checkins_from_real_brewery_page():
    client = FakeClient(brewery_page())
    result = fetch_brewery_checkins(client, DARGETT, CONFIG, NOW, {})
    assert client.urls == ["https://untappd.com/brewery/265165"]
    assert (result.key, result.source, result.ok, result.error) == (
        "untappd_brewery:265165", "untappd_brewery", True, None)
    assert (result.brewery_id, result.place_id) == (265165, None)
    # 20 check-ins on the page. Untappd at Home (4), Caffe Napoli, Number 8 in Tbilisi (3)
    # and the 2 check-ins without a venue are not places of the config.
    assert [(s.place_id, s.checkin_id) for s in result.sightings] == [
        ("ferment", 1528689976), ("ferment", 1528688333),
        ("dargett-brewpub", 1528656512), ("dargett-brewpub", 1528654307),
        ("dargett-brewpub", 1528643628), ("dargett-brewpub", 1528642716),
        ("dargett-brewpub", 1528641971), ("dargett-brewpub", 1528641572),
        ("dargett-brewpub", 1528641034), ("dargett-brewpub", 1528638060),
    ]
    assert {(s.source, s.kind, s.brewery_id) for s in result.sightings} == {
        ("untappd_brewery", "checkin", 265165)}


def test_checkin_sighting_fields():
    page = brewery_page()
    result = fetch_brewery_checkins(FakeClient(page), DARGETT, CONFIG, NOW, {})
    # The shared check-in sightings, only tagged with the page's brewery id.
    shared = checkins_to_sightings(parse_checkins(page), CONFIG, "untappd_brewery", NOW, {})
    assert result.sightings == [replace(s, brewery_id=265165) for s in shared]
    by_id = {s.checkin_id: s for s in result.sightings}
    cherry = by_id[1528641034]
    assert (cherry.place_id, cherry.beer_key, cherry.untappd_beer_id, cherry.name, cherry.brewery) == (
        "dargett-brewpub", "u:1559917", 1559917, "Cherry Ale (Morello)", "Dargett Brewery")
    assert (cherry.serving, cherry.seen_at, cherry.url, cherry.at_home) == (
        None, datetime(2025, 11, 15, 15, 13, 4, tzinfo=timezone.utc), "https://untappd.com/beer/1559917", False)
    assert (cherry.rating, cherry.style, cherry.abv) == (None, None, None)   # never a personal rating
    # serving is passed through as is: rules.py decides which servings count
    assert [(by_id[i].beer_key, by_id[i].serving) for i in (1528689976, 1528688333, 1528638060)] == [
        ("u:1570010", "Taster"), ("u:1570010", "Draft"), ("u:2301685", "Draft")]


def test_no_checkins_at_config_places_is_ok():
    tap_station = Place(id="tap-station", name="Tap Station", kind="bar",
                        sources={"untappd_checkins": {"slug": "tap-station", "venue_id": 8234456}})
    config = Config(places={"tap-station": tap_station}, breweries=(DARGETT,), settings=Settings())
    result = fetch_brewery_checkins(FakeClient(brewery_page()), DARGETT, config, NOW, {})
    assert (result.ok, result.error, result.sightings) == (True, None, [])


def test_page_without_checkins_is_empty():
    # Changed markup or a login wall: the brewery header is there, no check-in parses.
    page = brewery_page().replace('data-checkin-id="', 'data-old-id="')
    result = fetch_brewery_checkins(FakeClient(page), DARGETT, CONFIG, NOW, {})
    assert (result.key, result.source, result.ok, result.error, result.sightings) == (
        "untappd_brewery:265165", "untappd_brewery", False, "empty", [])


@pytest.mark.parametrize("kind", ["network", "cloudflare", "blocked", "budget", "http"])
def test_checkins_fetch_error(kind):
    result = fetch_brewery_checkins(FakeClient(error=FetchError(kind, DARGETT.url)), DARGETT, CONFIG, NOW, {})
    assert (result.key, result.source, result.ok, result.error, result.brewery_id, result.sightings) == (
        "untappd_brewery:265165", "untappd_brewery", False, kind, 265165, [])


# --- beer list (synthetic page) -----------------------------------------------------------
# No capture of the real list page (/w/<slug>/<id>/beer) exists yet. The header below is copied
# from the real brewery page (same "N Beers" counter); rows mirror Untappd's div.beer-item markup
# with real Dargett ids, names and styles from the page's "Top Beers" sidebar and made-up ABVs.
# parse_beer_list is checked against the live page at launch; list_enabled stays false until then.
HEADER = """
<div class="box b_info"><div class="content">
 <div class="top">
  <div class="basic">
   <div class="name">
    <h1>
						Dargett Brewery					</h1>
    <p class="brewery">
						Yerevan, Armenia					</p>
    <p class="style">Brew Pub</p>
   </div>
  </div>
  <div class="stats"><p><span class="stat">Total</span> <span class="count">28,799</span></p></div>
 </div>
 <div class="details brewery  claimed">
  <div class="caps" data-rating="3.51"></div><span class="num">(3.51)</span>
  <p class="raters">
				24,361 Ratings			</p><p class="count">
				<a href="/Dargett/beer">{total}</a>
			</p>
 </div>
</div></div>
"""
ROW = """
<div class="beer-item " data-bid="{bid}">
 <a class="label" href="/b/{slug}/{bid}">
  <img src="https://assets.untappd.com/site/beer_logos/beer-{bid}.jpeg" alt="{name} label"></a>
 <div class="beer-details">
  <p class="name"><a href="/b/{slug}/{bid}">{name}</a></p>
  <p class="style">{style}</p>
  <div class="desc desc-half-{bid}">Brewed in Yerevan.</div>
 </div>
 <div class="details beer">
  <div class="abv">
			{abv}
		</div>
  <div class="ibu">
			N/A IBU
		</div>
  <div class="rating"><div class="caps" data-rating="3.62"></div><span class="num">(3.62)</span></div>
  <div class="raters">512 Ratings</div>
 </div>
</div>
"""
ROWS = [
    dict(bid=1547626, slug="dargett-brewery-black-ipa-milestones", name="Black IPA (Milestones)",
         style="IPA - Black / Cascadian Dark Ale", abv="6.5% ABV"),
    dict(bid=2508041, slug="dargett-brewery-armenian-imperial-stout-brandy-barrel-aged",
         name="Armenian Imperial Stout (Brandy Barrel Aged)", style="Stout - Imperial / Double", abv="11% ABV"),
    dict(bid=1538345, slug="dargett-brewery-vienna-lager-metamorphosis", name="Vienna Lager (Metamorphosis)",
         style="Lager - Vienna", abv="N/A ABV"),
]


def list_page(total="3 Beers", header=HEADER):
    return ("<html><body>" + header.replace("{total}", total) + '<div class="beer-container beer-list pad">'
            + "".join(ROW.format(**r) for r in ROWS) + "</div></body></html>")


def test_parse_beer_list():
    assert parse_beer_list(list_page()) == BeerList(total=3, beers=[
        BreweryBeer(brewery_id=0, untappd_beer_id=1547626, name="Black IPA (Milestones)",
                    brewery="Dargett Brewery", style="IPA - Black / Cascadian Dark Ale", abv=6.5,
                    url="https://untappd.com/beer/1547626"),
        BreweryBeer(brewery_id=0, untappd_beer_id=2508041, name="Armenian Imperial Stout (Brandy Barrel Aged)",
                    brewery="Dargett Brewery", style="Stout - Imperial / Double", abv=11.0,
                    url="https://untappd.com/beer/2508041"),
        BreweryBeer(brewery_id=0, untappd_beer_id=1538345, name="Vienna Lager (Metamorphosis)",
                    brewery="Dargett Brewery", style="Lager - Vienna", abv=None,
                    url="https://untappd.com/beer/1538345"),
    ])


@pytest.mark.parametrize("counter, total", [
    ("39 Beers", 39), ("1,204 Beers", 1204), ("1 Beer", 1), ("Beers", None), ("", None)])
def test_parse_total(counter, total):
    assert parse_beer_list(list_page(total=counter)).total == total


def test_parse_real_brewery_page_header():
    # The list page shares this header. The brewery page links beers in check-ins and in the
    # "Top Beers" sidebar, but those are not list rows.
    assert parse_beer_list(brewery_page()) == BeerList(total=39, beers=[])


def test_broken_rows_are_skipped_and_make_the_list_incomplete():
    page = (list_page()
            .replace('<a href="/b/dargett-brewery-black-ipa-milestones/1547626">Black IPA (Milestones)</a>',
                     '<a href="/b/dargett-brewery-black-ipa-milestones/1547626"> </a>')
            .replace('<a href="/b/dargett-brewery-vienna-lager-metamorphosis/1538345">',
                     '<a href="/beer/1538345">'))
    parsed = parse_beer_list(page)
    assert (parsed.total, [b.untappd_beer_id for b in parsed.beers]) == (3, [2508041])
    assert fetch_brewery_list(FakeClient(page), DARGETT, NOW).error == "incomplete"


# --- beer list fetch ----------------------------------------------------------------------

def test_fetch_brewery_list_complete():
    client = FakeClient(list_page())
    result = fetch_brewery_list(client, DARGETT, NOW)
    assert client.urls == [LIST_URL]
    assert (result.key, result.source, result.ok, result.error) == (
        "untappd_brewery_list:265165", "untappd_brewery_list", True, None)
    assert (result.brewery_id, result.place_id, result.sightings) == (265165, None, [])
    assert result.brewery_beers[0] == BreweryBeer(
        brewery_id=265165, untappd_beer_id=1547626, name="Black IPA (Milestones)", brewery="Dargett Brewery",
        style="IPA - Black / Cascadian Dark Ale", abv=6.5, url="https://untappd.com/beer/1547626")
    assert [(b.brewery_id, b.untappd_beer_id, b.brewery) for b in result.brewery_beers] == [
        (265165, 1547626, "Dargett Brewery"), (265165, 2508041, "Dargett Brewery"),
        (265165, 1538345, "Dargett Brewery")]


def test_fetch_brewery_list_falls_back_to_config_name():
    result = fetch_brewery_list(FakeClient(list_page(header=HEADER.replace("Dargett Brewery", ""))), DARGETT, NOW)
    assert result.ok
    assert {b.brewery for b in result.brewery_beers} == {"Dargett"}


@pytest.mark.parametrize("counter", ["39 Beers", "4 Beers", "2 Beers", "Beers"])
def test_fetch_brewery_list_incomplete(counter):
    # "39 Beers" with 3 rows: only the first page of the list is visible without login
    result = fetch_brewery_list(FakeClient(list_page(total=counter)), DARGETT, NOW)
    assert (result.key, result.source, result.ok, result.error, result.brewery_id, result.brewery_beers) == (
        "untappd_brewery_list:265165", "untappd_brewery_list", False, "incomplete", 265165, [])


def test_fetch_brewery_list_on_brewery_page_is_incomplete():
    # If the list URL lands on the brewery page: counter 39, no list rows -> not an empty success
    result = fetch_brewery_list(FakeClient(brewery_page()), DARGETT, NOW)
    assert (result.ok, result.error, result.brewery_beers) == (False, "incomplete", [])


@pytest.mark.parametrize("kind", ["network", "cloudflare", "blocked", "budget", "http"])
def test_fetch_brewery_list_fetch_error(kind):
    result = fetch_brewery_list(FakeClient(error=FetchError(kind, LIST_URL)), DARGETT, NOW)
    assert (result.key, result.ok, result.error, result.brewery_id, result.brewery_beers) == (
        "untappd_brewery_list:265165", False, kind, 265165, [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 120 .venv/bin/pytest tests/test_untappd_brewery.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taps.sources.untappd_brewery'` (ошибка при сборе тестов, `1 error`)

- [ ] **Step 3: Write minimal implementation**

Create `taps/sources/untappd_brewery.py`:
```python
"""Source 4.2: Untappd brewery pages: recent check-ins (👀) and the full beer list (🏭)."""
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime

from bs4 import BeautifulSoup, Tag

from taps.config import Brewery, Config
from taps.fetch import FetchError, UntappdClient
from taps.model import BreweryBeer, SourceResult
from taps.sources.untappd_checkins import checkins_to_sightings, parse_checkins

# The page links the list as /w/<slug>/<brewery_id>/beer. The slug in places.yaml is unverified for
# most breweries; that Untappd accepts it is checked on the live page at launch (list_enabled).
BEER_LIST_URL = "https://untappd.com/w/{slug}/{brewery_id}/beer"
BEER_HREF_RE = re.compile(r"^/b/[^/]+/(\d+)/?$")
TOTAL_RE = re.compile(r"^(\d[\d,]*)\s+Beers?$", re.I)
ABV_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")


@dataclass(frozen=True)
class BeerList:
    total: int | None          # "N Beers" counter of the brewery header; None if not found
    beers: list[BreweryBeer]   # brewery_id is 0 here, fetch_brewery_list sets it


def _text(tag: Tag | None) -> str:
    return " ".join(tag.get_text(" ").split()) if tag else ""


def fetch_brewery_checkins(client: UntappdClient, brewery: Brewery, config: Config, now: datetime,
                           brewery_aliases: Mapping[str, str]) -> SourceResult:
    """Recent check-ins from the brewery page, kept only at venues of enabled places."""
    key = f"untappd_brewery:{brewery.brewery_id}"
    try:
        html = client.get(brewery.url)
    except FetchError as e:
        return SourceResult(key=key, source="untappd_brewery", ok=False, error=e.kind,
                            brewery_id=brewery.brewery_id)
    checkins = parse_checkins(html)
    if not checkins:   # the page always lists recent check-ins: none means changed markup or a login wall
        return SourceResult(key=key, source="untappd_brewery", ok=False, error="empty",
                            brewery_id=brewery.brewery_id)
    sightings = checkins_to_sightings(checkins, config, "untappd_brewery", now, brewery_aliases)
    return SourceResult(key=key, source="untappd_brewery", ok=True, brewery_id=brewery.brewery_id,
                        sightings=[replace(s, brewery_id=brewery.brewery_id) for s in sightings])


def parse_beer_list(html: str) -> BeerList:
    """Brewery beer list page. Rows without a /b/ link or name are skipped, so the total check fails."""
    soup = BeautifulSoup(html, "html.parser")
    total = next((int(m.group(1).replace(",", "")) for p in soup.select("p.count")
                  if (m := TOTAL_RE.match(_text(p)))), None)
    brewery = _text(soup.select_one("div.name h1"))
    beers = []
    for item in soup.select("div.beer-item"):
        link = item.select_one("p.name a[href]")
        m = BEER_HREF_RE.match(link["href"]) if link else None
        if not m or not _text(link):
            continue
        abv = ABV_RE.search(_text(item.select_one("div.abv")))   # "N/A ABV" -> None
        beers.append(BreweryBeer(
            brewery_id=0, untappd_beer_id=int(m.group(1)), name=_text(link), brewery=brewery,
            style=_text(item.select_one("p.style")) or None,
            abv=float(abv.group(1)) if abv else None,
            url=f"https://untappd.com/beer/{m.group(1)}",
        ))
    return BeerList(total=total, beers=beers)


def fetch_brewery_list(client: UntappdClient, brewery: Brewery, now: datetime) -> SourceResult:
    """All beers of a brewery; ok only if the page shows as many beers as its "N Beers" counter.

    now is unused: the contract keeps it for symmetry with the other sources.
    """
    key = f"untappd_brewery_list:{brewery.brewery_id}"
    try:
        page = parse_beer_list(client.get(BEER_LIST_URL.format(slug=brewery.slug, brewery_id=brewery.brewery_id)))
    except FetchError as e:
        return SourceResult(key=key, source="untappd_brewery_list", ok=False, error=e.kind,
                            brewery_id=brewery.brewery_id)
    if page.total != len(page.beers):   # no counter (None), or e.g. only the first page without login
        return SourceResult(key=key, source="untappd_brewery_list", ok=False, error="incomplete",
                            brewery_id=brewery.brewery_id)
    beers = [replace(b, brewery_id=brewery.brewery_id, brewery=b.brewery or brewery.name) for b in page.beers]
    return SourceResult(key=key, source="untappd_brewery_list", ok=True, brewery_id=brewery.brewery_id,
                        brewery_beers=beers)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `timeout 120 .venv/bin/pytest tests/test_untappd_brewery.py -v`
Expected: `29 passed`

- [ ] **Step 5: Commit**

```bash
git add tests/test_untappd_brewery.py taps/sources/untappd_brewery.py
git commit -m "feat: add Untappd brewery source for check-ins and beer list"
```

---

### Task 10: Источник buy.am (разливное Dargett)

**Files:**
- Create: `taps/sources/buyam.py`
- Test: `tests/test_buyam.py`

**Interfaces:**
- Consumes: `taps.config.Place` (`id`, `brewery_name`, `sources["buyam"]["url"]`); `taps.fetch.Http.get(url, headers=None) -> HttpResponse`, `FetchError` (`.kind`), `HttpResponse` (в тестах); `taps.model.Sighting`, `SourceResult`, `untappd_n_key(brewery, name, brewery_aliases)`; `tests.helpers.fixture_text`.
- Produces: `fetch_buyam(http: Http, place: Place, now: datetime, brewery_aliases: Mapping[str, str]) -> SourceResult` (key `"buyam:<place_id>"`, source `"buyam"`, kind `menu`; `error` = `FetchError.kind` или `"bad_response"`); `parse_page(html: str) -> BuyamPage(supplier_id: int, department_id: int, products_count: int)`; `parse_buyam(text: str) -> list[BuyamItem]`; `BuyamItem(item_id: str, name: str, price_amd: int | None)`; `clean_name(title: str, brewery: str | None) -> str`; константы `DEPARTMENT`, `LISTING_URL`, `LISTING_HEADERS`.

Источник ✅ для Dargett Brewpub (спец. §4.4, §10). В `__NEXT_DATA__` страницы `buy.am/en/restaurants/dargett` есть только отделы со счётчиками (`"Draught beer"`: id 9890, `productsCount` 16), а сами позиции браузер догружает из API сайта. Поэтому источник делает два запроса: страница даёт id ресторана и отдела, `https://api.buy.am/products/listing` отдаёт позиции. Адрес API найден в JS-бандле сайта и проверен живым запросом 2026-09-24; в тесте лежит урезанная копия настоящего ответа. Результат принимается, только если позиций ровно `productsCount`. Из названия убираются «Draught beer», пивоварня и объём, ключ строится как `n:<пивоварня> <имя>`, то есть той же формы, что у Untappd.

- [ ] **Step 1: Write the failing test**

Create `tests/test_buyam.py`:
```python
import json
from datetime import datetime, timezone

import pytest

from taps.config import Place
from taps.fetch import FetchError, HttpResponse
from taps.model import Sighting, untappd_n_key
from taps.sources.buyam import BuyamItem, BuyamPage, clean_name, fetch_buyam, parse_buyam, parse_page
from tests.helpers import fixture_text

NOW = datetime(2026, 9, 24, 6, 17, tzinfo=timezone.utc)
PAGE_URL = "https://buy.am/en/restaurants/dargett"
LIST_URL = "https://api.buy.am/products/listing?skip=0&s=1162&f=9890&take=100"
PAGE = fixture_text("buyam/dargett.html")
PLACE = Place(id="dargett-brewpub", name="Dargett Brewpub", kind="brewpub", sources={"buyam": {"url": PAGE_URL}},
              brewery_id=265165, brewery_name="Dargett", untappd_venue_id=4640403)

# Real reply of LIST_URL (api.buy.am, 2026-09-24), cut down to the fields the parser reads.
DRAUGHT = [
    (174894, "Draught beer Dargett Bohemian Pilsner 1l", 2000),
    (174895, "Draught beer Dargett Bavarian Weizen 1l", 2000),
    (174896, "Draught beer Dargett Oatmeal Stout 1l", 2000),
    (174897, "Draught beer Dargett Munich Lager 1l", 2000),
    (174898, "Draught beer Dargett Vienna Lager 1l", 2000),
    (174900, "Draught beer Dargett Biere Blanche 1l", 2000),
    (174901, "Draught beer Dargett Apricot Ale 1l", 2500),
    (174902, "Draught beer Dargett Belgian Tripel 1l", 2000),
    (174903, "Draught beer Dargett American Pale Ale 1l", 2500),
    (174904, "Draught beer Dargett Session IPA 1l", 2500),
    (174905, "Draught beer Dargett India Pale Ale 1l", 2500),
    (174906, "Draught beer Dargett Black IPA 1l", 2500),
    (174907, "Draught beer Dargett Apple Cider 1l", 3000),
    (174908, "Draught beer Dargett Cherry Ale 1l", 2500),
    (174909, "Draught beer Dargett Baltic Porter 1l", 2500),
    (174911, "Draught beer Dargett Imperial IPA 1l", 3000),
]


def listing(rows=DRAUGHT) -> str:
    items = [{"id": i, "name": n, "nameEn": n, "basePrice": p} for i, n, p in rows]
    return json.dumps({"code": 200, "data": {"items": items, "totalCount": len(items)}})


def next_data_page(restaurant: dict) -> str:
    data = json.dumps({"props": {"pageProps": {"restaurant": restaurant}}})
    return f'<html><body><script id="__NEXT_DATA__" type="application/json">{data}</script></body></html>'


class FakeHttp:
    def __init__(self, pages: dict):
        self.pages = pages
        self.calls = []

    def get(self, url, headers=None):
        self.calls.append((url, headers))
        body = self.pages[url]
        if isinstance(body, Exception):
            raise body
        return HttpResponse(200, {"content-type": "text/html"}, body)


# --- parse_page ----------------------------------------------------------------

def test_parse_page_finds_draught_department_in_next_data():
    assert parse_page(PAGE) == BuyamPage(supplier_id=1162, department_id=9890, products_count=16)


def test_parse_page_fixture_has_no_products_only_counts():
    # Why the listing API is needed: the saved page renders skeletons, the items come from the browser.
    assert "Bohemian Pilsner" not in PAGE
    assert '"name":"Draught beer","productsCount":16' in PAGE


@pytest.mark.parametrize("html", [
    "<html><body><div class='skeleton'></div></body></html>",                   # no __NEXT_DATA__
    '<script id="__NEXT_DATA__" type="application/json">{"props":</script>',    # broken JSON
    '<script id="__NEXT_DATA__" type="application/json"></script>',             # empty
    # supplier id is a string
    next_data_page({"id": "1162", "departments": [{"id": 9890, "name": "Draught beer", "productsCount": 16}]}),
    next_data_page({"id": 1162, "departments": None}),                                    # no department list
    next_data_page({"id": 1162, "departments": [{"id": 9890, "name": "Draught beer"}]}),  # no productsCount
])
def test_parse_page_rejects_broken_pages(html):
    with pytest.raises(ValueError):
        parse_page(html)


def test_parse_page_without_draught_department_raises():
    assert PAGE.count('"name":"Draught beer"') == 1
    with pytest.raises(ValueError, match="Draught beer"):
        parse_page(PAGE.replace('"name":"Draught beer"', '"name":"Lemonades"'))


def test_parse_page_matches_department_name_loosely():
    html = next_data_page({"id": 7, "departments": [{"id": 1, "name": "Beer", "productsCount": 9},
                                                    {"id": 2, "name": " Draught Beer ", "productsCount": 3}]})
    assert parse_page(html) == BuyamPage(7, 2, 3)


# --- parse_buyam ---------------------------------------------------------------

def test_parse_buyam_reads_real_listing():
    items = parse_buyam(listing())
    assert len(items) == 16
    assert items[0] == BuyamItem("174894", "Draught beer Dargett Bohemian Pilsner 1l", 2000)
    assert items[-1] == BuyamItem("174911", "Draught beer Dargett Imperial IPA 1l", 3000)
    prices = {i.name: i.price_amd for i in items}
    assert prices["Draught beer Dargett Apricot Ale 1l"] == 2500
    assert prices["Draught beer Dargett Apple Cider 1l"] == 3000
    assert sorted(set(prices.values())) == [2000, 2500, 3000]


def test_parse_buyam_prefers_english_name():
    # Without Content-Language the API sends an Armenian `name`; `nameEn` stays English.
    row = {"id": 174894, "name": "Լցնովի գարեջուր Dargett Bohemian Pilsner 1լ",
           "nameEn": "Draught beer Dargett Bohemian Pilsner 1l", "basePrice": 2000}
    fallback = {"id": 5, "name": "Draught beer Dargett Gose 1l", "nameEn": "", "basePrice": 2500}
    text = json.dumps({"data": {"items": [row, fallback]}})
    assert [i.name for i in parse_buyam(text)] == ["Draught beer Dargett Bohemian Pilsner 1l",
                                                  "Draught beer Dargett Gose 1l"]


@pytest.mark.parametrize("price", [None, 0, -5, "2000", True, 2000.5])
def test_parse_buyam_unusable_price_is_none(price):
    text = json.dumps({"data": {"items": [{"id": 1, "nameEn": "Draught beer Dargett Gose 1l", "basePrice": price}]}})
    assert parse_buyam(text) == [BuyamItem("1", "Draught beer Dargett Gose 1l", None)]


@pytest.mark.parametrize("text", [
    "<html>maintenance</html>",
    json.dumps({"code": 500, "data": None}),
    json.dumps({"code": 200, "data": {"items": {"174894": "x"}}}),
    json.dumps({"data": {"items": ["Draught beer Dargett Gose 1l"]}}),
    json.dumps({"data": {"items": [{"nameEn": "Draught beer Dargett Gose 1l", "basePrice": 2000}]}}),
    json.dumps({"data": {"items": [{"id": True, "nameEn": "Draught beer Dargett Gose 1l"}]}}),
    json.dumps({"data": {"items": [{"id": 1, "nameEn": "", "name": " "}]}}),
])
def test_parse_buyam_rejects_broken_listing(text):
    with pytest.raises(ValueError):
        parse_buyam(text)


# --- clean_name ----------------------------------------------------------------

@pytest.mark.parametrize("title, brewery, name", [
    ("Draught beer Dargett Bohemian Pilsner 1l", "Dargett", "Bohemian Pilsner"),
    ("Draught beer Dargett Apple Cider 1l", "Dargett", "Apple Cider"),
    ("draught beer DARGETT Session IPA 0.5 L", "Dargett", "Session IPA"),
    ("Draught beer Dargett Gose 500ml", "Dargett", "Gose"),
    ("Draught beer Dargetts Ale 1l", "Dargett", "Dargetts Ale"),          # brewery only as a whole word
    ("Draught beer Dargett Bohemian Pilsner 1l", None, "Dargett Bohemian Pilsner"),
    ("Draught beer Dargett 1l", "Dargett", "Draught beer Dargett 1l"),   # nothing left: keep the title
])
def test_clean_name(title, brewery, name):
    assert clean_name(title, brewery) == name


# --- fetch_buyam ---------------------------------------------------------------

def test_fetch_buyam_builds_menu_sightings():
    http = FakeHttp({PAGE_URL: PAGE, LIST_URL: listing()})
    result = fetch_buyam(http, PLACE, NOW, {})
    assert (result.key, result.source, result.ok, result.error, result.place_id, result.full) == \
        ("buyam:dargett-brewpub", "buyam", True, None, "dargett-brewpub", True)
    assert [url for url, _ in http.calls] == [PAGE_URL, LIST_URL]
    assert http.calls[1][1] == {"Accept": "application/json", "Content-Language": "en"}
    assert len(result.sightings) == 16
    assert result.sightings[0] == Sighting(
        place_id="dargett-brewpub", source="buyam", beer_key="n:dargett bohemian pilsner",
        title="Draught beer Dargett Bohemian Pilsner 1l", name="Bohemian Pilsner", seen_at=NOW,
        brewery="Dargett", price_amd=2000, volume_ml=1000, container="draft", url=PAGE_URL,
    )
    assert result.sightings[0].kind == "menu"
    by_name = {s.name: s for s in result.sightings}
    assert by_name["Imperial IPA"].beer_key == "n:dargett imperial ipa"
    assert by_name["Imperial IPA"].price_amd == 3000
    assert by_name["Biere Blanche"].beer_key == "n:dargett biere blanche"
    assert len({s.beer_key for s in result.sightings}) == 16
    assert {s.brewery for s in result.sightings} == {"Dargett"}
    assert all(s.untappd_beer_id is None and s.shop_item_id is None for s in result.sightings)


def test_fetch_buyam_key_is_brewery_plus_name_like_untappd():
    result = fetch_buyam(FakeHttp({PAGE_URL: PAGE, LIST_URL: listing()}), PLACE, NOW, {})
    black_ipa = next(s for s in result.sightings if s.name == "Black IPA")
    assert black_ipa.beer_key == untappd_n_key("Dargett", "Black IPA") == "n:dargett black ipa"


def test_fetch_buyam_applies_brewery_aliases():
    result = fetch_buyam(FakeHttp({PAGE_URL: PAGE, LIST_URL: listing()}), PLACE, NOW, {"Dargett": "Darget"})
    assert result.sightings[0].beer_key == "n:darget bohemian pilsner"


def test_fetch_buyam_reads_volume_from_title():
    page = next_data_page({"id": 1162, "departments": [{"id": 9890, "name": "Draught beer", "productsCount": 4}]})
    rows = [(1, "Draught beer Dargett Gose 1l", 2500), (2, "Draught beer Dargett Kvass 0,5 l", 900),
            (3, "Draught beer Dargett Mild 330ml", 1200), (4, "Draught beer Dargett Pint Special", 1500)]
    result = fetch_buyam(FakeHttp({PAGE_URL: page, LIST_URL: listing(rows)}), PLACE, NOW, {})
    assert [(s.name, s.volume_ml) for s in result.sightings] == \
        [("Gose", 1000), ("Kvass", 500), ("Mild", 330), ("Pint Special", None)]


def test_fetch_buyam_place_without_brewery_name_skips_empty_keys():
    place = Place(id="some-pub", name="Some Pub", kind="bar", sources={"buyam": {"url": PAGE_URL}})
    page = next_data_page({"id": 1162, "departments": [{"id": 9890, "name": "Draught beer", "productsCount": 2}]})
    rows = [(1, "Draught beer Dargett Bohemian Pilsner 1l", 2000), (2, "Draught beer 1l", 1500)]
    result = fetch_buyam(FakeHttp({PAGE_URL: page, LIST_URL: listing(rows)}), place, NOW, {})
    assert result.ok
    assert [(s.beer_key, s.name, s.brewery) for s in result.sightings] == \
        [("n:dargett bohemian pilsner", "Dargett Bohemian Pilsner", None)]


@pytest.mark.parametrize("pages, error, calls", [
    ({PAGE_URL: FetchError("network", PAGE_URL)}, "network", 1),
    ({PAGE_URL: FetchError("cloudflare", PAGE_URL)}, "cloudflare", 1),
    ({PAGE_URL: "<html><body>skeleton</body></html>"}, "bad_response", 1),
    ({PAGE_URL: PAGE.replace('"name":"Draught beer"', '"name":"Lemonades"')}, "bad_response", 1),
    ({PAGE_URL: PAGE, LIST_URL: FetchError("http", "503 " + LIST_URL)}, "http", 2),
    ({PAGE_URL: PAGE, LIST_URL: "<html>maintenance</html>"}, "bad_response", 2),
    ({PAGE_URL: PAGE, LIST_URL: listing(DRAUGHT[:15])}, "bad_response", 2),                    # fewer than 16
    ({PAGE_URL: PAGE, LIST_URL: listing(DRAUGHT + [(1, "Pizza Margherita", 3900)])}, "bad_response", 2),
])
def test_fetch_buyam_failures(pages, error, calls):
    http = FakeHttp(pages)
    result = fetch_buyam(http, PLACE, NOW, {})
    assert (result.ok, result.error, result.sightings) == (False, error, [])
    assert (result.key, result.source, result.place_id) == ("buyam:dargett-brewpub", "buyam", "dargett-brewpub")
    assert len(http.calls) == calls
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 120 .venv/bin/pytest tests/test_buyam.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taps.sources.buyam'`

- [ ] **Step 3: Write minimal implementation**

Create `taps/sources/buyam.py`:
```python
"""Source ✅ buy.am (spec §4.4): the «Draught beer» department of a restaurant.

The restaurant page's __NEXT_DATA__ lists departments with product counts but no products (the browser
loads them), so the page gives the ids and the site's own listing API gives the items.
"""
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup

from taps.config import Place
from taps.fetch import FetchError, Http
from taps.model import Sighting, SourceResult, untappd_n_key

DEPARTMENT = "draught beer"
LISTING_URL = "https://api.buy.am/products/listing?skip=0&s={supplier}&f={department}&take=100"
LISTING_HEADERS = {"Accept": "application/json", "Content-Language": "en"}
_DRAUGHT_RE = re.compile(r"^\s*draught\s+beer\b", re.I)
_VOLUME_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(ml|l)\b", re.I)


@dataclass(frozen=True)
class BuyamPage:
    supplier_id: int
    department_id: int
    products_count: int


@dataclass(frozen=True)
class BuyamItem:
    item_id: str
    name: str               # as on buy.am: "Draught beer Dargett Bohemian Pilsner 1l"
    price_amd: int | None


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _get(obj: Any, *keys: str) -> Any:
    for key in keys:
        obj = obj.get(key) if isinstance(obj, dict) else None
    return obj


def parse_page(html: str) -> BuyamPage:
    """Restaurant page -> supplier id and its «Draught beer» department; ValueError when missing."""
    tag = BeautifulSoup(html, "html.parser").find("script", id="__NEXT_DATA__")
    if tag is None or not tag.string:
        raise ValueError("no __NEXT_DATA__")
    restaurant = _get(json.loads(tag.string), "props", "pageProps", "restaurant")
    departments = _get(restaurant, "departments")
    if not _is_int(_get(restaurant, "id")) or not isinstance(departments, list):
        raise ValueError("no restaurant in __NEXT_DATA__")
    for dep in departments:
        name = _get(dep, "name")
        if isinstance(name, str) and name.strip().lower() == DEPARTMENT:
            if not (_is_int(dep.get("id")) and _is_int(dep.get("productsCount"))):
                raise ValueError(f"bad Draught beer department: {dep!r}")
            return BuyamPage(restaurant["id"], dep["id"], dep["productsCount"])
    raise ValueError("no Draught beer department")


def parse_buyam(text: str) -> list[BuyamItem]:
    """api.buy.am /products/listing JSON -> items; ValueError when the shape is wrong."""
    rows = _get(json.loads(text), "data", "items")
    if not isinstance(rows, list):
        raise ValueError("no data.items")
    items = []
    for row in rows:
        item_id, name, price = _get(row, "id"), _get(row, "nameEn") or _get(row, "name"), _get(row, "basePrice")
        if not _is_int(item_id) or not isinstance(name, str) or not name.strip():
            raise ValueError(f"bad item {item_id!r}")
        items.append(BuyamItem(str(item_id), name, price if _is_int(price) and price > 0 else None))
    return items


def clean_name(title: str, brewery: str | None) -> str:
    """'Draught beer Dargett Bohemian Pilsner 1l', 'Dargett' -> 'Bohemian Pilsner'."""
    name = _VOLUME_RE.sub(" ", _DRAUGHT_RE.sub("", title))
    if brewery:
        name = re.sub(rf"^\s*{re.escape(brewery)}(?!\w)", "", name, flags=re.I)
    return " ".join(name.split()) or title


def _volume_ml(title: str) -> int | None:
    m = _VOLUME_RE.search(title)
    if m is None:
        return None
    value = float(m.group(1).replace(",", "."))
    return round(value if m.group(2).lower() == "ml" else value * 1000)


def fetch_buyam(http: Http, place: Place, now: datetime, brewery_aliases: Mapping[str, str]) -> SourceResult:
    """Page for the ids, then the department listing; any failure discards the whole result."""
    key, url = f"buyam:{place.id}", place.sources["buyam"]["url"]

    def fail(error: str) -> SourceResult:
        return SourceResult(key=key, source="buyam", ok=False, error=error, place_id=place.id)

    try:
        page = parse_page(http.get(url).text)
        listing_url = LISTING_URL.format(supplier=page.supplier_id, department=page.department_id)
        items = parse_buyam(http.get(listing_url, headers=LISTING_HEADERS).text)
    except FetchError as e:
        return fail(e.kind)
    except ValueError:
        return fail("bad_response")
    if len(items) != page.products_count:   # e.g. the API ignored the department filter and sent the whole menu
        return fail("bad_response")

    sightings = []
    for item in items:
        name = clean_name(item.name, place.brewery_name)
        beer_key = untappd_n_key(place.brewery_name, name, brewery_aliases)
        if beer_key == "n:":
            continue
        sightings.append(Sighting(
            place_id=place.id, source="buyam", beer_key=beer_key, title=item.name, name=name, seen_at=now,
            brewery=place.brewery_name, price_amd=item.price_amd, volume_ml=_volume_ml(item.name),
            container="draft", url=url,
        ))
    return SourceResult(key=key, source="buyam", ok=True, sightings=sightings, place_id=place.id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `timeout 120 .venv/bin/pytest tests/test_buyam.py -v`
Expected: `45 passed`

- [ ] **Step 5: Commit**

```bash
git add tests/test_buyam.py taps/sources/buyam.py
git commit -m "feat: add buy.am draught source for Dargett Brewpub"
```

---

### Task 11: Источник: Beer City

**Files:**
- Create: `taps/sources/beercity.py`
- Test: `tests/test_beercity.py`

**Interfaces:**
- Consumes: `taps.config.Place` (Task 2); `taps.fetch.Http.get(url, headers=None) -> HttpResponse`, `FetchError(kind, message="")`, `HttpResponse(status, headers, text)` (Task 6); `taps.model.Sighting`, `SourceResult`, `n_key(text, brewery_aliases)` (Task 1); `tests.helpers.fixture_text`.
- Produces: `parse_listing(text: str) -> BCListing(items: list[BCItem], page: int, pages: int)`; `BCItem(item_id: str, title: str, price_amd: int | None, in_stock: bool, url: str)`; `parse_product(html: str) -> BCProduct(brand: str | None, volume_ml: int | None, abv: float | None, country: str | None, container: str | None)`; `fetch_beercity(http: Http, place: Place, known_item_ids: set[str], full: bool, now: datetime, brewery_aliases: Mapping[str, str]) -> SourceResult` (key `beercity:<place_id>`, source `beercity`); `clean_name(title: str) -> str`; `MAX_PAGES = 100`.

Магазин Beer City (спека §4.5). Листинг двух категорий каталога приходит через XHR: JSON `{link, products}`, новые товары сверху. Страница товара `/en/products/<slug>/` запрашивается только для незнакомых номеров. Частичный прогон читает первую страницу каждой категории и листает дальше, пока на странице были незнакомые номера; полный обход идёт по счётчику «Page i of N». Любой сбой листинга (сеть, ответ не JSON, счётчик не той страницы) валит весь результат, чтобы неполный обход не затёр данные на сайте (§10).

- [ ] **Step 1: Write the failing test**

Create `tests/test_beercity.py`:
```python
import html
import json
from datetime import datetime, timezone

import pytest
from bs4 import BeautifulSoup

from taps.config import Place
from taps.fetch import FetchError, HttpResponse
from taps.model import Sighting
from taps.sources.beercity import (
    MAX_PAGES, BCItem, BCProduct, clean_name, fetch_beercity, parse_listing, parse_product,
)
from tests.helpers import fixture_text

NOW = datetime(2026, 9, 24, 6, 17, tzinfo=timezone.utc)
PLACE = Place(id="beer-city", name="Beer City", kind="shop", sources={"beercity": {}})
XHR = {"X-Requested-With": "XMLHttpRequest"}

BOTTLES_P1 = fixture_text("beercity/list_bottles_p1.json")      # Page 1 of 23, ids 2053..2040
BOTTLES_LAST = fixture_text("beercity/list_bottles_last.json")  # Page 23 of 23, id 266
DRAFT_LAST = fixture_text("beercity/list_draft_last.json")      # Page 3 of 3, ids 18..7
PRODUCT_IPA = fixture_text("beercity/product_ipa.html")         # item 2047
PRODUCT_NONALC = fixture_text("beercity/product_nonalc.html")   # item 2048
P1_IDS = ["2053", "2052", "2051", "2050", "2049", "2048", "2047", "2046", "2045", "2044", "2041", "2040"]
DRAFT_IDS = ["18", "14", "12", "11", "10", "7"]


def bottles(page):
    return f"https://www.beer-city.am/en/catalog/sshalcavac-garejur/?sorting=-id&page={page}"


def draft(page):
    return f"https://www.beer-city.am/en/catalog/lcnovi-garejur/?sorting=-id&page={page}"


def product(slug):
    return f"https://www.beer-city.am/en/products/{slug}/"


IPA_URL = product("garejur-hard-rut-dabl-ipa-045l")
NONALC_URL = product("garejur-pur-vayv-ipa-non-alco-045l")


def cards(listing_json):
    """Real card HTML of a captured listing, by item id."""
    soup = BeautifulSoup(json.loads(listing_json)["products"], "html.parser")
    return {c.select_one("[data-product]")["data-product"]: str(c) for c in soup.select(".product-item")}


P1_CARDS = cards(BOTTLES_P1)
DRAFT_CARDS = cards(DRAFT_LAST)


def card(item_id, title):
    """A card in the real markup (cloned from item 2053) with another id and title."""
    return (P1_CARDS["2053"].replace('data-product="2053"', f'data-product="{item_id}"')
            .replace("garejur-bronx-05l", f"item-{item_id}")
            .replace('Beer "Bronx" 0.5L', html.escape(title)))


def page_json(card_html, page, pages):
    counter = f'<div class="number-show-pagination m-none"><span>Page <b>{page}</b> of {pages}</span></div>'
    return json.dumps({"link": f"sorting=-id&page={page}", "products": "".join(card_html) + counter})


class FakeHttp:
    """Answers GET by URL; an Exception value is raised."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, headers=None):
        self.calls.append((url, dict(headers or {})))
        if url not in self.responses:
            pytest.fail(f"unexpected GET {url}")
        r = self.responses[url]
        if isinstance(r, Exception):
            raise r
        return HttpResponse(200, {}, r)


def fetch(responses, known=(), full=False, aliases=None):
    http = FakeHttp(responses)
    return fetch_beercity(http, PLACE, set(known), full, NOW, aliases or {}), http


def by_id(result):
    return {s.shop_item_id: s for s in result.sightings}


def ids(result):
    return [s.shop_item_id for s in result.sightings]


# --- parse_listing -----------------------------------------------------------

def test_parse_listing_first_bottles_page():
    listing = parse_listing(BOTTLES_P1)
    assert (listing.page, listing.pages) == (1, 23)
    assert [i.item_id for i in listing.items] == P1_IDS
    assert listing.items[0] == BCItem("2053", 'Beer "Bronx" 0.5L', 730, True, product("garejur-bronx-05l"))
    assert listing.items[6] == BCItem("2047", 'Beer "Hard root" Double IPA 0.45 l', 2200, True, IPA_URL)
    assert listing.items[8].title == 'Beer "Starnberger helles" 0,45l'
    assert all(i.in_stock for i in listing.items)


def test_parse_listing_last_pages_and_out_of_stock():
    last = parse_listing(BOTTLES_LAST)
    assert (last.page, last.pages) == (23, 23)
    assert last.items == [BCItem("266", 'Пиво "379 American Wheat Ale" Citrus 0,33л', 740, True,
                                 product("garejowr-379-american-wheat-ale-citrus-033l"))]
    draft_page = parse_listing(DRAFT_LAST)
    assert (draft_page.page, draft_page.pages) == (3, 3)
    assert [i.item_id for i in draft_page.items] == DRAFT_IDS
    # a.addtocart-but.diss-prod marks the only card that is out of stock
    assert draft_page.items[2] == BCItem("12", 'Draught beer "379" non-filtered 1l', 1700, False,
                                         product("garejur-379-chfiltrvac-1l"))
    assert [i.item_id for i in draft_page.items if not i.in_stock] == ["12"]


def test_parse_listing_without_counter_is_a_single_page():
    listing = parse_listing(json.dumps({"link": "", "products": P1_CARDS["2053"]}))
    assert (listing.page, listing.pages) == (1, 1)
    assert [i.item_id for i in listing.items] == ["2053"]


def test_parse_listing_missing_price_and_incomplete_cards():
    no_price = P1_CARDS["2052"].replace('<span class="wh-point">730</span>', "")
    no_id = P1_CARDS["2051"].replace('data-product="2051"', "")
    no_name = P1_CARDS["2050"].replace("prod-item-name", "prod-item-title")
    listing = parse_listing(page_json([no_price, no_id, no_name], 1, 1))
    assert listing.items == [BCItem("2052", 'Beer "Bronx black cherry" 0.5L', None, True,
                                    product("garejur-bronx-black-cheri-05l"))]


@pytest.mark.parametrize("text", ["<html>Just a moment...</html>", "[]", '{"link": "x"}', '{"products": null}'])
def test_parse_listing_rejects_wrong_shape(text):
    with pytest.raises(ValueError):
        parse_listing(text)


# --- parse_product / clean_name ----------------------------------------------

def test_parse_product_reads_brand_and_characteristics():
    assert parse_product(PRODUCT_IPA) == BCProduct(brand="Konix", volume_ml=450, abv=7.6, country="Russia",
                                                   container="can")   # "0.45 liter", "7.6 %", "Tin"
    assert parse_product(PRODUCT_NONALC) == BCProduct("Konix", 450, 0.5, "Russia", "can")


def test_parse_product_units_packaging_and_missing_fields():
    glass = (PRODUCT_IPA.replace("<span>0.45 liter</span>", "<span>500ml</span>")
             .replace("<span>7.6 %</span>", "<span>4,7 %</span>")
             .replace("<span>Tin </span>", "<span>Glass </span>"))
    p = parse_product(glass)
    assert (p.volume_ml, p.abv, p.container) == (500, 4.7, "bottle")
    assert parse_product("<html><body>Not found</body></html>") == BCProduct(None, None, None, None, None)


@pytest.mark.parametrize("title, name", [
    ('Beer "Hard root" Double IPA 0.45 l', "Hard root Double IPA"),
    ('Beer "Starnberger helles" 0,45l', "Starnberger helles"),
    ("Beer «Estrella Galicia» 0,5l", "Estrella Galicia"),
    ('Beer "Corona cero 0%" 0.33l', "Corona cero 0%"),
    ('Draught beer "Kellers" non-filtered 1l', "Kellers non-filtered"),
    ('Пиво "379 American Wheat Ale" Citrus 0,33л', "379 American Wheat Ale Citrus"),
])
def test_clean_name_drops_type_word_quotes_and_volume(title, name):
    assert clean_name(title) == name


# --- fetch_beercity ------------------------------------------------------------

def test_partial_run_without_new_ids_reads_only_first_page_of_each_category():
    result, http = fetch({bottles(1): BOTTLES_P1, draft(1): page_json(DRAFT_CARDS.values(), 1, 3)},
                         known=P1_IDS + DRAFT_IDS, aliases={"starnberger": "starnberger brauhaus"})
    assert http.calls == [(bottles(1), XHR), (draft(1), XHR)]   # no product pages, no page 2
    assert (result.ok, result.full, result.error) == (True, False, None)
    assert (result.key, result.source, result.place_id) == ("beercity:beer-city", "beercity", "beer-city")
    assert ids(result) == P1_IDS + DRAFT_IDS   # known items are reported too
    s = by_id(result)
    assert s["2045"] == Sighting(
        place_id="beer-city", source="beercity", beer_key="n:starnberger brauhaus helles",   # alias applied
        title='Beer "Starnberger helles" 0,45l', name="Starnberger helles", seen_at=NOW,
        shop_item_id="2045", price_amd=730, in_stock=True, category="sshalcavac-garejur",
        url=product("garejur-starnberger-heles-045l"))   # known: no product page, so no brand/abv/volume
    assert s["12"] == Sighting(
        place_id="beer-city", source="beercity", beer_key="n:379 non",
        title='Draught beer "379" non-filtered 1l', name="379 non-filtered", seen_at=NOW,
        shop_item_id="12", price_amd=1700, container="draft", in_stock=False, category="lcnovi-garejur",
        url=product("garejur-379-chfiltrvac-1l"))
    assert s["2050"].beer_key == s["2049"].beer_key == "n:mythos"   # 0.3L and 0.5L share a key


def test_partial_run_fetches_new_product_pages_and_walks_on_while_a_page_had_new_ids():
    page2 = page_json([card("2039", 'Beer "Tomato method Adjika" 0.45l'),
                       card("2038", 'Beer "1715 lvivske" 0.45l')], 2, 23)
    known = [i for i in P1_IDS if i not in ("2048", "2047")] + ["2039", "2038"] + DRAFT_IDS
    result, http = fetch({bottles(1): BOTTLES_P1, NONALC_URL: PRODUCT_NONALC, IPA_URL: PRODUCT_IPA,
                          bottles(2): page2, draft(1): page_json(DRAFT_CARDS.values(), 1, 3)}, known=known)
    # page 1 had new ids -> page 2; page 2 had none -> stop before page 3
    assert http.calls == [(bottles(1), XHR), (NONALC_URL, {}), (IPA_URL, {}), (bottles(2), XHR), (draft(1), XHR)]
    assert result.ok and result.full is False
    assert ids(result) == P1_IDS + ["2039", "2038"] + DRAFT_IDS
    s = by_id(result)
    assert s["2047"] == Sighting(
        place_id="beer-city", source="beercity", beer_key="n:hard root double ipa",
        title='Beer "Hard root" Double IPA 0.45 l', name="Hard root Double IPA", seen_at=NOW,
        brewery="Konix", shop_item_id="2047", abv=7.6, price_amd=2200, volume_ml=450, container="can",
        in_stock=True, category="sshalcavac-garejur", url=IPA_URL)
    assert (s["2048"].brewery, s["2048"].abv, s["2048"].name) == ("Konix", 0.5, "Pure wave IPA non alco")
    assert s["2053"].brewery is None


def test_failed_product_page_drops_only_that_item():
    known = [i for i in P1_IDS if i != "2048"] + DRAFT_IDS
    result, http = fetch({bottles(1): BOTTLES_P1, NONALC_URL: FetchError("http", "404"),
                          bottles(2): page_json([], 2, 23), draft(1): page_json(DRAFT_CARDS.values(), 1, 3)},
                         known=known)
    assert result.ok
    assert ids(result) == [i for i in P1_IDS if i != "2048"] + DRAFT_IDS
    # the dropped id was the only new one on page 1 and still made the walk go on to page 2
    assert [url for url, _ in http.calls] == [bottles(1), NONALC_URL, bottles(2), draft(1)]


def test_full_run_walks_every_page_and_fetches_no_known_product_pages():
    responses = {bottles(n): page_json([], n, 23) for n in range(2, 23)}
    responses |= {bottles(1): BOTTLES_P1, bottles(23): BOTTLES_LAST,
                  draft(1): page_json([], 1, 3), draft(2): page_json([], 2, 3), draft(3): DRAFT_LAST}
    result, http = fetch(responses, known=P1_IDS + ["266"] + DRAFT_IDS, full=True)
    assert http.calls == [(bottles(n), XHR) for n in range(1, 24)] + [(draft(n), XHR) for n in (1, 2, 3)]
    assert (result.ok, result.full) == (True, True)
    assert ids(result) == P1_IDS + ["266"] + DRAFT_IDS
    old = by_id(result)["266"]   # the oldest items are titled "Пиво"
    assert (old.name, old.beer_key) == ("379 American Wheat Ale Citrus", "n:379 american wheat ale citrus")


def test_full_run_follows_a_list_that_grows_during_the_walk():
    # a new item pushed card 2051 from page 1 onto page 2 and added a third page
    responses = {bottles(1): page_json([P1_CARDS[i] for i in ("2053", "2052", "2051")], 1, 2),
                 bottles(2): page_json([P1_CARDS["2051"], P1_CARDS["2050"]], 2, 3),
                 bottles(3): page_json([P1_CARDS["2049"]], 3, 3),
                 draft(1): page_json(DRAFT_CARDS.values(), 1, 1)}
    result, http = fetch(responses, known=P1_IDS + DRAFT_IDS, full=True)
    assert [url for url, _ in http.calls] == [bottles(1), bottles(2), bottles(3), draft(1)]
    assert ids(result) == ["2053", "2052", "2051", "2050", "2049"] + DRAFT_IDS   # 2051 reported once


def test_titles_not_starting_with_beer_are_skipped_and_never_count_as_new():
    page1 = page_json([card("2060", "Gift card 10000 AMD"), card("2059", 'Set "Beer lovers" 4 bottles + glass'),
                       card("2058", "Beer 0.5L"),   # nothing left after normalization: key "n:"
                       P1_CARDS["2053"]], 1, 23)
    result, http = fetch({bottles(1): page1, draft(1): page_json(DRAFT_CARDS.values(), 1, 3)},
                         known=["2053"] + DRAFT_IDS)
    assert http.calls == [(bottles(1), XHR), (draft(1), XHR)]   # no product pages, no page 2
    assert ids(result) == ["2053"] + DRAFT_IDS


@pytest.mark.parametrize("responses, full, error", [
    ({bottles(1): FetchError("network", "timeout")}, False, "network"),
    ({bottles(1): FetchError("cloudflare")}, True, "cloudflare"),
    ({bottles(1): "<html>Just a moment...</html>"}, False, "bad_response"),
    ({bottles(1): BOTTLES_P1, draft(1): FetchError("http", "503")}, False, "http"),
    ({bottles(1): BOTTLES_P1, bottles(2): FetchError("network")}, True, "network"),
    ({bottles(1): BOTTLES_P1, bottles(2): page_json([], 1, 23)}, True, "bad_response"),   # page param ignored
    ({bottles(1): BOTTLES_P1, bottles(2): page_json([], 2, MAX_PAGES + 1)}, True, "bad_response"),
])
def test_any_listing_failure_fails_the_whole_run(responses, full, error):
    result, _ = fetch(responses, known=P1_IDS + DRAFT_IDS, full=full)
    assert (result.ok, result.error, result.full, result.sightings) == (False, error, full, [])
    assert (result.key, result.place_id) == ("beercity:beer-city", "beer-city")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 120 .venv/bin/pytest tests/test_beercity.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taps.sources.beercity'`

- [ ] **Step 3: Write minimal implementation**

Create `taps/sources/beercity.py`:
```python
"""Source 🛒 Beer City (spec §4.5): XHR catalog listing plus product pages for new item ids."""
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from taps.config import Place
from taps.fetch import FetchError, Http
from taps.model import Sighting, SourceResult, n_key

BASE = "https://www.beer-city.am"
LIST_URL = BASE + "/en/catalog/{category}/?sorting=-id&page={page}"
CATEGORIES = ("sshalcavac-garejur", "lcnovi-garejur")   # bottles and cans; draught to take away
DRAFT_CATEGORY = "lcnovi-garejur"
XHR = {"X-Requested-With": "XMLHttpRequest"}
MAX_PAGES = 100   # sanity bound on the page counter; a full walk is ~26 pages
# Spec: titles start with "Beer" or "Draught beer"; the oldest items are titled "Пиво" (id 266).
BEER_TITLE_RE = re.compile(r"^\s*(?:draught beer|beer|пиво)\b", re.I)
PAGES_RE = re.compile(r"Page\s+(\d+)\s+of\s+(\d+)")
VOLUME_RE = re.compile(r"\d+(?:[.,]\d+)?\s*(?:ml|cl|l|мл|л)(?!\w)", re.I)
QUOTES_RE = re.compile(r"[\"«»“”„]")
NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
CONTAINERS = {"tin": "can", "can": "can", "glass": "bottle", "bottle": "bottle", "pet": "bottle",
              "plastic": "bottle", "keg": "keg"}


@dataclass(frozen=True)
class BCItem:
    item_id: str
    title: str
    price_amd: int | None
    in_stock: bool
    url: str


@dataclass(frozen=True)
class BCListing:
    items: list[BCItem]
    page: int
    pages: int


@dataclass(frozen=True)
class BCProduct:
    brand: str | None
    volume_ml: int | None
    abv: float | None
    country: str | None
    container: str | None


def parse_listing(text: str) -> BCListing:
    """XHR JSON {link, products} -> cards and the "Page i of N" counter; ValueError on a wrong shape."""
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get("products"), str):
        raise ValueError("no products html")
    soup = BeautifulSoup(data["products"], "html.parser")
    items = []
    for card in soup.select(".product-item"):
        button = card.select_one("button.wish-list-icon[data-product]")
        link = card.select_one(".prod-item-name a[href]")
        item_id = button["data-product"].strip() if button else ""
        if not item_id or link is None:
            continue
        price = card.select_one(".new-price .wh-point")
        digits = re.sub(r"\D", "", price.get_text()) if price else ""
        items.append(BCItem(
            item_id=item_id,
            title=link.get_text(" ", strip=True),
            price_amd=int(digits) if digits else None,
            in_stock=card.select_one("a.addtocart-but.diss-prod") is None,
            url=urljoin(BASE, link["href"]),
        ))
    m = PAGES_RE.search(soup.get_text(" ", strip=True))
    return BCListing(items, int(m.group(1)), int(m.group(2))) if m else BCListing(items, 1, 1)


def _fields(soup: BeautifulSoup, row: str, name: str, value: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in soup.select(row):
        n, v = r.select_one(name), r.select_one(value)
        if n and v:
            out.setdefault(n.get_text(strip=True).rstrip(":"), v.get_text(" ", strip=True))
    return out


def _num(text: str | None) -> float | None:
    m = NUM_RE.search(text or "")
    return float(m.group(0).replace(",", ".")) if m else None


def parse_product(html: str) -> BCProduct:
    """Brand from the options list; volume, alcohol, country and packaging from the characteristics."""
    soup = BeautifulSoup(html, "html.parser")
    fields = _fields(soup, "ul.shop-options li", ".opt", ".val")
    fields |= _fields(soup, ".card-character-block", ".card-character-name", ".card-character-value")
    volume = fields.get("Volume", "")
    amount = _num(volume)
    if amount is not None and "ml" not in volume.lower():
        amount *= 1000   # "0.45 liter"
    packaging = fields.get("Type of packaging", "").lower().split()
    return BCProduct(
        brand=fields.get("Brand") or None,
        volume_ml=round(amount) if amount else None,
        abv=_num(fields.get("Alcohol")),
        country=fields.get("Country of origin") or None,
        container=next((CONTAINERS[w] for w in packaging if w in CONTAINERS), None),
    )


def clean_name(title: str) -> str:
    """'Beer "Hard root" Double IPA 0.45 l' -> 'Hard root Double IPA'."""
    name = VOLUME_RE.sub(" ", QUOTES_RE.sub(" ", BEER_TITLE_RE.sub("", title)))
    return " ".join(name.split())


def fetch_beercity(http: Http, place: Place, known_item_ids: set[str], full: bool, now: datetime,
                   brewery_aliases: Mapping[str, str]) -> SourceResult:
    """Full run: every page of both categories. Partial run: page 1, then the next page only while
    the page had unseen beer ids. Product pages only for unseen ids; a failed one drops that item.
    Any listing failure fails the whole run, so a full run is never incomplete."""
    key = f"beercity:{place.id}"

    def fail(error: str) -> SourceResult:
        return SourceResult(key=key, source="beercity", ok=False, error=error, full=full, place_id=place.id)

    sightings: list[Sighting] = []
    done: set[str] = set()
    for category in CATEGORIES:
        page = pages = 1
        while page <= pages:
            try:
                listing = parse_listing(http.get(LIST_URL.format(category=category, page=page), headers=XHR).text)
            except FetchError as e:
                return fail(e.kind)
            except ValueError:
                return fail("bad_response")
            if listing.page != page or listing.pages > MAX_PAGES:
                return fail("bad_response")   # page parameter ignored or a broken counter
            pages = listing.pages
            unseen = False
            for item in listing.items:
                beer_key = n_key(item.title, brewery_aliases)
                if item.item_id in done or not BEER_TITLE_RE.match(item.title) or beer_key == "n:":
                    continue   # repeated card, gift card or bundle, or a title without a name
                product = None
                if item.item_id not in known_item_ids:
                    unseen = True
                    try:
                        product = parse_product(http.get(item.url).text)
                    except FetchError:
                        continue   # spec §4.5: not recorded in this run, retried next run
                done.add(item.item_id)
                sightings.append(Sighting(
                    place_id=place.id, source="beercity", beer_key=beer_key, title=item.title,
                    name=clean_name(item.title), seen_at=now,
                    brewery=product.brand if product else None,
                    shop_item_id=item.item_id,
                    abv=product.abv if product else None,
                    price_amd=item.price_amd,
                    volume_ml=product.volume_ml if product else None,
                    container="draft" if category == DRAFT_CATEGORY else (product.container if product else None),
                    in_stock=item.in_stock, category=category, url=item.url,
                ))
            if not full and not unseen:
                break
            page += 1
    return SourceResult(key=key, source="beercity", ok=True, sightings=sightings, full=full, place_id=place.id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `timeout 120 .venv/bin/pytest tests/test_beercity.py -v`
Expected: `29 passed`

- [ ] **Step 5: Commit**

```bash
git add tests/test_beercity.py taps/sources/beercity.py
git commit -m "feat: add Beer City shop source with partial and full catalog runs"
```

---

### Task 12: Источник: Yerevan City

**Files:**
- Create: `taps/sources/yerevan_city.py`
- Test: `tests/test_yerevan_city.py`

**Interfaces:**
- Consumes: `taps.model.Sighting`, `taps.model.SourceResult`, `taps.model.n_key(text, brewery_aliases=None) -> str` (Task 1); `taps.config.Place` (Task 2); `taps.fetch.Http.post_json(url, payload, headers=None) -> Any`, `taps.fetch.FetchError(kind, message="")` с полем `kind` (Task 6); `tests.helpers.fixture_json`, фикстуры `tests/fixtures/yerevan_city/by_category.json` и `tests/fixtures/yerevan_city/search.json`.
- Produces: `YCItem(item_id: str, name_hy: str, price_amd: int, category: str)`, `YCListing(items: list[YCItem], item_count: int)`, `YCName(name_en: str, brand_id: int | None)`; `parse_by_category(data: dict) -> YCListing` и `parse_search(data: dict) -> dict[str, YCName]` (оба бросают `ValueError` на неуспешный или битый ответ); `brand_from(name_en: str | None, name_hy: str) -> str | None`; `fetch_yerevan_city(http: Http, place: Place, now: datetime, brewery_aliases: Mapping[str, str]) -> SourceResult` (ключ `yerevan_city:<place.id>`, source `yerevan_city`, `full=True`; у каждого `Sighting` заданы `in_stock=True`, `category` = английское `categoryName`, `brewery` = бренд, `shop_item_id`, `price_amd`, `volume_ml`, `container`, `url`); константы `BY_CATEGORY_URL`, `BY_CATEGORY_BODY`, `SEARCH_URL`, `SEARCH_BODY`, `PRODUCT_URL`, `HY_BRANDS`.

Yerevan City (spec §4.6) забирается двумя POST-запросами. GetByCategory отдаёт всю пивную категорию: армянское название, цену (со скидкой, если она есть) и категорию. Search отдаёт латинские `nameEn`, из которых строятся заголовок, ключ, название и бренд: текст в кавычках, а без кавычек — первое слово после «Beer». У товаров без `nameEn` (пивные напитки, сидры, коктейли: в их названии нет слова «գարեջուր») ключ строится по армянскому названию. Бренд таких товаров берётся из «» через таблицу `HY_BRANDS` армянских написаний брендов стоп-листа, и тест сверяет эту таблицу с фикстурой. Прогон не засчитывается (`bad_response`) в четырёх случаях: `success` не `true`, список пуст, в списке меньше `itemCount` позиций, Search не удался или ничего не нашёл. Ошибки самого запроса сохраняют свой вид (`network`, `http`, `cloudflare`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_yerevan_city.py`:
```python
import copy
from datetime import datetime, timezone

import pytest

from taps.config import Place
from taps.fetch import FetchError
from taps.sources.yerevan_city import (
    HY_BRANDS, YCItem, YCName, brand_from, fetch_yerevan_city, parse_by_category, parse_search,
)
from tests.helpers import fixture_json

NOW = datetime(2026, 9, 24, 14, 17, tzinfo=timezone.utc)
PLACE = Place(id="yerevan-city", name="Yerevan City", kind="shop", sources={"yerevan_city": {}})
BY_CATEGORY = fixture_json("yerevan_city/by_category.json")   # 243 items, itemCount 243
SEARCH = fixture_json("yerevan_city/search.json")             # 209 products, all with nameEn


class FakeHttp:
    """Answers post_json in call order; an exception instance is raised instead of returned."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def post_json(self, url, payload, headers=None):
        self.calls.append((url, payload))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def fetch(by_category=BY_CATEGORY, search=SEARCH, aliases=None):
    http = FakeHttp(by_category, search)
    return fetch_yerevan_city(http, PLACE, NOW, aliases or {}), http


def sightings(**kw):
    return {s.shop_item_id: s for s in fetch(**kw)[0].sightings}


# --- parse_by_category -----------------------------------------------------------

def test_parse_by_category_reads_whole_category():
    listing = parse_by_category(BY_CATEGORY)
    assert (listing.item_count, len(listing.items)) == (243, 243)
    assert listing.items[0] == YCItem("3102", "Գարեջուր «Կրոմբախեր» Փիլս թ/տ 5լ", 12990, "Imported beer")
    assert {i.category for i in listing.items} == {"Imported beer", "Armenian beer", "Low alcohol cocktails and cider"}


def test_parse_by_category_takes_discounted_price():
    items = {i.item_id: i for i in parse_by_category(BY_CATEGORY).items}
    assert items["73930"].price_amd == 1300    # Duvel Blond: price 1460.0, discountedPrice 1299.984
    assert items["112509"].price_amd == 1250   # Primator IPA: price 1250.0, discountedPrice 0


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(success=False),
    lambda d: d.update(success="true"),
    lambda d: d.update(data=None),
    lambda d: d["data"].update(list=None),
    lambda d: d["data"].update(itemCount="243"),
    lambda d: d["data"]["list"].append("3102"),
    lambda d: d["data"]["list"][0].update(id="3102"),
    lambda d: d["data"]["list"][0].update(name=" "),
    lambda d: d["data"]["list"][0].update(price=0),
    lambda d: d["data"]["list"][0].update(price=float("inf")),
    lambda d: d["data"]["list"][0].update(discountedPrice="500"),
    lambda d: d["data"]["list"][0].update(categoryName=None),
], ids=["not_success", "success_text", "no_data", "no_list", "item_count_text", "item_not_object", "id_text",
        "blank_name", "zero_price", "infinite_price", "discount_text", "no_category"])
def test_parse_by_category_rejects_malformed(mutate):
    data = copy.deepcopy(BY_CATEGORY)
    mutate(data)
    with pytest.raises(ValueError):
        parse_by_category(data)


# --- parse_search ------------------------------------------------------------------

def test_parse_search_maps_id_to_latin_name():
    names = parse_search(SEARCH)
    assert len(names) == 209
    assert names["8158"] == YCName('Beer "Kilikia" 1l', 2788)
    assert names["166205"] == YCName('Beer "Paulaner" Munchner hell (can) 5l', None)      # brandId null
    assert names["192953"].name_en == 'Beer "Grevensteiner" unfiltered, light g/b 0.5l'  # leading space
    assert "175294" not in names    # beer drink: its Armenian name lacks the search word


def test_parse_search_skips_blank_name_and_ignores_odd_brand_id():
    data = copy.deepcopy(SEARCH)
    by_id = {p["id"]: p for p in data["data"]["products"]}
    by_id[8158]["nameEn"] = "  "
    by_id[13620]["brandId"] = "2835"
    names = parse_search(data)
    assert "8158" not in names and len(names) == 208
    assert names["13620"] == YCName('Beer "Kotayk" 1l', None)


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(success=False),
    lambda d: d["data"].update(products=None),
    lambda d: d["data"]["products"].append(None),
    lambda d: d["data"]["products"][0].update(id="8158"),
    lambda d: d["data"]["products"][0].update(nameEn=8158),
], ids=["not_success", "no_products", "product_not_object", "id_text", "name_not_text"])
def test_parse_search_rejects_malformed(mutate):
    data = copy.deepcopy(SEARCH)
    mutate(data)
    with pytest.raises(ValueError):
        parse_search(data)


# --- brand_from ------------------------------------------------------------------------

@pytest.mark.parametrize("name_en,brand", [
    ('Beer "Kilikia" 1l', "Kilikia"),
    ('Draft wheat beer "Dargett" Weizen 1l', "Dargett"),
    ('Beer "Volfas Engelman" Ipa (can) 0.568l', "Volfas Engelman"),
    ("Beer ''Corona'' Zero light (can) 330ml", "Corona"),
    ("Beer Kilikia 1l", "Kilikia"),                  # no quotes: first word after "beer"
    ("Draft beer Gyumri, light 1l", "Gyumri"),
])
def test_brand_from_latin_name(name_en, brand):
    assert brand_from(name_en, "Գարեջուր «Կոտայք» 1լ") == brand    # the Armenian name is not used


@pytest.mark.parametrize("name_hy,brand", [
    ("Գարեջուր «Կիլիկիա» 1լ", "Kilikia"),
    ("Գարեջուր «Կոտայք» Գոլդ 1լ", "Kotayk"),
    ("Գարեջուր «Բալտիկա» 7 թ/տ 0.9լ", "Baltika"),
    ("Գարեջուր «Գյումրի» 1լ", "Gyumri"),
    ("Գարեջուր «Հայնեկեն» ա/տ 0.5լ", "Heineken"),         # spelling of the shop's brand list
    ("Գարեջուր «Հեյնեկեն» ա/տ 0.5լ", "Heineken"),         # spelling of its item names
    ("Գարեջուր «Ժիգուլի» Բառնոե ա/տ 0.9լ", "Zhiguli"),     # stop-list spelling; nameEn says Jiguly
])
def test_brand_from_armenian_name_via_table(name_hy, brand):
    assert brand_from(None, name_hy) == brand


def test_brand_from_armenian_name_fallbacks():
    assert brand_from(None, "Գարեջրային ըմպելիք «Կրոմբախեր» Ռադլեր թ/տ 0.5լ") == "Կրոմբախեր"   # not in table
    assert brand_from("Lager 1l", "Գարեջուր «Կոտայք» 1լ") == "Kotayk"     # nameEn names no brand
    assert brand_from(None, "Գարեջուր 1լ") is None


# The shop's Latin spelling -> the stop-list spelling HY_BRANDS uses for that brand.
STOP_LIST_SPELLING = {"Jiguly": "Zhiguli", "Jigulyovskoye": "Zhigulevskoe", "Cooler": "Kuler",
                      "Prazacka": "Pražečka", "Staroceske": "Staročeské", "Lvovskoe": "Lvivske"}


def test_armenian_table_agrees_with_latin_names_in_fixture():
    names = parse_search(SEARCH)
    checked = set()
    for item in parse_by_category(BY_CATEGORY).items:
        if item.item_id not in names:
            continue
        hy = item.name_hy.split("«")[1].split("»")[0]
        latin = brand_from(names[item.item_id].name_en, item.name_hy)
        stop = STOP_LIST_SPELLING.get(latin, latin)
        if hy in HY_BRANDS or stop in HY_BRANDS.values():
            assert HY_BRANDS.get(hy) == stop, item
            checked.add(hy)
    assert set(HY_BRANDS) - checked == {"Հայնեկեն"}
    assert "Հայնեկեն" in {b["name"] for b in SEARCH["data"]["searchBrands"]}


# --- fetch_yerevan_city -------------------------------------------------------------------

def test_fetch_posts_both_requests_with_spec_bodies():
    result, http = fetch()
    assert http.calls == [
        ("https://apishopv2.yerevan-city.am/api/Product/GetByCategory",
         {"categoryId": 119, "parentId": 119, "count": 1000, "page": 1}),
        ("https://apishopv2.yerevan-city.am/api/Product/Search",
         {"search": "գարեջուր", "count": 500, "page": 1, "countries": [], "categories": [], "tags": [],
          "brands": [], "isDiscounted": False, "sortBy": 3}),
    ]
    assert (result.ok, result.error, result.full) == (True, None, True)
    assert (result.key, result.source, result.place_id) == ("yerevan_city:yerevan-city", "yerevan_city", "yerevan-city")
    assert len(result.sightings) == 243
    for s in result.sightings:
        assert (s.place_id, s.source, s.kind, s.seen_at, s.in_stock) == ("yerevan-city", "yerevan_city", "shop", NOW, True)
        assert s.url == f"https://yerevan-city.am/shop/product-details/{s.shop_item_id}"


def test_fetch_sighting_from_latin_name():
    s = sightings()["112509"]
    assert s.title == 'Beer "Primator" IPA, light g/b 0.5l'
    assert (s.brewery, s.name) == ("Primator", "IPA, light")
    assert s.beer_key == "n:primator ipa light g b"
    assert s.category == "Imported beer"
    assert (s.price_amd, s.volume_ml, s.container) == (1250, 500, "bottle")


@pytest.mark.parametrize("item_id,brewery,name,beer_key,volume_ml,container,category", [
    ("51047", "Dargett", "Pilsner", "n:dargett pilsner", 1000, "draft", "Armenian beer"),
    ("3102", "Krombacher", "Pils", "n:krombacher pils", 5000, "can", "Imported beer"),
    ("195013", "Corona", "Zero light", "n:corona zero light", 330, "can", "Imported beer"),
    ("8158", "Kilikia", "Kilikia", "n:kilikia", 1000, None, "Armenian beer"),   # nothing after the brand
])
def test_fetch_latin_names_volume_and_container(item_id, brewery, name, beer_key, volume_ml, container, category):
    s = sightings()[item_id]
    assert (s.brewery, s.name, s.beer_key) == (brewery, name, beer_key)
    assert (s.volume_ml, s.container, s.category) == (volume_ml, container, category)


def test_fetch_sighting_from_armenian_name_when_missing_in_search():
    found = sightings()
    s = found["175294"]
    assert s.title == "Գարեջրային ըմպ. «Տրյոխգորնոե»Բլանշ,բաց ա/տ 0.45լ"
    assert s.beer_key == "n:գարեջրային ըմպ տրյոխգորնոե բլանշ բաց ա տ"
    assert (s.brewery, s.name) == ("Տրյոխգորնոե", "Բլանշ,բաց")
    assert (s.price_amd, s.volume_ml, s.container, s.category) == (500, 450, "bottle", "Imported beer")
    assert found["173458"].container == "can"     # Krombacher Radler, թ/տ


def test_fetch_stop_list_brand_missing_in_search_gets_latin_brand():
    search = copy.deepcopy(SEARCH)
    search["data"]["products"] = [p for p in search["data"]["products"] if p["id"] not in (8158, 195013)]
    found = sightings(search=search)
    s = found["8158"]
    assert s.title == "Գարեջուր «Կիլիկիա» 1լ"
    assert s.beer_key == "n:գարեջուր կիլիկիա"
    assert (s.brewery, s.name, s.volume_ml) == ("Kilikia", "Kilikia", 1000)
    s = found["195013"]
    assert s.title == "Գարեջուր «Կորոնա» զերո, բաց ա/տ 330մլ"
    assert (s.brewery, s.name, s.volume_ml, s.container) == ("Corona", "զերո, բաց", 330, "bottle")


def test_fetch_applies_brewery_aliases_to_key_only():
    s = sightings(aliases={"Jiguly": "Zhiguli"})["3989"]
    assert s.beer_key == "n:zhiguli barnoe g b"
    assert s.brewery == "Jiguly"


def test_fetch_skips_item_whose_title_gives_empty_key():
    search = copy.deepcopy(SEARCH)
    next(p for p in search["data"]["products"] if p["id"] == 8158)["nameEn"] = "Beer 1l"
    result, _ = fetch(search=search)
    assert result.ok and len(result.sightings) == 242
    assert "8158" not in {s.shop_item_id for s in result.sightings}


@pytest.mark.parametrize("mutate,requests", [
    (lambda c, s: c.update(success=False), 1),
    (lambda c, s: c["data"].update(list=[], itemCount=0), 1),
    (lambda c, s: c["data"]["list"].pop(), 1),
    (lambda c, s: s.update(success=False), 2),
    (lambda c, s: s["data"].update(products=None), 2),
    (lambda c, s: s["data"].update(products=[]), 2),
], ids=["listing_not_success", "empty_list", "fewer_than_item_count", "search_not_success", "search_malformed",
        "search_found_nothing"])
def test_fetch_bad_response(mutate, requests):
    c, s = copy.deepcopy(BY_CATEGORY), copy.deepcopy(SEARCH)
    mutate(c, s)
    result, http = fetch(c, s)
    assert (result.ok, result.error, result.sightings) == (False, "bad_response", [])
    assert (result.key, result.source, result.place_id) == ("yerevan_city:yerevan-city", "yerevan_city", "yerevan-city")
    assert len(http.calls) == requests


@pytest.mark.parametrize("by_category,search,error,requests", [
    (FetchError("network", "timeout"), SEARCH, "network", 1),
    (BY_CATEGORY, FetchError("cloudflare"), "cloudflare", 2),
    (BY_CATEGORY, FetchError("http", "invalid JSON"), "http", 2),
])
def test_fetch_request_failure_keeps_fetch_error_kind(by_category, search, error, requests):
    result, http = fetch(by_category, search)
    assert (result.ok, result.error, result.sightings) == (False, error, [])
    assert len(http.calls) == requests
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 120 .venv/bin/pytest tests/test_yerevan_city.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taps.sources.yerevan_city'`

- [ ] **Step 3: Write minimal implementation**

Create `taps/sources/yerevan_city.py`:
```python
"""Source 🛒 Yerevan City (spec 4.6): the whole beer category from the shop's JSON API, Latin names from Search."""
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from taps.config import Place
from taps.fetch import FetchError, Http
from taps.model import Sighting, SourceResult, n_key

API = "https://apishopv2.yerevan-city.am/api/Product"
BY_CATEGORY_URL = f"{API}/GetByCategory"
BY_CATEGORY_BODY = {"categoryId": 119, "parentId": 119, "count": 1000, "page": 1}
SEARCH_URL = f"{API}/Search"
# shortcut: one Search page of 500; ~209 products match today, past 500 the rest would lose their Latin names
SEARCH_BODY = {"search": "գարեջուր", "count": 500, "page": 1, "countries": [], "categories": [],
               "tags": [], "brands": [], "isDiscounted": False, "sortBy": 3}
PRODUCT_URL = "https://yerevan-city.am/shop/product-details/{}"

# Stop-list (not_craft) brands as the shop writes them in «» of its Armenian names -> stop-list spelling,
# so the shop filter still knows them when an item is missing from Search. Հայնեկեն is the spelling of
# the shop's brand list; its item names use Հեյնեկեն.
# shortcut: covers the stop-list brands the shop sold on 2026-09-23; a new one needs a line here.
HY_BRANDS = {
    # Armenia
    "Կոտայք": "Kotayk", "Գյումրի": "Gyumri", "Կիլիկիա": "Kilikia", "Արարատ": "Ararat",
    "Ալեքսանդրապոլ": "Aleksandrapol", "Դիլիջան": "Dilijan", "Դեբեդ": "Debed", "Լինքոլն": "Lincoln",
    # Russia and CIS
    "Բալտիկա": "Baltika", "Ժիգուլյովսկոե": "Zhigulevskoe", "Ժիգուլի": "Zhiguli",
    "Զոլոտայա Բոչկա": "Zolotaya Bochka", "Բելիյ Մեդվեդ": "Beliy Medved", "Մոտոր": "Motor",
    "Ժատեցկիյ Գուս": "Zatecky Gus", "Կոզել": "Kozel", "Լվովսկոյե": "Lvivske",
    # international lagers
    "Հեյնեկեն": "Heineken", "Հայնեկեն": "Heineken", "Ստելլա Արտուա": "Stella Artois",
    "Կորոնա": "Corona", "Կոռոնա": "Corona", "Բադ": "Bud", "Բուդվայզեր": "Budweiser", "Միլլեր": "Miller",
    "Կարլսբերգ": "Carlsberg", "Տուբորգ": "Tuborg", "Էստրելլա Դամմ": "Estrella Damm", "Կուլեր": "Kuler",
    "Ալմազա": "Almaza",
    # Georgia and Czechia
    "Նատախտարի": "Natakhtari", "Կազբեգի": "Kazbegi", "Զեդազենի": "Zedazeni",
    "Պրաժեչկա": "Pražečka", "Ստարոչեսկոյե": "Staročeské", "Սանտանոս": "Santanos",
}

_QUOTED_RE = re.compile(r'"([^"]+)"|\'\'(.+?)\'\'|«([^»]+)»')     # "Kilikia", ''Corona'', «Կիլիկիա»
_AFTER_BEER_RE = re.compile(r"\bbeer\s+([^\s,]+)", re.I)
_VOLUME_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(ml|մլ|l|լ)(?!\w)", re.I)
_MARKS_RE = re.compile(r"\b\d+\s*x\b|\(can\)|\bg/b\b|[աթ]/տ", re.I)   # multipack, can, glass bottle


@dataclass(frozen=True)
class YCItem:
    item_id: str
    name_hy: str
    price_amd: int
    category: str


@dataclass(frozen=True)
class YCListing:
    items: list[YCItem]
    item_count: int


@dataclass(frozen=True)
class YCName:
    name_en: str
    brand_id: int | None


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _body(data: Any) -> dict:
    """The 'data' object of a successful API answer; ValueError otherwise."""
    if not isinstance(data, dict) or data.get("success") is not True or not isinstance(data.get("data"), dict):
        raise ValueError("success != true or no data")
    return data["data"]


def parse_by_category(data: dict) -> YCListing:
    """GetByCategory answer -> the category's items; ValueError when unsuccessful or malformed."""
    body = _body(data)
    rows, item_count = body.get("list"), body.get("itemCount")
    if not isinstance(rows, list) or not _is_int(item_count):
        raise ValueError("no data.list or data.itemCount")
    items = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("item is not an object")
        item_id, name, category = row.get("id"), row.get("name"), row.get("categoryName")
        price, discounted = row.get("price"), row.get("discountedPrice") or 0   # 0 = no discount
        if not (_is_int(item_id) and isinstance(name, str) and name.strip() and isinstance(category, str)
                and _is_num(price) and price > 0 and _is_num(discounted)):
            raise ValueError(f"bad item {item_id!r}")
        items.append(YCItem(str(item_id), name.strip(), round(discounted if discounted > 0 else price), category))
    return YCListing(items, item_count)


def parse_search(data: dict) -> dict[str, YCName]:
    """Search answer -> {item id: YCName}; products without nameEn are left out."""
    products = _body(data).get("products")
    if not isinstance(products, list):
        raise ValueError("no data.products")
    names = {}
    for p in products:
        if not isinstance(p, dict):
            raise ValueError("product is not an object")
        item_id, name_en, brand_id = p.get("id"), p.get("nameEn"), p.get("brandId")
        if not _is_int(item_id) or not (name_en is None or isinstance(name_en, str)):
            raise ValueError(f"bad product {item_id!r}")
        if name_en and name_en.strip():
            names[str(item_id)] = YCName(name_en.strip(), brand_id if _is_int(brand_id) else None)
    return names


def _brand_match(text: str) -> re.Match | None:
    return _QUOTED_RE.search(text) or _AFTER_BEER_RE.search(text)


def _inner(m: re.Match) -> str:
    return next(g for g in m.groups() if g).strip()


def brand_from(name_en: str | None, name_hy: str) -> str | None:
    """Quoted text of nameEn, else its first word after 'beer'; otherwise the «» of the Armenian name
    through HY_BRANDS, or the raw Armenian text for a brand not in the table."""
    m = _brand_match(name_en) if name_en else None
    if m:
        return _inner(m)
    m = _QUOTED_RE.search(name_hy)
    if not m:
        return None
    brand = _inner(m)
    return HY_BRANDS.get(brand, brand)


def _clean_name(title: str, brand: str | None) -> str:
    """Human name: the title after its brand, without volume, multipack and container marks."""
    m = _brand_match(title)
    rest = title[m.end():] if m else title
    rest = " ".join(_MARKS_RE.sub(" ", _VOLUME_RE.sub(" ", rest)).split())
    return rest or brand or title


def _volume_ml(title: str) -> int | None:
    m = _VOLUME_RE.search(title)
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    return round(value if m.group(2).lower() in ("ml", "մլ") else value * 1000)


def _container(name_en: str | None, name_hy: str) -> str | None:
    """From nameEn ('Draft beer', '(can)', 'g/b') or the Armenian marks թ/տ (tin) and ա/տ (glass)."""
    en = (name_en or "").lower()
    if en.startswith("draft"):
        return "draft"
    if "(can)" in en or "թ/տ" in name_hy:
        return "can"
    if "g/b" in en or "ա/տ" in name_hy:
        return "bottle"
    return None


def fetch_yerevan_city(http: Http, place: Place, now: datetime, brewery_aliases: Mapping[str, str]) -> SourceResult:
    """Two POSTs. Spec 4.6: the run fails on success != true, an empty or short list, or a failed Search."""
    key = f"yerevan_city:{place.id}"

    def fail(error: str) -> SourceResult:
        return SourceResult(key=key, source="yerevan_city", ok=False, error=error, place_id=place.id)

    try:
        listing = parse_by_category(http.post_json(BY_CATEGORY_URL, BY_CATEGORY_BODY))
        if not listing.items or len(listing.items) < listing.item_count:
            return fail("bad_response")
        names = parse_search(http.post_json(SEARCH_URL, SEARCH_BODY))
    except FetchError as e:
        return fail(e.kind)
    except ValueError:
        return fail("bad_response")
    if not names:   # the shop always has beer, so an empty search is a failed one
        return fail("bad_response")

    sightings = []
    for item in listing.items:
        # beer drinks, ciders and cocktails lack the search word, so they have no Latin name
        name_en = names[item.item_id].name_en if item.item_id in names else None
        title = name_en or item.name_hy
        beer_key = n_key(title, brewery_aliases)
        if beer_key == "n:":
            continue
        brand = brand_from(name_en, item.name_hy)
        sightings.append(Sighting(
            place_id=place.id, source="yerevan_city", beer_key=beer_key, title=title,
            name=_clean_name(title, brand), seen_at=now, brewery=brand, shop_item_id=item.item_id,
            price_amd=item.price_amd, volume_ml=_volume_ml(title), container=_container(name_en, item.name_hy),
            in_stock=True,   # no stock flag: a sold-out item just leaves the list
            category=item.category, url=PRODUCT_URL.format(item.item_id),
        ))
    return SourceResult(key=key, source="yerevan_city", ok=True, sightings=sightings, place_id=place.id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `timeout 120 .venv/bin/pytest tests/test_yerevan_city.py -v`
Expected: `55 passed`

- [ ] **Step 5: Commit**

```bash
git add tests/test_yerevan_city.py taps/sources/yerevan_city.py
git commit -m "feat: add Yerevan City shop source"
```

---

### Task 13: Источник: Parma

**Files:**
- Create: `taps/sources/parma.py`
- Test: `tests/test_parma.py`

**Interfaces:**
- Consumes: `taps.config.Place`; `taps.fetch.Http.get(url, headers=None) -> HttpResponse`, `taps.fetch.FetchError` (поле `.kind`), `taps.fetch.HttpResponse(status, headers, text)` (в тестах); `taps.model.Sighting`, `taps.model.SourceResult`, `taps.model.n_key(text, brewery_aliases)`; `tests.helpers.fixture_text`.
- Produces: `parse_listing(html: str) -> list[ParmaCard]`; `ParmaCard(item_id: str, title: str, price_amd: int | None, in_stock: bool, url: str)`; `parse_product(html: str) -> ParmaProduct`; `ParmaProduct(manufacturer: str | None, country: str | None, abv: float | None)`; `fetch_parma(http: Http, place: Place, known_item_ids: set[str], now: datetime, brewery_aliases: Mapping[str, str]) -> SourceResult` (key `parma:<place_id>`, source `parma`, всегда `full=True`); дополнительно `clean_name(title) -> str`, `volume_ml(title) -> int | None`, константы `LIST_URL`, `HEADERS`, `PAGE_SIZE = 60`, `MAX_PAGES = 8`, `MIN_CARDS = 150`, `CATEGORY = "beer"`.

Источник 🛒 Parma по спеке §4.6. Листаем категорию `beer` по 60 карточек (не больше 8 страниц). Страницу товара запрашиваем только для новых кодов: с неё берём производителя, страну и крепость из описания. Если разных карточек меньше 150, прогон не засчитывается (§4.6, §10). Ключ пива — `n_key` латинского названия из листинга (§5), пивоварня — производитель со страницы товара. В фикстурах есть только страницы 1 и 4, и нет ни одной карточки «нет в наличии». Поэтому тесты собирают страницы 2–3 из копии страницы 1 с перенумерованными кодами, а `div.not_av_content` вставляют в настоящую карточку.

- [ ] **Step 1: Write the failing test**

Create `tests/test_parma.py`:
```python
import re
from datetime import datetime, timezone

import pytest
from bs4 import BeautifulSoup

from taps.config import Place
from taps.fetch import FetchError, HttpResponse
from taps.model import Sighting
from taps.sources.parma import (
    ParmaCard, ParmaProduct, clean_name, fetch_parma, parse_listing, parse_product, volume_ml,
)
from tests.helpers import fixture_text

NOW = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)
PLACE = Place(id="parma", name="Parma", kind="shop", sources={"parma": {}})
P1 = fixture_text("parma/list_p1.html")            # 60 cards, all in stock
P4 = fixture_text("parma/list_p4.html")            # last page, 7 cards
PRODUCT_1645 = fixture_text("parma/product_1645.html")
PRODUCT_28051 = fixture_text("parma/product_28051.html")
URL_28051 = "https://parma.am/en/product/product?slug=beer-dahook-ipa-light-330ml_28051"
URL_28637 = "https://parma.am/en/product/product?slug=beer-dargett-imperial-stout-dark-330ml_28637"
GZIP = {"Accept-Encoding": "gzip"}


def list_url(page):
    return f"https://parma.am/en/product/category?slug=beer&available=false&page={page}"


def renumber(html, prefix):
    """Prefix every product code in the page, so a copy of a real page acts as a page of new items."""
    return re.sub(r"_(\d+)(?=[\"'])", lambda m: f"_{prefix}{m.group(1)}", html)


CARDS = [str(c) for c in BeautifulSoup(P1, "html.parser").select("div.product_item")]


def page(n, prefix):
    """Listing page of the first n real list_p1 cards with prefixed codes."""
    return renumber("".join(CARDS[:n]), prefix)


def all_ids(pages):
    return {c.item_id for html in pages for c in parse_listing(html)}


class FakeHttp:
    """Serves pages by URL; an exception value is raised; an unexpected URL fails the test."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url, headers=None):
        self.calls.append((url, headers))
        assert url in self.pages, f"unexpected GET {url}"
        body = self.pages[url]
        if isinstance(body, Exception):
            raise body
        return HttpResponse(200, {"content-type": "text/html; charset=UTF-8"}, body)

    def urls(self):
        return [url for url, _ in self.calls]


def listing_pages(*pages):
    return {list_url(i): html for i, html in enumerate(pages, 1)}


# pages 2 and 3 are not in the fixtures: renumbered copies of page 1 stand in for them (187 cards in total)
REAL_WALK = (P1, renumber(P1, "2"), renumber(P1, "3"), P4)


# --- parse_listing ------------------------------------------------------------

def test_parse_listing_first_page():
    cards = parse_listing(P1)
    assert len(cards) == 60
    assert len({c.item_id for c in cards}) == 60
    assert cards[0] == ParmaCard(
        "48761", 'Beer "Paulaner Original" light 330ml', 970, True,
        "https://parma.am/en/product/product?slug=beer-paulaner-original-light-330ml_48761")
    by_id = {c.item_id: c for c in cards}
    assert by_id["21593"].price_amd == 520          # discount card: old price 650 is not data-price
    assert by_id["26934"].title == 'Beer "379" cherry, dark 330ml'
    baltika = by_id["21063"]                        # percent-encoded slug ending in "-_21063"
    assert baltika.title == 'Beer "Baltika №9" light 450ml'
    assert baltika.url == "https://parma.am/en/product/product?slug=beer-baltika-%E2%84%969-450ml-_21063"
    assert all(c.in_stock for c in cards)


def test_parse_listing_last_page():
    cards = parse_listing(P4)
    assert [c.item_id for c in cards] == ["99846", "28721", "28052", "28050", "28047", "28637", "28051"]
    assert cards[-1] == ParmaCard("28051", 'Beer "Dahook Ipa" light 330ml', 790, True, URL_28051)
    assert cards[5].url == URL_28637


def test_parse_listing_code_is_after_last_underscore():
    cards = parse_listing(P4.replace("beer-paulaner-weissbier-light-500ml_99846", "beer_paulaner-weissbier_99846"))
    assert (cards[0].item_id, cards[0].url) == \
        ("99846", "https://parma.am/en/product/product?slug=beer_paulaner-weissbier_99846")


def test_parse_listing_out_of_stock_card():
    soup = BeautifulSoup(P4, "html.parser")
    last = soup.select("div.product_item")[-1]
    last.append(soup.new_tag("div", attrs={"class": "not_av_content"}))
    cards = parse_listing(str(soup))
    assert [c.in_stock for c in cards] == [True] * 6 + [False]
    assert cards[-1].item_id == "28051" and cards[-1].price_amd == 790


def _drop_title_span(html):
    soup = BeautifulSoup(html, "html.parser")
    soup.select_one("a.item_name > span").decompose()
    return str(soup)


@pytest.mark.parametrize("mutate", [
    lambda h: h.replace("beer-paulaner-weissbier-light-500ml_99846", "beer-paulaner-weissbier-light-500ml"),
    lambda h: h.replace("_99846", "_abc"),
    lambda h: h.replace('href="/en/product/product?slug=beer-paulaner-weissbier',
                        'href="https://evil.example/en/product/product?slug=beer-paulaner-weissbier'),
    _drop_title_span,
], ids=["no-code", "code-not-digits", "other-host", "no-title"])
def test_parse_listing_skips_card_without_code_title_or_parma_link(mutate):
    cards = parse_listing(mutate(P4))
    assert [c.item_id for c in cards] == ["28721", "28052", "28050", "28047", "28637", "28051"]


@pytest.mark.parametrize("bad", ["", "1 270", "abc"])
def test_parse_listing_keeps_card_with_unreadable_price(bad):
    cards = parse_listing(P4.replace('data-price="1270"', f'data-price="{bad}"'))
    assert (cards[0].item_id, cards[0].price_amd) == ("99846", None)
    assert len(cards) == 7


# --- parse_product ------------------------------------------------------------

def test_parse_product_fixtures():
    assert parse_product(PRODUCT_1645) == ParmaProduct("ЗАО МПК", "Russia", 5.9)
    assert parse_product(PRODUCT_28051) == ParmaProduct("Dahook LLC", "Armenia", 6.0)


@pytest.mark.parametrize("written", ["5,9%", "5\u20249%", "5.9 %"])
def test_parse_product_abv_separators(written):
    assert parse_product(PRODUCT_1645.replace("5.9%", written)).abv == 5.9


def test_parse_product_reads_abv_only_from_description():
    assert "-19%" in PRODUCT_1645                   # discount badges of related products
    assert parse_product(PRODUCT_1645.replace("Alcohol volume: 5.9%.", "")).abv is None


def test_parse_product_missing_fields():
    assert parse_product("<html><body><p>Not found</p></body></html>") == ParmaProduct(None, None, None)


# --- name and volume ----------------------------------------------------------

@pytest.mark.parametrize("title, name, ml", [
    ('Beer "Dahook Ipa" light 330ml', "Dahook Ipa light", 330),
    ('Beer "379" cherry, dark 330ml', "379 cherry, dark", 330),
    ('Beer "Baltika №9" light 450ml', "Baltika №9 light", 450),
    ('Beer "Volfas Engelman Sviesusis 1410" light 568ml', "Volfas Engelman Sviesusis 1410 light", 568),
    ('Beer "Trappistes Rochefort 10" dark 330ml', "Trappistes Rochefort 10 dark", 330),
    ('Beer "Kilikia" 1.5l', "Kilikia", 1500),
    ('Beer "Kilikia"', "Kilikia", None),
])
def test_clean_name_and_volume(title, name, ml):
    assert clean_name(title) == name
    assert volume_ml(title) == ml


# --- fetch_parma --------------------------------------------------------------

def test_fetch_parma_walks_pages_and_fetches_product_pages_only_for_new_codes():
    known = all_ids(REAL_WALK) - {"28051", "28637"}
    http = FakeHttp({**listing_pages(*REAL_WALK),
                     URL_28051: PRODUCT_28051,
                     URL_28637: FetchError("http", "404")})
    result = fetch_parma(http, PLACE, known, NOW, {})

    assert (result.ok, result.error, result.key, result.source, result.place_id, result.full) == \
        (True, None, "parma:parma", "parma", "parma", True)
    # page 4 has 7 cards, so page 5 is never asked for; then only the two new codes get product pages
    assert http.urls() == [list_url(1), list_url(2), list_url(3), list_url(4), URL_28637, URL_28051]
    assert all(headers == GZIP for _, headers in http.calls)

    by_id = {s.shop_item_id: s for s in result.sightings}
    assert len(result.sightings) == len(by_id) == 186      # 187 cards minus the one whose product page failed
    assert "28637" not in by_id
    assert by_id["28051"] == Sighting(
        place_id="parma", source="parma", beer_key="n:dahook ipa light", title='Beer "Dahook Ipa" light 330ml',
        name="Dahook Ipa light", seen_at=NOW, brewery="Dahook LLC", shop_item_id="28051", abv=6.0,
        price_amd=790, volume_ml=330, in_stock=True, category="beer", url=URL_28051)
    vimpel = by_id["21593"]                                 # known code: no product page, so no brewery or abv
    assert (vimpel.beer_key, vimpel.brewery, vimpel.abv, vimpel.price_amd, vimpel.in_stock) == \
        ("n:vimpel lager light", None, None, 520, True)
    assert by_id["26934"].beer_key == "n:379 cherry dark"
    assert by_id["226934"].title == 'Beer "379" cherry, dark 330ml'   # renumbered copy on page 2


def test_fetch_parma_sighting_keeps_out_of_stock_flag():
    soup = BeautifulSoup(P4, "html.parser")
    soup.select("div.product_item")[-1].append(soup.new_tag("div", attrs={"class": "not_av_content"}))
    pages = (P1, renumber(P1, "2"), renumber(P1, "3"), str(soup))
    http = FakeHttp({**listing_pages(*pages), URL_28051: PRODUCT_28051})
    result = fetch_parma(http, PLACE, all_ids(pages) - {"28051"}, NOW, {})
    by_id = {s.shop_item_id: s for s in result.sightings}
    assert (by_id["28051"].in_stock, by_id["28051"].brewery) == (False, "Dahook LLC")
    assert by_id["28637"].in_stock is True


def test_fetch_parma_uses_brewery_aliases_in_keys():
    http = FakeHttp(listing_pages(*REAL_WALK))
    result = fetch_parma(http, PLACE, all_ids(REAL_WALK), NOW, {"dahook": "dahook craft"})
    by_id = {s.shop_item_id: s for s in result.sightings}
    assert by_id["28051"].beer_key == "n:dahook craft ipa light"


def test_fetch_parma_skips_title_without_key_and_its_product_page():
    pages = (P1, renumber(P1, "2"), renumber(P1, "3"),
             P4.replace('Beer "Paulaner Weissbier" light 500ml', "Beer 500ml"))
    http = FakeHttp(listing_pages(*pages))                  # no product page for 99846 is served
    result = fetch_parma(http, PLACE, all_ids(pages) - {"99846"}, NOW, {})
    assert result.ok
    assert "99846" not in {s.shop_item_id for s in result.sightings}
    assert len(result.sightings) == 186


def test_fetch_parma_stops_after_eight_pages():
    pages = [page(60, str(i)) for i in range(1, 10)]        # a 9th full page exists but must not be read
    http = FakeHttp(listing_pages(*pages))
    result = fetch_parma(http, PLACE, all_ids(pages), NOW, {})
    assert http.urls() == [list_url(i) for i in range(1, 9)]
    assert result.ok and len(result.sightings) == 480


def test_fetch_parma_counts_each_code_once():
    http = FakeHttp({list_url(i): P1 for i in range(1, 9)})   # site ignores ?page=: same 60 cards every time
    result = fetch_parma(http, PLACE, set(), NOW, {})
    assert len(http.calls) == 8
    assert (result.ok, result.error, result.sightings) == (False, "empty", [])


@pytest.mark.parametrize("last, ok", [(29, False), (30, True)])
def test_fetch_parma_needs_150_cards(last, ok):
    pages = (page(60, "1"), page(60, "2"), page(last, "3"))
    http = FakeHttp(listing_pages(*pages))
    result = fetch_parma(http, PLACE, all_ids(pages), NOW, {})
    assert http.urls() == [list_url(1), list_url(2), list_url(3)]   # stops after the short page
    assert result.ok is ok
    assert result.error == (None if ok else "empty")
    assert len(result.sightings) == (150 if ok else 0)


def test_fetch_parma_short_first_page_is_empty_without_product_requests():
    http = FakeHttp(listing_pages(P4))
    result = fetch_parma(http, PLACE, set(), NOW, {})
    assert http.urls() == [list_url(1)]
    assert (result.ok, result.error, result.key, result.place_id) == (False, "empty", "parma:parma", "parma")


@pytest.mark.parametrize("kind", ["network", "cloudflare", "http"])
def test_fetch_parma_listing_failure_fails_the_run(kind):
    http = FakeHttp({list_url(1): P1, list_url(2): FetchError(kind, list_url(2))})
    result = fetch_parma(http, PLACE, set(), NOW, {})
    assert http.urls() == [list_url(1), list_url(2)]
    assert (result.ok, result.error, result.sightings, result.key) == (False, kind, [], "parma:parma")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 120 .venv/bin/pytest tests/test_parma.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taps.sources.parma'`

- [ ] **Step 3: Write minimal implementation**

Create `taps/sources/parma.py`:
```python
"""Source 🛒 Parma (spec 4.6): beer category listing plus product pages for new codes."""
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from taps.config import Place
from taps.fetch import FetchError, Http
from taps.model import Sighting, SourceResult, n_key

BASE = "https://parma.am"
CATEGORY = "beer"
LIST_URL = "https://parma.am/en/product/category?slug=beer&available=false&page={page}"
HEADERS = {"Accept-Encoding": "gzip"}
PAGE_SIZE = 60           # a shorter page is the last one
MAX_PAGES = 8            # shortcut: spec cap of 480 cards, fine at ~224 beers; cards past page 8 would be missed
MIN_CARDS = 150          # fewer distinct cards: the run does not count
ABV_RE = re.compile(r"(\d+(?:[.,\u2024]\d+)?)\s*%")
# Product link on parma.am; the code is the part after the last "_" of the slug.
_PRODUCT_URL_RE = re.compile(r"https://parma\.am/en/product/product\?slug=[^&#]*_(\d+)")
_PRICE_RE = re.compile(r"\d+(?:\.\d+)?")
_PREFIX_RE = re.compile(r"^\s*beer\b", re.I)
_QUOTES_RE = re.compile(r"[\"«»“”„]")
_VOLUME_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(ml|l)\b", re.I)


@dataclass(frozen=True)
class ParmaCard:
    item_id: str
    title: str
    price_amd: int | None
    in_stock: bool
    url: str


@dataclass(frozen=True)
class ParmaProduct:
    manufacturer: str | None
    country: str | None
    abv: float | None


def _text(tag) -> str:
    return " ".join(tag.get_text(" ").split())


def parse_listing(html: str) -> list[ParmaCard]:
    """Cards of one category page; a card without a parma.am product link, code or title is skipped."""
    cards = []
    for card in BeautifulSoup(html, "html.parser").select("div.product_item"):
        link = card.select_one("a.item_name[href]")
        span = link.find("span", recursive=False) if link else None
        m = _PRODUCT_URL_RE.fullmatch(urljoin(BASE, link["href"])) if link else None
        title = _text(span) if span else ""
        if m is None or not title:
            continue
        price = card.select_one("span.product_price[data-price]")
        p = _PRICE_RE.fullmatch(price["data-price"].strip()) if price else None
        cards.append(ParmaCard(
            item_id=m.group(1),
            title=title,
            price_amd=round(float(p.group(0))) if p else None,
            in_stock=card.select_one("div.not_av_content") is None,
            url=m.group(0),
        ))
    return cards


def parse_product(html: str) -> ParmaProduct:
    """Manufacturer and country rows; ABV only from the description (the page has other % badges)."""
    soup = BeautifulSoup(html, "html.parser")
    rows: dict[str, str] = {}
    for row in soup.select("p.description-item"):
        spans = row.find_all("span", recursive=False)
        if len(spans) >= 2:
            rows.setdefault(_text(spans[0]).rstrip(" :").lower(), _text(spans[1]))
    desc = soup.select_one("div.ingredients")
    m = ABV_RE.search(desc.get_text(" ")) if desc else None
    return ParmaProduct(
        manufacturer=rows.get("manufacturer") or None,
        country=rows.get("production country") or None,
        abv=float(m.group(1).replace(",", ".").replace("\u2024", ".")) if m else None,
    )


def clean_name(title: str) -> str:
    """'Beer "379" cherry, dark 330ml' -> '379 cherry, dark'."""
    name = _VOLUME_RE.sub(" ", _QUOTES_RE.sub(" ", _PREFIX_RE.sub("", title)))
    return " ".join(name.split()).strip(" ,")


def volume_ml(title: str) -> int | None:
    m = _VOLUME_RE.search(title)
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    return round(value if m.group(2).lower() == "ml" else value * 1000)


def fetch_parma(http: Http, place: Place, known_item_ids: set[str], now: datetime,
                brewery_aliases: Mapping[str, str]) -> SourceResult:
    """Listing pages until a short one (max 8), then product pages for new codes only;
    a new code whose product page fails is left out of this run."""
    key = f"parma:{place.id}"
    cards: dict[str, ParmaCard] = {}
    try:
        for page in range(1, MAX_PAGES + 1):
            found = parse_listing(http.get(LIST_URL.format(page=page), headers=HEADERS).text)
            for card in found:
                cards.setdefault(card.item_id, card)
            if len(found) < PAGE_SIZE:
                break
    except FetchError as e:
        return SourceResult(key=key, source="parma", ok=False, error=e.kind, place_id=place.id)
    if len(cards) < MIN_CARDS:
        return SourceResult(key=key, source="parma", ok=False, error="empty", place_id=place.id)

    sightings = []
    for card in cards.values():
        beer_key = n_key(card.title, brewery_aliases)
        if beer_key == "n:":
            continue
        product = None
        if card.item_id not in known_item_ids:
            try:
                product = parse_product(http.get(card.url, headers=HEADERS).text)
            except FetchError:
                continue
        sightings.append(Sighting(
            place_id=place.id, source="parma", beer_key=beer_key, title=card.title,
            name=clean_name(card.title), seen_at=now,
            brewery=product.manufacturer if product else None,
            shop_item_id=card.item_id,
            abv=product.abv if product else None,
            price_amd=card.price_amd, volume_ml=volume_ml(card.title),
            in_stock=card.in_stock, category=CATEGORY, url=card.url,
        ))
    return SourceResult(key=key, source="parma", ok=True, sightings=sightings, place_id=place.id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `timeout 120 .venv/bin/pytest tests/test_parma.py -v`
Expected: `36 passed`

- [ ] **Step 5: Commit**

```bash
git add tests/test_parma.py taps/sources/parma.py
git commit -m "feat: add Parma shop source"
```

---

### Task 14: Предохранитель источников

**Files:**
- Create: `taps/breaker.py`
- Test: `tests/test_breaker.py`

**Interfaces:**
- Consumes: `taps.model.SOURCE_KINDS`, `SourceResult`, `Sighting`, `BreweryBeer`, `u_key`; `taps.state.State`, `SourceRec`, `PairRec`, `empty_state`, `State.source(key)`; `taps.timeutil.iso`, `parse_iso`, `age_days`.
- Produces: `BREAKER_MIN_COUNT = 20`, `TRIP_ACCEPT_STREAK = 3`, `TRIP_OVERLAP = 0.8`, `STALE_DAYS = 3`, `LIST_STALE_DAYS = 30`, `LIST_MAX_NEW = 5`, `NONEMPTY_KINDS`; `@dataclass Verdict(action: str, reason: str | None)`; `result_keys(result: SourceResult) -> set[str]`; `overlap(a: set[str], b: set[str]) -> float`; `evaluate(result: SourceResult, state: State, now: datetime) -> Verdict`.

Модуль решает, что `rules.merge_results` делает с каждым результатом источника: влить (`merge`), влить молча (`baseline`: первый прогон, `last_ok` старше 3 суток или 30 суток для списка сортов, принятие после трёх похожих срабатываний) или отбросить (`discard`: ошибка, пустой результат, резкое уменьшение, массовые новые ключи, больше 5 новых id в списке сортов). Разделы спеки §10 (предохранитель) и §6 (триггеры тихого первого прогона). `evaluate` меняет только `fail_streak`/`last_error` и поля серии срабатываний; `last_ok`, `last_count`, `baseline_done` обновляет `rules.py` после слияния.

- [ ] **Step 1: Write the failing test**

Create `tests/test_breaker.py`:
```python
from datetime import datetime, timedelta, timezone

from taps.breaker import Verdict, evaluate, overlap, result_keys
from taps.model import BreweryBeer, Sighting, SourceResult
from taps.state import PairRec, empty_state
from taps.timeutil import iso

NOW = datetime(2026, 9, 23, 13, 0, tzinfo=timezone.utc)
PLACE = "gargoyle"
KEY = f"untappd_menu:{PLACE}"


def sighting(key, source="untappd_menu", place=PLACE, item_id=None):
    return Sighting(place_id=place, source=source, beer_key=key, title=key, name=key,
                    seen_at=NOW, shop_item_id=item_id)


def menu(keys, key=KEY, source="untappd_menu", place=PLACE, full=True):
    return SourceResult(key=key, source=source, ok=True, place_id=place, full=full,
                        sightings=[sighting(k, source, place) for k in keys])


def keys(prefix, n, start=0):
    return [f"n:{prefix} {i}" for i in range(start, start + n)]


def state_with(key=KEY, last_count=0, days_ago=1.0, pairs=(), place=PLACE, **rec_fields):
    st = empty_state(NOW - timedelta(days=60))
    rec = st.source(key)
    rec.baseline_done = True
    rec.last_ok = iso(NOW - timedelta(days=days_ago))
    rec.last_count = last_count
    for name, value in rec_fields.items():
        setattr(rec, name, value)
    t = iso(NOW - timedelta(days=10))
    st.pairs[place] = {k: PairRec(first_seen=t, last_seen=t) for k in pairs}
    return st


def brewery_list(ids, key="untappd_brewery_list:265165"):
    beers = [BreweryBeer(brewery_id=265165, untappd_beer_id=i, name=f"B{i}", brewery="Dargett") for i in ids]
    return SourceResult(key=key, source="untappd_brewery_list", ok=True, brewery_id=265165, brewery_beers=beers)


# --- failures ---------------------------------------------------------------

def test_failed_result_is_discarded_with_its_error_and_counts_a_failure():
    st = state_with(fail_streak=2, last_count=30, pairs=keys("a", 30))
    res = SourceResult(key=KEY, source="untappd_menu", ok=False, error="cloudflare", place_id=PLACE)
    assert evaluate(res, st, NOW) == Verdict("discard", "cloudflare")
    rec = st.sources[KEY]
    assert rec.fail_streak == 3 and rec.last_error == "cloudflare"
    assert rec.last_ok == iso(NOW - timedelta(days=1))   # not touched here
    assert rec.trip_streak == 0


def test_failed_result_on_a_new_key_creates_the_source_record():
    st = empty_state(NOW)
    res = SourceResult(key="parma:parma", source="parma", ok=False, error="network", place_id="parma")
    assert evaluate(res, st, NOW) == Verdict("discard", "network")
    assert st.sources["parma:parma"].fail_streak == 1


def test_ok_result_with_zero_items_is_empty_failure():
    st = state_with(last_count=30, pairs=keys("a", 30))
    assert evaluate(menu([]), st, NOW) == Verdict("discard", "empty")
    assert st.sources[KEY].fail_streak == 1 and st.sources[KEY].last_error == "empty"


def test_zero_checkins_is_not_a_failure():
    key = f"untappd_checkins:{PLACE}"
    st = state_with(key=key)
    res = SourceResult(key=key, source="untappd_checkins", ok=True, place_id=PLACE)
    assert evaluate(res, st, NOW) == Verdict("merge", None)


# --- baseline triggers ------------------------------------------------------

def test_first_run_is_baseline():
    st = empty_state(NOW)
    assert evaluate(menu(keys("a", 40)), st, NOW) == Verdict("baseline", "first_run")


def test_last_ok_four_days_old_is_stale_baseline():
    st = state_with(days_ago=4, last_count=5)
    assert evaluate(menu(keys("a", 5)), st, NOW) == Verdict("baseline", "stale")


def test_last_ok_under_three_days_merges():
    st = state_with(days_ago=2.9, last_count=5)
    assert evaluate(menu(keys("a", 5)), st, NOW) == Verdict("merge", None)


def test_last_ok_exactly_three_days_merges_one_minute_more_is_stale():
    st = state_with(days_ago=3.0, last_count=5)
    assert evaluate(menu(keys("a", 5)), st, NOW) == Verdict("merge", None)
    st = state_with(days_ago=3.0 + 1 / 1440, last_count=5)
    assert evaluate(menu(keys("a", 5)), st, NOW) == Verdict("baseline", "stale")


def test_brewery_list_uses_thirty_day_staleness():
    st = state_with(key="untappd_brewery_list:265165", days_ago=10, max_beer_id=100)
    assert evaluate(brewery_list([90, 95, 101]), st, NOW) == Verdict("merge", None)
    st = state_with(key="untappd_brewery_list:265165", days_ago=31, max_beer_id=100)
    assert evaluate(brewery_list([90, 95, 101]), st, NOW) == Verdict("baseline", "stale")


def test_brewery_list_staleness_boundary_at_exactly_thirty_days():
    key = "untappd_brewery_list:265165"
    st = state_with(key=key, days_ago=30.0, max_beer_id=100)
    assert evaluate(brewery_list([90, 95, 101]), st, NOW) == Verdict("merge", None)
    st = state_with(key=key, days_ago=30.0 + 1 / 1440, max_beer_id=100)
    assert evaluate(brewery_list([90, 95, 101]), st, NOW) == Verdict("baseline", "stale")


# --- breaker trips ----------------------------------------------------------

def test_shrink_below_half_discards_and_starts_trip_streak():
    old = keys("a", 100)
    st = state_with(last_count=100, pairs=old)
    assert evaluate(menu(old[:40]), st, NOW) == Verdict("discard", "shrink")
    rec = st.sources[KEY]
    assert rec.trip_streak == 1
    assert rec.last_trip_keys == sorted(old[:40])
    assert rec.fail_streak == 1 and rec.last_error == "shrink"


def test_breaker_min_count_boundary_nineteen_never_trips_twenty_does():
    st = state_with(last_count=19, pairs=keys("a", 19))
    assert evaluate(menu(keys("a", 5)), st, NOW) == Verdict("merge", None)
    st = state_with(last_count=20, pairs=keys("a", 20))
    assert evaluate(menu(keys("a", 5)), st, NOW) == Verdict("discard", "shrink")


def test_exactly_half_is_not_a_shrink():
    old = keys("a", 100)
    st = state_with(last_count=100, pairs=old)
    assert evaluate(menu(old[:50]), st, NOW) == Verdict("merge", None)


def test_mass_new_keys_discard():
    st = state_with(last_count=30, pairs=keys("a", 30))
    res = menu(keys("a", 14) + keys("b", 16))          # 16/30 unknown > 0.5
    assert evaluate(res, st, NOW) == Verdict("discard", "mass_new")
    st = state_with(last_count=30, pairs=keys("a", 30))
    res = menu(keys("a", 15) + keys("b", 15))          # exactly half unknown
    assert evaluate(res, st, NOW) == Verdict("merge", None)


def test_mass_new_counts_shop_items_already_stored_under_another_key_as_known():
    place, key = "parma", "parma:parma"
    old = keys("old", 30)
    st = state_with(key=key, place=place, last_count=30, pairs=old)
    st.shop_items[place] = {str(i): k for i, k in enumerate(old)}
    renamed = [Sighting(place_id=place, source="parma", beer_key=f"n:renamed {i}", title="x", name="x",
                        seen_at=NOW, shop_item_id=str(i)) for i in range(30)]
    res = SourceResult(key=key, source="parma", ok=True, place_id=place, sightings=renamed)
    assert evaluate(res, st, NOW) == Verdict("merge", None)


def test_small_source_never_trips():
    st = state_with(last_count=10, pairs=keys("a", 10))
    assert evaluate(menu(keys("b", 2)), st, NOW) == Verdict("merge", None)


def test_beercity_partial_run_never_trips():
    place, key = "beer-city", "beercity:beer-city"
    st = state_with(key=key, place=place, last_count=300, pairs=keys("a", 300))
    res = menu(keys("z", 12), key=key, source="beercity", place=place, full=False)
    assert evaluate(res, st, NOW) == Verdict("merge", None)
    assert st.sources[key].trip_streak == 0


def test_checkins_never_trip():
    key = f"untappd_checkins:{PLACE}"
    st = state_with(key=key, last_count=100)
    res = menu(keys("z", 3), key=key, source="untappd_checkins")
    assert evaluate(res, st, NOW) == Verdict("merge", None)


def test_three_similar_trips_are_accepted_as_baseline_and_reset():
    old = keys("a", 100)
    new = keys("a", 40)
    st = state_with(last_count=100, pairs=old)
    assert evaluate(menu(new), st, NOW) == Verdict("discard", "shrink")
    assert evaluate(menu(new[:36] + keys("c", 4)), st, NOW) == Verdict("discard", "shrink")   # 36/40 = 0.9
    assert st.sources[KEY].trip_streak == 2
    assert evaluate(menu(new[:35] + keys("d", 5)), st, NOW) == Verdict("baseline", "accepted")
    rec = st.sources[KEY]
    assert rec.trip_streak == 0 and rec.last_trip_keys == []


def test_overlap_exactly_point_eight_counts_as_similar():
    old = keys("a", 100)
    st = state_with(last_count=100, pairs=old)
    evaluate(menu(old[:40]), st, NOW)
    assert st.sources[KEY].trip_streak == 1
    second = old[:32] + keys("c", 8)          # 32 shared of 40: overlap == 0.8
    assert evaluate(menu(second), st, NOW) == Verdict("discard", "shrink")
    assert st.sources[KEY].trip_streak == 2


def test_dissimilar_trip_restarts_streak_at_one():
    old = keys("a", 100)
    st = state_with(last_count=100, pairs=old)
    evaluate(menu(old[:40]), st, NOW)
    evaluate(menu(old[:40]), st, NOW)
    assert st.sources[KEY].trip_streak == 2
    other = old[60:100]                                    # no overlap with the previous trip
    assert evaluate(menu(other), st, NOW) == Verdict("discard", "shrink")
    assert st.sources[KEY].trip_streak == 1
    assert st.sources[KEY].last_trip_keys == sorted(other)


def test_a_normal_run_between_trips_ends_the_series():
    old = keys("a", 100)
    st = state_with(last_count=100, pairs=old)
    evaluate(menu(old[:40]), st, NOW)
    evaluate(menu(old[:40]), st, NOW)
    assert evaluate(menu(old), st, NOW) == Verdict("merge", None)
    assert st.sources[KEY].trip_streak == 0 and st.sources[KEY].last_trip_keys == []
    assert evaluate(menu(old[:40]), st, NOW) == Verdict("discard", "shrink")
    assert st.sources[KEY].trip_streak == 1


def test_failed_run_does_not_break_trip_series():
    old = keys("a", 100)
    st = state_with(last_count=100, pairs=old)
    evaluate(menu(old[:40]), st, NOW)
    evaluate(menu(old[:40]), st, NOW)
    evaluate(SourceResult(key=KEY, source="untappd_menu", ok=False, error="network"), st, NOW)
    assert evaluate(menu(old[:40]), st, NOW) == Verdict("baseline", "accepted")


def test_four_consecutive_failures_never_accepted_and_leave_trip_streak_at_zero():
    st = state_with(last_count=30, pairs=keys("a", 30))
    for kind in ("cloudflare", "network", "cloudflare", "network"):
        res = SourceResult(key=KEY, source="untappd_menu", ok=False, error=kind, place_id=PLACE)
        assert evaluate(res, st, NOW) == Verdict("discard", kind)
    assert st.sources[KEY].trip_streak == 0 and st.sources[KEY].fail_streak == 4
    assert evaluate(menu([]), st, NOW) == Verdict("discard", "empty")
    assert st.sources[KEY].trip_streak == 0 and st.sources[KEY].fail_streak == 5


# --- brewery list -----------------------------------------------------------

def test_brewery_list_six_new_ids_discard_five_merge():
    lkey = "untappd_brewery_list:265165"
    st = state_with(key=lkey, max_beer_id=1000)
    assert evaluate(brewery_list([900, 1000] + list(range(1001, 1007))), st, NOW) == Verdict("discard", "list_mass_new")
    st = state_with(key=lkey, max_beer_id=1000)
    assert evaluate(brewery_list([900, 1000] + list(range(1001, 1006))), st, NOW) == Verdict("merge", None)


def test_brewery_list_first_run_is_baseline_not_trip():
    st = empty_state(NOW)
    assert evaluate(brewery_list(range(1, 40)), st, NOW) == Verdict("baseline", "first_run")


# --- manual -----------------------------------------------------------------

def test_manual_always_merges_even_on_first_run_and_old_last_ok():
    st = empty_state(NOW)
    res = SourceResult(key="manual", source="manual", ok=True,
                       sightings=[sighting("u:1", source="manual")])
    assert evaluate(res, st, NOW) == Verdict("merge", None)
    st = state_with(key="manual", days_ago=40)
    assert evaluate(SourceResult(key="manual", source="manual", ok=True), st, NOW) == Verdict("merge", None)


# --- helpers ----------------------------------------------------------------

def test_result_keys_and_overlap():
    assert result_keys(menu(["n:a", "u:2", "n:a"])) == {"n:a", "u:2"}
    assert result_keys(brewery_list([5, 7])) == {"u:5", "u:7"}
    assert overlap({"a", "b", "c", "d", "e"}, {"a", "b", "c", "d"}) == 0.8
    assert overlap(set(), {"a"}) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 120 .venv/bin/pytest tests/test_breaker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taps.breaker'`

- [ ] **Step 3: Write minimal implementation**

Create `taps/breaker.py`:
```python
"""Source breaker (spec §10) and silent-baseline triggers (spec §6)."""
from dataclasses import dataclass
from datetime import datetime

from taps.model import SOURCE_KINDS, SourceResult, u_key
from taps.state import SourceRec, State
from taps.timeutil import age_days, parse_iso

BREAKER_MIN_COUNT = 20
TRIP_ACCEPT_STREAK = 3
TRIP_OVERLAP = 0.8
STALE_DAYS = 3
LIST_STALE_DAYS = 30
LIST_MAX_NEW = 5
NONEMPTY_KINDS = ("menu", "shop", "brewery_list")   # an ok result with 0 items from these is "empty"


@dataclass
class Verdict:
    action: str          # "merge" | "baseline" | "discard"
    reason: str | None   # "first_run"|"stale"|"accepted"|"shrink"|"mass_new"|"list_mass_new"|<result.error>


def result_keys(result: SourceResult) -> set[str]:
    if result.brewery_beers:
        return {u_key(b.untappd_beer_id) for b in result.brewery_beers}
    return {s.beer_key for s in result.sightings}


def overlap(a: set[str], b: set[str]) -> float:
    """Share of common keys relative to the larger set; 0 when either is empty."""
    if not a or not b:
        return 0.0
    return len(a & b) / max(len(a), len(b))


def _trip_reason(result: SourceResult, rec: SourceRec, state: State, keys: set[str]) -> str | None:
    kind = SOURCE_KINDS[result.source]
    if kind == "brewery_list":
        if not rec.baseline_done:
            return None
        new_ids = {b.untappd_beer_id for b in result.brewery_beers if b.untappd_beer_id > rec.max_beer_id}
        return "list_mass_new" if len(new_ids) > LIST_MAX_NEW else None
    if kind not in ("menu", "shop") or not result.full or rec.last_count < BREAKER_MIN_COUNT:
        return None
    if len(keys) < rec.last_count / 2:
        return "shrink"
    known = state.pairs.get(result.place_id, {})
    items = state.shop_items.get(result.place_id, {})   # a shop item keeps the key it was first stored under
    unknown = {
        s.beer_key for s in result.sightings
        if s.beer_key not in known and items.get(s.shop_item_id) not in known
    }
    return "mass_new" if len(unknown) / len(keys) > 0.5 else None


def evaluate(result: SourceResult, state: State, now: datetime) -> Verdict:
    """Decide what rules.py does with one result.

    Mutates only fail_streak/last_error and the trip fields of the source rec;
    last_ok, last_count, baseline_done etc. are updated by rules.py after a merge.
    """
    rec = state.source(result.key)
    kind = SOURCE_KINDS[result.source]
    keys = result_keys(result)
    error = None
    if not result.ok:
        error = result.error or "error"
    elif not keys and kind in NONEMPTY_KINDS:
        error = "empty"
    if error:
        rec.fail_streak += 1
        rec.last_error = error
        return Verdict("discard", error)
    if kind == "manual":
        return Verdict("merge", None)

    reason = _trip_reason(result, rec, state, keys)
    if reason:
        similar = overlap(keys, set(rec.last_trip_keys)) >= TRIP_OVERLAP
        rec.trip_streak = rec.trip_streak + 1 if similar else 1
        rec.last_trip_keys = sorted(keys)
        if rec.trip_streak < TRIP_ACCEPT_STREAK:
            rec.fail_streak += 1
            rec.last_error = reason
            return Verdict("discard", reason)
    # accepted after repeated trips, or no trip at all: a trip series ends here
    rec.trip_streak = 0
    rec.last_trip_keys = []
    if reason:
        return Verdict("baseline", "accepted")
    if not rec.baseline_done:
        return Verdict("baseline", "first_run")
    limit = LIST_STALE_DAYS if kind == "brewery_list" else STALE_DAYS
    if rec.last_ok is None or age_days(parse_iso(rec.last_ok), now) > limit:
        return Verdict("baseline", "stale")
    return Verdict("merge", None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `timeout 120 .venv/bin/pytest tests/test_breaker.py -v`
Expected: `28 passed`

- [ ] **Step 5: Commit**

```bash
git add tests/test_breaker.py taps/breaker.py
git commit -m "feat: add source breaker with trip acceptance and baseline triggers"
```

---

### Task 15: Правила: слияние наблюдений и события

**Files:**
- Create: `taps/rules.py`
- Test: `tests/test_rules.py`

**Interfaces:**
- Consumes: `taps.breaker.evaluate(result, state, now) -> Verdict` и `taps.breaker.result_keys(result) -> set[str]`; `taps.state.State` (`source(key)`, `pair(place_id, beer_key)`, `pairs`, `beers`, `brewery_new`, `shop_items`, `sources`, `announced_manual`), `PairRec`, `BeerRec`, `BreweryNewRec`, `SourceRec`, `resolve_alias(key, aliases)`; `taps.model.Sighting` (`kind`), `SourceResult`, `u_key`, `untappd_n_key`, `normalize_title`, `strip_color`; `taps.shop_filter.classify(title, brand, not_craft, category)`; `taps.config.Config.places`, `Place.sources` / `source_keys()` / `has_menu` / `kind` / `brewery_id` / `brewery_name`; `taps.corrections.Corrections` (`hide`, `aliases`, `brewery_aliases`, `not_craft`); `taps.timeutil.iso`, `parse_iso`, `age_days`, `to_yerevan`. Тесты также используют `taps.state.empty_state`, `apply_aliases`, `taps.corrections.ManualEntry`, `taps.model.BreweryBeer` и `taps.sources.manual.manual_result(corrections, config, now)`.
- Produces: `CHECKIN_EVENT_DAYS = 7`, `CHECKIN_KEEP_DAYS = 21`, `MANUAL_EVENT_DAYS = 3`; `MergeOutcome(events, brewery_events, failed, tripped, accepted, ok)`; `merge_results(state, results, config, corrections, now) -> MergeOutcome`. В `PairRec.info` пишутся ключи `source, kind, title, name, brewery, style, abv, ibu, rating, price_amd, volume_ml, container, serving, url, menu_id, shop_item_id, manual_id, manual_by, manual_date, checkin_at, hidden` (значения None не пишутся), в `BreweryNewRec.info` — `name, brewery, style, abv, url`. Дополнительные константы: `TRIP_REASONS`, `INFO_FIELDS`, `BREWERY_INFO_FIELDS`, `KIND_RANK`.

Сердце защиты от спама (спека §6, §10, §8): `merge_results` прогоняет каждый результат источника через `breaker.evaluate`. Затем по правилам §6 сливает наблюдения в `state`: тихий первый прогон (новый источник, новое место, новая вкладка меню, устаревание, принятый предохранитель), ⭐ по `beers` до вызова, фильтры чекинов, «со слов», `hide` и фильтр магазинов, наличие в магазине, склейки, фиксированные ключи товаров, цвет Parma и 🏭 по списку сортов. После удачного слияния обновляет `sources[...]` и `last_in_result` и пишет в `PairRec.info` поля, которые читают `digest.py` и `site_data.py`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_rules.py`:
```python
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from taps.config import Brewery, Config, Place, Settings
from taps.corrections import Corrections, ManualEntry
from taps.model import BreweryBeer, Sighting, SourceResult
from taps.rules import CHECKIN_EVENT_DAYS, CHECKIN_KEEP_DAYS, MANUAL_EVENT_DAYS, MergeOutcome, merge_results
from taps.sources.manual import manual_result
from taps.state import BeerRec, BreweryNewRec, PairRec, SourceRec, apply_aliases, empty_state
from taps.timeutil import iso, to_yerevan

NOW = datetime(2026, 9, 24, 14, 17, tzinfo=timezone.utc)   # 18:17 in Yerevan
H = timedelta(hours=1)
DAY = timedelta(days=1)


def place(pid, kind, sources, **kw):
    return Place(id=pid, name=pid.title(), kind=kind, sources=sources, **kw)


def venue(venue_id):
    return {"slug": f"venue-{venue_id}", "venue_id": venue_id}


CONFIG = Config(
    places={p.id: p for p in (
        place("gargoyle", "bar", {"untappd_menu": venue(1)}),
        place("dargett-brewpub", "brewpub", {"buyam": {"url": "https://buy.am/en/restaurants/dargett"}},
              brewery_id=265165, brewery_name="Dargett", untappd_venue_id=2),
        place("dors", "brewpub", {"untappd_checkins": venue(3)}, brewery_id=441775, brewery_name="Dors"),
        place("379-torch-brew", "brewpub", {"untappd_checkins": venue(4)}, brewery_id=518994, brewery_name="379"),
        place("tap-station", "bar", {"untappd_checkins": venue(5)}),
        place("izh", "bar", {"untappd_checkins": venue(6)}),
        place("beer-city", "shop", {"beercity": {}}),
        place("parma", "shop", {"parma": {}}),
    )},
    breweries=(Brewery("dors", "Dors", 441775, "dors"),
              Brewery("dargett", "Dargett", 265165, "dargett", list_enabled=True)),
    settings=Settings(),
)


def ready(*keys):
    """State where these sources had a successful run 12 hours ago."""
    state = empty_state(NOW - 30 * DAY)
    for key in keys:
        rec = state.source(key)
        rec.baseline_done, rec.last_ok = True, iso(NOW - 12 * H)
    return state


def merge(state, *results, corrections=Corrections(), now=NOW):
    return merge_results(state, results, CONFIG, corrections, now)


def beer(beer_id, place="gargoyle", **kw):
    """Untappd menu item: key u:<id>, n-key n:zagovor ale <id>."""
    fields = dict(place_id=place, source="untappd_menu", beer_key=f"u:{beer_id}", title=f"Zagovor Ale {beer_id}",
                  name=f"Ale {beer_id}", seen_at=NOW, brewery="Zagovor", untappd_beer_id=beer_id,
                  url=f"https://untappd.com/beer/{beer_id}")
    return Sighting(**{**fields, **kw})


def menu(*sightings, place="gargoyle", **kw):
    return SourceResult(key=f"untappd_menu:{place}", source="untappd_menu", ok=True, sightings=list(sightings),
                        place_id=place, **kw)


def item(item_id, key, place="beer-city", source="beercity", in_stock=True, **kw):
    fields = dict(place_id=place, source=source, beer_key=key, title=f'Beer "{key[2:]}" 0.5 l', name=key[2:],
                  seen_at=NOW, shop_item_id=str(item_id), in_stock=in_stock)
    return Sighting(**{**fields, **kw})


def shop(*sightings, place="beer-city", source="beercity", full=True):
    return SourceResult(key=f"{source}:{place}", source=source, ok=True, sightings=list(sightings),
                        place_id=place, full=full)


def checkin(beer_id, place="tap-station", days=1.0, serving="Draft", **kw):
    fields = dict(place_id=place, source="untappd_checkins", beer_key=f"u:{beer_id}", title=f"Ale {beer_id}",
                  name=f"Ale {beer_id}", seen_at=NOW - days * DAY, brewery="Tap Brewers", untappd_beer_id=beer_id,
                  serving=serving, checkin_id=1000 + beer_id)
    return Sighting(**{**fields, **kw})


def venue_checkins(*sightings, place="tap-station"):
    return SourceResult(key=f"untappd_checkins:{place}", source="untappd_checkins", ok=True,
                        sightings=list(sightings), place_id=place)


def brewery_checkins(*sightings, brewery_id=441775):
    """Brewery page check-ins: like the real source, sightings carry the page's brewery_id."""
    return SourceResult(key=f"untappd_brewery:{brewery_id}", source="untappd_brewery", ok=True,
                        sightings=[replace(s, source="untappd_brewery", brewery_id=brewery_id) for s in sightings],
                        brewery_id=brewery_id)


def entry(place, beer_name, days_ago, by="Аня", brewery="379", untappd_id=None):
    day = to_yerevan(NOW).date() - timedelta(days=days_ago)
    return ManualEntry(f"{place}|{day.isoformat()}|{by}", place, brewery, beer_name, untappd_id, by, day)


def manual(*entries):
    return manual_result(Corrections(sightings=entries), CONFIG, NOW)


def beer_list(*ids, brewery_id=265165):
    beers = [BreweryBeer(brewery_id=brewery_id, untappd_beer_id=i, name=f"Ale {i}", brewery="Dargett",
                         style="IPA - New England", abv=6.5, url=f"https://untappd.com/b/dargett-ale/{i}")
             for i in ids]
    return SourceResult(key=f"untappd_brewery_list:{brewery_id}", source="untappd_brewery_list", ok=True,
                        brewery_id=brewery_id, brewery_beers=beers)


# --- silent first run and events --------------------------------------------

def test_first_run_of_a_source_is_a_silent_baseline():
    state = empty_state(NOW)
    out = merge(state, menu(beer(1), beer(2), beer(3)))
    assert out == MergeOutcome(ok=["untappd_menu:gargoyle"])
    pairs = state.pairs["gargoyle"]
    assert sorted(pairs) == ["u:1", "u:2", "u:3"]
    assert all(p.notified_at == "baseline" and p.event_at is None for p in pairs.values())
    assert state.beers["u:2"] == BeerRec(first_seen_city=iso(NOW), n_key="n:zagovor ale 2")
    assert pairs["u:2"].info == {"source": "untappd_menu", "kind": "menu", "title": "Zagovor Ale 2",
                                 "name": "Ale 2", "brewery": "Zagovor", "url": "https://untappd.com/beer/2"}


def test_second_run_announces_only_the_new_beer():
    state = empty_state(NOW - DAY)
    merge(state, menu(beer(1, price_amd=2300), beer(2)), now=NOW - 12 * H)
    out = merge(state, menu(beer(1, price_amd=2500), beer(2), beer(3)))
    assert out.events == [("gargoyle", "u:3")] and out.ok == ["untappd_menu:gargoyle"]
    new = state.pairs["gargoyle"]["u:3"]
    assert (new.first_seen, new.event_at, new.notified_at, new.star) == (iso(NOW), iso(NOW), None, True)
    old = state.pairs["gargoyle"]["u:1"]
    assert (old.notified_at, old.event_at, old.info["price_amd"]) == ("baseline", None, 2500)   # price: no event
    out = merge(state, menu(beer(1), beer(2), beer(3)), now=NOW + 12 * H)
    assert out.events == []
    assert new.star is True and new.event_at == iso(NOW)   # ⭐ is set once, when the pair is created


def test_star_compares_with_beers_known_before_the_call():
    state = ready("untappd_menu:gargoyle", "beercity:beer-city", "untappd_checkins:tap-station")
    t = iso(NOW - 9 * DAY)
    state.beers.update({
        "u:5": BeerRec(first_seen_city=t, n_key="n:zagovor ale 5"),        # seen at another bar
        "n:konix cassis ruby": BeerRec(first_seen_city=t),                 # seen in a shop
        "u:7": BeerRec(first_seen_city=t, n_key="n:mad hatter porter"),    # seen on a menu
    })
    out = merge(state,
                menu(beer(4), beer(5), beer(6, brewery="Konix Brewery", name="Cassis Ruby")),
                shop(item(1, "n:mad hatter porter"), item(2, "n:brand new stout")),
                venue_checkins(checkin(4)))    # u:4 again: written to beers earlier in this same call
    star = {(p, k): state.pairs[p][k].star for p, k in out.events}
    assert star == {
        ("gargoyle", "u:4"): True,                          # new to the city
        ("gargoyle", "u:5"): False,                         # known u: key
        ("gargoyle", "u:6"): False,                         # its n-key was seen in a shop
        ("beer-city", "n:mad hatter porter"): False,        # the n-key of a known Untappd beer
        ("beer-city", "n:brand new stout"): True,
        ("tap-station", "u:4"): True,                       # beers written in this call do not count
    }


def test_hidden_pair_and_not_craft_item_are_suppressed_hidden_and_not_counted_as_seen():
    state = ready("untappd_menu:gargoyle", "beercity:beer-city")
    corrections = Corrections(hide=frozenset({("gargoyle", "u:9")}), not_craft=("Kilikia",))
    out = merge(state, menu(beer(8), beer(9)),
                shop(item(1, "n:kilikia", title='Beer "Kilikia" 1l', brewery="Kilikia"), item(2, "n:konix bronx")),
                corrections=corrections)
    assert out.events == [("gargoyle", "u:8"), ("beer-city", "n:konix bronx")]
    for pid, key in (("gargoyle", "u:9"), ("beer-city", "n:kilikia")):
        rec = state.pairs[pid][key]
        assert (rec.notified_at, rec.event_at, rec.info["hidden"]) == ("suppressed", None, True)
        assert key not in state.beers
    assert "hidden" not in state.pairs["gargoyle"]["u:8"].info and "u:8" in state.beers


def test_hide_added_later_hides_the_pair_at_once_and_cancels_its_pending_event():
    state = ready("untappd_menu:gargoyle")
    merge(state, menu(beer(1), beer(2), beer(3)), now=NOW - 12 * H)    # three pending events
    corrections = Corrections(hide=frozenset({("gargoyle", "u:1"), ("gargoyle", "n:old name")}),
                              aliases={"n:old name": "u:2"})          # hide written with a key aliased later
    assert merge(state, corrections=corrections) == MergeOutcome()   # Untappd is not fetched in this run
    pairs = state.pairs["gargoyle"]
    for key in ("u:1", "u:2"):
        assert pairs[key].info["hidden"] is True and pairs[key].notified_at == "suppressed"
    assert "hidden" not in pairs["u:3"].info and pairs["u:3"].notified_at is None


def test_shop_filter_added_later_cancels_the_event_and_lifting_it_gives_none():
    state = ready("beercity:beer-city")
    kilikia = item(1, "n:kilikia", title='Beer "Kilikia" 1l', brewery="Kilikia")
    assert merge(state, shop(kilikia), now=NOW - 12 * H).events == [("beer-city", "n:kilikia")]
    rec = state.pairs["beer-city"]["n:kilikia"]
    merge(state, shop(kilikia), corrections=Corrections(not_craft=("Kilikia",)))
    assert rec.notified_at == "suppressed" and rec.info["hidden"] is True
    out = merge(state, shop(kilikia), now=NOW + 12 * H)               # Kilikia removed from not_craft
    assert out.events == [] and rec.notified_at == "suppressed" and "hidden" not in rec.info


def test_hide_lifted_later_gives_no_event_and_stays_suppressed():
    state = ready("untappd_menu:gargoyle")
    hidden = Corrections(hide=frozenset({("gargoyle", "u:1")}))
    assert merge(state, menu(beer(1), beer(2)), corrections=hidden, now=NOW - 12 * H).events == [("gargoyle", "u:2")]
    rec = state.pairs["gargoyle"]["u:1"]
    assert rec.notified_at == "suppressed" and rec.info["hidden"] is True
    out = merge(state, menu(beer(1), beer(2)))                      # hide removed from corrections.yaml
    assert out.events == [] and rec.notified_at == "suppressed" and "hidden" not in rec.info


def test_shop_item_hidden_by_brand_stays_hidden_when_a_later_sighting_lacks_brand():
    state = ready("beercity:beer-city")
    corrections = Corrections(not_craft=("Efes",))
    key = "n:lager"
    out1 = merge(state, shop(item(1, key, brewery="Efes")), corrections=corrections)
    rec = state.pairs["beer-city"][key]
    assert out1.events == [] and (rec.notified_at, rec.info["hidden"]) == ("suppressed", True)
    out2 = merge(state, shop(item(1, key, brewery=None)), corrections=corrections, now=NOW + 12 * H)
    assert out2.events == []
    assert rec.notified_at == "suppressed" and rec.info["hidden"] is True


# --- shops: stock, item keys, aliases, Parma colours -------------------------

def test_out_of_stock_new_item_becomes_an_event_only_when_in_stock():
    state = ready("beercity:beer-city")
    key = "n:hard root ipa"
    out = merge(state, shop(item(1, key, in_stock=False)))
    rec = state.pairs["beer-city"][key]
    assert out.events == [] and (rec.notified_at, rec.event_at, rec.in_stock) == (None, None, False)
    assert key in state.beers                    # an out-of-stock item still counts as seen in the city
    assert merge(state, shop(item(1, key, in_stock=False)), now=NOW + 12 * H).events == []
    assert rec.event_at is None
    out = merge(state, shop(item(1, key)), now=NOW + 24 * H)
    assert out.events == [("beer-city", key)]
    assert (rec.event_at, rec.notified_at, rec.in_stock, rec.star) == (iso(NOW + 24 * H), None, True, True)
    merge(state, shop(item(1, key, in_stock=False)), now=NOW + 36 * H)
    assert merge(state, shop(item(1, key)), now=NOW + 48 * H).events == []   # back in stock: not an event
    assert rec.event_at == iso(NOW + 24 * H)


def test_out_of_stock_items_seen_in_the_baseline_run_never_become_events():
    state = empty_state(NOW)
    merge(state, shop(item(1, "n:gose", in_stock=False), item(2, "n:pale")))
    assert state.pairs["beer-city"]["n:gose"].notified_at == "baseline"
    out = merge(state, shop(item(1, "n:gose"), item(2, "n:pale")), now=NOW + 12 * H)
    assert out.events == [] and state.pairs["beer-city"]["n:gose"].event_at is None


def test_never_in_stock_item_seen_in_a_silent_run_is_baseline():
    state = ready("beercity:beer-city")
    merge(state, shop(item(1, "n:gose", in_stock=False)))
    rec = state.pairs["beer-city"]["n:gose"]
    out = merge(state, shop(item(1, "n:gose")), now=NOW + 4 * DAY)    # last run 4 days ago: stale baseline
    assert out.events == [] and (rec.notified_at, rec.event_at) == ("baseline", None)
    assert merge(state, shop(item(1, "n:gose")), now=NOW + 4 * DAY + 12 * H).events == []


@pytest.mark.parametrize("stock", [(True, False), (False, True)])
def test_one_key_sold_as_two_items_is_in_stock_if_either_is(stock):
    state = ready("beercity:beer-city")
    small = item(3, "n:mythos", title='Beer "Mythos" 0.33 l', in_stock=stock[0])
    big = item(5, "n:mythos", title='Beer "Mythos" 0.5 l', in_stock=stock[1])
    out = merge(state, shop(small, big))
    rec = state.pairs["beer-city"]["n:mythos"]
    assert out.events == [("beer-city", "n:mythos")] and rec.in_stock is True and rec.event_at == iso(NOW)
    merge(state, shop(replace(small, in_stock=False), replace(big, in_stock=False)), now=NOW + 12 * H)
    assert rec.in_stock is False


def test_shop_item_keeps_its_first_key_when_the_title_changes():
    state = ready("beercity:beer-city")
    merge(state, shop(item(555, "n:konix bronx", title='Beer "Konix" Bronx 0.33 l')))
    renamed = item(555, "n:konix bronx neipa", title='Beer "Konix" Bronx NEIPA 0.33 l')
    out = merge(state, shop(renamed), now=NOW + 12 * H)
    assert out.events == []
    assert list(state.pairs["beer-city"]) == ["n:konix bronx"]
    assert state.shop_items["beer-city"] == {"555": "n:konix bronx"}
    assert state.pairs["beer-city"]["n:konix bronx"].info["title"] == 'Beer "Konix" Bronx NEIPA 0.33 l'


def test_aliased_shop_item_is_not_announced_again_in_three_runs():
    state = ready("beercity:beer-city")
    bronx = item(555, "n:konix bronx", title='Beer "Konix" Bronx 0.33 l')
    assert merge(state, shop(bronx), now=NOW - 12 * H).events == [("beer-city", "n:konix bronx")]
    state.pairs["beer-city"]["n:konix bronx"].notified_at = iso(NOW - 11 * H)   # sent in a digest
    aliases = {"n:konix bronx": "u:3539672"}                                     # Oleg glued it to Untappd
    twin = item(556, "n:konix bronx", title='Beer "Konix" Bronx 0.5 l')         # a new item id, same beer
    for run in range(3):
        apply_aliases(state, aliases)            # run.py does this right after loading state
        out = merge(state, shop(bronx, twin), corrections=Corrections(aliases=aliases), now=NOW + run * 12 * H)
        assert out.events == []
    assert list(state.pairs["beer-city"]) == ["u:3539672"]
    assert state.shop_items["beer-city"] == {"555": "u:3539672", "556": "u:3539672"}


def test_parma_code_without_colour_joins_the_existing_key_with_colour():
    state = ready("parma:parma", "beercity:beer-city")
    t = iso(NOW - 5 * DAY)
    for pid, source in (("parma", "parma"), ("beer-city", "beercity")):
        state.pairs[pid] = {"n:dahook light": PairRec(first_seen=t, last_seen=t, notified_at=t,
                                                      info={"source": source, "kind": "shop"})}
        state.shop_items[pid] = {"111": "n:dahook light"}
    out = merge(state,
                shop(item(222, "n:dahook", place="parma", source="parma"), place="parma", source="parma"),
                shop(item(222, "n:dahook")))
    assert out.events == [("beer-city", "n:dahook")]     # only Parma joins colours
    assert list(state.pairs["parma"]) == ["n:dahook light"]
    assert state.shop_items["parma"] == {"111": "n:dahook light", "222": "n:dahook light"}


def test_display_fields_survive_sightings_that_lack_them():
    state = ready("beercity:beer-city")
    key = "n:hard root ipa"
    merge(state, shop(item(1, key, brewery="Hard Root", abv=6.5, volume_ml=450, container="can", price_amd=1900)))
    merge(state, shop(item(1, key, price_amd=1700)), now=NOW + 12 * H)   # known item: product page not read
    assert state.pairs["beer-city"][key].info == {
        "source": "beercity", "kind": "shop", "title": 'Beer "hard root ipa" 0.5 l', "name": "hard root ipa",
        "brewery": "Hard Root", "abv": 6.5, "volume_ml": 450, "container": "can", "price_amd": 1700,
        "shop_item_id": "1",
    }


# --- menus and places --------------------------------------------------------

def test_items_of_a_new_menu_tab_are_silent():
    state = ready("untappd_menu:gargoyle")
    state.sources["untappd_menu:gargoyle"].seen_menu_ids = ["100"]
    tabs = [beer(1, menu_id="100"), beer(2, menu_id="200"), beer(3, menu_id="200")]
    out = merge(state, menu(*tabs))
    assert out.events == [("gargoyle", "u:1")]
    assert state.pairs["gargoyle"]["u:2"].notified_at == "baseline"
    assert state.sources["untappd_menu:gargoyle"].seen_menu_ids == ["100", "200"]
    out = merge(state, menu(*tabs, beer(4, menu_id="200")), now=NOW + 12 * H)
    assert out.events == [("gargoyle", "u:4")]           # the tab is known now


def test_a_new_place_is_silent_until_its_own_source_has_run():
    state = ready("untappd_brewery:441775", "untappd_checkins:tap-station")   # izh was just added
    out = merge(state, brewery_checkins(checkin(1, "tap-station"), checkin(2, "izh")))
    assert out.events == [("tap-station", "u:1")]
    assert state.pairs["izh"]["u:2"].notified_at == "baseline" and "u:2" in state.beers
    out = merge(state, venue_checkins(checkin(3, "izh"), place="izh"), now=NOW + 12 * H)   # its first run
    assert out.events == [] and state.pairs["izh"]["u:3"].notified_at == "baseline"
    out = merge(state, brewery_checkins(checkin(4, "izh")), now=NOW + 24 * H)
    assert out.events == [("izh", "u:4")]


def test_same_beer_under_another_key_at_the_place_is_not_an_event():
    state = ready("untappd_menu:gargoyle", "untappd_checkins:tap-station")
    t = iso(NOW - 5 * DAY)
    state.pairs["gargoyle"] = {"u:100": PairRec(first_seen=t, last_seen=t, notified_at=t,
                                                info={"source": "untappd_menu", "kind": "menu"})}
    state.beers["u:100"] = BeerRec(first_seen_city=t, n_key="n:zagovor black sails")
    state.pairs["tap-station"] = {"n:379 hazy pale": PairRec(first_seen=t, last_seen=t, notified_at=t,
                                                             info={"source": "manual", "kind": "manual"})}
    out = merge(state,
                menu(beer(100, name="Black Sails"), beer(200, name="Black Sails", brewery="Zagovor Brewery"),
                     beer(201)),
                venue_checkins(checkin(300, name="Hazy Pale", brewery="379")))
    assert out.events == [("gargoyle", "u:201")]
    assert state.pairs["gargoyle"]["u:200"].notified_at == "suppressed"      # Untappd changed the id
    assert state.pairs["tap-station"]["u:300"].notified_at == "suppressed"   # a friend reported it by name


def test_menu_row_stays_a_menu_row_when_a_friend_reports_the_same_beer():
    state = ready("untappd_menu:gargoyle", "untappd_checkins:tap-station")
    merge(state, menu(beer(1)), manual(entry("tap-station", None, 1, brewery=None, untappd_id=5)), now=NOW - 12 * H)
    out = merge(state, menu(beer(1)), manual(entry("gargoyle", None, 1, brewery=None, untappd_id=1)),
                venue_checkins(checkin(5)))
    assert out.events == []
    on_menu = state.pairs["gargoyle"]["u:1"].info
    assert on_menu["source"] == "untappd_menu" and "manual_id" not in on_menu
    seen = state.pairs["tap-station"]["u:5"].info        # a check-in outranks a friend's report
    assert seen["kind"] == "checkin" and seen["checkin_at"] == iso(NOW - DAY)


# --- check-ins ---------------------------------------------------------------

IGNORED_CHECKINS = {
    "place-with-menu": brewery_checkins(checkin(1, "gargoyle")),
    "brewpub-with-buyam-menu": brewery_checkins(checkin(1, "dargett-brewpub", serving=None), brewery_id=265165),
    "untappd-at-home": venue_checkins(checkin(1, at_home=True)),
    "bottle": venue_checkins(checkin(1, serving="Bottle")),
    "no-serving-at-a-bar": venue_checkins(checkin(1, serving=None)),
    "no-serving-other-brewery-at-a-brewpub": venue_checkins(
        checkin(1, "dors", serving=None, brewery="Pulpulak Brewery"), place="dors"),
    "25-days-old": venue_checkins(checkin(1, days=25)),
}


@pytest.mark.parametrize("result", IGNORED_CHECKINS.values(), ids=IGNORED_CHECKINS.keys())
def test_ignored_checkins_leave_no_trace(result):
    state = ready(result.key, "untappd_checkins:dors", "untappd_checkins:tap-station")
    out = merge(state, result)
    assert out == MergeOutcome(ok=[result.key])
    assert state.pairs == {} and state.beers == {}


def test_checkin_without_serving_counts_at_the_brewerys_own_brewpub():
    state = ready("untappd_brewery:441775", "untappd_checkins:dors", "untappd_checkins:379-torch-brew")
    out = merge(state,
                brewery_checkins(checkin(1, "dors", serving=None, brewery=None)),      # by brewery id
                venue_checkins(checkin(2, "379-torch-brew", serving=None, brewery="379 Torch & Brew"),
                               place="379-torch-brew"),                                # by name prefix
                venue_checkins(checkin(3, "dors", serving=None, brewery="Dors Brewery"),
                               checkin(4, "dors", serving=None, brewery="Dorsia Brewing"), place="dors"))
    assert out.events == [("dors", "u:1"), ("379-torch-brew", "u:2"), ("dors", "u:3")]
    info = state.pairs["dors"]["u:1"].info
    assert info["kind"] == "checkin" and info["source"] == "untappd_brewery" and "serving" not in info
    assert info["checkin_at"] == iso(NOW - DAY)


def test_checkin_age_limits():
    assert (CHECKIN_EVENT_DAYS, CHECKIN_KEEP_DAYS, MANUAL_EVENT_DAYS) == (7, 21, 3)
    state = ready("untappd_checkins:tap-station")
    out = merge(state, venue_checkins(checkin(1, days=7), checkin(2, days=10), checkin(3, days=21),
                                      checkin(4, days=21.01)))
    assert out.events == [("tap-station", "u:1")]
    pairs = state.pairs["tap-station"]
    assert (pairs["u:2"].notified_at, pairs["u:2"].event_at) == ("suppressed", None)
    assert pairs["u:3"].notified_at == "suppressed"
    assert pairs["u:2"].info["checkin_at"] == iso(NOW - 10 * DAY)
    assert "u:4" not in pairs and "u:4" not in state.beers
    assert {"u:1", "u:2", "u:3"} <= set(state.beers)


def test_checkin_time_is_the_latest_checkin_of_the_beer_at_the_place():
    state = ready("untappd_brewery:441775", "untappd_checkins:tap-station")
    out = merge(state, brewery_checkins(checkin(1, days=1)), venue_checkins(checkin(1, days=4)))
    assert out.events == [("tap-station", "u:1")]
    assert state.pairs["tap-station"]["u:1"].info["checkin_at"] == iso(NOW - DAY)


def test_checkin_backfills_style_and_abv_from_a_menu_pair_with_the_same_key():
    state = ready("untappd_menu:gargoyle", "untappd_brewery:441775", "untappd_checkins:dors")
    merge(state, menu(beer(5, style="Porter", abv=5.5)), now=NOW - 12 * H)
    out = merge(state, brewery_checkins(checkin(5, "dors", serving=None, brewery=None)))
    assert out.events == [("dors", "u:5")]
    info = state.pairs["dors"]["u:5"].info
    assert info["style"] == "Porter" and info["abv"] == 5.5


# --- manual ------------------------------------------------------------------

def test_manual_entry_is_an_event_within_three_days_and_once_per_entry_id():
    # started well before the entries' dates: every place is new (no source has run yet), but this is not
    # a state-loss situation, so recent manual entries still give events.
    state = empty_state(NOW - 10 * DAY)
    state.announced_manual = [entry("izh", "Hazy Pale", 1, by="Олег").id]
    out = merge(state, manual(entry("tap-station", "Hazy Pale", 2),
                              entry("tap-station", "Gose", 3, by="Ваня"),
                              entry("tap-station", "Old Stout", 5, by="Лёша"),
                              entry("izh", "Hazy Pale Ale", 1, by="Олег")))   # renamed after it was announced
    assert out.events == [("tap-station", "n:379 hazy pale"), ("tap-station", "n:379 gose")]
    rec = state.pairs["tap-station"]["n:379 hazy pale"]
    assert (rec.event_at, rec.notified_at) == (iso(NOW), None)
    assert rec.info == {"source": "manual", "kind": "manual", "title": "379 Hazy Pale", "name": "Hazy Pale",
                        "brewery": "379", "manual_id": "tap-station|2026-09-22|Аня", "manual_by": "Аня",
                        "manual_date": "2026-09-22"}
    assert state.pairs["tap-station"]["n:379 old stout"].notified_at == "suppressed"
    assert state.pairs["izh"]["n:379 hazy pale ale"].notified_at == "suppressed"
    assert {"n:379 hazy pale", "n:379 old stout", "n:379 hazy pale ale"} <= set(state.beers)


def test_manual_entry_dated_before_state_started_is_suppressed_after_state_loss():
    state = empty_state(NOW - H)             # state.json was just (re)created, an hour ago
    out = merge(state, manual(entry("tap-station", "Hazy Pale", 2)))    # dated two days ago: before started_at
    assert out.events == []
    rec = state.pairs["tap-station"]["n:379 hazy pale"]
    assert (rec.notified_at, rec.event_at) == ("suppressed", None)


def test_manual_pair_no_longer_in_corrections_becomes_suppressed_and_out_of_result():
    state = empty_state(NOW - 10 * DAY)
    merge(state, manual(entry("tap-station", "Hazy Pale", 2)), now=NOW - 12 * H)
    rec = state.pairs["tap-station"]["n:379 hazy pale"]
    assert rec.last_in_result is True and rec.event_at is not None and rec.notified_at is None
    out = merge(state, manual())             # the entry was withdrawn from corrections.yaml
    assert out.events == []
    assert rec.last_in_result is False
    assert rec.notified_at == "suppressed"


def test_manual_reconciliation_does_not_touch_pairs_taken_over_by_a_later_source_in_the_same_run():
    state = ready("untappd_menu:gargoyle", "untappd_checkins:tap-station")
    merge(state, menu(beer(1)), manual(entry("tap-station", None, 1, brewery=None, untappd_id=5)), now=NOW - 12 * H)
    merge(state, menu(beer(1)), manual(entry("gargoyle", None, 1, brewery=None, untappd_id=1)),
          venue_checkins(checkin(5)))
    # the check-in (processed after the manual result in the same run) took ownership of u:5
    seen = state.pairs["tap-station"]["u:5"]
    assert seen.info["source"] == "untappd_checkins" and seen.last_in_result is True and seen.notified_at is None


# --- breaker verdicts ----------------------------------------------------------

def test_failed_result_changes_only_the_failure_fields_of_its_source():
    state = ready("untappd_menu:gargoyle")
    merge(state, menu(beer(1), beer(2)), now=NOW - 12 * H)
    before = state.to_dict()
    failed = SourceResult(key="untappd_menu:gargoyle", source="untappd_menu", ok=False, error="cloudflare",
                          place_id="gargoyle")
    assert merge(state, failed) == MergeOutcome(failed=[("untappd_menu:gargoyle", "cloudflare")])
    after = state.to_dict()
    rec_before = before["sources"].pop("untappd_menu:gargoyle")
    rec_after = after["sources"].pop("untappd_menu:gargoyle")
    assert after == before
    assert rec_after == {**rec_before, "fail_streak": 1, "last_error": "cloudflare"}


def test_tripped_results_change_no_pairs_until_accepted_as_a_silent_baseline():
    key = "untappd_menu:gargoyle"
    state = ready(key)
    merge(state, menu(*[beer(i) for i in range(1, 31)]), now=NOW - 12 * H)
    pairs_before = state.to_dict()["pairs"]
    rebuilt = menu(*[beer(i) for i in range(1, 11)], beer(99))    # 11 of 30: fewer than half
    for run in range(2):
        assert merge(state, rebuilt, now=NOW + run * 12 * H) == MergeOutcome(tripped=[(key, "shrink")])
        assert state.to_dict()["pairs"] == pairs_before and "u:99" not in state.beers
    assert state.sources[key].trip_streak == 2
    out = merge(state, rebuilt, now=NOW + 24 * H)                 # third similar result: a real new menu
    assert out == MergeOutcome(accepted=[key], ok=[key])
    pairs = state.pairs["gargoyle"]
    assert (pairs["u:99"].notified_at, pairs["u:99"].event_at) == ("baseline", None)
    assert pairs["u:5"].last_in_result is True and pairs["u:20"].last_in_result is False


# --- last_in_result and the source record ----------------------------------------

def test_last_in_result_follows_the_latest_full_result_of_the_same_source():
    state = ready("untappd_menu:gargoyle", "beercity:beer-city")
    merge(state, menu(beer(1), beer(2)), shop(item(1, "n:gose"), item(2, "n:pale")),
          manual(entry("gargoyle", "Gose", 1, brewery="Dargett")), now=NOW - 12 * H)
    merge(state, menu(beer(1)))
    g = state.pairs["gargoyle"]
    assert (g["u:1"].last_in_result, g["u:2"].last_in_result, g["n:dargett gose"].last_in_result) == (True, False, True)
    merge(state, shop(item(3, "n:stout"), full=False), now=NOW + 12 * H)      # partial Beer City run
    b = state.pairs["beer-city"]
    assert all(b[k].last_in_result for k in ("n:gose", "n:pale", "n:stout"))
    merge(state, shop(item(1, "n:gose"), item(3, "n:stout")), now=NOW + 24 * H)   # full walk
    assert [k for k, r in b.items() if not r.last_in_result] == ["n:pale"]
    merge(state, shop(item(2, "n:pale"), full=False), now=NOW + 36 * H)       # back in a partial run
    assert b["n:pale"].last_in_result is True


def test_partial_beercity_run_never_touches_already_known_items():
    state = ready("beercity:beer-city")
    merge(state, shop(item(1, "n:gose", price_amd=1500, in_stock=True)), now=NOW - 12 * H)
    rec = state.pairs["beer-city"]["n:gose"]
    out = merge(state, shop(item(1, "n:gose", price_amd=1900, in_stock=False),
                            item(2, "n:new-arrival"), full=False), now=NOW)
    assert rec.info["price_amd"] == 1500 and rec.in_stock is True
    assert "n:new-arrival" in state.pairs["beer-city"]
    assert state.shop_items["beer-city"] == {"1": "n:gose", "2": "n:new-arrival"}
    assert out.events == [("beer-city", "n:new-arrival")]


def test_source_record_after_a_full_and_a_partial_merge():
    state = empty_state(NOW)
    rec = state.source("untappd_menu:gargoyle")
    rec.fail_streak, rec.last_error = 2, "network"
    updated = datetime(2026, 9, 23, 20, 0, tzinfo=timezone.utc)
    merge(state, menu(beer(1, menu_id="100"), beer(2, menu_id="200"), beer(3, menu_id="100"),
                      menu_updated_at=updated))
    assert rec == SourceRec(baseline_done=True, last_ok=iso(NOW), last_full=iso(NOW), last_count=3,
                            seen_menu_ids=["100", "200"], menu_updated_at=iso(updated))
    bc = state.source("beercity:beer-city")
    bc.baseline_done, bc.last_ok, bc.last_full, bc.last_count = True, iso(NOW - 6 * H), iso(NOW - 6 * H), 250
    merge(state, shop(item(1, "n:gose"), full=False))
    assert (bc.last_ok, bc.last_full, bc.last_count) == (iso(NOW), iso(NOW - 6 * H), 250)


# --- brewery beer list (🏭) ----------------------------------------------------------

def test_brewery_list_baseline_then_new_ids_above_the_maximum():
    state = empty_state(NOW - 7 * DAY)
    out = merge(state, beer_list(100, 200, 300), now=NOW - 6 * DAY)
    rec = state.sources["untappd_brewery_list:265165"]
    assert out == MergeOutcome(ok=["untappd_brewery_list:265165"])
    assert (rec.max_beer_id, state.brewery_new, state.pairs) == (300, {}, {})
    assert state.beers["u:300"] == BeerRec(first_seen_city=iso(NOW - 6 * DAY), n_key="n:dargett ale 300")
    state.beers["u:302"] = BeerRec(first_seen_city=iso(NOW - DAY), n_key="n:dargett ale 302")   # on a menu
    out = merge(state, beer_list(100, 200, 250, 300, 301, 302))       # six days later
    assert out.brewery_events == ["u:301", "u:302"] and out.events == []
    assert state.brewery_new["u:301"] == BreweryNewRec(
        brewery_id=265165, found_at=iso(NOW), star=True, notified_at=None,
        info={"name": "Ale 301", "brewery": "Dargett", "style": "IPA - New England", "abv": 6.5,
              "url": "https://untappd.com/b/dargett-ale/301"})
    assert state.brewery_new["u:302"].star is False
    assert "u:250" in state.beers and "u:250" not in state.brewery_new    # a lower unknown id is silent
    assert rec.max_beer_id == 302 and state.pairs == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 120 .venv/bin/pytest tests/test_rules.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taps.rules'`

- [ ] **Step 3: Write minimal implementation**

Create `taps/rules.py`:
```python
"""Merge source results into state: pairs, events and silent baselines (spec §6)."""
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

from taps.breaker import evaluate, result_keys
from taps.config import Config, Place
from taps.corrections import Corrections
from taps.model import Sighting, SourceResult, normalize_title, strip_color, u_key, untappd_n_key
from taps.shop_filter import classify
from taps.state import BeerRec, BreweryNewRec, PairRec, SourceRec, State, resolve_alias
from taps.timeutil import age_days, iso, parse_iso, to_yerevan

CHECKIN_EVENT_DAYS = 7     # older check-ins are stored silently
CHECKIN_KEEP_DAYS = 21     # older check-ins are ignored
MANUAL_EVENT_DAYS = 3      # older manual entries are stored silently
TRIP_REASONS = ("shrink", "mass_new", "list_mass_new")   # breaker trips; any other discard is a failure
INFO_FIELDS = ("title", "name", "brewery", "style", "abv", "ibu", "rating", "price_amd", "volume_ml", "container",
               "serving", "url", "menu_id", "shop_item_id", "manual_id", "manual_by", "manual_date")
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
        """A check-in counts only when poured at a place without a menu (spec §6)."""
        if s.kind != "checkin":
            return False
        if place.has_menu or s.at_home or age_days(s.seen_at, self.now) > CHECKIN_KEEP_DAYS:
            return True
        if s.serving == "Draft":
            return False
        return not (s.serving is None and place.kind == "brewpub" and self._own_beer(s, place))

    def _own_beer(self, s: Sighting, place: Place) -> bool:
        if s.brewery_id is not None and s.brewery_id == place.brewery_id:
            return True
        aliases = self.corrections.brewery_aliases
        own = normalize_title(place.brewery_name or "", aliases)
        return bool(own) and f"{normalize_title(s.brewery or '', aliases)} ".startswith(f"{own} ")

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
        if s.kind == "checkin":
            rec.info["checkin_at"] = iso(max(s.seen_at, parse_iso(old_checkin)) if old_checkin else s.seen_at)
            self._backfill_checkin(rec, key)
        rec.info.pop("hidden", None)      # set again by _drop while hidden or filtered

    def _backfill_checkin(self, rec: PairRec, key: str) -> None:
        """A check-in poured without menu details borrows style/abv/ibu/rating from a menu pair of the same beer."""
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `timeout 120 .venv/bin/pytest tests/test_rules.py -v`
Expected: `42 passed`

- [ ] **Step 5: Commit**

```bash
git add tests/test_rules.py taps/rules.py
git commit -m "feat: merge source results into state with event and silent-baseline rules"
```

---

### Task 16: Сводка — когда слать и как выглядит

**Files:**
- Create: `taps/digest.py`
- Test: `tests/test_digest.py`

**Interfaces:**
- Consumes: `taps.config.Config`, `Place`, `Settings` (`digest_time`, `digest_max_lines`, `hot_rating`, `preview_digests`); `taps.state.State`, `PairRec`, `BreweryNewRec`, `DigestRec`; `taps.model.SOURCE_KINDS`; `taps.timeutil.YEREVAN`, `iso`, `parse_iso`, `to_yerevan`, `yerevan_date`, `age_days`.
- Produces: `STALE_EVENT_DAYS = 3`, `WEEKDAYS`, `MONTHS`, `pending_pairs(state) -> list[tuple[str, str]]`, `pending_brewery(state) -> list[str]`, `drop_stale_events(state, now) -> int`, `is_due(state, settings, now) -> bool`, `Digest(html, lines_total, lines_shown, pairs, brewery_keys, manual_ids, to_admin)`, `build_digest(state, config, settings, now) -> Digest | None`, `SentMark(digest, pairs, brewery, manual_before)`, `mark_sent(state, digest, now) -> SentMark`, `rollback(state, mark) -> None`.

Модуль решает, пора ли слать сводку (окно 09:00–23:00 по Еревану, не чаще раза в сутки и раз в 20 часов, после `digest_time` или если событие старше вчерашних 17:00), собирает текст в формате контракта (бары с ✅ и ⭐ первыми, 🏭, 👀, ✍️, магазины с «+ ещё в X», лимит строк, `html.escape`) и ставит или откатывает отметку отправки. Разделы спеки: §6 (значки) и §7 (сводка, протокол отправки, гарантии против спама).

- [ ] **Step 1: Write the failing test**

Create `tests/test_digest.py`:
```python
import copy
from datetime import datetime, timedelta, timezone

import pytest

from taps.config import Config, Place, Settings
from taps.digest import (
    build_digest, drop_stale_events, is_due, mark_sent, pending_brewery, pending_pairs, rollback,
)
from taps.state import BreweryNewRec, DigestRec, PairRec, State
from taps.timeutil import YEREVAN, iso, to_yerevan


def yv(day: int, hour: int, minute: int = 0, month: int = 9) -> datetime:
    """Yerevan wall-clock time as aware UTC."""
    return datetime(2026, month, day, hour, minute, tzinfo=YEREVAN).astimezone(timezone.utc)


NOW = yv(24, 18, 17)          # Thursday 24 Sep 2026, 18:17 Yerevan
SETTINGS = Settings()


def place(pid, name, kind, sources):
    return Place(id=pid, name=name, kind=kind, sources=sources)


CONFIG = Config(
    places={
        "gargoyle": place("gargoyle", "Gargoyle Bar", "bar", {"untappd_menu": {"slug": "g", "venue_id": 1}}),
        "beatles": place("beatles", "Beatles Pub", "bar", {"untappd_menu": {"slug": "b", "venue_id": 2}}),
        "dors": place("dors", "Dors Craft Beer & Kitchen", "brewpub", {"untappd_checkins": {"slug": "d", "venue_id": 3}}),
        "tap-station": place("tap-station", "Tap Station", "bar", {"untappd_checkins": {"slug": "t", "venue_id": 4}}),
        "beer-city": place("beer-city", "Beer City", "shop", {"beercity": {}}),
        "parma": place("parma", "Parma", "shop", {"parma": {}}),
    },
    breweries=(),
    settings=SETTINGS,
)


def pair(event_at, notified_at=None, star=False, **info):
    seen = iso(event_at) if event_at else iso(NOW)
    return PairRec(first_seen=seen, last_seen=seen, event_at=iso(event_at) if event_at else None,
                   star=star, notified_at=notified_at, info=info)


def new_state(**kw) -> State:
    return State(started_at=iso(datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)), **kw)


def full_state() -> State:
    return new_state(
        pairs={
            "gargoyle": {
                "u:1": pair(yv(24, 10), kind="menu", brewery="Ayinger Privatbrauerei", name="Celebrator",
                            style="Doppelbock", abv=6.7, rating=3.76, price_amd=2300),
                "u:2": pair(yv(24, 12), star=True, kind="menu", brewery="Zagovor", name="Black Sails",
                            style="Imperial Stout", abv=11.0, rating=4.12),
                "u:3": pair(yv(24, 11), kind="menu", brewery="A&B", name="Tom & <Jerry>", style="Sour", rating=3.75),
                "u:4": pair(yv(20, 10), notified_at="baseline", kind="menu", name="Old Beer"),
                "u:5": pair(yv(23, 10), notified_at=iso(yv(23, 18)), kind="menu", name="Sent Yesterday"),
                "u:6": pair(None, kind="menu", name="Never An Event"),
            },
            "beatles": {"u:7": pair(yv(24, 9), kind="menu", name="Mystery Lager", rating=3.74)},
            "dors": {"u:8": pair(yv(22, 20), kind="checkin", source="untappd_checkins", brewery="Dors",
                                 name="Smoked Porter", serving="Draft", checkin_at=iso(yv(22, 20)))},
            "tap-station": {"n:379 hazy pale": pair(yv(24, 14), kind="manual", brewery="379", name="Hazy Pale",
                                                    manual_id="tap-station|2026-09-24|Аня", manual_by="Аня")},
            "beer-city": {"n:konix cassis ruby": pair(yv(24, 10), star=True, kind="shop", brewery="Konix",
                                                      name="Cassis Ruby", volume_ml=450, container="can",
                                                      price_amd=1900)},
            "parma": {"n:konix cassis ruby": pair(yv(24, 11), kind="shop", brewery="Konix", name="Cassis Ruby",
                                                  volume_ml=330, container="bottle", price_amd=2100)},
        },
        brewery_new={"u:10": BreweryNewRec(brewery_id=265165, found_at=iso(yv(24, 9)), star=True,
                                           info={"name": "DDH NEIPA", "brewery": "Dargett", "abv": 6.5})},
    )


EXPECTED = """🍺 <b>Новое в Ереване</b> · чт, 24 сен

🍻 <b>БАРЫ</b>
<b>Gargoyle Bar</b> ✅
• ⭐ Zagovor — Black Sails · Imperial Stout 11% · 🔥4.12
• Ayinger Privatbrauerei — Celebrator · Doppelbock 6.7% · 🔥3.76 · 2300 ֏
• A&amp;B — Tom &amp; &lt;Jerry&gt; · Sour · 🔥3.75
<b>Beatles Pub</b> ✅
• Mystery Lager · 3.74
🏭 <b>Новые сорта пивоварен</b>
• ⭐ Dargett — DDH NEIPA · 6.5% (новый сорт в Untappd, где наливают — пока неизвестно)
👀 <b>Похоже, появилось</b>
• Dors — Smoked Porter · в Dors Craft Beer &amp; Kitchen, разлив, видели 2 дня назад
✍️ <b>Со слов</b>
• 379 — Hazy Pale · в Tap Station (от Аня)

🛒 <b>МАГАЗИНЫ</b>
<b>Beer City</b>
• ⭐ Konix — Cassis Ruby · 0.45 л банка · 1900 ֏ + ещё в Parma

<i>⭐ — возможно, впервые в Ереване (с тех пор, как следим, с 20.09)</i>"""


def test_build_digest_full_text():
    d = build_digest(full_state(), CONFIG, SETTINGS, NOW)
    assert d.html == EXPECTED
    assert (d.lines_total, d.lines_shown) == (8, 8)
    assert sorted(d.pairs) == sorted([
        ("gargoyle", "u:1"), ("gargoyle", "u:2"), ("gargoyle", "u:3"), ("beatles", "u:7"), ("dors", "u:8"),
        ("tap-station", "n:379 hazy pale"), ("beer-city", "n:konix cassis ruby"), ("parma", "n:konix cassis ruby"),
    ])
    assert d.brewery_keys == ["u:10"]
    assert d.manual_ids == ["tap-station|2026-09-24|Аня"]
    assert d.to_admin is True


def test_pending_lists():
    s = full_state()
    assert ("gargoyle", "u:4") not in pending_pairs(s)      # baseline
    assert ("gargoyle", "u:5") not in pending_pairs(s)      # already sent
    assert ("gargoyle", "u:6") not in pending_pairs(s)      # no event_at
    assert len(pending_pairs(s)) == 8
    assert pending_brewery(s) == ["u:10"]


def test_checkin_seen_today_and_unknown_serving():
    s = new_state(pairs={"dors": {"u:8": pair(yv(24, 9), kind="checkin", name="Pils", serving=None,
                                             checkin_at=iso(yv(24, 9)))}})
    d = build_digest(s, CONFIG, SETTINGS, NOW)
    assert "• Pils · в Dors Craft Beer &amp; Kitchen, подача неизвестна, видели сегодня" in d.html


@pytest.mark.parametrize("days_ago,expected", [
    (0, "видели сегодня"), (1, "видели 1 день назад"), (2, "видели 2 дня назад"),
    (4, "видели 4 дня назад"), (5, "видели 5 дней назад"), (11, "видели 11 дней назад"),
])
def test_checkin_seen_ago_uses_correct_russian_plural(days_ago, expected):
    when = yv(24, 18) - timedelta(days=days_ago)
    s = new_state(pairs={"dors": {"u:8": pair(when, kind="checkin", name="Pils", serving="Draft",
                                              checkin_at=iso(when))}})
    d = build_digest(s, CONFIG, SETTINGS, NOW)
    assert expected in d.html


def test_to_admin_false_after_preview_digests():
    s = full_state()
    s.digest.sent_count = 2
    assert build_digest(s, CONFIG, SETTINGS, NOW).to_admin is False


def test_none_when_nothing_pending():
    s = new_state(pairs={"gargoyle": {"u:4": pair(yv(20, 10), notified_at="baseline", kind="menu", name="X")}})
    assert build_digest(s, CONFIG, SETTINGS, NOW) is None
    # pending only at a place no longer in config: nothing to show
    s = new_state(pairs={"closed-bar": {"u:1": pair(yv(24, 10), kind="menu", name="X")}})
    assert build_digest(s, CONFIG, SETTINGS, NOW) is None


def cap_state(bars: int, shops: int) -> State:
    s = new_state(pairs={"gargoyle": {}, "beer-city": {}})
    for i in range(bars):
        s.pairs["gargoyle"][f"u:{100 + i}"] = pair(yv(24, 10, i), kind="menu", name=f"Bar {i:02d}")
    for i in range(shops):
        s.pairs["beer-city"][f"n:shop {i}"] = pair(yv(24, 10, i), kind="shop", name=f"Shop {i:02d}")
    return s


def test_cap_hides_tail_but_keeps_all_pairs():
    d = build_digest(cap_state(17, 0), CONFIG, SETTINGS, NOW)
    assert (d.lines_total, d.lines_shown) == (17, 15)
    assert d.html.count("\n• ") == 15
    assert "• Bar 14" in d.html and "Bar 15" not in d.html and "Bar 16" not in d.html
    assert d.html.endswith("• Bar 14\n\n…и ещё 2 — на сайте\n"
                           "<i>⭐ — возможно, впервые в Ереване (с тех пор, как следим, с 20.09)</i>")
    assert len(d.pairs) == 17


def test_cap_drops_empty_shop_block():
    d = build_digest(cap_state(15, 2), CONFIG, SETTINGS, NOW)
    assert "МАГАЗИНЫ" not in d.html and "Beer City" not in d.html
    assert "…и ещё 2 — на сайте" in d.html
    d = build_digest(cap_state(12, 5), CONFIG, SETTINGS, NOW)
    assert "• Shop 02" in d.html and "Shop 03" not in d.html


def test_drop_stale_events():
    s = new_state(
        pairs={"gargoyle": {
            "u:1": pair(yv(21, 17), kind="menu", name="Stale"),          # 3 days 1h17m old
            "u:2": pair(yv(22, 10), kind="menu", name="Fresh"),          # 2 days 8h old
            "u:3": pair(yv(1, 10), notified_at="baseline", kind="menu", name="Baseline"),
        }},
        brewery_new={
            "u:10": BreweryNewRec(brewery_id=1, found_at=iso(yv(20, 10))),
            "u:11": BreweryNewRec(brewery_id=1, found_at=iso(yv(24, 10))),
        },
    )
    assert drop_stale_events(s, NOW) == 2
    g = s.pairs["gargoyle"]
    assert g["u:1"].notified_at == iso(NOW)
    assert g["u:2"].notified_at is None
    assert g["u:3"].notified_at == "baseline"
    assert s.brewery_new["u:10"].notified_at == iso(NOW)
    assert s.brewery_new["u:11"].notified_at is None
    assert drop_stale_events(s, NOW) == 0


def test_drop_stale_events_uses_manual_date_not_merge_time():
    s = new_state(pairs={"tap-station": {
        "n:x": pair(yv(24, 17), kind="manual", manual_date="2026-09-20", name="X"),   # merged an hour ago
    }})
    assert drop_stale_events(s, NOW) == 1
    assert s.pairs["tap-station"]["n:x"].notified_at == iso(NOW)


def due_state(event_at=None, found_at=None, last_sent_at=None, last_sent_date=None) -> State:
    s = new_state(digest=DigestRec(last_sent_date=last_sent_date, last_sent_at=last_sent_at and iso(last_sent_at)))
    if event_at:
        s.pairs["gargoyle"] = {"u:1": pair(event_at, kind="menu", name="X")}
    if found_at:
        s.brewery_new["u:10"] = BreweryNewRec(brewery_id=1, found_at=iso(found_at))
    return s


def test_is_due_evening_with_pending():
    assert is_due(due_state(event_at=yv(24, 10, 17)), SETTINGS, yv(24, 18, 17)) is True


def test_is_due_morning_waits_for_evening():
    assert is_due(due_state(event_at=yv(24, 10, 17)), SETTINGS, yv(24, 10, 17)) is False
    # found yesterday after 17:00: still waits for this evening
    assert is_due(due_state(event_at=yv(23, 17, 30)), SETTINGS, yv(24, 10, 17)) is False


def test_is_due_morning_catches_up_missed_evening():
    s = due_state(event_at=yv(23, 10, 17), last_sent_at=yv(22, 18, 17), last_sent_date="2026-09-22")
    assert is_due(s, SETTINGS, yv(24, 10, 17)) is True
    assert is_due(due_state(found_at=yv(23, 10, 17)), SETTINGS, yv(24, 10, 17)) is True   # brewery event


def test_is_due_already_sent_today():
    assert is_due(due_state(event_at=yv(24, 10), last_sent_date="2026-09-24"), SETTINGS, yv(24, 18, 17)) is False


def test_is_due_twenty_hour_gap():
    old = yv(23, 10, 17)
    s = due_state(event_at=old, last_sent_at=yv(23, 18, 17), last_sent_date="2026-09-23")   # 16h ago
    assert is_due(s, SETTINGS, yv(24, 10, 17)) is False
    s = due_state(event_at=old, last_sent_at=yv(23, 13, 0), last_sent_date="2026-09-23")    # 21h17m ago
    assert is_due(s, SETTINGS, yv(24, 10, 17)) is True


def test_is_due_quiet_hours():
    s = due_state(event_at=yv(23, 10))
    assert is_due(s, SETTINGS, yv(24, 23, 30)) is False
    assert is_due(s, SETTINGS, yv(24, 8, 59)) is False
    assert is_due(s, SETTINGS, yv(24, 9, 0)) is True


def test_is_due_uses_manual_date_for_a_manual_pair_merged_late():
    manual_date = (to_yerevan(NOW).date() - timedelta(days=2)).isoformat()   # "2026-09-22"
    s = new_state(pairs={"tap-station": {
        "n:x": pair(yv(24, 8, 30), kind="manual", manual_date=manual_date, name="X"),   # merged this morning
    }})
    assert is_due(s, SETTINGS, yv(24, 9, 30)) is True


def test_is_due_nothing_pending():
    s = due_state()
    s.pairs["gargoyle"] = {"u:1": pair(yv(23, 10), notified_at="baseline", kind="menu", name="X")}
    assert is_due(s, SETTINGS, yv(24, 18, 17)) is False


def test_mark_sent_and_rollback():
    s = cap_state(17, 0)
    s.pairs["tap-station"] = {"n:x": pair(yv(24, 14), kind="manual", name="X", manual_id="m1", manual_by="Аня")}
    s.brewery_new["u:10"] = BreweryNewRec(brewery_id=1, found_at=iso(yv(24, 9)), info={"name": "N"})
    s.digest = DigestRec(last_sent_date="2026-09-22", last_sent_at=iso(yv(22, 18)), sent_count=3)
    s.announced_manual = ["old"]
    before = copy.deepcopy(s.to_dict())
    d = build_digest(s, CONFIG, SETTINGS, NOW)
    assert d.lines_shown == 15 and len(d.pairs) == 18

    later = yv(25, 0, 30)          # past midnight Yerevan: the date is the Yerevan one
    mark = mark_sent(s, d, later)
    assert s.digest == DigestRec(last_sent_date="2026-09-25", last_sent_at=iso(later), sent_count=4)
    assert all(s.pairs[p][k].notified_at == iso(later) for p, k in d.pairs)   # hidden ones too
    assert s.brewery_new["u:10"].notified_at == iso(later)
    assert s.announced_manual == ["old", "m1"]
    assert pending_pairs(s) == [] and pending_brewery(s) == []

    rollback(s, mark)
    assert s.to_dict() == before
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 120 .venv/bin/pytest tests/test_digest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taps.digest'`

- [ ] **Step 3: Write minimal implementation**

Create `taps/digest.py`:
```python
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
CONTAINER_RU = {"can": "банка", "bottle": "бутылка", "keg": "кег", "draft": "разлив"}
SERVING_RU = {"Draft": "разлив", None: "подача неизвестна"}
BREWERY_NEW_NOTE = "новый сорт в Untappd, где наливают — пока неизвестно"

esc = html.escape


def pending_pairs(state: State) -> list[tuple[str, str]]:
    return [(p, k) for p, recs in state.pairs.items() for k, r in recs.items()
            if r.event_at is not None and r.notified_at is None]


def pending_brewery(state: State) -> list[str]:
    return [k for k, r in state.brewery_new.items() if r.notified_at is None]


def _pair_time(rec: PairRec) -> datetime:
    """A manual pair's event time is the reported date (Yerevan midnight), not when it was merged."""
    manual_date = rec.info.get("manual_date")
    if manual_date:
        return datetime.combine(date.fromisoformat(manual_date), time.min, tzinfo=YEREVAN)
    return parse_iso(rec.event_at)


def drop_stale_events(state: State, now: datetime) -> int:
    """Pending events older than STALE_EVENT_DAYS are marked notified without sending."""
    count = 0
    for p, k in pending_pairs(state):
        rec = state.pairs[p][k]
        if age_days(_pair_time(rec), now) > STALE_EVENT_DAYS:
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
    times = [_pair_time(state.pairs[p][k]) for p, k in pending_pairs(state)]
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


def _line(star: bool, info: dict, details: list[str | None], also: list[str]) -> str:
    head = ("⭐ " if star else "") + (f"{esc(info['brewery'])} — " if info.get("brewery") else "")
    name = info.get("name") or info.get("title") or ""
    text = "• " + head + esc(name) + "".join(f" · {d}" for d in details if d)
    if also:
        text += " + ещё в " + ", ".join(esc(a) for a in also)
    return text


def _rating(info: dict, settings: Settings) -> str | None:
    r = info.get("rating")
    if r is None:
        return None
    return f"🔥{r:.2f}" if r >= settings.hot_rating else f"{r:.2f}"


def _price(info: dict) -> str | None:
    return f"{info['price_amd']} ֏" if info.get("price_amd") is not None else None


def _pack(info: dict) -> str | None:
    vol, cont = info.get("volume_ml"), info.get("container")
    text = " ".join(filter(None, (f"{vol / 1000:g} л" if vol is not None else None,
                                  CONTAINER_RU.get(cont, cont) if cont else None)))
    return esc(text) or None


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

    def sort_key(item):   # config order of the first place, then ⭐ first, then oldest event
        found = item[1]
        place, rec = found[0]
        return (order[place.id], not any(r.star for _, r in found), rec.event_at, rec.info.get("name") or "")

    def details(section: str, place, rec: PairRec) -> list[str | None]:
        info = rec.info
        if section == "menu":
            return [_style_abv(info), _rating(info, settings), _price(info)]
        if section == "shop":
            return [_style_abv(info), _rating(info, settings), _pack(info), _price(info)]
        if section == "checkin":
            serving = info.get("serving")
            where = f"в {esc(place.name)}, {esc(SERVING_RU.get(serving, serving))}, {_seen(info, rec, now)}"
        else:
            where = f"в {esc(place.name)} (от {esc(info.get('manual_by') or '?')})"
        return [_style_abv(info), where]

    group_header = {"checkin": "👀 <b>Похоже, появилось</b>", "manual": "✍️ <b>Со слов</b>"}
    entries: list[tuple[str, str, str]] = []   # (block, group header, line)
    for section in ("menu", "brewery", "checkin", "manual", "shop"):
        if section == "brewery":
            for key in sorted(brewery_keys, key=lambda k: (not state.brewery_new[k].star, state.brewery_new[k].found_at)):
                rec = state.brewery_new[key]
                line = _line(rec.star, rec.info, [_style_abv(rec.info)], []) + f" ({BREWERY_NEW_NOTE})"
                entries.append(("bars", "🏭 <b>Новые сорта пивоварен</b>", line))
            continue
        for key, found in sorted(groups[section].items(), key=sort_key):
            place, rec = found[0]
            line = _line(any(r.star for _, r in found), rec.info, details(section, place, rec),
                         [p.name for p, _ in found[1:]])
            header = group_header.get(section) or f"<b>{esc(place.name)}</b>" + (" ✅" if section == "menu" else "")
            entries.append(("shops" if section == "shop" else "bars", header, line))

    if not entries:
        return None
    shown = entries[:settings.digest_max_lines]
    hidden = len(entries) - len(shown)

    local = to_yerevan(now)
    blocks: list[list[str]] = []
    last_block = last_group = None
    for block, group, line in shown:
        if block != last_block:
            blocks.append(["🍻 <b>БАРЫ</b>" if block == "bars" else "🛒 <b>МАГАЗИНЫ</b>"])
            last_block, last_group = block, None
        if group != last_group:
            blocks[-1].append(group)
            last_group = group
        blocks[-1].append(line)
    since = to_yerevan(parse_iso(state.started_at)).strftime("%d.%m")
    footer = f"<i>⭐ — возможно, впервые в Ереване (с тех пор, как следим, с {since})</i>"
    tail = (f"…и ещё {hidden} — на сайте\n" if hidden else "") + footer
    header = f"🍺 <b>Новое в Ереване</b> · {WEEKDAYS[local.weekday()]}, {local.day} {MONTHS[local.month - 1]}"
    text = "\n\n".join([header, *("\n".join(b) for b in blocks), tail])

    manual_ids = []
    for p, k in pairs:
        mid = state.pairs[p][k].info.get("manual_id")
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `timeout 120 .venv/bin/pytest tests/test_digest.py -v`
Expected: `24 passed`

- [ ] **Step 5: Commit**

```bash
git add tests/test_digest.py taps/digest.py
git commit -m "feat: digest scheduling, Telegram HTML text and sent mark with rollback"
```

---

### Task 17: Отправка в Telegram и служебные предупреждения

**Files:**
- Create: `taps/telegram.py`
- Test: `tests/test_telegram.py`

**Interfaces:**
- Consumes: `taps.state.State` (поле `alerts: dict[str, str]`), `taps.state.empty_state(now)` (только в тестах).
- Produces: `API`, `SendOutcome(status, description="", migrate_to_chat_id=None)`, `send_message(token, chat_id, html, button=None, silent=True, post=requests.post, sleep=time.sleep, timeout=30) -> SendOutcome`, `Alerter(state)` с методами `alert(key, text)`, `resolve(key)`, `pending_text() -> str | None`, `flush(send: Callable[[str], SendOutcome]) -> None`.

Модуль реализует шаг 4 протокола отправки (спека §7): один вызов `sendMessage` с таймаутом 30 с и сведение ответа Telegram к трём исходам: `sent`, `rejected` (точно не принято, откатываем отметки) и `unknown` (считаем, что ушло). `Alerter` шлёт Олегу служебные предупреждения по одному на серию: в `state.alerts` хранится хеш текста, а все новые предупреждения прогона уходят одним сообщением (спека §7 п. 8, §9).

- [ ] **Step 1: Write the failing test**

Create `tests/test_telegram.py`:
```python
from datetime import datetime, timezone

import requests

from taps.state import empty_state
from taps.telegram import API, Alerter, SendOutcome, send_message

NOW = datetime(2026, 9, 24, 13, 0, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakePost:
    """Returns (or raises) the queued items one per call and records every call."""

    def __init__(self, *items):
        self.items = list(items)
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeSleep:
    def __init__(self):
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)


OK = FakeResponse(200, {"ok": True, "result": {"message_id": 7}})


def too_many(retry_after=3):
    return FakeResponse(429, {"ok": False, "error_code": 429, "description": "Too Many Requests: retry after 3",
                              "parameters": {"retry_after": retry_after}})


def send(post, sleep=None, **kwargs):
    return send_message("TOKEN", "-100123", "<b>Новое</b>", post=post, sleep=sleep or FakeSleep(), **kwargs)


def test_payload_is_silent_html_without_preview_and_uses_timeout():
    post = FakePost(OK)
    send(post)
    [(url, kwargs)] = post.calls
    assert url == API.format(token="TOKEN") == "https://api.telegram.org/botTOKEN/sendMessage"
    assert kwargs["timeout"] == 30
    assert kwargs["json"] == {
        "chat_id": "-100123",
        "text": "<b>Новое</b>",
        "parse_mode": "HTML",
        "disable_notification": True,
        "link_preview_options": {"is_disabled": True},
    }


def test_payload_has_url_button_and_custom_timeout_and_loud_mode():
    post = FakePost(OK)
    send(post, button=("Открыть список", "https://example.github.io/yerevan-taps/"), silent=False, timeout=5)
    payload = post.calls[0][1]["json"]
    assert payload["reply_markup"] == {
        "inline_keyboard": [[{"text": "Открыть список", "url": "https://example.github.io/yerevan-taps/"}]]}
    assert payload["disable_notification"] is False
    assert post.calls[0][1]["timeout"] == 5


def test_ok_is_sent():
    assert send(FakePost(OK)) == SendOutcome("sent")


def test_429_sleeps_retry_after_and_retries_once():
    post, sleep = FakePost(too_many(3), OK), FakeSleep()
    assert send(post, sleep).status == "sent"
    assert sleep.calls == [3]
    assert len(post.calls) == 2
    assert post.calls[0][1]["json"] == post.calls[1][1]["json"]


def test_429_twice_is_rejected():
    post, sleep = FakePost(too_many(3), too_many(3)), FakeSleep()
    outcome = send(post, sleep)
    assert outcome.status == "rejected"
    assert "Too Many Requests" in outcome.description
    assert len(post.calls) == 2
    assert sleep.calls == [3]


def test_429_huge_or_missing_retry_after_is_bounded():
    sleep = FakeSleep()
    send(FakePost(too_many(86400), OK), sleep)
    send(FakePost(FakeResponse(429, {"ok": False, "error_code": 429, "description": "x"}), OK), sleep)
    assert sleep.calls == [60, 1]


def test_400_chat_not_found_is_rejected_with_description():
    post = FakePost(FakeResponse(400, {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}))
    outcome = send(post)
    assert outcome == SendOutcome("rejected", "Bad Request: chat not found", None)
    assert len(post.calls) == 1


def test_403_is_rejected():
    post = FakePost(FakeResponse(403, {"ok": False, "error_code": 403,
                                       "description": "Forbidden: bot was kicked from the supergroup chat"}))
    assert send(post).status == "rejected"


def test_migrate_to_chat_id_is_rejected_and_carries_new_id():
    body = {"ok": False, "error_code": 400, "description": "Bad Request: group chat was upgraded to a supergroup chat",
            "parameters": {"migrate_to_chat_id": -1001234567890}}
    outcome = send(FakePost(FakeResponse(400, body)))
    assert outcome.status == "rejected"
    assert outcome.migrate_to_chat_id == -1001234567890
    assert "upgraded" in outcome.description


def test_timeout_is_unknown():
    outcome = send(FakePost(requests.Timeout("read timed out")))
    assert outcome.status == "unknown"
    assert "Timeout" in outcome.description


def test_connection_error_is_unknown():
    assert send(FakePost(requests.ConnectionError("reset"))).status == "unknown"


def test_any_request_exception_is_unknown():
    assert send(FakePost(requests.exceptions.SSLError("bad cert"))).status == "unknown"


def test_502_is_unknown_even_with_json_body():
    post = FakePost(FakeResponse(502, {"ok": False, "error_code": 502, "description": "Bad Gateway"}))
    outcome = send(post)
    assert outcome.status == "unknown"
    assert "502" in outcome.description
    assert len(post.calls) == 1


def test_non_json_body_is_unknown():
    outcome = send(FakePost(FakeResponse(200, ValueError("Expecting value"))))
    assert outcome.status == "unknown"


def test_json_without_ok_flag_is_unknown():
    assert send(FakePost(FakeResponse(200, ["ok"]))).status == "unknown"
    assert send(FakePost(FakeResponse(200, {"result": {}}))).status == "unknown"


def test_timeout_on_retry_after_429_is_unknown():
    assert send(FakePost(too_many(1), requests.Timeout())).status == "unknown"


# ---- Alerter ----

class FakeSend:
    def __init__(self, status="sent"):
        self.status = status
        self.texts = []

    def __call__(self, text):
        self.texts.append(text)
        return SendOutcome(self.status)


def test_same_key_and_text_twice_is_queued_once_across_runs():
    state = empty_state(NOW)
    first = Alerter(state)
    first.alert("source:beer-city", "Beer City: не удалось проверить")
    first.alert("source:beer-city", "Beer City: не удалось проверить")
    assert first.pending_text().count("Beer City") == 1
    assert len(state.alerts["source:beer-city"]) == 12

    second = Alerter(state)  # next run, same state
    second.alert("source:beer-city", "Beer City: не удалось проверить")
    assert second.pending_text() is None


def test_changed_text_is_queued_again():
    state = empty_state(NOW)
    Alerter(state).alert("corrections", "ошибка YAML в строке 3")
    alerter = Alerter(state)
    alerter.alert("corrections", "ошибка YAML в строке 5")
    assert "строке 5" in alerter.pending_text()


def test_resolve_then_same_text_is_queued_again():
    state = empty_state(NOW)
    Alerter(state).alert("source:parma", "Parma: не удалось проверить")
    alerter = Alerter(state)
    alerter.resolve("source:parma")
    assert "source:parma" not in state.alerts
    alerter.resolve("source:never-alerted")  # no error
    alerter.alert("source:parma", "Parma: не удалось проверить")
    assert "Parma" in alerter.pending_text()


def test_resolve_drops_alert_queued_in_same_run():
    alerter = Alerter(empty_state(NOW))
    alerter.alert("source:parma", "Parma: не удалось проверить")
    alerter.resolve("source:parma")
    assert alerter.pending_text() is None


def test_pending_text_combines_alerts_into_one_escaped_message():
    alerter = Alerter(empty_state(NOW))
    alerter.alert("a", "Beer City: не удалось проверить")
    alerter.alert("b", "сводка, возможно, не дошла: <Timeout> & обрыв")
    text = alerter.pending_text()
    assert text.startswith("⚠️")
    assert text == ("⚠️ taps:\n"
                    "• Beer City: не удалось проверить\n"
                    "• сводка, возможно, не дошла: &lt;Timeout&gt; &amp; обрыв")


def test_pending_text_stays_within_telegram_limit():
    alerter = Alerter(empty_state(NOW))
    for i in range(60):
        alerter.alert(f"k{i}", f"{i}: " + "ошибка " * 20)
    text = alerter.pending_text()
    assert len(text) <= 4096
    assert text.endswith("предупреждений не поместилось")
    assert "• 0: " in text


def test_flush_sends_once_and_clears_queue():
    alerter = Alerter(empty_state(NOW))
    alerter.alert("a", "первое")
    alerter.alert("b", "второе")
    fake = FakeSend()
    alerter.flush(fake)
    assert fake.texts == ["⚠️ taps:\n• первое\n• второе"]
    assert alerter.pending_text() is None
    alerter.flush(fake)
    assert len(fake.texts) == 1


def test_flush_with_nothing_queued_does_not_send():
    fake = FakeSend()
    Alerter(empty_state(NOW)).flush(fake)
    assert fake.texts == []


def test_rejected_flush_forgets_hashes_so_next_run_retries():
    state = empty_state(NOW)
    state.alerts["old"] = "abc"
    alerter = Alerter(state)
    alerter.alert("a", "первое")
    alerter.flush(FakeSend("rejected"))
    assert state.alerts == {"old": "abc"}
    retry = Alerter(state)
    retry.alert("a", "первое")
    assert retry.pending_text() is not None


def test_alert_dedupe_by_key_ignores_changed_text_until_resolved():
    state = empty_state(NOW)
    first = Alerter(state)
    first.alert("source:parma", "parma: не удалось получить данные (network)", dedupe_by_key=True)
    assert first.pending_text() is not None

    second = Alerter(state)   # next run, same state, same key, different text
    second.alert("source:parma", "parma: не удалось получить данные (http)", dedupe_by_key=True)
    assert second.pending_text() is None

    second.resolve("source:parma")
    third = Alerter(state)
    third.alert("source:parma", "parma: не удалось получить данные (network)", dedupe_by_key=True)
    assert third.pending_text() is not None


def test_unknown_flush_keeps_hashes():
    state = empty_state(NOW)
    alerter = Alerter(state)
    alerter.alert("a", "первое")
    alerter.flush(FakeSend("unknown"))
    assert "a" in state.alerts
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 120 .venv/bin/pytest tests/test_telegram.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taps.telegram'`

- [ ] **Step 3: Write minimal implementation**

Create `taps/telegram.py`:
```python
"""Telegram sendMessage with the spec §7 step 4 outcome rules, and one-per-series admin alerts."""
from __future__ import annotations

import hashlib
import html
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests

from taps.state import State

API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_RETRY_AFTER = 60      # never stall a run longer than this on a 429
DEFAULT_RETRY_AFTER = 1
MAX_TEXT = 4096           # Telegram message length limit


@dataclass
class SendOutcome:
    status: str                       # "sent" | "rejected" | "unknown"
    description: str = ""
    migrate_to_chat_id: int | None = None


def _retry_after(body: dict) -> int:
    value = (body.get("parameters") or {}).get("retry_after")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return DEFAULT_RETRY_AFTER
    return min(value, MAX_RETRY_AFTER)


def _post_once(post: Callable[..., Any], url: str, payload: dict, timeout: float) -> SendOutcome | dict:
    """Return a final "unknown" outcome, or the parsed Telegram body (a dict with a bool "ok")."""
    try:
        resp = post(url, json=payload, timeout=timeout)
    except requests.RequestException as e:
        return SendOutcome("unknown", f"{type(e).__name__}: {e}")
    if resp.status_code >= 500:
        return SendOutcome("unknown", f"HTTP {resp.status_code}")
    try:
        body = resp.json()
    except ValueError:
        return SendOutcome("unknown", f"HTTP {resp.status_code}: ответ не JSON")
    if not isinstance(body, dict) or not isinstance(body.get("ok"), bool):
        return SendOutcome("unknown", f"HTTP {resp.status_code}: непонятный ответ")
    body.setdefault("error_code", resp.status_code)
    return body


def send_message(token: str, chat_id: str, html: str, button: tuple[str, str] | None = None, silent: bool = True,
                 post: Callable[..., Any] = requests.post, sleep: Callable[[float], None] = time.sleep,
                 timeout: float = 30) -> SendOutcome:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": html,
        "parse_mode": "HTML",
        "disable_notification": silent,
        "link_preview_options": {"is_disabled": True},
    }
    if button:
        payload["reply_markup"] = {"inline_keyboard": [[{"text": button[0], "url": button[1]}]]}
    url = API.format(token=token)

    body = _post_once(post, url, payload, timeout)
    if isinstance(body, dict) and not body["ok"] and body["error_code"] == 429:
        sleep(_retry_after(body))
        body = _post_once(post, url, payload, timeout)
    if isinstance(body, SendOutcome):
        return body
    if body["ok"]:
        return SendOutcome("sent")
    migrate = (body.get("parameters") or {}).get("migrate_to_chat_id")
    if isinstance(migrate, bool) or not isinstance(migrate, int):
        migrate = None
    return SendOutcome("rejected", str(body.get("description", "")), migrate)


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


class Alerter:
    """Admin alerts, one per series: a key is re-sent only when its text changes or after resolve()."""

    def __init__(self, state: State):
        self.state = state
        self.queue: dict[str, str] = {}

    def alert(self, key: str, text: str, dedupe_by_key: bool = False) -> None:
        """dedupe_by_key: any active alert on this key skips a new one, even if the text changed
        (a failing source's error text may vary run to run without starting a new series)."""
        if dedupe_by_key and key in self.state.alerts:
            return
        digest = _hash(text)
        if not dedupe_by_key and self.state.alerts.get(key) == digest:
            return
        self.state.alerts[key] = digest
        self.queue[key] = text

    def resolve(self, key: str) -> None:
        self.state.alerts.pop(key, None)
        self.queue.pop(key, None)

    def pending_text(self) -> str | None:
        if not self.queue:
            return None
        text = "⚠️ taps:"
        texts = list(self.queue.values())
        for i, alert in enumerate(texts):
            line = "\n• " + html.escape(alert)
            rest = len(texts) - i
            tail = f"\n…и ещё {rest} предупреждений не поместилось"
            if len(text) + len(line) + len(tail) > MAX_TEXT:
                return text + tail
            text += line
        return text

    def flush(self, send: Callable[[str], SendOutcome]) -> None:
        text = self.pending_text()
        if text is None:
            return
        outcome = send(text)
        if outcome.status == "rejected":
            # The admin surely did not get it: forget the hashes so the next run tries again.
            for key in self.queue:
                self.state.alerts.pop(key, None)
        self.queue.clear()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `timeout 120 .venv/bin/pytest tests/test_telegram.py -v`
Expected: `27 passed`

- [ ] **Step 5: Commit**

```bash
git add tests/test_telegram.py taps/telegram.py
git commit -m "feat: send Telegram messages with outcome classification and admin alerts"
```

---

### Task 18: Синхронизация состояния через git

**Files:**
- Create: `taps/gitsync.py`
- Test: `tests/test_gitsync.py`

**Interfaces:**
- Consumes: ничего из предыдущих задач (только стандартная библиотека и системный `git`).
- Produces:
  - `class GitError(Exception)`
  - `git(repo: Path, *args: str) -> str`: stdout команды; при ненулевом коде выбрасывает `GitError` со stderr в тексте
  - `pull_ff(repo: Path) -> None`: `git pull --ff-only origin main`; при разошедшейся истории или сбое сети выбрасывает `GitError`
  - `commit_and_push(repo: Path, paths: Sequence[str], message: str, attempts: int = 3) -> bool`

Модуль реализует протокол из разделов 3 и 7 спеки. В начале прогона делается только fast-forward pull. Затем коммитим `state.json` и пушим в `main`. Если push отклонён, делаем fetch. Если в новых удалённых коммитах менялся `state.json`, сразу возвращаем `False`: чужое состояние никогда не сливаем. Если менялось что-то другое (например, `corrections.yaml`), делаем `pull --rebase` и пробуем снова, всего до 3 попыток. Force-push не используется нигде. Тесты работают с настоящим git в `tmp_path`: bare-репозиторий `origin` и несколько клонов. Глобальный конфиг git разработчика отключён через `GIT_CONFIG_GLOBAL=/dev/null`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_gitsync.py`:
```python
import os
import subprocess
from pathlib import Path

import pytest

from taps import gitsync
from taps.gitsync import GitError, commit_and_push, pull_ff


def sh(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch):
    # Ignore the developer's global/system git config (signing, pull.rebase, hooks...).
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture
def origin(tmp_path) -> Path:
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)], check=True, capture_output=True)
    seed = clone(tmp_path, "seed", bare)
    (seed / "state.json").write_text('{"v": 0}\n')
    (seed / "corrections.yaml").write_text("sightings: []\n")
    sh(seed, "add", ".")
    sh(seed, "commit", "-m", "seed")
    sh(seed, "push", "origin", "HEAD:main")
    return bare


def clone(tmp_path: Path, name: str, bare: Path) -> Path:
    path = tmp_path / name
    subprocess.run(["git", "clone", "-q", str(bare), str(path)], check=True, capture_output=True)
    sh(path, "config", "user.name", "Test")
    sh(path, "config", "user.email", "test@example.com")
    sh(path, "symbolic-ref", "HEAD", "refs/heads/main")
    return path


def push_change(tmp_path: Path, origin: Path, name: str, file: str, text: str, message: str) -> str:
    other = clone(tmp_path, name, origin)
    (other / file).write_text(text)
    sh(other, "commit", "-am", message)
    sh(other, "push", "origin", "HEAD:main")
    return sh(other, "rev-parse", "HEAD")


def origin_head(origin: Path) -> str:
    return sh(origin, "rev-parse", "main")


@pytest.fixture
def git_calls(monkeypatch):
    calls: list[tuple[str, ...]] = []
    real = gitsync.git

    def recording(repo, *args):
        calls.append(args)
        return real(repo, *args)

    monkeypatch.setattr(gitsync, "git", recording)
    return calls


def assert_never_forced(calls):
    for args in calls:
        assert not any(a in ("-f", "--force", "--force-with-lease") for a in args), args
        if args[0] == "push":
            assert not any(a.startswith("+") for a in args), args


def test_commit_and_push_pushes_new_commit(tmp_path, origin):
    repo = clone(tmp_path, "run", origin)
    (repo / "state.json").write_text('{"v": 1}\n')

    assert commit_and_push(repo, ["state.json"], "state: run") is True

    assert origin_head(origin) == sh(repo, "rev-parse", "HEAD")
    assert sh(origin, "log", "-1", "--format=%s", "main") == "state: run"
    assert sh(origin, "show", "main:state.json") == '{"v": 1}'


def test_commit_and_push_commits_only_given_paths(tmp_path, origin):
    repo = clone(tmp_path, "run", origin)
    (repo / "state.json").write_text('{"v": 1}\n')
    (repo / "corrections.yaml").write_text("sightings: [local]\n")
    sh(repo, "add", "corrections.yaml")

    assert commit_and_push(repo, ["state.json"], "state: run") is True

    assert sh(origin, "show", "--name-only", "--format=", "main") == "state.json"
    assert sh(origin, "show", "main:corrections.yaml") == "sightings: []"


def test_nothing_to_commit_returns_true_without_new_commit(tmp_path, origin):
    repo = clone(tmp_path, "run", origin)
    before = origin_head(origin)

    assert commit_and_push(repo, ["state.json"], "state: run") is True

    assert origin_head(origin) == before
    assert sh(repo, "rev-parse", "HEAD") == before


def test_remote_state_change_returns_false_and_leaves_origin_untouched(tmp_path, origin, git_calls):
    repo = clone(tmp_path, "run", origin)
    other_head = push_change(tmp_path, origin, "other", "state.json", '{"v": "other"}\n', "state: other run")
    (repo / "state.json").write_text('{"v": "mine"}\n')

    assert commit_and_push(repo, ["state.json"], "state: my run") is False

    assert origin_head(origin) == other_head
    assert sh(origin, "show", "main:state.json") == '{"v": "other"}'
    assert "state: my run" not in sh(origin, "log", "--format=%s", "main")
    assert sum(1 for c in git_calls if c[0] == "push") == 1
    assert not any(c[0] == "pull" for c in git_calls)
    assert_never_forced(git_calls)
    # give up: local is reset so it matches origin/main, not left diverged with our failed commit
    assert sh(repo, "rev-parse", "HEAD") == other_head
    assert (repo / "state.json").read_text() == '{"v": "other"}\n'


def test_remote_corrections_change_is_rebased_then_pushed(tmp_path, origin, git_calls):
    repo = clone(tmp_path, "run", origin)
    push_change(tmp_path, origin, "oleg", "corrections.yaml", "sightings: [new]\n", "corrections: add beer")
    (repo / "state.json").write_text('{"v": "mine"}\n')

    assert commit_and_push(repo, ["state.json"], "state: my run") is True

    assert origin_head(origin) == sh(repo, "rev-parse", "HEAD")
    assert sh(origin, "log", "--format=%s", "main").splitlines() == ["state: my run", "corrections: add beer", "seed"]
    assert sh(origin, "rev-list", "--merges", "main") == ""
    assert sh(origin, "show", "main:state.json") == '{"v": "mine"}'
    assert sh(origin, "show", "main:corrections.yaml") == "sightings: [new]"
    assert sum(1 for c in git_calls if c[0] == "push") == 2
    assert_never_forced(git_calls)


def test_rebase_conflict_is_aborted_and_returns_false(tmp_path, origin):
    repo = clone(tmp_path, "run", origin)
    other_head = push_change(tmp_path, origin, "oleg", "corrections.yaml", "sightings: [theirs]\n", "corrections: theirs")
    (repo / "corrections.yaml").write_text("sightings: [mine]\n")

    assert commit_and_push(repo, ["corrections.yaml"], "corrections: mine") is False

    assert origin_head(origin) == other_head
    assert not (repo / ".git" / "rebase-merge").exists()
    assert not (repo / ".git" / "rebase-apply").exists()
    # give up: the local commit attempt is discarded, local now matches origin/main
    assert sh(repo, "rev-parse", "HEAD") == other_head
    assert sh(repo, "log", "-1", "--format=%s") == "corrections: theirs"
    assert (repo / "corrections.yaml").read_text() == "sightings: [theirs]\n"


def test_gives_up_after_attempts(tmp_path, origin, git_calls):
    hook = origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    before = origin_head(origin)
    repo = clone(tmp_path, "run", origin)
    (repo / "state.json").write_text('{"v": 1}\n')

    assert commit_and_push(repo, ["state.json"], "state: run", attempts=3) is False

    assert sum(1 for c in git_calls if c[0] == "push") == 3
    assert origin_head(origin) == before
    assert_never_forced(git_calls)
    # give up: local matches origin/main again, our unpushed commit is discarded
    assert sh(repo, "rev-parse", "HEAD") == before
    assert (repo / "state.json").read_text() == '{"v": 0}\n'


def test_pull_ff_brings_remote_changes(tmp_path, origin):
    repo = clone(tmp_path, "run", origin)
    other_head = push_change(tmp_path, origin, "oleg", "corrections.yaml", "sightings: [new]\n", "corrections: add beer")

    pull_ff(repo)

    assert sh(repo, "rev-parse", "HEAD") == other_head
    assert (repo / "corrections.yaml").read_text() == "sightings: [new]\n"


def test_pull_ff_refuses_diverged_history(tmp_path, origin):
    repo = clone(tmp_path, "run", origin)
    push_change(tmp_path, origin, "other", "state.json", '{"v": "other"}\n', "state: other run")
    (repo / "state.json").write_text('{"v": "mine"}\n')
    sh(repo, "commit", "-am", "state: my run")
    mine = sh(repo, "rev-parse", "HEAD")

    with pytest.raises(GitError):
        pull_ff(repo)

    assert sh(repo, "rev-parse", "HEAD") == mine
    assert (repo / "state.json").read_text() == '{"v": "mine"}\n'


def test_git_returns_stdout_and_raises_git_error_with_stderr(tmp_path, origin):
    repo = clone(tmp_path, "run", origin)
    assert gitsync.git(repo, "log", "-1", "--format=%s") == "seed\n"
    with pytest.raises(GitError, match="no-such-ref"):
        gitsync.git(repo, "show", "no-such-ref")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 120 .venv/bin/pytest tests/test_gitsync.py -v`
Expected: FAIL — `ImportError: cannot import name 'gitsync' from 'taps'`

- [ ] **Step 3: Write minimal implementation**

Create `taps/gitsync.py`:
```python
"""State sync through git: fast-forward pull, commit + push without ever forcing (spec §3, §7)."""
import contextlib
import subprocess
from collections.abc import Sequence
from pathlib import Path

STATE_FILE = "state.json"


class GitError(Exception):
    pass


def git(repo: Path, *args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout
    except subprocess.CalledProcessError as e:
        raise GitError(f"git {' '.join(args)} failed ({e.returncode}): {e.stderr.strip()}") from e


def pull_ff(repo: Path) -> None:
    git(repo, "pull", "--ff-only", "origin", "main")


def _give_up(repo: Path) -> bool:
    """Local never stays diverged from a push we gave up on: reset it to match origin/main."""
    git(repo, "fetch", "origin", "main")
    git(repo, "reset", "--hard", "origin/main")
    return False


def commit_and_push(repo: Path, paths: Sequence[str], message: str, attempts: int = 3) -> bool:
    """True when origin/main has our commit (or there was nothing new); False when the push was not accepted."""
    git(repo, "add", "--", *paths)
    if git(repo, "diff", "--cached", "--name-only", "--", *paths).strip():
        git(repo, "commit", "-m", message, "--", *paths)
    for _ in range(attempts):
        try:
            git(repo, "push", "origin", "HEAD:main")
            return True
        except GitError:
            pass
        git(repo, "fetch", "origin", "main")
        # Remote state.json is never merged automatically: a concurrent run owns it.
        if STATE_FILE in git(repo, "diff", "--name-only", "HEAD...origin/main").splitlines():
            return _give_up(repo)
        try:
            git(repo, "pull", "--rebase", "origin", "main")
        except GitError:
            with contextlib.suppress(GitError):
                git(repo, "rebase", "--abort")
            return _give_up(repo)
    return _give_up(repo)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `timeout 120 .venv/bin/pytest tests/test_gitsync.py -v`
Expected: `10 passed`

- [ ] **Step 5: Commit**

```bash
git add tests/test_gitsync.py taps/gitsync.py
git commit -m "feat: sync state through git with ff-only pull and safe rebase-retry push"
```

---

### Task 19: Данные для сайта: data.json

**Files:**
- Create: `taps/site_data.py`
- Test: `tests/test_site_data.py`

**Interfaces:**
- Consumes: `taps.state.State`, `PairRec`, `SourceRec`, `MARKERS`; `taps.config.Config`, `Place`, `Settings`; `taps.model.SOURCE_KINDS`; `taps.sources.manual.MANUAL_KEEP_DAYS`; `taps.timeutil.iso`, `parse_iso`, `to_yerevan`, `yerevan_date`, `age_days`
- Produces: `build_site_data(state: State, config: Config, now: datetime) -> dict`, `write_site_data(path: Path, data: dict) -> None` (компактный JSON, `ensure_ascii=False`)

Модуль собирает `site/data.json` для страницы по схеме из контракта (раздел site_data) и правилам видимости строк из спеки §8. Строка меню видна, только если пиво было в последнем удачном результате. Строка магазина видна при том же условии и если товар не закончился; у Beer City видны ещё и пары, которые добавили частичные прогоны после `last_full`. Чекины видны 21 день, записи «со слов» 14 дней. Строки с `info["hidden"]` не показываются никогда. 🆕 и ⭐ ставятся только на 7 дней после настоящей отправки события. Карточки мест показывают `last_ok`, `menu_updated_at`, `failing` и `failing_days`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_site_data.py`:
```python
import json
from datetime import datetime, timedelta, timezone

from taps.config import Config, Place, Settings
from taps.site_data import build_site_data, write_site_data
from taps.state import PairRec, SourceRec, State
from taps.timeutil import iso

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)  # 16:00 in Yerevan
STARTED = "2026-09-20T10:00:00+00:00"

GARGOYLE = Place(id="gargoyle", name="Gargoyle Bar", kind="bar",
                 sources={"untappd_menu": {"slug": "gargoyle", "venue_id": 1},
                          "untappd_checkins": {"slug": "gargoyle", "venue_id": 1}})
DORS = Place(id="dors", name="Dors Craft Beer & Kitchen", kind="brewpub",
             sources={"untappd_checkins": {"slug": "dors", "venue_id": 2}})
BEER_CITY = Place(id="beer-city", name="Beer City", kind="shop", sources={"beercity": {}})
PARMA = Place(id="parma", name="Парма", kind="shop", sources={"parma": {}})
CONFIG = Config(places={p.id: p for p in (GARGOYLE, DORS, BEER_CITY, PARMA)},
                breweries=(), settings=Settings(hot_rating=3.8))


def ago(days: float) -> str:
    return iso(NOW - timedelta(days=days))


def pair(source: str, first_seen: str = STARTED, **kw) -> PairRec:
    info = {"source": source, "name": "Black Sails", **kw.pop("info", {})}
    return PairRec(first_seen=first_seen, last_seen=first_seen, info=info, **kw)


def state(pairs: dict, sources: dict | None = None) -> State:
    return State(started_at=STARTED, pairs=pairs, sources=sources or {})


def keys(data: dict) -> set[tuple[str, str]]:
    return {(r["place_id"], r["beer_key"]) for r in data["rows"]}


def build(st: State) -> dict:
    return build_site_data(st, CONFIG, NOW)


def test_menu_row_visible_only_when_in_last_result():
    st = state({"gargoyle": {"u:1": pair("untappd_menu", last_in_result=True),
                             "u:2": pair("untappd_menu", last_in_result=False)}})
    assert keys(build(st)) == {("gargoyle", "u:1")}


def test_shop_row_hidden_when_out_of_stock():
    st = state({"parma": {"n:a": pair("parma", in_stock=True),
                          "n:b": pair("parma", in_stock=None),
                          "n:c": pair("parma", in_stock=False),
                          "n:d": pair("parma", in_stock=True, last_in_result=False)}})
    assert keys(build(st)) == {("parma", "n:a"), ("parma", "n:b")}


def test_beercity_pairs_from_partial_runs_after_last_full_are_visible():
    full = ago(1)
    st = state(
        {"beer-city": {
            "n:partial": pair("beercity", first_seen=ago(0.5), in_stock=True, last_in_result=False),
            "n:old": pair("beercity", first_seen=ago(3), in_stock=True, last_in_result=False),
            "n:partial-out": pair("beercity", first_seen=ago(0.5), in_stock=False, last_in_result=False),
        }},
        {"beercity:beer-city": SourceRec(last_ok=ago(0.5), last_full=full)},
    )
    assert keys(build(st)) == {("beer-city", "n:partial")}


def test_checkin_row_visible_21_days_after_checkin():
    st = state({"dors": {
        "u:20": pair("untappd_checkins", info={"checkin_at": ago(20), "serving": "Draft"}),
        "u:22": pair("untappd_checkins", info={"checkin_at": ago(22)}),
        "u:0": pair("untappd_checkins", info={"checkin_at": ago(0.1)}),
    }})
    rows = {r["beer_key"]: r for r in build(st)["rows"]}
    assert set(rows) == {"u:20", "u:0"}
    assert rows["u:20"]["seen_days_ago"] == 20
    assert rows["u:0"]["seen_days_ago"] == 0
    assert rows["u:20"]["badge"] == "checkin"


def test_manual_row_visible_14_days_from_date():
    st = state({"gargoyle": {
        "n:379 hazy pale": pair("manual", info={"manual_date": "2026-09-27", "manual_by": "Аня"}),
        "n:old": pair("manual", info={"manual_date": "2026-09-25", "manual_by": "Аня"}),
    }})
    rows = build(st)["rows"]
    assert [r["beer_key"] for r in rows] == ["n:379 hazy pale"]
    assert rows[0]["by"] == "Аня" and rows[0]["badge"] == "manual" and rows[0]["seen_days_ago"] is None


def test_manual_row_hidden_when_not_in_the_last_manual_result():
    st = state({"gargoyle": {
        "n:379 hazy pale": pair("manual", last_in_result=False, info={"manual_date": "2026-09-27", "manual_by": "Аня"}),
    }})
    assert build(st)["rows"] == []


def test_hidden_rows_never_shown():
    st = state({
        "gargoyle": {"u:1": pair("untappd_menu", info={"hidden": True})},
        "parma": {"n:corona": pair("parma", in_stock=True, notified_at="suppressed", info={"hidden": True})},
    })
    assert build(st)["rows"] == []


def test_new_and_star_only_for_recent_real_notifications():
    st = state({"gargoyle": {
        "u:1": pair("untappd_menu", notified_at=ago(6), star=True),
        "u:2": pair("untappd_menu", notified_at=ago(6), star=False),
        "u:3": pair("untappd_menu", notified_at=ago(8), star=True),
        "u:4": pair("untappd_menu", notified_at="baseline", star=True),
        "u:5": pair("untappd_menu", notified_at="suppressed", star=True),
        "u:6": pair("untappd_menu", notified_at=None, event_at=ago(0.1), star=True),
    }})
    flags = {r["beer_key"]: (r["new"], r["star"]) for r in build(st)["rows"]}
    assert flags == {"u:1": (True, True), "u:2": (True, False), "u:3": (False, False),
                     "u:4": (False, False), "u:5": (False, False), "u:6": (False, False)}


def test_section_follows_place_kind():
    st = state({"gargoyle": {"u:1": pair("untappd_menu")},
                "dors": {"u:2": pair("untappd_checkins", info={"checkin_at": ago(1)})},
                "parma": {"n:x": pair("parma", in_stock=True)}})
    data = build(st)
    assert {r["place_id"]: r["section"] for r in data["rows"]} == {
        "gargoyle": "bars", "dors": "bars", "parma": "shops"}
    assert [(p["id"], p["kind"], p["section"]) for p in data["places"]] == [
        ("gargoyle", "bar", "bars"), ("dors", "brewpub", "bars"),
        ("beer-city", "shop", "shops"), ("parma", "shop", "shops")]


def test_pairs_of_unknown_places_are_skipped():
    st = state({"closed-bar": {"u:1": pair("untappd_menu")}})
    assert build(st)["rows"] == []


def test_row_carries_display_fields():
    info = {"name": "Celebrator", "brewery": "Ayinger", "style": "Doppelbock", "abv": 6.7, "ibu": 24,
            "rating": 3.76, "price_amd": 2300, "volume_ml": 330, "container": "bottle",
            "url": "https://untappd.com/b/ayinger-celebrator/4280"}
    st = state({"gargoyle": {"u:4280": pair("untappd_menu", first_seen="2026-09-24T21:30:00+00:00", info=info)}})
    assert build(st)["rows"] == [{
        "place_id": "gargoyle", "section": "bars", "beer_key": "u:4280", "name": "Celebrator",
        "brewery": "Ayinger", "style": "Doppelbock", "abv": 6.7, "ibu": 24, "rating": 3.76,
        "price_amd": 2300, "volume_ml": 330, "container": "bottle",
        "url": "https://untappd.com/b/ayinger-celebrator/4280",
        "badge": "menu", "since": "2026-09-25",  # 01:30 next day in Yerevan
        "seen_days_ago": None, "new": False, "star": False, "by": None,
    }]


def test_places_status():
    st = state({}, {
        "untappd_menu:gargoyle": SourceRec(last_ok=ago(0.2), menu_updated_at="2026-10-08T09:00:00+00:00"),
        "untappd_checkins:gargoyle": SourceRec(last_ok=ago(2)),
        "untappd_checkins:dors": SourceRec(last_ok=ago(3.5), fail_streak=4, last_error="network"),
        "parma:parma": SourceRec(fail_streak=1, last_error="http"),
    })
    places = {p["id"]: p for p in build(st)["places"]}
    assert places["gargoyle"] == {
        "id": "gargoyle", "name": "Gargoyle Bar", "kind": "bar", "section": "bars",
        "last_ok": ago(0.2), "menu_updated_at": "2026-10-08T09:00:00+00:00",
        "failing": False, "failing_days": 0}
    assert (places["dors"]["failing"], places["dors"]["failing_days"]) == (True, 3)
    assert places["dors"]["menu_updated_at"] is None
    # never succeeded: days counted from started_at
    assert (places["parma"]["last_ok"], places["parma"]["failing"], places["parma"]["failing_days"]) == (None, True, 20)
    assert places["beer-city"]["last_ok"] is None and places["beer-city"]["failing"] is False
    assert "beercity:beer-city" not in st.sources  # building data never creates source records


def test_tripped_source_shows_as_failing():
    st = state({}, {"untappd_menu:gargoyle": SourceRec(fail_streak=1, last_error="shrink", trip_streak=1)})
    places = {p["id"]: p for p in build(st)["places"]}
    assert places["gargoyle"]["failing"] is True


def test_top_level_fields():
    data = build(state({}))
    assert data["generated_at"] == "2026-10-10T12:00:00+00:00"
    assert data["started_at"] == STARTED
    assert data["hot_rating"] == 3.8


def test_write_site_data_is_compact_utf8(tmp_path):
    data = build(state({"parma": {"n:x": pair("parma", in_stock=True, info={"name": "Ծիրան Էյլ"})}}))
    path = tmp_path / "data.json"
    write_site_data(path, data)
    text = path.read_text(encoding="utf-8")
    assert "Ծիրան Էյլ" in text and "Парма" in text
    assert "\\u" not in text and ", " not in text and ": " not in text and "\n" not in text
    assert json.loads(text) == data
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 120 .venv/bin/pytest tests/test_site_data.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taps.site_data'`

- [ ] **Step 3: Write minimal implementation**

Create `taps/site_data.py`:
```python
"""Build site/data.json from state (spec §8)."""
import json
from datetime import date, datetime
from pathlib import Path

from taps.config import Config, Place
from taps.model import SOURCE_KINDS
from taps.sources.manual import MANUAL_KEEP_DAYS
from taps.state import MARKERS, PairRec, State
from taps.timeutil import age_days, iso, parse_iso, to_yerevan, yerevan_date

CHECKIN_KEEP_DAYS = 21   # same window as rules.CHECKIN_KEEP_DAYS
NEW_DAYS = 7             # 🆕/⭐ badges live this long after the event was sent
INFO_FIELDS = ("brewery", "style", "abv", "ibu", "rating", "price_amd", "volume_ml", "container", "url")


def _section(place: Place) -> str:
    return "shops" if place.kind == "shop" else "bars"


def _latest(values) -> str | None:
    values = [v for v in values if v]
    return max(values, key=parse_iso) if values else None


def _place(place: Place, state: State, now: datetime) -> dict:
    recs = [state.sources[k] for k in place.source_keys() if k in state.sources]  # never create records
    last_ok = _latest(r.last_ok for r in recs)
    failing = any(r.fail_streak > 0 for r in recs)
    failing_days = 0
    if failing:
        failing_days = max(0, int(age_days(parse_iso(last_ok or state.started_at), now)))
    return {
        "id": place.id, "name": place.name, "kind": place.kind, "section": _section(place),
        "last_ok": last_ok, "menu_updated_at": _latest(r.menu_updated_at for r in recs),
        "failing": failing, "failing_days": failing_days,
    }


def _days_ago(day: str, now: datetime) -> int:
    return (to_yerevan(now).date() - date.fromisoformat(day)).days


def _kind(rec: PairRec) -> str | None:
    return rec.info.get("kind") or SOURCE_KINDS.get(rec.info.get("source"))


def _visible(place: Place, rec: PairRec, kind: str, state: State, now: datetime) -> bool:
    info = rec.info
    if info.get("hidden"):
        return False
    if kind == "menu":
        return rec.last_in_result
    if kind == "shop":
        in_result = rec.last_in_result
        if info.get("source") == "beercity":
            # Beer City partial runs add items without refreshing last_in_result
            last_full = state.sources.get(f"beercity:{place.id}")
            last_full = last_full.last_full if last_full else None
            if last_full and parse_iso(rec.first_seen) > parse_iso(last_full):
                in_result = True
        return in_result and rec.in_stock is not False
    if kind == "checkin":
        at = info.get("checkin_at")
        return bool(at) and age_days(parse_iso(at), now) <= CHECKIN_KEEP_DAYS
    if kind == "manual":
        day = info.get("manual_date")
        return rec.last_in_result and bool(day) and 0 <= _days_ago(day, now) <= MANUAL_KEEP_DAYS
    return False


def _is_new(rec: PairRec, now: datetime) -> bool:
    n = rec.notified_at
    return n is not None and n not in MARKERS and age_days(parse_iso(n), now) <= NEW_DAYS


def _row(place: Place, key: str, rec: PairRec, kind: str, now: datetime) -> dict:
    info = rec.info
    new = _is_new(rec, now)
    row = {"place_id": place.id, "section": _section(place), "beer_key": key,
           "name": info.get("name") or info.get("title") or key}
    row.update({f: info.get(f) for f in INFO_FIELDS})
    row.update({
        "badge": kind,
        "since": yerevan_date(parse_iso(rec.first_seen)),
        "seen_days_ago": _days_ago(yerevan_date(parse_iso(info["checkin_at"])), now) if kind == "checkin" else None,
        "new": new,
        "star": new and rec.star,
        "by": info.get("manual_by") if kind == "manual" else None,
    })
    return row


def build_site_data(state: State, config: Config, now: datetime) -> dict:
    rows = []
    for place in config.places.values():
        for key, rec in state.pairs.get(place.id, {}).items():
            kind = _kind(rec)
            if kind and _visible(place, rec, kind, state, now):
                rows.append(_row(place, key, rec, kind, now))
    return {
        "generated_at": iso(now),
        "started_at": state.started_at,
        "hot_rating": config.settings.hot_rating,
        "places": [_place(p, state, now) for p in config.places.values()],
        "rows": rows,
    }


def write_site_data(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=None, separators=(",", ":")), encoding="utf-8")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `timeout 120 .venv/bin/pytest tests/test_site_data.py -v`
Expected: `15 passed`

- [ ] **Step 5: Commit**

```bash
git add tests/test_site_data.py taps/site_data.py
git commit -m "feat: build site data.json from state"
```

---

### Task 20: Страница сайта

**Files:**
- Create: `site/index.html`
- Test: `tests/test_site_page.py`

**Interfaces:**
- Consumes: `site/data.json`, который пишет `taps/site_data.py` (`build_site_data(state, config, now) -> dict`, `write_site_data(path, data)`). Поля верхнего уровня: `generated_at`, `started_at`, `hot_rating`. `places[]`: `id, name, section ("bars"|"shops"), last_ok, menu_updated_at, failing, failing_days`. `rows[]`: `place_id, section, name, brewery, style, abv, ibu, rating, price_amd, volume_ml, container, badge ("menu"|"checkin"|"manual"|"shop"), since ("YYYY-MM-DD"), seen_days_ago, new, star, url, by`.
- Produces: статичный `site/index.html`, который читает `./data.json` из той же папки. Шаг публикации в workflow выкладывает на GitHub Pages всю папку `site/`, включая `data.json` (он в `.gitignore`). id элементов: `tab-bars`, `tab-shops`, `only-new`, `sort` (значения `since|rating|abv`), `search`, `places`, `count`, `rows`, `stale-banner`, `updated`, `legend-star`, `legend-hot`.

Одна статичная страница без сборки и без внешних скриптов, на чистом JS и по-русски (спека §8). Что на ней есть:
- вкладки «Бары» и «Магазины», переключатель «только новинки», сортировка, кнопки мест и поиск;
- карточки мест со свежестью проверки, значки строк, плашка «данные устарели» старше 36 часов;
- таблица на широком экране и карточки на телефоне; всё время показано по Asia/Yerevan.

Значения из `data.json` попадают в DOM только как текст. Ссылкой становится только адрес `http(s)`. Тест разбирает HTML через `html.parser` и проверяет разметку, доступные подписи и то, что скрипт использует все поля `data.json`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_site_page.py`:
```python
import re
from pathlib import Path

from bs4 import BeautifulSoup

PAGE = Path(__file__).resolve().parent.parent / "site" / "index.html"

ROW_FIELDS = ("place_id", "section", "name", "brewery", "style", "abv", "ibu", "rating", "price_amd",
              "volume_ml", "container", "badge", "since", "seen_days_ago", "new", "star", "url", "by")
PLACE_FIELDS = ("id", "name", "section", "last_ok", "menu_updated_at", "failing", "failing_days")


def _soup():
    return BeautifulSoup(PAGE.read_text(encoding="utf-8"), "html.parser")


def _js(soup):
    return "\n".join(s.get_text() for s in soup.find_all("script"))


def _css(soup):
    return "\n".join(s.get_text() for s in soup.find_all("style"))


def _label_text(soup, control):
    """Accessible name of a form control: aria-label, <label for=id> or wrapping <label>."""
    if control.get("aria-label"):
        return control["aria-label"]
    label = soup.find("label", attrs={"for": control.get("id")}) or control.find_parent("label")
    return label.get_text(" ", strip=True) if label else ""


def test_page_is_russian_and_mobile_ready():
    soup = _soup()
    assert soup.html["lang"] == "ru"
    assert soup.find("meta", attrs={"charset": True})["charset"].lower() == "utf-8"
    assert "width=device-width" in soup.find("meta", attrs={"name": "viewport"})["content"]


def test_required_elements_exist():
    soup = _soup()
    for element_id in ("tab-bars", "tab-shops", "only-new", "sort", "search", "places", "count", "rows",
                       "stale-banner", "updated", "legend-star", "legend-hot"):
        assert soup.find(id=element_id) is not None, element_id


def test_tabs_are_toggle_buttons_with_bars_selected():
    soup = _soup()
    bars, shops = soup.find(id="tab-bars"), soup.find(id="tab-shops")
    assert bars.name == shops.name == "button"
    assert "🍻 Бары" in bars.get_text() and "🛒 Магазины" in shops.get_text()
    assert (bars["data-section"], shops["data-section"]) == ("bars", "shops")
    assert (bars["aria-pressed"], shops["aria-pressed"]) == ("true", "false")


def test_sort_select_defaults_to_newness():
    soup = _soup()
    options = soup.find("select", id="sort").find_all("option")
    assert [o["value"] for o in options] == ["since", "rating", "abv"]
    assert [o.has_attr("selected") for o in options] == [True, False, False]
    texts = " ".join(o.get_text() for o in options)
    assert "новизне" in texts and "рейтингу" in texts and "крепости" in texts


def test_only_new_toggle_is_a_checkbox():
    soup = _soup()
    toggle = soup.find(id="only-new")
    assert toggle.name == "input" and toggle["type"] == "checkbox"
    assert "🆕 только новинки за 7 дней" in _label_text(soup, toggle)


def test_every_form_control_has_an_accessible_name():
    soup = _soup()
    controls = soup.find_all(["input", "select"])
    assert {c["id"] for c in controls} == {"search", "sort", "only-new"}
    for control in controls:
        assert _label_text(soup, control), control["id"]
    assert soup.find(id="places")["aria-label"]


def test_stale_banner_hidden_until_script_decides():
    banner = _soup().find(id="stale-banner")
    assert banner.has_attr("hidden")
    assert "данные устарели" in banner.get_text().lower()


def test_loads_data_json_bypassing_cache():
    js = _js(_soup())
    assert re.search(r"""fetch\(\s*["']\./data\.json["']\s*,\s*\{\s*cache:\s*["']no-store["']\s*\}\s*\)""", js)


def test_no_external_scripts_or_styles():
    soup = _soup()
    for script in soup.find_all("script"):
        src = script.get("src", "")
        assert not re.match(r"(?i)(https?:)?//", src), src
    for link in soup.find_all("link", rel="stylesheet"):
        assert link["href"].startswith("https://fonts.googleapis.com/"), link["href"]


def test_script_uses_every_data_field():
    js = _js(_soup())
    for field in ROW_FIELDS + PLACE_FIELDS + ("generated_at", "started_at", "hot_rating", "rows", "places"):
        assert re.search(rf"\.{field}\b", js), field


def test_script_builds_dom_safely():
    js = _js(_soup())
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "document.write" not in js
    assert "https?:" in js  # only http(s) links from data.json become href
    assert "noopener" in js


def test_script_texts_and_rules():
    js = _js(_soup())
    assert "Asia/Yerevan" in js
    assert re.search(r"STALE_HOURS\s*=\s*36\b", js)
    assert "min-width: 700px" in js
    for text in ("✅ меню", "👀 видели", "✍️ со слов", "🛒 в магазине", "🔥", "⭐", "🆕",
                 "меню обновлено", "проверено", "⚠️ не удалось проверить", "обновлено",
                 "ч назад", "Данные устарели", "с тех пор, как следим, с "):
        assert text in js, text
    for word in ("день", "дня", "дней"):
        assert f'"{word}"' in js, word


def test_colours_are_custom_properties_with_dark_variant():
    css = _css(_soup())
    root = re.search(r":root\s*\{([^}]*)\}", css).group(1)
    assert "--bg:" in root and "--text:" in root and "--warn-bg:" in root
    dark = css.split("prefers-color-scheme: dark", 1)[1]
    assert "--bg:" in dark and "--warn-bg:" in dark
    assert "overflow-x: auto" in css  # wide table scrolls inside its box, never the page


def test_footer_sources_legend_and_credits():
    soup = _soup()
    footer = soup.find("footer")
    text = footer.get_text(" ", strip=True)
    assert "Идея и основа — hopandshot.github.io/hopsandshot" in text
    assert "нашли ошибку — напишите олегу" in text.lower()
    assert soup.find(id="legend-star").get_text().startswith("⭐ — возможно, впервые в Ереване (с тех пор, как следим")
    hrefs = " ".join(a["href"] for a in footer.find_all("a"))
    for host in ("hopandshot.github.io/hopsandshot", "untappd.com", "buy.am", "beer-city.am",
                 "yerevan-city.am", "parma.am"):
        assert host in hrefs, host
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 120 .venv/bin/pytest tests/test_site_page.py -v`
Expected: FAIL — `FileNotFoundError: [Errno 2] No such file or directory: '.../site/index.html'` (14 failed)

- [ ] **Step 3: Write minimal implementation**

Create `site/index.html`:
```html
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ереван на кранах</title>
<meta name="description" content="Крафтовое пиво в барах и магазинах Еревана: что наливают и что продают сейчас">
<style>
  :root {
    color-scheme: light dark;
    --bg: #faf7f2;
    --surface: #ffffff;
    --surface-2: #f3eee6;
    --border: #e3dccf;
    --text: #1f1a14;
    --muted: #6b5f52;
    --accent: #9a5412;
    --on-accent: #ffffff;
    --hot: #b93a0e;
    --danger: #b42318;
    --warn-bg: #ffe98a;
    --warn-text: #3d2e00;
    --radius: 10px;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #1a1612;
      --surface: #221c17;
      --surface-2: #2b231d;
      --border: #3a322a;
      --text: #f2ebdd;
      --muted: #a89a89;
      --accent: #e0a458;
      --on-accent: #1a1612;
      --hot: #ff8a5c;
      --danger: #ff8f84;
      --warn-bg: #5a4700;
      --warn-text: #ffe99a;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font: 16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    overflow-wrap: anywhere;
  }
  .wrap { max-width: 1180px; margin: 0 auto; padding: 16px 16px 48px; }
  a { color: var(--accent); }
  :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .sr-only {
    position: absolute; width: 1px; height: 1px; overflow: hidden;
    clip: rect(0 0 0 0); white-space: nowrap;
  }
  .banner {
    background: var(--warn-bg); color: var(--warn-text); font-weight: 600;
    border-radius: var(--radius); padding: 10px 14px; margin-bottom: 12px;
  }
  h1 { font-size: clamp(1.6rem, 5vw, 2.4rem); line-height: 1.15; margin: 0; }
  .sub { margin: 4px 0 16px; color: var(--muted); }
  .tabs { display: flex; gap: 8px; margin-bottom: 12px; }
  .tab {
    flex: 1 1 0; max-width: 220px; font: inherit; font-weight: 600; cursor: pointer;
    padding: 10px 14px; border: 1px solid var(--border); border-radius: var(--radius);
    background: var(--surface); color: var(--text);
  }
  .tab[aria-pressed="true"] { background: var(--accent); border-color: var(--accent); color: var(--on-accent); }
  .controls { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; margin-bottom: 12px; }
  #search, select {
    font: inherit; color: var(--text); background: var(--surface);
    border: 1px solid var(--border); border-radius: var(--radius); padding: 8px 12px;
  }
  #search { flex: 1 1 240px; min-width: 0; }
  .sort, .toggle { display: inline-flex; gap: 8px; align-items: center; }
  .toggle { cursor: pointer; }
  .toggle input { width: 18px; height: 18px; margin: 0; accent-color: var(--accent); }
  .places { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
  .place {
    display: flex; flex-direction: column; align-items: flex-start; max-width: 100%;
    font: inherit; font-size: .8rem; line-height: 1.3; text-align: left; cursor: pointer;
    padding: 6px 12px; border: 1px solid var(--border); border-radius: var(--radius);
    background: var(--surface); color: var(--muted);
  }
  .place b { font-size: .95rem; color: var(--text); }
  .place .warn { color: var(--danger); }
  .place[aria-pressed="true"] { border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); background: var(--surface-2); }
  .count { color: var(--muted); font-size: .85rem; margin: 0 0 8px; }
  .empty { text-align: center; color: var(--muted); padding: 32px 16px; }
  .table-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
  table { width: 100%; border-collapse: collapse; font-size: .92rem; }
  th {
    text-align: left; font-size: .75rem; text-transform: uppercase; letter-spacing: .05em;
    color: var(--muted); background: var(--surface-2); padding: 10px 12px; white-space: nowrap;
  }
  td { padding: 10px 12px; border-top: 1px solid var(--border); vertical-align: top; overflow-wrap: normal; }
  .num { text-align: right; white-space: nowrap; }
  .nowrap { white-space: nowrap; }
  .beer { font-weight: 600; }
  .small { display: block; color: var(--muted); font-size: .82rem; }
  .flag { margin-right: .25em; }
  .hot { color: var(--hot); font-weight: 700; white-space: nowrap; }
  .cards { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 10px 12px; }
  .card .top { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
  .card .meta { color: var(--muted); font-size: .85rem; }
  .card .where { font-size: .85rem; margin-top: 2px; }
  footer { margin-top: 32px; padding-top: 8px; border-top: 1px solid var(--border); color: var(--muted); font-size: .88rem; }
  footer h2 { font-size: .95rem; color: var(--text); margin: 16px 0 4px; }
  footer p { margin: 4px 0; }
  .legend { list-style: none; margin: 0; padding: 0; }
</style>
</head>
<body>
<div class="wrap">
  <div id="stale-banner" class="banner" role="alert" hidden>⚠️ Данные устарели.</div>
  <main>
    <header>
      <h1><span aria-hidden="true">🍺</span> Ереван на кранах</h1>
      <p class="sub">Крафтовое пиво в барах и магазинах Еревана · <span id="updated">загружаем…</span></p>
    </header>
    <div class="tabs" role="group" aria-label="Раздел">
      <button type="button" class="tab" id="tab-bars" data-section="bars" aria-pressed="true">🍻 Бары</button>
      <button type="button" class="tab" id="tab-shops" data-section="shops" aria-pressed="false">🛒 Магазины</button>
    </div>
    <div class="controls">
      <label for="search" class="sr-only">Поиск по названию, стилю и пивоварне</label>
      <input type="search" id="search" placeholder="Название, стиль или пивоварня" autocomplete="off">
      <span class="sort">
        <label for="sort">Сортировка</label>
        <select id="sort">
          <option value="since" selected>по новизне</option>
          <option value="rating">по рейтингу</option>
          <option value="abv">по крепости</option>
        </select>
      </span>
      <label class="toggle"><input type="checkbox" id="only-new"> 🆕 только новинки за 7 дней</label>
    </div>
    <div id="places" class="places" role="group" aria-label="Места"></div>
    <p id="count" class="count" aria-live="polite"></p>
    <div id="rows"></div>
  </main>
  <footer>
    <h2>Значки</h2>
    <ul class="legend">
      <li>✅ — в меню бара (Untappd или buy.am)</li>
      <li>👀 — видели в чекинах Untappd: разлив в этом месте</li>
      <li>✍️ — со слов: кто-то из друзей видел сам</li>
      <li>🛒 — в онлайн-ассортименте магазина (сети, а не конкретного филиала)</li>
      <li>🆕 — появилось за последние 7 дней</li>
      <li id="legend-star">⭐ — возможно, впервые в Ереване (с тех пор, как следим)</li>
      <li id="legend-hot">🔥 — высокий рейтинг Untappd</li>
    </ul>
    <h2>Источники</h2>
    <p>Untappd: меню <a href="https://untappd.com/v/gargoyle-bar/12252462">Gargoyle Bar</a> и
      <a href="https://untappd.com/v/beatles-pub-yerevan/2162817">Beatles Pub</a>, чекины баров и пивоварен
      (<a href="https://untappd.com/">untappd.com</a>).
      Меню Dargett Brewpub — <a href="https://buy.am/en/restaurants/dargett">buy.am</a>.
      Магазины: <a href="https://www.beer-city.am/en/">Beer City</a>, <a href="https://yerevan-city.am/">Yerevan City</a>,
      <a href="https://parma.am/en/">Parma</a>.</p>
    <p>Идея и основа — <a href="https://hopandshot.github.io/hopsandshot/">hopandshot.github.io/hopsandshot</a></p>
    <p>Нашли ошибку — напишите Олегу.</p>
  </footer>
</div>
<script>
"use strict";
const TZ = "Asia/Yerevan";
const STALE_HOURS = 36;
const CONTAINER_RU = { can: "банка", bottle: "бутылка", keg: "кег", draft: "разлив" };
const wide = window.matchMedia("(min-width: 700px)");
const ui = { data: null, places: new Map(), section: "bars", place: null };
const $ = (id) => document.getElementById(id);

// Date parts in Yerevan time: {ymd: "2026-09-23", dm: "23.09", hm: "14:17"}.
const fmt = new Intl.DateTimeFormat("en-GB", {
  timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});
function yerevan(date) {
  const p = {};
  for (const part of fmt.formatToParts(date)) p[part.type] = part.value;
  return { ymd: `${p.year}-${p.month}-${p.day}`, dm: `${p.day}.${p.month}`, hm: `${p.hour}:${p.minute}` };
}
function parseTime(s) {
  if (!s) return null;
  const d = new Date(s);
  return isNaN(d) ? null : d;
}
function daysBetween(then, now) {  // calendar days in Yerevan
  return Math.max(0, Math.round((Date.parse(yerevan(now).ymd) - Date.parse(yerevan(then).ymd)) / 864e5));
}
function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}
const days = (n) => `${n} ${plural(n, "день", "дня", "дней")}`;
const ago = (n) => (n === 0 ? "сегодня" : n === 1 ? "вчера" : `${days(n)} назад`);
const dot = (parts) => parts.filter(Boolean).join(" · ");

// Every value from data.json goes into the DOM as text, never as HTML.
function el(tag, attrs, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v != null) node.setAttribute(k, v);
  for (const kid of kids) if (kid != null && kid !== "") node.append(kid);
  return node;
}
const safeUrl = (u) => (typeof u === "string" && /^https?:\/\//i.test(u) ? u : null);

const abvText = (r) => (r.abv != null ? `${r.abv}%` : "");
const ibuText = (r) => (r.ibu != null ? `IBU ${r.ibu}` : "");
const priceText = (r) => (r.price_amd != null ? `${r.price_amd} ֏` : "");
const volumeText = (r) => [r.volume_ml != null ? `${r.volume_ml} мл` : "", CONTAINER_RU[r.container] || ""].filter(Boolean).join(" ");
const placeName = (id) => (ui.places.get(id) || {}).name || id;
function sinceText(s) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s || "");
  return m ? `с ${m[3]}.${m[2]}` : "";
}
function badgeText(r) {
  switch (r.badge) {
    case "menu": return "✅ меню";
    case "checkin": return r.seen_days_ago == null ? "👀 видели" : `👀 видели ${ago(r.seen_days_ago)}`;
    case "manual": return r.by ? `✍️ со слов (${r.by})` : "✍️ со слов";
    case "shop": return "🛒 в магазине";
    default: return "";
  }
}
function flagNodes(r) {
  const flag = (label, sign) => el("span", { class: "flag", role: "img", "aria-label": label, title: label }, sign);
  return [r.star ? flag("возможно, впервые в Ереване", "⭐") : null, r.new ? flag("новинка за 7 дней", "🆕") : null];
}
function nameNode(r) {
  const url = safeUrl(r.url);
  return url
    ? el("a", { class: "beer", href: url, target: "_blank", rel: "noopener noreferrer" }, r.name)
    : el("span", { class: "beer" }, r.name);
}
function ratingNode(r) {
  if (r.rating == null) return null;
  const value = Number(r.rating).toFixed(2);
  return r.rating >= ui.data.hot_rating
    ? el("span", { class: "hot", title: "рейтинг Untappd" }, `🔥${value}`)
    : el("span", { title: "рейтинг Untappd" }, value);
}

function cmpDesc(a, b) {  // descending, empty values last
  if (a == null || b == null) return (a == null) - (b == null);
  return a < b ? 1 : a > b ? -1 : 0;
}
function visibleRows() {
  const q = $("search").value.trim().toLowerCase();
  const onlyNew = $("only-new").checked;
  const key = $("sort").value;
  return ui.data.rows
    .filter((r) => r.section === ui.section
      && (!onlyNew || r.new)
      && (!ui.place || r.place_id === ui.place)
      && (!q || [r.name, r.style, r.brewery].some((s) => s && String(s).toLowerCase().includes(q))))
    .sort((a, b) => cmpDesc(a[key], b[key]) || cmpDesc(a.since, b.since)
      || String(a.name).localeCompare(String(b.name), "ru"));
}

function table(rows) {
  const heads = ["Пиво", "Стиль", "Крепость", "IBU", "Рейтинг", "Цена", "Где", "Замечено"];
  const numeric = new Set(["Крепость", "IBU", "Рейтинг", "Цена"]);
  const head = el("tr", null, ...heads.map((h) => el("th", { scope: "col", class: numeric.has(h) ? "num" : null }, h)));
  const body = rows.map((r) => el("tr", null,
    el("td", null, ...flagNodes(r), nameNode(r), r.brewery ? el("span", { class: "small" }, r.brewery) : null),
    el("td", null, r.style || ""),
    el("td", { class: "num" }, abvText(r)),
    el("td", { class: "num" }, r.ibu ?? ""),
    el("td", { class: "num" }, ratingNode(r)),
    el("td", { class: "num" }, priceText(r), volumeText(r) ? el("span", { class: "small" }, volumeText(r)) : null),
    el("td", null, placeName(r.place_id), el("span", { class: "small" }, badgeText(r))),
    el("td", { class: "nowrap" }, sinceText(r.since))));
  return el("div", { class: "table-wrap" }, el("table", null, el("thead", null, head), el("tbody", null, ...body)));
}
function cards(rows) {
  const line = (cls, text) => (text ? el("div", { class: cls }, text) : null);
  return el("ul", { class: "cards" }, ...rows.map((r) => el("li", { class: "card" },
    el("div", { class: "top" }, el("span", null, ...flagNodes(r), nameNode(r)), ratingNode(r)),
    line("meta", dot([r.brewery, r.style, abvText(r), ibuText(r)])),
    line("meta", dot([volumeText(r), priceText(r)])),
    line("where", dot([placeName(r.place_id), badgeText(r), sinceText(r.since)])))));
}
function render() {
  if (!ui.data) return;
  const rows = visibleRows();
  const total = ui.data.rows.filter((r) => r.section === ui.section).length;
  $("count").textContent = rows.length === total ? `Позиций: ${total}` : `Показано ${rows.length} из ${total}`;
  if (!rows.length) {
    $("rows").replaceChildren(el("p", { class: "empty" }, total ? "Ничего не нашлось — попробуйте другой запрос или фильтр." : "Пока пусто."));
    return;
  }
  $("rows").replaceChildren(wide.matches ? table(rows) : cards(rows));
}

function placeStatus(p, now) {
  const lines = [];
  const menu = parseTime(p.menu_updated_at);
  if (menu) lines.push([`меню обновлено ${ago(daysBetween(menu, now))}`, false]);
  const ok = parseTime(p.last_ok);
  if (p.failing) {
    const n = Number(p.failing_days) || 0;
    lines.push([n > 0 ? `⚠️ не удалось проверить ${days(n)}` : "⚠️ последняя проверка не удалась", true]);
  } else if (ok) {
    const n = daysBetween(ok, now), t = yerevan(ok);
    lines.push([`проверено ${n === 0 ? "сегодня" : n === 1 ? "вчера" : t.dm} в ${t.hm}`, false]);
  } else {
    lines.push(["ещё не проверялось", false]);
  }
  return lines;
}
function renderPlaces() {
  if (!ui.data) return;
  const now = new Date();
  const chip = (id, ...kids) => {
    const button = el("button", { type: "button", class: "place", "aria-pressed": String(ui.place === id) }, ...kids);
    button.addEventListener("click", () => {
      ui.place = ui.place === id ? null : id;
      renderPlaces();
      render();
    });
    return button;
  };
  $("places").replaceChildren(
    chip(null, el("b", null, "Все места")),
    ...ui.data.places.filter((p) => p.section === ui.section).map((p) => chip(p.id, el("b", null, p.name),
      ...placeStatus(p, now).map(([text, warn]) => el("span", { class: warn ? "warn" : null }, text)))));
}
function renderHeader() {
  const now = new Date();
  const generated = parseTime(ui.data.generated_at);
  const hours = generated ? Math.floor((now - generated) / 36e5) : null;
  $("updated").textContent = generated == null ? "время обновления неизвестно"
    : hours < 1 ? "обновлено меньше часа назад"
    : hours < 48 ? `обновлено ${hours} ч назад`
    : `обновлено ${days(Math.floor(hours / 24))} назад`;
  const banner = $("stale-banner");
  banner.hidden = generated != null && hours < STALE_HOURS;
  if (generated) {
    const t = yerevan(generated);
    banner.textContent = `⚠️ Данные устарели: последнее обновление ${t.dm} в ${t.hm}.`;
  }
  const started = parseTime(ui.data.started_at);
  if (started) $("legend-star").textContent = `⭐ — возможно, впервые в Ереване (с тех пор, как следим, с ${yerevan(started).dm})`;
  if (ui.data.hot_rating != null) $("legend-hot").textContent = `🔥 — рейтинг Untappd от ${ui.data.hot_rating}`;
}
function setSection(section) {
  ui.section = section;
  ui.place = null;
  for (const b of document.querySelectorAll(".tab")) b.setAttribute("aria-pressed", String(b.dataset.section === section));
  renderPlaces();
  render();
}

async function load() {
  try {
    const res = await fetch("./data.json", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (!data || !Array.isArray(data.rows) || !Array.isArray(data.places)) throw new Error("bad data.json");
    ui.data = data;
    ui.places = new Map(data.places.map((p) => [p.id, p]));
  } catch (err) {
    console.error(err);
    $("updated").textContent = "нет данных";
    $("rows").replaceChildren(el("p", { class: "empty" }, "Не удалось загрузить данные. Обновите страницу через минуту."));
    return;
  }
  renderHeader();
  renderPlaces();
  render();
}

for (const b of document.querySelectorAll(".tab")) b.addEventListener("click", () => setSection(b.dataset.section));
$("search").addEventListener("input", render);
$("sort").addEventListener("change", render);
$("only-new").addEventListener("change", render);
wide.addEventListener("change", render);
load();
</script>
</body>
</html>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `timeout 120 .venv/bin/pytest tests/test_site_page.py -v`
Expected: `14 passed`

- [ ] **Step 5: Проверить страницу в браузере без данных**

Run: `python3 -m http.server 8000 --bind 127.0.0.1 -d site` и открыть http://127.0.0.1:8000/
Expected: без `site/data.json` в шапке «нет данных», вместо списка «Не удалось загрузить данные. Обновите страницу через минуту.», в консоли одна ошибка `HTTP 404`. Остановить сервер (Ctrl+C). Когда прогон создаст `site/data.json`, повторить проверку при ширине 360 px (карточки, без горизонтальной прокрутки) и 1280 px (таблица).

- [ ] **Step 6: Commit**

```bash
git add tests/test_site_page.py site/index.html
git commit -m "feat: add static site page reading data.json"
```

---

### Task 21: Оркестрация прогона: `python -m taps run`

**Files:**
- Create: `taps/run.py`
- Create: `taps/__main__.py`
- Test: `tests/test_run.py`

**Interfaces:**
- Consumes: `load_config`, `ConfigError`, `Config`, `Place`, `Brewery` (config); `load_corrections`, `CorrectionsLoad` (corrections); `load_state`, `save_state`, `empty_state`, `apply_aliases`, `prune`, `State` (state); `Http`, `UntappdClient`, `untappd_due`, `playwright_fetcher`, `PageFetcher`, `FetchError`, `HttpResponse` (fetch); `fetch_menu`, `fetch_brewery_checkins`, `fetch_brewery_list`, `fetch_venue_checkins`, `fetch_beercity`, `fetch_yerevan_city`, `fetch_parma`, `fetch_buyam`, `manual_result` (sources); `merge_results`, `MergeOutcome` (rules); `build_site_data`, `write_site_data` (site_data); `drop_stale_events`, `is_due`, `build_digest`, `mark_sent`, `rollback` (digest); `send_message`, `SendOutcome`, `Alerter` (telegram); `pull_ff`, `commit_and_push`, `GitError` (gitsync); `iso`, `parse_iso`, `yerevan_date`, `utcnow` (timeutil).
- Produces: `Deps(http, untappd_fetcher, send, pull, push, sleep)` (все поля со значениями по умолчанию), `run(repo: Path, now: datetime, env: Mapping[str, str], deps: Deps, dry_run: bool = False, no_digest: bool = False) -> int`, `main(argv: Sequence[str] | None = None) -> int`; вспомогательные `collect_untappd`, `collect_shops`, `update_alerts`, `digest_alerts`; `taps/__main__.py` → `raise SystemExit(main())`. Коды выхода: 0 — прогон завершён; 1 — не прошёл git pull или push, ничего не отправлено; 2 — прогон невозможен: нет переменных окружения, сломаны `places.yaml` или `state.json`, `corrections.yaml` не читается и снимка нет.

Одна команда `python -m taps run` связывает все модули в порядке из контракта (раздел run.py) и спеки §3. Сначала pull, затем чтение config, state и corrections. Untappd собирается не чаще раза в 20 часов (меню → чекины пивоварен → чекины заведений → 1–2 списка сортов), магазины и buy.am — в каждом прогоне, «со слов» — тоже. Дальше merge, prune, предупреждения, `site/data.json` и протокол сводки из §7: отметка, push, отправка, откат при явном отказе. Сбои обрабатываются по §10: первая проверка Cloudflare останавливает весь Untappd, сбой одного источника не мешает остальным. Тесты интеграционные: настоящие парсеры и правила на фикстурах, поддельные веб-запросы, Telegram и git.

- [ ] **Step 1: Write the failing test**

Create `tests/test_run.py`:
```python
"""Integration tests for one run: real sources and rules on fixtures, fake web, Telegram and git."""
import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from taps import run as run_mod
from taps.config import ConfigError
from taps.fetch import FetchError, HttpResponse
from taps.gitsync import commit_and_push, pull_ff
from taps.model import SourceResult
from taps.rules import MergeOutcome
from taps.run import Deps, main, run, update_alerts
from taps.sources.parma import fetch_parma as real_fetch_parma
from taps.state import empty_state, load_state, save_state
from taps.telegram import Alerter, SendOutcome, send_message
from taps.timeutil import iso
from tests.helpers import fixture_json, fixture_text

ROOT = Path(__file__).parent.parent
NOW = datetime(2026, 9, 24, 14, 17, tzinfo=timezone.utc)       # Thu 18:17 in Yerevan
NEXT_EVENING = NOW + timedelta(days=1)
ENV = {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "-100chat", "TELEGRAM_ADMIN_CHAT_ID": "42",
       "SITE_URL": "https://example.github.io/yerevan-taps/"}

PLACES_YAML = """
places:
  - {id: gargoyle, name: Gargoyle Bar, kind: bar, sources: {untappd_menu: {slug: gargoyle-bar, venue_id: 12252462}}}
  - {id: beatles, name: Beatles Pub, kind: bar, sources: {untappd_menu: {slug: beatles-pub-yerevan, venue_id: 2162817}}}
  - id: dargett-brewpub
    name: Dargett Brewpub
    kind: brewpub
    brewery_id: 265165
    brewery_name: Dargett
    untappd_venue_id: 4640403
    sources: {buyam: {url: "https://buy.am/en/restaurants/dargett"}}
  - {id: craft-story, name: Craft Story, kind: bar, sources: {untappd_checkins: {slug: craft-story, venue_id: 12281551}}}
  - {id: beer-city, name: Beer City, kind: shop, sources: {beercity: {}}}
  - {id: yerevan-city, name: Yerevan City, kind: shop, sources: {yerevan_city: {}}}
  - {id: parma, name: Parma, kind: shop, sources: {parma: {}}}
breweries:
  - {id: dargett, name: Dargett, brewery_id: 265165, slug: dargett-brewery}
"""
SOURCE_KEYS = {"untappd_menu:gargoyle", "untappd_menu:beatles", "untappd_brewery:265165",
               "untappd_checkins:craft-story", "beercity:beer-city", "yerevan_city:yerevan-city",
               "parma:parma", "buyam:dargett-brewpub", "manual"}

# --- fake Untappd (pages by URL, as the Playwright fetcher returns them) ------------------------

GARGOYLE = "https://untappd.com/v/gargoyle-bar/12252462"
BEATLES = "https://untappd.com/v/beatles-pub-yerevan/2162817"
BEATLES_HTML = fixture_text("untappd/beatles_menu.html")
EXTRA_LI = ('<li class="menu-item" id="beer"><div class="beer-details"><h5>'
            '<a href="/b/zagovor-black-sails/999001">Black Sails</a><em>Imperial Stout</em></h5>'
            '<h6><span>11% ABV • <a href="/w/zagovor/777">Zagovor</a></span>'
            '<div class="caps small" data-rating="4.12"></div></h6></div></li>')
FIRST_LI = '<li class="menu-item" id="beer">'
UNTAPPD_PAGES = {
    GARGOYLE: fixture_text("untappd/gargoyle_menu.html"),
    GARGOYLE + "?menu_id=203568": fixture_text("untappd/gargoyle_menu_tab.html"),
    BEATLES: BEATLES_HTML,
    "https://untappd.com/brewery/265165": fixture_text("untappd/dargett_brewery.html"),
    "https://untappd.com/v/craft-story/12281551": fixture_text("untappd/craftstory_checkins.html"),
}
WITH_EXTRA_BEER = {**UNTAPPD_PAGES, BEATLES: BEATLES_HTML.replace(FIRST_LI, EXTRA_LI + FIRST_LI, 1)}
CHALLENGE = HttpResponse(403, {"cf-mitigated": "challenge", "server": "cloudflare"},
                         fixture_text("untappd/cloudflare_challenge.html"))


class FakeUntappd:
    def __init__(self, pages=UNTAPPD_PAGES, challenge=False):
        self.pages, self.challenge = pages, challenge
        self.started = self.closed = 0
        self.urls = []

    def __call__(self):
        self.started += 1
        return self.fetch_page, self.close

    def fetch_page(self, url):
        self.urls.append(url)
        if self.challenge:
            return CHALLENGE
        return HttpResponse(200, {}, self.pages[url]) if url in self.pages else HttpResponse(404, {}, "")

    def close(self):
        self.closed += 1


# --- fake shops (Http by URL) -------------------------------------------------------------------

def _single_page(listing_json, page, pages):
    return listing_json.replace(f"Page <b>{page}</b> of {pages}", "Page <b>1</b> of 1")


def _renumber(html, prefix):
    return re.sub(r"_(\d+)(?=[\"'])", lambda m: f"_{prefix}{m.group(1)}", html)


PARMA_P1 = fixture_text("parma/list_p1.html")
BUYAM_NAMES = ["Bohemian Pilsner", "Bavarian Weizen", "Oatmeal Stout", "Munich Lager", "Vienna Lager",
               "Biere Blanche", "Apricot Ale", "Belgian Tripel", "American Pale Ale", "Session IPA",
               "India Pale Ale", "Black IPA", "Apple Cider", "Cherry Ale", "Baltic Porter", "Imperial IPA"]
BUYAM_LISTING = json.dumps({"code": 200, "data": {"totalCount": 16, "items": [
    {"id": 174894 + i, "name": f"Draught beer Dargett {n} 1l", "nameEn": f"Draught beer Dargett {n} 1l",
     "basePrice": 2500} for i, n in enumerate(BUYAM_NAMES)]}})
SHOP_PAGES = {
    # Beer City: one page per category (18 beers), counters rewritten to "1 of 1"
    "https://www.beer-city.am/en/catalog/sshalcavac-garejur/?sorting=-id&page=1":
        _single_page(fixture_text("beercity/list_bottles_p1.json"), 1, 23),
    "https://www.beer-city.am/en/catalog/lcnovi-garejur/?sorting=-id&page=1":
        _single_page(fixture_text("beercity/list_draft_last.json"), 3, 3),
    # Parma: pages 2-3 are renumbered copies of page 1 (187 cards)
    **{f"https://parma.am/en/product/category?slug=beer&available=false&page={n}": html for n, html in
       enumerate([PARMA_P1, _renumber(PARMA_P1, "2"), _renumber(PARMA_P1, "3"),
                  fixture_text("parma/list_p4.html")], 1)},
    "https://buy.am/en/restaurants/dargett": fixture_text("buyam/dargett.html"),
    "https://api.buy.am/products/listing?skip=0&s=1162&f=9890&take=100": BUYAM_LISTING,
}
PRODUCT_PAGE = re.compile(r"https://(www\.beer-city\.am/en/products/|parma\.am/en/product/product\?)")
YC_POSTS = {
    "https://apishopv2.yerevan-city.am/api/Product/GetByCategory": fixture_json("yerevan_city/by_category.json"),
    "https://apishopv2.yerevan-city.am/api/Product/Search": fixture_json("yerevan_city/search.json"),
}


class FakeHttp:
    def __init__(self):
        self.urls = []

    def get(self, url, headers=None):
        self.urls.append(url)
        if url in SHOP_PAGES:
            return HttpResponse(200, {}, SHOP_PAGES[url])
        if PRODUCT_PAGE.match(url):
            return HttpResponse(200, {}, "<html><body></body></html>")   # product pages: fields unknown
        raise FetchError("http", f"404 {url}")

    def post_json(self, url, payload, headers=None):
        self.urls.append(url)
        return YC_POSTS[url]


# --- fake Telegram and git ----------------------------------------------------------------------

class World:
    """A repo folder plus recording fakes; outcomes are scripted per call."""

    def __init__(self, tmp_path, untappd=None, send=(), push=()):
        self.repo = tmp_path
        (tmp_path / "site").mkdir(exist_ok=True)
        (tmp_path / "places.yaml").write_text(PLACES_YAML, encoding="utf-8")
        shutil.copy(ROOT / "corrections.yaml", tmp_path / "corrections.yaml")
        self.untappd = untappd or FakeUntappd()
        self.http = FakeHttp()
        self.sends, self.pushes, self.pulls = [], [], []
        self.send_outcomes, self.push_results = list(send), list(push)

    def send(self, token, chat_id, text, button=None):
        self.sends.append({"token": token, "chat": chat_id, "text": text, "button": button})
        return self.send_outcomes.pop(0) if self.send_outcomes else SendOutcome("sent")

    def push(self, repo, paths, message):
        state = json.loads((repo / "state.json").read_text(encoding="utf-8"))
        self.pushes.append({"paths": list(paths), "state": state})
        return self.push_results.pop(0) if self.push_results else True

    def deps(self):
        return Deps(http=self.http, untappd_fetcher=self.untappd, send=self.send,
                    pull=self.pulls.append, push=self.push, sleep=lambda s: None)

    def run(self, now, **kw):
        return run(self.repo, now, ENV, self.deps(), **kw)

    def state(self):
        return load_state(self.repo / "state.json", NOW)

    def edit_state(self, change):
        state = self.state()
        change(state)
        save_state(self.repo / "state.json", state)

    def next_run(self, untappd=None, send=(), push=()):
        """Fresh fakes for the next run in the same repo."""
        self.untappd = untappd or FakeUntappd()
        self.http = FakeHttp()
        self.sends, self.pushes, self.pulls = [], [], []
        self.send_outcomes, self.push_results = list(send), list(push)


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def first_run(world):
    assert world.run(NOW) == 0
    world.next_run(untappd=FakeUntappd(WITH_EXTRA_BEER))


# --- tests --------------------------------------------------------------------------------------

def test_first_run_is_silent_and_saves_state_and_site(world):
    assert world.run(NOW) == 0

    state = world.state()
    assert world.pulls == [world.repo]
    assert world.sends == []
    assert [p["paths"] for p in world.pushes] == [["state.json"]]
    assert set(state.sources) == SOURCE_KEYS
    assert all(state.sources[k].baseline_done and state.sources[k].last_ok == iso(NOW) for k in SOURCE_KEYS)
    pairs = [rec for recs in state.pairs.values() for rec in recs.values()]
    assert len(pairs) > 300 and {rec.notified_at for rec in pairs} == {"baseline"}
    assert "u:4473" in state.pairs["beatles"]                      # Guinness Draught from the Beatles menu
    assert len(state.pairs["dargett-brewpub"]) == 16
    assert state.untappd.last_attempt == iso(NOW)
    assert world.untappd.started == world.untappd.closed == 1
    assert state.alerts == {}
    assert state.corrections_snapshot is not None
    data = json.loads((world.repo / "site" / "data.json").read_text(encoding="utf-8"))
    assert data["generated_at"] == iso(NOW)
    assert [p["id"] for p in data["places"]][:2] == ["gargoyle", "beatles"]
    assert any(r["name"] == "Guinness Draught" for r in data["rows"])


def test_next_evening_new_beer_goes_to_admin_as_preview(world):
    first_run(world)
    assert world.run(NEXT_EVENING) == 0

    assert len(world.sends) == 1
    sent = world.sends[0]
    assert sent["chat"] == ENV["TELEGRAM_ADMIN_CHAT_ID"] and sent["token"] == "tok"
    assert sent["button"] == ("Открыть список", ENV["SITE_URL"])
    assert "Black Sails" in sent["text"] and "Beatles Pub" in sent["text"]
    state = world.state()
    assert state.digest.sent_count == 1
    assert state.digest.last_sent_at == iso(NEXT_EVENING) and state.digest.last_sent_date == "2026-09-25"
    assert state.pairs["beatles"]["u:999001"].notified_at == iso(NEXT_EVENING)
    # the mark was pushed before sending
    assert world.pushes[0]["state"]["digest"]["sent_count"] == 1
    assert len(world.pushes) == 1


def test_second_run_same_evening_sends_nothing(world):
    first_run(world)
    assert world.run(NEXT_EVENING) == 0
    later = NEXT_EVENING + timedelta(hours=2)

    def add_pending(state):
        rec = state.pairs["parma"][next(iter(state.pairs["parma"]))]
        rec.event_at, rec.notified_at = iso(later), None
    world.edit_state(add_pending)
    world.next_run()
    assert world.run(later) == 0

    assert world.sends == []
    assert world.untappd.started == 0            # Untappd was fetched 2 h ago
    assert world.state().digest.sent_count == 1


def test_third_digest_goes_to_the_chat(world):
    first_run(world)
    world.edit_state(lambda s: setattr(s.digest, "sent_count", 2))
    assert world.run(NEXT_EVENING) == 0

    assert [s["chat"] for s in world.sends] == [ENV["TELEGRAM_CHAT_ID"]]
    assert world.state().digest.sent_count == 3


def test_group_chat_digest_excludes_alert_text_even_with_a_failing_source(world, monkeypatch):
    first_run(world)
    world.edit_state(lambda s: setattr(s.digest, "sent_count", 2))

    def failing_parma(http, place, known, now, ba):
        return SourceResult(key=f"parma:{place.id}", source="parma", ok=False, error="network", place_id=place.id)
    monkeypatch.setattr(run_mod, "fetch_parma", failing_parma)
    world.next_run(untappd=FakeUntappd(WITH_EXTRA_BEER))

    assert world.run(NEXT_EVENING) == 0

    chat_sends = [s for s in world.sends if s["chat"] == ENV["TELEGRAM_CHAT_ID"]]
    admin_sends = [s for s in world.sends if s["chat"] == ENV["TELEGRAM_ADMIN_CHAT_ID"]]
    assert len(chat_sends) == 1 and "⚠️" not in chat_sends[0]["text"]
    assert len(admin_sends) == 1 and "⚠️" in admin_sends[0]["text"] and "parma" in admin_sends[0]["text"]


def test_stale_pending_event_is_dropped_silently_in_a_morning_run(world):
    first_run(world)
    morning = datetime(2026, 9, 25, 6, 17, tzinfo=timezone.utc)   # Fri 10:17 in Yerevan
    stale_since = morning - timedelta(days=4)
    picked = {}

    def add_stale_pending(state):
        beer_key = next(iter(state.pairs["parma"]))
        rec = state.pairs["parma"][beer_key]
        rec.event_at, rec.notified_at = iso(stale_since), None
        picked["beer_key"] = beer_key
    world.edit_state(add_stale_pending)
    world.next_run()

    assert world.run(morning) == 0

    assert world.sends == []
    rec = world.state().pairs["parma"][picked["beer_key"]]
    assert rec.notified_at == iso(morning)      # dropped, not sent
    assert rec.event_at == iso(stale_since)


def test_renamed_place_id_starts_a_fresh_silent_baseline(world):
    first_run(world)
    world.next_run()
    renamed = PLACES_YAML.replace("id: beer-city, name: Beer City", "id: beer-city-2, name: Beer City")
    (world.repo / "places.yaml").write_text(renamed, encoding="utf-8")

    assert world.run(NEXT_EVENING) == 0

    assert world.sends == []
    state = world.state()
    assert "beercity:beer-city-2" in state.sources and state.sources["beercity:beer-city-2"].baseline_done
    assert state.pairs["beer-city-2"] and all(
        rec.notified_at == "baseline" for rec in state.pairs["beer-city-2"].values())
    assert "beer-city" in state.pairs   # old pairs untouched, not yet pruned


def test_failed_push_sends_nothing_and_exits_1(world):
    first_run(world)
    world.push_results = [False]
    assert world.run(NEXT_EVENING) == 1
    assert world.sends == []
    assert len(world.pushes) == 1


@pytest.mark.parametrize("outcome, alert", [
    (SendOutcome("rejected", "Bad Request: chat not found"), "Telegram не принял сводку (Bad Request: chat not found)"),
    (SendOutcome("rejected", "Bad Request: group chat was upgraded", migrate_to_chat_id=-100777), "новый id: -100777"),
])
def test_rejected_digest_rolls_back_pushes_and_alerts_admin(world, outcome, alert):
    first_run(world)
    world.send_outcomes = [outcome]
    assert world.run(NEXT_EVENING) == 0

    state = world.state()
    assert (state.digest.sent_count, state.digest.last_sent_at, state.digest.last_sent_date) == (0, None, None)
    assert state.pairs["beatles"]["u:999001"].notified_at is None      # the event waits for the next digest
    assert world.pushes[0]["state"]["digest"]["sent_count"] == 1
    assert world.pushes[1]["state"]["digest"]["sent_count"] == 0       # the rollback was pushed
    assert world.pushes[1]["state"]["pairs"]["beatles"]["u:999001"]["notified_at"] is None
    assert [s["chat"] for s in world.sends] == [ENV["TELEGRAM_ADMIN_CHAT_ID"]] * 2
    assert "Black Sails" in world.sends[0]["text"] and alert in world.sends[1]["text"]
    assert world.pushes[-1]["state"]["alerts"] == state.alerts and "digest" in state.alerts


def test_rejected_digest_with_failed_rollback_push_exits_1_and_next_run_does_not_resend(world):
    first_run(world)

    class GiveUpPush:
        """Simulates gitsync's give-up-and-reset (fix for commit_and_push): a push whose message
        names the rollback fails and restores local state.json to the last one that did land."""
        def __init__(self):
            self.remote_state = None

        def __call__(self, repo, paths, message):
            content = (repo / "state.json").read_bytes()
            if "отменена" in message:
                (repo / "state.json").write_bytes(self.remote_state)
                return False
            self.remote_state = content
            return True

    world.next_run(untappd=FakeUntappd(WITH_EXTRA_BEER),
                   send=[SendOutcome("rejected", "Bad Request: chat not found")])
    deps = Deps(http=world.http, untappd_fetcher=world.untappd, send=world.send,
               pull=world.pulls.append, push=GiveUpPush(), sleep=lambda s: None)

    assert run(world.repo, NEXT_EVENING, ENV, deps) == 1
    assert len(world.sends) == 1   # only the rejected digest attempt: the recovery push failed first

    world.next_run()
    assert world.run(NEXT_EVENING + timedelta(hours=1)) == 0
    assert world.sends == []       # no resend: the first (successfully pushed) mark stands


def test_broken_corrections_with_snapshot_falls_back_silently_and_exits_0(world):
    first_run(world)
    good_snapshot = world.state().corrections_snapshot
    world.next_run()
    (world.repo / "corrections.yaml").write_text("sightings: [\n", encoding="utf-8")

    assert world.run(NEXT_EVENING) == 0

    state = world.state()
    assert state.corrections_snapshot == good_snapshot   # kept as is: the broken file was never stored
    assert any(k.startswith("corrections:") for k in state.alerts)   # a soft warning, not a fatal alert


def test_unknown_digest_outcome_keeps_the_mark_and_alerts(world):
    first_run(world)
    world.send_outcomes = [SendOutcome("unknown", "ReadTimeout: timed out")]
    assert world.run(NEXT_EVENING) == 0

    state = world.state()
    assert state.digest.sent_count == 1
    assert state.pairs["beatles"]["u:999001"].notified_at == iso(NEXT_EVENING)
    assert len(world.sends) == 2
    assert "сводка, возможно, не дошла (ReadTimeout: timed out)" in world.sends[1]["text"]
    assert world.sends[1]["chat"] == ENV["TELEGRAM_ADMIN_CHAT_ID"]


def test_untappd_is_not_fetched_before_20_hours(world):
    first_run(world)
    world.edit_state(lambda s: setattr(s.untappd, "last_attempt", iso(NEXT_EVENING - timedelta(hours=5))))
    assert world.run(NEXT_EVENING) == 0

    state = world.state()
    assert world.untappd.started == 0 and world.untappd.urls == []
    assert state.sources["untappd_menu:beatles"].last_ok == iso(NOW)          # not a failure either
    assert state.sources["untappd_menu:beatles"].fail_streak == 0
    assert state.sources["parma:parma"].last_ok == iso(NEXT_EVENING)          # shops ran
    assert world.sends == []                                                  # the new beer was not seen


def test_first_cloudflare_challenge_stops_untappd_but_not_shops(world):
    world.untappd = FakeUntappd(challenge=True)
    assert world.run(NOW) == 0

    state = world.state()
    assert world.untappd.urls == [GARGOYLE]                                   # nothing after the challenge
    assert state.sources["untappd_menu:gargoyle"].last_error == "cloudflare"
    for key in ("untappd_menu:beatles", "untappd_brewery:265165", "untappd_checkins:craft-story"):
        assert (state.sources[key].last_error, state.sources[key].fail_streak) == ("blocked", 1)
    for key in ("beercity:beer-city", "yerevan_city:yerevan-city", "parma:parma", "buyam:dargett-brewpub"):
        assert state.sources[key].baseline_done
    assert state.untappd.last_attempt == iso(NOW)                             # Untappd did answer
    assert len(world.sends) == 1 and world.sends[0]["chat"] == ENV["TELEGRAM_ADMIN_CHAT_ID"]
    assert world.sends[0]["text"].count("\n• ") == 1 and "Cloudflare" in world.sends[0]["text"]


def test_sources_out_of_untappd_budget_are_skipped_without_failure(world):
    state = empty_state(NOW)
    state.untappd.pages_today, state.untappd.pages_date = 29, "2026-09-24"   # 1 of 30 pages left
    save_state(world.repo / "state.json", state)
    assert world.run(NOW) == 0

    state = world.state()
    assert world.untappd.urls == [GARGOYLE]            # its second tab and everything after: no budget
    assert not any(k.startswith("untappd") for k in state.sources)
    assert state.untappd.pages_today == 30 and state.alerts == {} and world.sends == []


def test_broken_corrections_without_snapshot_stops_with_exit_2(world):
    (world.repo / "corrections.yaml").write_text("sightings: [\n", encoding="utf-8")
    assert world.run(NOW) == 2

    # the (empty) state loaded fine, so it is used for alert dedup and persisted
    assert (world.repo / "state.json").exists()
    assert not (world.repo / "site" / "data.json").exists()
    assert world.untappd.started == 0 and world.http.urls == []
    assert [p["paths"] for p in world.pushes] == [["state.json"]]
    assert len(world.sends) == 1 and world.sends[0]["chat"] == ENV["TELEGRAM_ADMIN_CHAT_ID"]
    assert "corrections.yaml не читается" in world.sends[0]["text"]


@pytest.mark.parametrize("break_repo", [
    lambda repo: (repo / "corrections.yaml").write_text("sightings: [\n", encoding="utf-8"),
    lambda repo: (repo / "state.json").write_text("{not json", encoding="utf-8"),
    lambda repo: (repo / "places.yaml").write_text("not: [a, valid\n", encoding="utf-8"),
], ids=["broken_corrections_no_snapshot", "corrupt_state_json", "broken_places_yaml"])
def test_fatal_alert_is_sent_once_per_series_across_runs(world, break_repo):
    break_repo(world.repo)
    assert world.run(NOW) == 2
    assert world.run(NOW) == 2   # same World: no next_run(), so world.sends accumulates across both calls

    assert len(world.sends) == 1
    assert world.sends[0]["chat"] == ENV["TELEGRAM_ADMIN_CHAT_ID"]


def test_dry_run_prints_no_digest_and_touches_no_git_or_telegram(world, capsys):
    assert world.run(NOW, dry_run=True) == 0

    out = capsys.readouterr().out
    assert out.splitlines()[0] == "нет сводки"
    assert '"rows"' in out
    assert world.pulls == world.pushes == world.sends == []
    assert not (world.repo / "state.json").exists()
    assert (world.repo / "site" / "data.json").exists()


def test_dry_run_prints_the_digest_and_the_site_data_without_marking_it(world, capsys):
    first_run(world)
    before = (world.repo / "state.json").read_bytes()
    capsys.readouterr()
    assert world.run(NEXT_EVENING, dry_run=True) == 0

    out = capsys.readouterr().out
    assert "Black Sails" in out and "Новое в Ереване" in out
    assert '"rows"' in out
    assert world.pulls == world.pushes == world.sends == []
    assert (world.repo / "state.json").read_bytes() == before


def test_no_digest_flag_skips_the_digest(world):
    first_run(world)
    assert world.run(NEXT_EVENING, no_digest=True) == 0
    assert world.sends == []
    assert world.state().pairs["beatles"]["u:999001"].notified_at is None


def test_missing_env_stops_before_anything(world):
    assert run(world.repo, NOW, {"TELEGRAM_BOT_TOKEN": "tok"}, world.deps()) == 2
    assert world.pulls == [] and world.http.urls == [] and not (world.repo / "state.json").exists()


def test_alert_series_dedupes_failures_by_key_and_resolves_on_success():
    """A failing source's error text may change (network -> http -> empty) without starting a new alert."""
    state = empty_state(NOW)
    a1 = Alerter(state)
    update_alerts(a1, state, MergeOutcome(failed=[("parma:parma", "network")]), None, [])
    assert a1.pending_text() is not None and "network" in a1.pending_text()

    a2 = Alerter(state)   # next run, same state
    update_alerts(a2, state, MergeOutcome(failed=[("parma:parma", "http")]), None, [])
    assert a2.pending_text() is None

    a3 = Alerter(state)
    update_alerts(a3, state, MergeOutcome(failed=[("parma:parma", "empty")]), None, [])
    assert a3.pending_text() is None

    a4 = Alerter(state)   # the source recovers: resolved
    update_alerts(a4, state, MergeOutcome(ok=["parma:parma"]), None, [])
    assert "source:parma:parma" not in state.alerts

    a5 = Alerter(state)   # a fresh failure after recovery alerts again
    update_alerts(a5, state, MergeOutcome(failed=[("parma:parma", "network")]), None, [])
    assert a5.pending_text() is not None


def test_trip_alerts_once_then_accept_alerts_once():
    state = empty_state(NOW)
    a1 = Alerter(state)
    update_alerts(a1, state, MergeOutcome(tripped=[("beercity:beer-city", "shrink")]), None, [])
    assert a1.pending_text() is not None

    a2 = Alerter(state)   # same trip reason next run: still one alert for the series
    update_alerts(a2, state, MergeOutcome(tripped=[("beercity:beer-city", "shrink")]), None, [])
    assert a2.pending_text() is None

    a3 = Alerter(state)   # accepted after 3 runs: a fresh, one-time acceptance message
    update_alerts(a3, state, MergeOutcome(ok=["beercity:beer-city"], accepted=["beercity:beer-city"]), None, [])
    assert a3.pending_text() is not None and "принял" in a3.pending_text()


def test_main_parses_args_and_env(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(run_mod, "run", lambda repo, now, env, deps, dry_run=False, no_digest=False:
                        calls.append((repo, now, env, deps, dry_run, no_digest)) or 7)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100chat")

    assert main(["run", "--dry-run", "--no-digest", "--repo", str(tmp_path)]) == 7
    repo, now, env, deps, dry_run, no_digest = calls[0]
    assert (repo, dry_run, no_digest) == (tmp_path, True, True)
    assert env["TELEGRAM_CHAT_ID"] == "-100chat" and now.tzinfo is not None
    assert (deps.send, deps.pull, deps.push) == (send_message, pull_ff, commit_and_push)

    assert main(["run"]) == 7
    assert calls[1][0] == Path(".") and calls[1][4:] == (False, False)
    with pytest.raises(SystemExit):
        main([])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 120 .venv/bin/pytest tests/test_run.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taps.run'`

- [ ] **Step 3: Write minimal implementation**

Create `taps/run.py`:
```python
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
ENV_KEYS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_ADMIN_CHAT_ID", "SITE_URL")
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
        alerter.alert("untappd:browser", f"не запустился браузер для Untappd: {type(e).__name__}: {e}")
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
    """Dedup a fatal alert when no state.json can be trusted (broken places.yaml/state.json, failed
    pull): the hash lives in an untracked marker file instead, since nothing else is safe to persist."""
    path = repo / FATAL_FILE
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
    prev = path.read_text(encoding="utf-8").strip() if path.exists() else None
    if prev != digest:
        send(text)
    path.write_text(digest + "\n", encoding="utf-8")


def run(repo: Path, now: datetime, env: Mapping[str, str], deps: Deps, dry_run: bool = False,
        no_digest: bool = False) -> int:
    """0 done; 1 git pull/push failed (nothing sent); 2 cannot run (settings, config, state, corrections)."""
    missing = [k for k in ENV_KEYS if not env.get(k)]
    if missing and not dry_run:
        print(f"не заданы переменные окружения: {', '.join(missing)}", file=sys.stderr)
        return 2

    def to_admin(text: str) -> SendOutcome:
        return deps.send(env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_ADMIN_CHAT_ID"], text)

    def fatal(text: str, code: int, state: State | None = None) -> int:
        """Dedup once per series: with a loadable state use its own alerts (persisted); without one
        (broken places.yaml/state.json, or a failed pull) fall back to an untracked marker file."""
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
    try:
        config = load_config(repo / "places.yaml")
    except ConfigError as e:
        return fatal(str(e), 2)
    try:   # before corrections: the corrections snapshot lives in the state
        state = load_state(repo / STATE_FILE, now)
    except ValueError as e:
        return fatal(str(e), 2)
    load = load_corrections(repo / "corrections.yaml", state.corrections_snapshot, set(config.places))
    if load.corrections is None:
        return fatal(load.errors[0], 2, state)
    corrections = load.corrections
    if load.raw is not None:
        state.corrections_snapshot = load.raw

    alerter = Alerter(state)
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
    alerter.flush(to_admin)
    # alert hashes changed after the last push: push them too
    if state.to_dict() != pushed and not _save_push(repo, state, deps, f"state {iso(now)}: предупреждения"):
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m taps")
    commands = parser.add_subparsers(dest="command", required=True)
    cmd = commands.add_parser("run", help="один прогон: сбор, сайт, сводка")
    cmd.add_argument("--dry-run", action="store_true", help="ничего не отправлять и не коммитить")
    cmd.add_argument("--no-digest", action="store_true", help="не слать сводку")
    cmd.add_argument("--repo", type=Path, default=Path("."), help="папка репозитория")
    args = parser.parse_args(argv)
    return run(args.repo, utcnow(), os.environ, Deps(), dry_run=args.dry_run, no_digest=args.no_digest)
```

Create `taps/__main__.py`:
```python
from taps.run import main

raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `timeout 120 .venv/bin/pytest tests/test_run.py -v`
Expected: `27 passed`

- [ ] **Step 5: Commit**

```bash
git add tests/test_run.py taps/run.py taps/__main__.py
git commit -m "feat: orchestrate a full run with the digest send protocol"
```

---

### Task 22: GitHub Actions, README и чек-лист запуска

**Files:**
- Create: `.github/workflows/run.yml`
- Create: `.github/workflows/tests.yml`
- Create: `README.md`
- Test: `tests/test_workflows.py`

**Interfaces:**
- Consumes: CLI `python -m taps run [--dry-run] [--no-digest]` (`taps/__main__.py` → `taps.run.main`); переменные окружения, которые читает `taps.run.main`: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_ADMIN_CHAT_ID`, `SITE_URL`; каталог `site/` (`site/index.html` + `site/data.json` из `taps.site_data.write_site_data`, файл в `.gitignore`); `taps.gitsync.commit_and_push` (push `origin HEAD:main` через учётные данные checkout); форматы `places.yaml` (`taps.config.load_config`) и `corrections.yaml` (`taps.corrections.load_corrections`); `requirements.txt`.
- Produces: workflow `taps` (`.github/workflows/run.yml`), workflow `tests` (`.github/workflows/tests.yml`), `README.md`. Кода, от которого зависят другие задачи, нет.

Workflow `taps` запускает прогон дважды в день (10:17 и 18:17 по Еревану) и вручную, никогда не идёт параллельно сам с собой, берёт вершину `main` и публикует `site/` на GitHub Pages в том же job, даже если прогон упал после записи `data.json` (спека §3, §7 «Гарантии», §10). Workflow `tests` гоняет pytest на каждый push и PR без браузера. README на русском объясняет проект, правку `places.yaml` / `corrections.yaml`, секреты и файлы (спека §1, §5, §9, §12).

- [ ] **Step 1: Write the failing test**

Create `tests/test_workflows.py`:
```python
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
TELEGRAM_SECRETS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_ADMIN_CHAT_ID")


def load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def triggers(wf: dict):
    # PyYAML (YAML 1.1) parses the bare key `on` as boolean True
    return wf[True] if True in wf else wf["on"]


def only_job(wf: dict) -> dict:
    jobs = wf["jobs"]
    assert len(jobs) == 1
    return next(iter(jobs.values()))


def step_index(steps: list[dict], *, uses: str | None = None, run: str | None = None) -> int:
    for i, step in enumerate(steps):
        if uses and step.get("uses", "").startswith(uses):
            return i
        if run and run in step.get("run", ""):
            return i
    raise AssertionError(f"step not found: uses={uses!r} run={run!r}")


def test_run_schedule_and_manual_trigger():
    wf = load("run.yml")
    assert wf["name"] == "taps"
    on = triggers(wf)
    assert [s["cron"] for s in on["schedule"]] == ["17 6 * * *", "17 14 * * *"]
    assert "workflow_dispatch" in on
    assert "push" not in on and "pull_request" not in on


def test_run_never_overlaps_and_never_cancels_a_running_job():
    wf = load("run.yml")
    assert wf["concurrency"] == {"group": "taps", "cancel-in-progress": False}


def test_run_permissions_allow_state_push_and_pages_deploy():
    wf = load("run.yml")
    assert wf["permissions"] == {"contents": "write", "pages": "write", "id-token": "write"}


def test_run_job_environment_and_timeout():
    job = only_job(load("run.yml"))
    assert job["runs-on"] == "ubuntu-latest"
    assert job["environment"]["name"] == "github-pages"
    assert "steps.deploy.outputs.page_url" in job["environment"]["url"]
    assert 0 < job["timeout-minutes"] <= 60


def test_run_checks_out_tip_of_main_with_history():
    steps = only_job(load("run.yml"))["steps"]
    checkout = steps[step_index(steps, uses="actions/checkout@v4")]
    assert checkout["with"]["ref"] == "main"
    assert checkout["with"]["fetch-depth"] == 0


def test_run_installs_python_deps_and_chromium():
    steps = only_job(load("run.yml"))["steps"]
    setup = steps[step_index(steps, uses="actions/setup-python@v5")]
    assert str(setup["with"]["python-version"]) == "3.12"
    assert setup["with"]["cache"] == "pip"
    step_index(steps, run="pip install -r requirements.txt")
    step_index(steps, run="python -m playwright install --with-deps chromium")


def test_run_sets_bot_git_identity():
    steps = only_job(load("run.yml"))["steps"]
    script = steps[step_index(steps, run="git config user.name")]["run"]
    assert 'git config user.name "taps-bot"' in script
    assert 'git config user.email "taps-bot@users.noreply.github.com"' in script


def test_run_step_command_and_env_from_secrets_and_vars():
    steps = only_job(load("run.yml"))["steps"]
    step = steps[step_index(steps, run="python -m taps run")]
    assert step["run"].strip() == "python -m taps run"
    env = step["env"]
    assert set(env) == {*TELEGRAM_SECRETS, "SITE_URL"}
    for name in TELEGRAM_SECRETS:
        assert env[name] == f"${{{{ secrets.{name} }}}}"
    assert env["SITE_URL"] == "${{ vars.SITE_URL }}"


def test_run_deploys_site_even_after_failed_run_if_data_exists():
    steps = only_job(load("run.yml"))["steps"]
    configure = steps[step_index(steps, uses="actions/configure-pages@v5")]
    upload = steps[step_index(steps, uses="actions/upload-pages-artifact@v3")]
    deploy = steps[step_index(steps, uses="actions/deploy-pages@v4")]
    assert upload["with"]["path"] == "site"
    assert deploy["id"] == "deploy"
    for step in (configure, upload, deploy):
        assert "always()" in step["if"]
        assert "hashFiles('site/data.json') != ''" in step["if"]


def test_run_step_order():
    steps = only_job(load("run.yml"))["steps"]
    order = [
        step_index(steps, uses="actions/checkout@v4"),
        step_index(steps, uses="actions/setup-python@v5"),
        step_index(steps, run="pip install -r requirements.txt"),
        step_index(steps, run="playwright install"),
        step_index(steps, run="git config user.name"),
        step_index(steps, run="python -m taps run"),
        step_index(steps, uses="actions/configure-pages@v5"),
        step_index(steps, uses="actions/upload-pages-artifact@v3"),
        step_index(steps, uses="actions/deploy-pages@v4"),
    ]
    assert order == sorted(order)


def test_tests_workflow_runs_pytest_on_push_and_pr_without_browser():
    wf = load("tests.yml")
    on = triggers(wf)
    assert "push" in on and "pull_request" in on
    steps = only_job(wf)["steps"]
    step_index(steps, uses="actions/checkout@v4")
    setup = steps[step_index(steps, uses="actions/setup-python@v5")]
    assert str(setup["with"]["python-version"]) == "3.12"
    install = step_index(steps, run="pip install -r requirements.txt")
    tests = step_index(steps, run="pytest -q")
    assert install < tests
    assert not any("playwright install" in s.get("run", "") for s in steps)


def test_readme_lists_every_setting_the_run_workflow_reads():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for name in (*TELEGRAM_SECRETS, "SITE_URL"):
        assert name in readme
    for command in ("python -m taps run --dry-run", "--no-digest", "pytest"):
        assert command in readme
    assert "github.com/hopandshot/hopsandshot" in readme
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 120 .venv/bin/pytest tests/test_workflows.py -v`
Expected: FAIL — `12 failed`, `FileNotFoundError: [Errno 2] No such file or directory: '.../.github/workflows/run.yml'`

- [ ] **Step 3: Write the scheduled workflow**

Create `.github/workflows/run.yml`:
```yaml
name: taps

on:
  schedule:
    # 10:17 and 18:17 Asia/Yerevan (UTC+4); off the top of the hour on purpose
    - cron: "17 6 * * *"
    - cron: "17 14 * * *"
  workflow_dispatch:

# One run at a time; a queued run waits, a running one is never cancelled
concurrency:
  group: taps
  cancel-in-progress: false

permissions:
  contents: write
  pages: write
  id-token: write

jobs:
  run:
    runs-on: ubuntu-latest
    timeout-minutes: 60
    environment:
      name: github-pages
      url: ${{ steps.deploy.outputs.page_url }}
    steps:
      - uses: actions/checkout@v4
        with:
          ref: main
          fetch-depth: 0

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip

      - run: pip install -r requirements.txt

      - run: python -m playwright install --with-deps chromium

      - run: |
          git config user.name "taps-bot"
          git config user.email "taps-bot@users.noreply.github.com"

      - run: python -m taps run
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          TELEGRAM_ADMIN_CHAT_ID: ${{ secrets.TELEGRAM_ADMIN_CHAT_ID }}
          SITE_URL: ${{ vars.SITE_URL }}

      # Publish whatever site/data.json the run produced, even if the run failed later
      - if: always() && hashFiles('site/data.json') != ''
        uses: actions/configure-pages@v5

      - if: always() && hashFiles('site/data.json') != ''
        uses: actions/upload-pages-artifact@v3
        with:
          path: site

      - if: always() && hashFiles('site/data.json') != ''
        id: deploy
        uses: actions/deploy-pages@v4
```

- [ ] **Step 4: Write the tests workflow**

Create `.github/workflows/tests.yml`:
```yaml
name: tests

on: [push, pull_request]

jobs:
  pytest:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip

      - run: pip install -r requirements.txt

      - run: pytest -q
```

- [ ] **Step 5: Write the README**

Create `README.md`:
```markdown
# Ереван на кранах

Живой список крафтового пива в барах и магазинах Еревана на одной странице и одна тихая сводка новинок в день в Telegram-чат друзей. Бесплатно: GitHub Actions собирает данные, GitHub Pages показывает сайт.

Основа — проект знакомого для Валенсии [hopsandshot](https://github.com/hopandshot/hopsandshot) (Python + Playwright + BeautifulSoup, одна страница на GitHub Pages). Автор разрешил взять код. Лицензии в его репозитории нет, поэтому источник указан здесь и в подвале сайта. Спасибо!

## Как это работает

Два раза в день, в 10:17 и 18:17 по Еревану, GitHub запускает `python -m taps run`. Прогон:

1. забирает данные из источников;
2. сравнивает их с `state.json` и находит новинки;
3. пишет `site/data.json` для сайта;
4. коммитит `state.json` обратно в репозиторий (если пора слать сводку, вместе с отметкой «отправлено»);
5. если пора, шлёт сводку в Telegram;
6. публикует сайт.

Источники:

| Значок | Источник | Что это |
|---|---|---|
| ✅ | меню Untappd (Gargoyle, Beatles), buy.am (Dargett Brewpub) | пиво на кранах по меню бара |
| 👀 | чекины Untappd в барах без меню и на страницах местных пивоварен | «похоже, появилось»: кто-то отметил разливное |
| 🏭 | список сортов местной пивоварни в Untappd | новый сорт, где наливают — пока неизвестно (включается вручную, см. `list_enabled` ниже) |
| 🛒 | Beer City, Yerevan City, Parma | интересное пиво в магазинах, без массовых лагеров |
| ✍️ | `corrections.yaml` | «со слов»: друг рассказал Олегу |

⭐ — возможно, впервые в Ереване (с тех пор, как следим). 🔥 — рейтинг Untappd от 3.75.

Untappd запрещает автоматический сбор данных, поэтому к нему ходим бережно: не чаще раза в 20 часов и не больше 30 страниц в сутки. У каждого пива из Untappd на сайте есть ссылка на его страницу там.

### Сводка и защита от спама

- **Не больше одного сообщения в чат в сутки** и не чаще раза в 20 часов. Сводка уходит вечерним прогоном (после 17:00), если за день нашлось новое. Пропал вечерний прогон — уйдёт утром.
- **Сначала отметка, потом отправка.** Отметка «отправлено» коммитится в `state.json` до отправки. Не получилось запушить — сводку не шлём. Лучше пропустить сводку, чем прислать её дважды.
- **Ничего не объявляется дважды**, в том числе то, что спряталось за «…и ещё N».
- **Не больше 15 строк с пивом**, остальное на сайте.
- **Первый прогон тихий.** Новый источник, новое место, новая вкладка меню, потерянное или устаревшее состояние: всё найденное запоминается молча.
- **Предохранитель.** Если бар или магазин вдруг вернул пустоту, меньше половины прежних позиций или больше половины незнакомых, результат отбрасывается, на сайте остаются старые данные с пометкой ⚠️, Олегу приходит предупреждение.
- **Первые две сводки приходят Олегу в личку**, дальше в чат. Служебные сообщения всегда только Олегу, одно на серию сбоев.
- Сводка беззвучная, без превью ссылок, с кнопкой «Открыть список».

## Локально на Маке

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium   # браузер для Untappd, один раз

timeout 120 .venv/bin/pytest -q                   # тесты, сети не нужно

.venv/bin/python -m taps run --dry-run            # настоящий сбор, но ничего не отправляет и не коммитит
.venv/bin/python -m taps run --dry-run --no-digest   # то же, без сводки
```

`--dry-run` печатает сводку, которая ушла бы в чат (или «нет сводки»), и собирает `site/data.json`. Посмотреть сайт: `python3 -m http.server -d site 8000` и открыть http://localhost:8000. Если после пробного прогона `git status` показывает изменённый `state.json`, не коммитьте его: `git restore state.json` (новый, ещё не закоммиченный файл просто удалите).

Запасной вариант, если GitHub Actions не работает: обычный прогон с Мака, `.venv/bin/python -m taps run --no-digest` (соберёт, обновит `state.json`, запушит, сводку не пошлёт). Чтобы с Мака уходили сводка и служебные сообщения, нужны те же переменные, что и в облаке (см. ниже); токен вводите так, чтобы он не попал в историю терминала: `read -s TELEGRAM_BOT_TOKEN && export TELEGRAM_BOT_TOKEN`.

## Как править списки

Файлы правятся прямо на GitHub: открыть файл в репозитории → карандаш (Edit this file) → изменить → Commit changes. Или попросить Claude. Следующий прогон подхватит изменения сам. **`state.json` руками не трогайте**: его пишет бот.

Репозиторий публичный: всё, что написано в этих файлах, видно всем.

### `places.yaml` — места и пивоварни

Адрес заведения на Untappd выглядит так: `https://untappd.com/v/<slug>/<число>`. Например, Gargoyle: `https://untappd.com/v/gargoyle-bar/12252462`.

Добавить бар без меню (следим за чекинами):

```yaml
places:
  - id: vertigo                        # короткое имя латиницей, потом не менять
    name: Vertigo Bar & Bottleshop     # как показывать на сайте и в сводке
    kind: bar                          # bar | brewpub | shop
    sources:
      untappd_checkins: {slug: vertigo-bar-and-bottleshop, venue_id: 11856429}
```

Бар с меню на Untappd — то же, но источник `untappd_menu: {slug: ..., venue_id: ...}`. Временно выключить место: добавить `enabled: false`.

Пивоварни (для 👀 и 🏭) — отдельный список, на сайт они не выводятся:

```yaml
breweries:
  - id: dargett
    name: Dargett
    brewery_id: 265165                 # из адреса https://untappd.com/brewery/265165
    list_enabled: false                # true только после проверки списка сортов
```

Настройки:

```yaml
settings:
  preview_digests: 2        # сколько первых сводок уходит Олегу в личку
  digest_time: "17:00"      # раньше этого времени сводка ждёт (должно быть раньше вечернего запуска 18:17)
  digest_max_lines: 15
  hot_rating: 3.75          # с какого рейтинга 🔥
  untappd_daily_pages: 30
```

### `corrections.yaml` — правки от друзей

```yaml
sightings:                       # ✍️ со слов: попадёт в сводку, если date не старше 3 дней
  - place: tap-station           # id места из places.yaml
    brewery: "379"
    beer: "Hazy Pale"            # или untappd: 1234567 (число из адреса пива на Untappd)
    by: Аня
    date: 2026-09-24
hide:                            # «этого уже нет» или мусор
  - place: gargoyle
    beer: u:3539672              # u:<число из адреса пива на Untappd>
aliases:                         # одно и то же пиво из разных источников
  "n:konix bronx": u:3539672
brewery_aliases:                 # разные написания пивоварни
  "v engelman": "volfas engelman"
not_craft:                       # бренды, которые не показывать в магазинах
  - Kilikia
```

- Ключ пива `n:...` (для магазинов) проще всего спросить у Claude.
- Склейки, снятие `hide` и правка `not_craft` новинок в чат не дают.
- Если файл сломан (ошибка в YAML), прогон работает по последней удачно прочитанной версии, а Олегу приходит предупреждение. Ошибочная отдельная запись (неизвестное место, нет даты) пропускается, тоже с предупреждением.
- Если сломан `places.yaml`, прогон падает, и GitHub присылает письмо. Исправьте файл.

## Секреты и переменные GitHub

Settings → Secrets and variables → Actions.

| Имя | Где | Что |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Secrets | токен бота от @BotFather |
| `TELEGRAM_ADMIN_CHAT_ID` | Secrets | id личного чата Олега с ботом: пробные сводки и служебные сообщения |
| `TELEGRAM_CHAT_ID` | Secrets | id чата друзей (отрицательное число) |
| `SITE_URL` | Variables | адрес сайта, `https://olegbeers.github.io/yerevan/`, для кнопки «Открыть список» |

Токен нигде не публикуйте и никому не пересылайте, в том числе в чат с Claude. Если он утёк: в @BotFather `/revoke`, новый токен — в секрет.

## Файлы

```
taps/
  run.py            прогон целиком, протокол отправки сводки (python -m taps run)
  fetch.py          браузер и HTTP, паузы, бюджет Untappd, распознавание Cloudflare
  model.py          Sighting, ключи пива, нормализация названий
  config.py         чтение places.yaml
  corrections.py    чтение corrections.yaml
  state.py          state.json: чтение, склейки, чистка
  breaker.py        предохранитель и тихий первый прогон
  rules.py          новинки, ⭐, фильтр чекинов
  shop_filter.py    фильтр «интересное пиво» для магазинов
  digest.py         когда слать и текст сводки
  telegram.py       отправка и служебные предупреждения
  gitsync.py        pull / commit / push state.json
  site_data.py      site/data.json
  timeutil.py       время, Asia/Yerevan
  sources/          untappd_menu, untappd_checkins, untappd_brewery, buyam, beercity, yerevan_city, parma, manual
site/index.html     сайт (читает data.json, который собирается при каждом прогоне и в git не хранится)
places.yaml         места, пивоварни, настройки
corrections.yaml    правки Олега
state.json          память бота, коммитится каждым прогоном
tests/              тесты; fixtures/ — сохранённые настоящие страницы источников
.github/workflows/
  run.yml           расписание: сбор, сводка, публикация сайта
  tests.yml         тесты на каждый push
docs/superpowers/specs/   дизайн
```
```

- [ ] **Step 6: Run test to verify it passes**

Run: `timeout 120 .venv/bin/pytest tests/test_workflows.py -v`
Expected: `12 passed`

- [ ] **Step 7: Run the whole suite (the same command tests.yml runs on GitHub)**

Run: `timeout 120 .venv/bin/pytest -q`
Expected: все тесты проходят, `0 failed`

- [ ] **Step 8: Commit**

```bash
git add .github/workflows/run.yml .github/workflows/tests.yml README.md tests/test_workflows.py
git commit -m "ci: add scheduled taps workflow, tests workflow and README"
```

## Запуск (ручные шаги Олега)

**Коротко про GitHub.** GitHub — сайт, где лежит код проекта (репозиторий). Там же бесплатно работают «роботы» (GitHub Actions: запускают сбор по расписанию) и хостинг страницы (GitHub Pages). Нужен **один** аккаунт GitHub, владелец репозитория. Второй аккаунт не нужен, отдельный аккаунт GitHub для бота тоже не нужен: бот живёт в Telegram, а коммиты в репозиторий подписывает встроенное имя `taps-bot`, без аккаунта.

0. **Аккаунт GitHub — уже готов.** Аккаунт `OlegBeers` создан и подключён к `gh` на этом Маке (сделано 2026-09-23). Перед шагом 1 проверьте, что активен именно он: `gh auth status` → `Active account: OlegBeers`; если активен `etema57` — `gh auth switch --user OlegBeers`. Шаги 1–6 займут минут 20. Начните так, чтобы до ближайшего запуска по расписанию (10:17 или 18:17 по Еревану) оставался хотя бы час.

1. **Создать репозиторий и залить код.** В Терминале:
   ```bash
   cd ~/Claude/Projects/yerevan-taps
   gh auth status        # должно быть: Active account: OlegBeers
   git checkout main && git merge --ff-only feat/v1   # репозиторий создаём из main
   gh repo create OlegBeers/yerevan --public --source . --push
   ```
   Репозиторий нужно создавать именно из `main`: иначе веткой по умолчанию станет `feat/v1`, а запуск по расписанию берёт `main` и упадёт. Репозиторий публичный: так Actions и Pages бесплатны. Токенов и паролей в нём нет, они только в секретах (шаги 3–5). **Не включайте защиту ветки `main` и обязательные pull request'ы:** бот пушит `state.json` прямо в `main`, с защитой каждый прогон будет падать.

2. **Включить сайт.** Откройте https://github.com/OlegBeers/yerevan → Settings → Pages → Build and deployment → Source: **GitHub Actions**. Больше там ничего не нужно.

3. **Секрет `TELEGRAM_BOT_TOKEN`.** В Терминале:
   ```bash
   gh secret set TELEGRAM_BOT_TOKEN --repo OlegBeers/yerevan
   ```
   Вставьте токен от @BotFather и нажмите Enter. Ввод не отображается и не попадает в историю. То же можно сделать в браузере: Settings → Secrets and variables → Actions → New repository secret. Токен никуда больше не вставляйте, в том числе в чат с Claude.

4. **Секрет `TELEGRAM_ADMIN_CHAT_ID` (ваш личный id). Делаете сами.**
   1. В Telegram откройте своего бота и напишите ему что угодно, например «привет».
   2. В браузере откройте `https://api.telegram.org/bot<TOKEN>/getUpdates`, где `<TOKEN>` — токен целиком, сразу после `bot`, без пробела и угловых скобок.
   3. Найдите `"chat":{"id":123456789, ... "type":"private"}`. Число после `"id":` — ваш id. Если видно `"result":[]`, напишите боту ещё раз и обновите страницу.
   4. `gh secret set TELEGRAM_ADMIN_CHAT_ID --repo OlegBeers/yerevan` → вставьте число → Enter.
   В адресе этой страницы есть токен: не пересылайте его и не делайте скриншотов.

5. **Секрет `TELEGRAM_CHAT_ID` (чат друзей).**
   1. Добавьте бота в группу: название группы → Добавить участника → найти бота по имени.
   2. Напишите в группе `/start@<имя_бота>` (имя, которое заканчивается на `bot`). Обычные сообщения в группе бот не видит, а адресованную ему команду видит. Отвечать бот не будет, так и задумано.
   3. Обновите ту же страницу `getUpdates` и найдите запись с `"type":"group"` или `"type":"supergroup"`. Возьмите число из `"chat":{"id":-...}`. Оно отрицательное: копируйте вместе с минусом.
   4. `gh secret set TELEGRAM_CHAT_ID --repo OlegBeers/yerevan` → вставьте число с минусом → Enter.
   Если группа позже станет супергруппой, её id поменяется. Бот сам пришлёт вам новый id, тогда повторите пункт 4.

6. **Переменная `SITE_URL`.**
   ```bash
   gh variable set SITE_URL --body "https://olegbeers.github.io/yerevan/" --repo OlegBeers/yerevan
   gh secret list --repo OlegBeers/yerevan      # три секрета (значения не показываются)
   gh variable list --repo OlegBeers/yerevan    # SITE_URL
   ```
   В браузере то же самое: Settings → Secrets and variables → Actions → вкладка Variables → New repository variable.

7. **Пробный прогон на Маке.**
   ```bash
   cd ~/Claude/Projects/yerevan-taps
   # если папки .venv ещё нет: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   .venv/bin/python -m playwright install chromium
   .venv/bin/python -m taps run --dry-run --no-digest
   ```
   Прогон ничего не отправляет и не коммитит. Идёт до получаса: в первый раз магазины открывают страницу каждого товара. Прочитайте вывод: по каждому источнику должны быть позиции или понятная ошибка (`cloudflare`, `network`, `http`, `empty`). Токен здесь не используется, поэтому вывод можно показать Claude. Потом `git status`: если `state.json` изменён — `git restore state.json`, если появился новый — удалите его.

8. **Первый прогон в облаке.** https://github.com/OlegBeers/yerevan → вкладка **Actions** → слева **taps** → справа **Run workflow** → Branch: `main` → зелёная кнопка **Run workflow**. Через минуту появится запуск. Откройте его → job **run** → шаг **Run python -m taps run**. Проверьте в журнале:
   - Untappd: страницы меню Gargoyle и Beatles загрузились, нет `cloudflare` / `blocked`;
   - магазины beer-city, yerevan-city, parma и buy.am отвечают серверам GitHub, нет `network` / `http`. Если какой-то не отвечает, скажите Claude: этот источник переведём на браузер (Playwright);
   - у баров без меню (чекины) не 0 позиций. Если 0 — сохраните страницу этого бара на Untappd, как в шаге 9, и отдайте Claude;
   - шаги **deploy** зелёные. Откройте https://olegbeers.github.io/yerevan/ — там бары и магазины.
   В репозитории появится коммит `state.json` от `taps-bot`. Так и должно быть. Перед правками на Маке делайте `git pull`.

9. **Проверка списка сортов пивоварни (до неё 🏭 выключен).**
   1. В приватном окне браузера (без входа в Untappd) откройте https://untappd.com/brewery/265165 (Dargett). Под рейтингом счётчик вида «39 Beers».
   2. Нажмите на него, откроется список сортов. Посчитайте сорта, видные сразу, ничего не нажимая (кнопка «Show More» не считается).
   3. Сохраните страницу (Cmd+S, формат «Веб-страница, только HTML») и отдайте Claude: он проверит разбор на живой странице и добавит её в тесты.
   4. Число совпало с «N Beers» — только тогда в `places.yaml` у пивоварен ставится `list_enabled: true` (Claude сделает). Не совпало — остаётся `false`: 🏭 в v1 не будет, остальное работает.

10. **Чего ждать.**
    - Первый прогон тихий: всё найденное запоминается молча, в чат ничего не приходит. Вам в личку могут прийти служебные «⚠️ taps: …», если какой-то источник не ответил.
    - Дальше запуски в 10:17 и 18:17 по Еревану (GitHub иногда опаздывает на 10–30 минут). Сводка не чаще раза в сутки, вечером, и только если появилось новое.
    - Первые две сводки придут вам в личку: проверьте, что в них нет мусора. С третьей сводки — в чат друзей.
    - Если запуск упал, GitHub пришлёт письмо на почту аккаунта. На сайте через 36 часов без обновлений появится жёлтая плашка «данные устарели».
