"""Untappd beer search (v1.1 §3): matches shop beers (Beer City, Yerevan City, Parma) to Untappd, for
rating/style/abv/logo/link on shop rows.

No real POPULATED fixture exists for https://untappd.com/search?q=<query>&type=beer (v1.3 search fix:
18 real captures exist, tests/fixtures/untappd/search_*.html, but Untappd's own Algolia widget found
zero results on all of them -- a bad query, not a parser problem) -- tests build a SYNTHETIC page
modeled on markup used elsewhere on Untappd (div.beer-item as in the brewery beer list,
div.caps[data-rating] as in menu/list rows, a.label img as in check-ins/menu rows). Check
parse_search_results against a real POPULATED page before trusting this parser in production."""
import re
from dataclasses import dataclass
from urllib.parse import quote

from bs4 import BeautifulSoup, Tag

from taps.model import normalize_base
from taps.sources.local_match import _GENERIC_BREWERY_WORDS, clean_text

SEARCH_URL = "https://untappd.com/search?q={query}&type=beer"
BEER_HREF_RE = re.compile(r"^/b/([^/]+)/(\d+)/?$")
ABV_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
MIN_NAME_OVERLAP = 0.5


@dataclass(frozen=True)
class SearchResult:
    beer_id: int
    slug: str
    name: str
    brewery: str
    style: str | None
    abv: float | None
    rating: float | None
    logo: str | None

    @property
    def url(self) -> str:
        return f"https://untappd.com/b/{self.slug}/{self.beer_id}"


def _text(tag: Tag | None) -> str:
    return " ".join(tag.get_text(" ").split()) if tag else ""


def _rating(item: Tag) -> float | None:
    caps = item.select_one("div.caps[data-rating]")
    try:
        rating = float(caps["data-rating"]) if caps else None
    except ValueError:
        return None
    return rating or None   # 0 means no ratings yet


def search_url(query: str) -> str:
    return SEARCH_URL.format(query=quote(query))


def parse_search_results(html: str) -> list[SearchResult]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for item in soup.select("div.beer-item"):
        link = item.select_one("p.name a[href]")
        m = BEER_HREF_RE.match(link["href"]) if link else None
        if not m or not _text(link):
            continue
        abv = ABV_RE.search(_text(item.select_one(".abv")))
        logo = item.select_one("a.label img[src]")
        out.append(SearchResult(
            beer_id=int(m.group(2)), slug=m.group(1), name=_text(link),
            brewery=_text(item.select_one("p.brewery")),
            style=_text(item.select_one("p.style")) or None,
            abv=float(abv.group(1)) if abv else None,
            rating=_rating(item),
            logo=logo["src"] if logo else None,
        ))
    return out


def _shop_tokens(text: str) -> set[str]:
    """A shop's own brand/name text: noisy (colour suffixes, "unfiltered", container words -- v1.3
    search fix), so the same cleaning as the search query itself applies -- those words will never
    appear in Untappd's own (canonical) brewery/beer name, so they must not count against the
    required overlap below."""
    return set(normalize_base(clean_text(text)).split())


def _result_tokens(text: str) -> set[str]:
    """An Untappd result's own brewery/name: already canonical, no shop noise to strip."""
    return set(normalize_base(text).split())


# Acceptance guards (production false matches): a result that adds a distinguishing word the shop's
# own title lacks is another beer, however well the rest overlaps.
_QUALIFIERS = {"black", "white", "red", "dark", "double", "imperial", "triple", "tripel", "quadrupel",
               "barrel", "smoked", "sour", "session", "hazy", "nitro"}
_BREWERY_KINDS = {"meadery", "cidery", "winery", "distillery", "mead", "cider", "wine", "wines",
                  "vineyard", "vineyards", "spirits"}   # a mead/cider/wine maker is never the shop's beer brand
_NON_ALCOHOLIC_RE = re.compile(
    r"(?<![\d.,])0(?:[.,]0+)?\s*%|\b0[.,]0\b|alkoholfrei|alcohol[\s-]*free|non[\s-]*alcoholic|безалкогол",
    re.IGNORECASE)
_PAREN_RE = re.compile(r"\([^()]*\)")


def is_brand_only(shop_brand: str, shop_name: str) -> bool:
    """The shop name says nothing beyond its brand ("Krombacher 0%" under Krombacher): any beer of that
    brewery would pass matches(), so such a match can only be a guess at the flagship -- flag it weak."""
    brand_tokens = _shop_tokens(shop_brand)
    return bool(brand_tokens) and not _shop_tokens(shop_name) - brand_tokens


def _non_alcoholic_conflict(shop_text: str, shop_abv: float | None, result: SearchResult) -> bool:
    """The shop beer and the result disagree on being non-alcoholic (name words 0%/alkoholfrei/..., or
    ABV under 1 when the shop's own ABV is known too, or the shop already says it is non-alcoholic)."""
    shop_na = bool(_NON_ALCOHOLIC_RE.search(shop_text)) or (shop_abv is not None and shop_abv < 1)
    low_abv = result.abv is not None and result.abv < 1 and (shop_abv is not None or shop_na)
    result_na = bool(_NON_ALCOHOLIC_RE.search(result.name)) or low_abv
    return shop_na != result_na


def _unnamed_variant(shop_text: str, shop_name_tokens: set[str], result: SearchResult) -> bool:
    """The result's own name carries a variant word (Black, Imperial, Barrel...) neither the shop's
    title nor the result's brewery has. A parenthesised nickname is not counted, nor is the name
    outside it when the whole shop name is spelled inside it ("Dr. White (Bière Blanche)")."""
    if shop_name_tokens and shop_name_tokens <= _result_tokens(" ".join(_PAREN_RE.findall(result.name))):
        return False
    extra = _result_tokens(_PAREN_RE.sub(" ", result.name)) & _QUALIFIERS
    return bool(extra - set(normalize_base(shop_text).split()) - _result_tokens(result.brewery))


def _plausible(shop_text: str, shop_abv: float | None, result: SearchResult) -> bool:
    """Guards shared by matches() and matches_russian_name(): non-alcoholic agreement, and the result's
    brewery is not a meadery/cidery/winery the shop text does not name."""
    if _non_alcoholic_conflict(shop_text, shop_abv, result):
        return False
    return not (_result_tokens(result.brewery) & _BREWERY_KINDS) - set(normalize_base(shop_text).split())


def _brewery_is_brand(brand_tokens: set[str], result: SearchResult) -> bool:
    """For a brand-only shop name: the result's brewery (parenthesised aside and generic words like
    Brewing/Co/Gruppe aside) has no distinctive word beyond the shop brand ("The Bentley Brook Brewing
    Co." is not "Bentley")."""
    tokens = _result_tokens(_PAREN_RE.sub(" ", result.brewery)) - _GENERIC_BREWERY_WORDS
    return tokens <= brand_tokens


def matches(shop_brand: str, shop_name: str, result: SearchResult, shop_abv: float | None = None) -> bool:
    """Accept only if the shop brand's tokens overlap the result's brewery, and at least half of the
    shop name's remaining tokens (brand words removed) are found in the result's own name -- and the
    result adds no variant word (_unnamed_variant) the shop title lacks, agrees on being
    non-alcoholic, and is not a meadery/cidery (_plausible). A brand-only shop name needs a brewery
    with no distinctive word beyond the brand (_brewery_is_brand)."""
    brand_tokens = _shop_tokens(shop_brand)
    if not brand_tokens or not brand_tokens & _result_tokens(result.brewery):
        return False
    shop_text = f"{shop_brand} {shop_name}"
    if not _plausible(shop_text, shop_abv, result):
        return False
    name_tokens = _shop_tokens(shop_name) - brand_tokens
    if not name_tokens:
        return _brewery_is_brand(brand_tokens, result)
    if _unnamed_variant(shop_text, name_tokens, result):
        return False
    return len(name_tokens & _result_tokens(result.name)) / len(name_tokens) >= MIN_NAME_OVERLAP


_RU_COLOURS = {"светлое", "светлый", "темное", "темный"}   # the shop's colour suffix; Untappd names rarely carry it


def matches_russian_name(shop_name: str, result: SearchResult, shop_abv: float | None = None) -> bool:
    """For a Russian shop name whose Latin brewery is only a transliteration: its first word (the brand)
    must be in the result's brewery or name, and at least half of the remaining words (colour suffix aside) in the result's name."""
    if not _plausible(shop_name, shop_abv, result):
        return False
    words = normalize_base(clean_text(shop_name)).split()
    own = _result_tokens(result.brewery) | _result_tokens(result.name)
    if not words or words[0] not in own:
        return False
    rest = set(words[1:]) - _RU_COLOURS
    return not rest or len(rest & _result_tokens(result.name)) / len(rest) >= MIN_NAME_OVERLAP


def _candidate_score(brand: str, name: str, result: SearchResult, shop_abv: float | None) -> float:
    """How well `result` fits (brand, name): the same overlap signals matches() itself checks
    (brand-in-brewery, remaining-name-in-name), each as a 0..1 fraction, plus an ABV-closeness bonus
    when both sides know it -- used only to RANK results that already failed matches(), for a
    one-tap owner suggestion (v1.4), never to accept one."""
    brand_tokens = _shop_tokens(brand)
    brand_overlap = (len(brand_tokens & _result_tokens(result.brewery)) / len(brand_tokens)
                     if brand_tokens else 0.0)
    name_tokens = _shop_tokens(name) - brand_tokens
    name_overlap = (len(name_tokens & _result_tokens(result.name)) / len(name_tokens)
                    if name_tokens else 1.0)
    score = brand_overlap + name_overlap
    if shop_abv is not None and result.abv is not None:
        score += max(0.0, 1.0 - abs(result.abv - shop_abv) / 5.0)
    return score


def best_candidate(results: list[SearchResult], brand: str, name: str,
                   shop_abv: float | None = None) -> SearchResult | None:
    """The best-scoring result (see _candidate_score), or None for an empty list -- for suggesting a
    doubtful match to the owner (site/matches.html "Найти на Untappd") when a search page returned
    results but none was accepted by matches()/matches_russian_name()."""
    if not results:
        return None
    return max(results, key=lambda r: _candidate_score(brand, name, r, shop_abv))
