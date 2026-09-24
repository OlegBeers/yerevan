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
class VenueCheckin:
    """One check-in seen at some venue (4.2/4.3), tracked or not, for the venues discovery list (v1.1)."""
    venue_id: int
    venue_name: str
    venue_url: str
    checkin_id: int
    at: datetime


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
    venue_meta: dict | None = None            # {"venue_id","name","url","logo","verified"} of this result's own venue
    venue_checkins: list[VenueCheckin] = field(default_factory=list)   # every venue seen in check-ins (v1.1)


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
