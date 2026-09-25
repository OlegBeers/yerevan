"""v1.2 beer identity: match a shop/menu/manual beer to an Untappd beer we already know from a bar's
own menu/check-ins -- zero extra Untappd pages. Conservative: a beer matches only when exactly one
known Untappd beer satisfies both the brewery and name checks in local_match(); ambiguous or partial
evidence means no match, since a false merge is worse than a miss.
"""
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from taps.model import has_cyrillic, normalize_base

# Noise dropped only for fuzzy matching -- deliberately NOT the same as taps.model's own
# normalize_title/_STOP_TOKENS, which feed beer_key/n_key identity (must not change) and drop
# packaging/serving words like "draught"/"unfiltered" that pair-key equivalence never cares about.
# Those words can be a genuine, distinguishing part of an Untappd beer's own name (e.g. "Guinness
# Draught" vs "Guinness Foreign Extra Stout" -- found via the real dry run), so they stay OUT of
# this list on purpose: stripping them risks exactly the false merge this matcher must avoid.
_NOISE_WORDS = (
    "beer", "пиво", "drink",                                                          # "a beer", generically
    "brewery", "brewing", "brewers", "brewpub", "brauerei", "privatbrauerei",           # "a brewery", generically
    "brasserie", "birrificio", "brouwerij", "bieres",
    "co", "company", "llc", "gmbh", "ltd", "craft", "de", "ооо", "спс",                 # corporate/legal form
    "can", "bottle", "keg", "tin", "glass", "pet",                                      # container, not the beer
    "dark", "light", "semi",                                                            # Yerevan City/Parma's own bottle-colour suffix
)
_NOISE_RE = re.compile(
    r"\b(?:" + "|".join(_NOISE_WORDS) + r")\b"
    r"|\bg\s*/\s*b\b"
    r"|\d+(?:[.,]\d+)?\s*(?:ml|cl|l|мл|л|լ|%)(?![a-zа-яёա-և])",
    re.IGNORECASE,
)


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def clean_text(text: str) -> str:
    """Case-preserving noise removal shared by the search query (untappd_search) and the shop-side
    token cleaning below: colour suffixes Yerevan City/Parma append, generic brewery/container
    words, "g/b", and embedded ABV/volume numbers."""
    text = _NOISE_RE.sub(" ", _strip_accents(text))
    return " ".join(text.split())


def has_russian_name(brewery: str | None, name: str) -> bool:
    """A Russian shop name under a Latin-only brewery (Yerevan City: "Jigulyovskoye" / "Жигулевское
    светлое"): the Latin brewery is a transliteration, so Untappd's own Cyrillic spelling ignores it."""
    return has_cyrillic(name) and bool(brewery) and not has_cyrillic(brewery)


def clean_query(brewery: str | None, name: str) -> str:
    """The same noise-cleaning applied to an Untappd search query string (v1.1 §3 match_shop_beers);
    a Russian name is searched alone."""
    return clean_text(f"{brewery} {name}" if brewery and not has_russian_name(brewery, name) else name)


@dataclass(frozen=True)
class KnownBeer:
    """An Untappd beer already known from a bar's own menu/check-ins (state.pairs u: keys)."""
    untappd_id: int
    name: str
    brewery: str
    rating: float | None = None
    style: str | None = None
    abv: float | None = None
    logo: str | None = None


_LATIN_RE = re.compile("[a-z]", re.IGNORECASE)
# Words that mark a distinct edition of a base beer; a shop title must name them to match it
_VARIANT_TOKENS = {"alkoholfrei", "alcoholfree", "non", "free", "zero", "0", "barrel", "aged", "ba", "bourbon",
                   "brandy", "cognac", "rum", "whisky", "whiskey", "wine",
                   "double", "imperial", "triple", "session", "hazy", "extra", "foreign", "nitro", "milk",
                   "smoked", "vanilla", "shake"}
# Too generic to serve as brewery evidence on their own -- many unrelated breweries share one of
# these words (e.g. "St. Bernardus" and "St-Feuillien", or "Pivovar Svijany" and "Pivovar Chotěboř").
# Deliberately NOT here: real brand words like "black"/"old" that happen to coincide between two
# unrelated breweries (e.g. "Black Sheep" vs "Black Tie Brewing") -- those are caught instead by
# requiring EVERY non-generic shop-brewery token to be verified, not just one of them.
_GENERIC_BREWERY_WORDS = {"st", "saint", "sint", "pivovar", "pivovarna", "the", "brau", "brauerei", "brasserie",
                          "brouwerij", "birrificio", "cerveceria", "brewery", "brewing", "beer", "co", "company",
                          "craft", "group", "gruppe", "abbaye", "abbey", "abdij", "browar", "brauhaus", "birra",
                          "cerveza", "pivo", "gmbh", "kg", "ag", "llc", "ltd", "sa", "nv", "bv", "srl", "spa",
                          "inc", "oy", "ab", "as"}
_PAREN_RE = re.compile(r"\(([^()]*)\)")


def _shop_tokens(text: str) -> list[str]:
    """A shop's own brewery/title text: noisy, so the extra cleaning above applies."""
    return normalize_base(clean_text(text)).split()


def _candidate_tokens(text: str) -> list[str]:
    """An Untappd beer's own brewery/name: already canonical, so only case/accent/volume-number
    normalization applies -- none of the noise words above, since any of them (colour included) may
    be a real, distinguishing part of its own name rather than shop noise."""
    return normalize_base(text).split()


def _is_latin(text: str) -> bool:
    return bool(_LATIN_RE.search(text))


def _paren_tokens(name: str) -> set[str]:
    """Tokens inside (...) in an Untappd beer's own name -- a scientific/regional aside (Apricot Ale
    (Prunus Armeniaca), Pilsner (La Rapsodia)), not a real difference from the plain title."""
    tokens: set[str] = set()
    for m in _PAREN_RE.finditer(name):
        tokens |= set(_candidate_tokens(m.group(1)))
    return tokens


def _best_fit_leftover(name: str, remaining: set[str]) -> set[str]:
    """Extra candidate-name tokens still unexplained by `remaining`, after picking whichever
    " / "-separated alternative spelling (Paulaner "Hefe-Weißbier / Hefe-Weizen / Weissbier") it
    best fits: the other alternatives are just another phrasing of the same beer, not evidence of a
    real difference, so only the chosen alternative's own leftover tokens count as "extra"."""
    alts = [set(_candidate_tokens(part)) for part in name.split(" / ")]
    fits = [alt - remaining for alt in alts if remaining <= alt]
    return min(fits, key=len) if fits else set(_candidate_tokens(name)) - remaining


_ABV_TOLERANCE = 0.3   # a "loose" match that only works via a parenthesised aside is a rival beyond this


def _evaluate(shop_brewery: str | None, shop_name_tokens: Sequence[str], candidate: KnownBeer,
             shop_abv: float | None = None) -> tuple[str, bool] | None:
    """(grade, weak) for how well `candidate` fits, or None to reject it outright. grade is "exact",
    "loose", or "variant" (never chosen, but makes the name ambiguous) -- used to break ties when
    several candidates pass (local_match prefers an exact token-set match). weak is True only for a
    "loose" match that relied on an unnamed parenthesised aside with no ABV on either side to
    corroborate it (code review round 3, finding C2): still accepted, but flagged.

    Brewery compatibility: EVERY non-generic token of the shop brewery (ignoring generic words like
    "st"/"pivovar"/"brewery" -- too common across unrelated breweries to serve as evidence on their
    own) must appear in the candidate's own brewery or name tokens -- a single shared word is not
    enough (e.g. "Black" alone must not merge "Black Sheep" into an unrelated "Black Tie Brewing");
    or, when the shop brewery is empty/non-latin (often an importer's legal-entity name, e.g. Parma)
    or disagrees outright, the shop name's first token stands in for it instead. Either way,
    whichever tokens served as the brewery signal are excluded from the name-containment check below.
    """
    shop_brewery_tokens = _shop_tokens(shop_brewery or "")
    brewery_tokens = set(shop_brewery_tokens)
    candidate_brewery_tokens = set(_candidate_tokens(candidate.brewery))
    candidate_name_tokens = set(_candidate_tokens(candidate.name))
    candidate_tokens = candidate_brewery_tokens | candidate_name_tokens
    consumed = brewery_tokens

    significant = [t for t in shop_brewery_tokens if t not in _GENERIC_BREWERY_WORDS]
    if not significant or not all(t in candidate_tokens for t in significant):
        if brewery_tokens and _is_latin(shop_brewery or ""):
            return None   # a present, Latin brewery that only partly overlaps or disagrees
        first = shop_name_tokens[0] if shop_name_tokens else None
        if first is None or first not in candidate_brewery_tokens:
            return None
        consumed = brewery_tokens | {first}

    remaining = {t for t in shop_name_tokens if t not in consumed}
    if not remaining or not remaining <= candidate_name_tokens:
        return None
    extra = _best_fit_leftover(candidate.name, remaining)
    allowed_no_parens = consumed | candidate_brewery_tokens
    paren_tokens = _paren_tokens(candidate.name)
    if (extra & _VARIANT_TOKENS) or (extra - (allowed_no_parens | paren_tokens)):
        return "variant", False   # an edition/flavour the title doesn't name: another beer, yet a rival
    relies_on_parens = bool(extra) and not extra <= allowed_no_parens
    abv_known = shop_abv is not None and candidate.abv is not None
    if relies_on_parens and abv_known and abs(shop_abv - candidate.abv) > _ABV_TOLERANCE:
        return "variant", False   # the parenthesised aside papers over a real difference in strength
    weak = relies_on_parens and not abv_known
    return ("exact" if not extra else "loose"), weak


def local_match_with_confidence(shop_brewery: str | None, shop_name: str, candidates: Sequence[KnownBeer],
                                shop_abv: float | None = None) -> tuple[KnownBeer | None, bool]:
    """(found, weak): the one KnownBeer that (conservatively) matches (shop_brewery, shop_name),
    preferring an exact token-set match when several pass -- (None, False) when that still leaves
    zero or more than one candidate, since ambiguous or no evidence means no match. shop_abv (when
    known) guards a "loose" match that only works via a parenthesised aside in the candidate's name
    against papering over a real difference in strength (code review round 2, finding C); when such
    a match has no ABV on either side to corroborate it, it is still returned but weak is True (code
    review round 3, finding C2 -- flag it for review rather than reject a possibly-correct match)."""
    if has_russian_name(shop_brewery, shop_name):
        shop_brewery = None   # the name's first word stands in for the brewery, as for a non-Latin one
    shop_name_tokens = _shop_tokens(shop_name)
    passing = [(c, r) for c in candidates if (r := _evaluate(shop_brewery, shop_name_tokens, c, shop_abv))]
    if len(passing) == 1:
        c, (grade, weak) = passing[0]
        return (c, weak) if grade != "variant" else (None, False)
    exact = [(c, weak) for c, (grade, weak) in passing if grade == "exact"]
    return exact[0] if len(exact) == 1 else (None, False)


def local_match(shop_brewery: str | None, shop_name: str, candidates: Sequence[KnownBeer],
                shop_abv: float | None = None) -> KnownBeer | None:
    """The one KnownBeer that (conservatively) matches (shop_brewery, shop_name); None when that
    still leaves zero or more than one candidate -- ambiguous or no evidence means no match. See
    local_match_with_confidence for the "weak" (uncorroborated parenthetical match) signal."""
    return local_match_with_confidence(shop_brewery, shop_name, candidates, shop_abv)[0]
