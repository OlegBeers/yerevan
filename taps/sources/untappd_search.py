"""Untappd beer search (v1.1 §3): matches shop beers (Beer City, Yerevan City, Parma) to Untappd, for
rating/style/abv/logo/link on shop rows.

No real fixture exists for https://untappd.com/search?q=<query>&type=beer -- tests build a SYNTHETIC
page modeled on markup used elsewhere on Untappd (div.beer-item as in the brewery beer list,
div.caps[data-rating] as in menu/list rows, a.label img as in check-ins/menu rows). Check it against a
real search results page before trusting this parser in production."""
import re
from dataclasses import dataclass
from urllib.parse import quote

from bs4 import BeautifulSoup, Tag

from taps.model import normalize_base

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


def _tokens(text: str) -> set[str]:
    return set(normalize_base(text).split())


def matches(shop_brand: str, shop_name: str, result: SearchResult) -> bool:
    """Accept only if the shop brand's tokens overlap the result's brewery, and at least half of the
    shop name's remaining tokens (brand words removed) are found in the result's own name."""
    brand_tokens = _tokens(shop_brand)
    if not brand_tokens or not brand_tokens & _tokens(result.brewery):
        return False
    name_tokens = _tokens(shop_name) - brand_tokens
    if not name_tokens:
        return True
    return len(name_tokens & _tokens(result.name)) / len(name_tokens) >= MIN_NAME_OVERLAP


_RU_COLOURS = {"светлое", "светлый", "темное", "темный"}   # the shop's colour suffix; Untappd names rarely carry it


def matches_russian_name(shop_name: str, result: SearchResult) -> bool:
    """For a Russian shop name whose Latin brewery is only a transliteration: its first word (the brand)
    must be in the result's brewery or name, and at least half of the remaining words (colour suffix aside) in the result's name."""
    words = normalize_base(shop_name).split()
    own = _tokens(result.brewery) | _tokens(result.name)
    if not words or words[0] not in own:
        return False
    rest = set(words[1:]) - _RU_COLOURS
    return not rest or len(rest & _tokens(result.name)) / len(rest) >= MIN_NAME_OVERLAP
