"""v1.2 beer identity: match a shop/menu/manual beer to an Untappd beer we already know from a bar's
own menu/check-ins -- zero extra Untappd pages. Conservative: a beer matches only when exactly one
known Untappd beer satisfies both the brewery and name checks in local_match(); ambiguous or partial
evidence means no match, since a false merge is worse than a miss.
"""
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from taps.model import normalize_title

# Extra noise beyond taps.model.normalize_title's own stopwords: that set feeds beer_key/n_key
# identity and must not change (spec invariant), so anything added only for fuzzy matching lives here.
_NOISE_WORDS = ("brouwerij", "bieres", "de", "company", "craft", "dark", "light", "semi", "ооо", "спс")
_NOISE_RE = re.compile(
    r"\b(?:" + "|".join(_NOISE_WORDS) + r")\b"
    r"|\bg\s*/\s*b\b"
    r"|\d+(?:[.,]\d+)?\s*(?:ml|cl|l|мл|л|լ|%)(?![a-zа-яёա-և])",
    re.IGNORECASE,
)


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def clean_text(text: str) -> str:
    """Case-preserving noise removal shared by the search query (untappd_search) and the token
    cleaning below: colour suffixes Yerevan City/Parma append, generic brewery words, "g/b", and
    embedded ABV/volume numbers."""
    text = _NOISE_RE.sub(" ", _strip_accents(text))
    return " ".join(text.split())


def clean_query(brewery: str | None, name: str) -> str:
    """The same noise-cleaning applied to an Untappd search query string (v1.1 §3 match_shop_beers)."""
    return clean_text(f"{brewery} {name}" if brewery else name)


@dataclass(frozen=True)
class KnownBeer:
    """An Untappd beer already known from a bar's own menu/check-ins (state.pairs u: keys)."""
    untappd_id: int
    name: str
    brewery: str


_LATIN_RE = re.compile("[a-z]", re.IGNORECASE)


def _shop_tokens(text: str, brewery_aliases: Mapping[str, str] | None) -> list[str]:
    """A shop's own brewery/title text: noisy, so the extra cleaning above applies."""
    return normalize_title(clean_text(text), brewery_aliases).split()


def _candidate_tokens(text: str, brewery_aliases: Mapping[str, str] | None) -> list[str]:
    """An Untappd beer's own brewery/name: already canonical, so only the identity-key normalizer
    applies -- notably NOT the colour-word stripping above, since e.g. "Light" may be a real,
    distinct beer name rather than shop packaging noise (see the ambiguous-suffix test)."""
    return normalize_title(text, brewery_aliases).split()


def _is_latin(text: str) -> bool:
    return bool(_LATIN_RE.search(text))


def _evaluate(shop_brewery: str | None, shop_name_tokens: Sequence[str], candidate: KnownBeer,
             brewery_aliases: Mapping[str, str] | None) -> str | None:
    """"exact", "loose" or None for how well `candidate` fits -- used to break ties when several
    candidates pass (local_match prefers an exact token-set match).

    Brewery compatibility: a shop brewery token appears in the candidate's own brewery or name
    tokens; or, when the shop brewery is empty/non-latin (often an importer's legal-entity name,
    e.g. Parma), the shop name's first token stands in for it instead. Either way, whichever tokens
    served as the brewery signal are excluded from the name-containment check below.
    """
    brewery_tokens = set(_shop_tokens(shop_brewery or "", brewery_aliases))
    candidate_brewery_tokens = set(_candidate_tokens(candidate.brewery, brewery_aliases))
    candidate_name_tokens = set(_candidate_tokens(candidate.name, brewery_aliases))
    consumed = brewery_tokens

    if not brewery_tokens & (candidate_brewery_tokens | candidate_name_tokens):
        if brewery_tokens and _is_latin(shop_brewery or ""):
            return None   # a present, Latin brewery that simply disagrees (e.g. an importer's name)
        first = shop_name_tokens[0] if shop_name_tokens else None
        if first is None or first not in candidate_brewery_tokens:
            return None
        consumed = brewery_tokens | {first}

    remaining = {t for t in shop_name_tokens if t not in consumed}
    if not remaining or not remaining <= candidate_name_tokens:
        return None
    return "exact" if remaining == candidate_name_tokens else "loose"


def local_match(shop_brewery: str | None, shop_name: str, candidates: Sequence[KnownBeer],
               brewery_aliases: Mapping[str, str] | None = None) -> KnownBeer | None:
    """The one KnownBeer that (conservatively) matches (shop_brewery, shop_name), preferring an exact
    token-set match when several pass; None when that still leaves zero or more than one candidate --
    ambiguous or no evidence means no match."""
    shop_name_tokens = _shop_tokens(shop_name, brewery_aliases)
    passing = [(c, _evaluate(shop_brewery, shop_name_tokens, c, brewery_aliases)) for c in candidates]
    passing = [(c, grade) for c, grade in passing if grade is not None]
    if len(passing) == 1:
        return passing[0][0]
    exact = [c for c, grade in passing if grade == "exact"]
    return exact[0] if len(exact) == 1 else None
