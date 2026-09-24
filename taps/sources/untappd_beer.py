"""An Untappd beer's own page (v1.1 §2): rating/style/abv/ibu for beers seen only in check-ins.

No real fixture exists for this page (it is fetched sparingly, a few beers per run) -- the fixture
tests/fixtures/untappd/beer_page.html is a SYNTHETIC page modeled on markup used elsewhere on Untappd
(div.caps[data-rating] as in the menu/brewery-list rows, div.name/p.style/div.abv/div.ibu as in the
brewery beer-list rows). It must be checked against a real https://untappd.com/b/<slug>/<id> page
before this parser is trusted in production."""
import re

from bs4 import BeautifulSoup

ABV_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*ABV", re.I)
IBU_RE = re.compile(r"(\d+(?:\.\d+)?)\s*IBU", re.I)


def _text(tag) -> str:
    return " ".join(tag.get_text(" ").split()) if tag else ""


def _rating(soup: BeautifulSoup) -> float | None:
    caps = soup.select_one("div.caps[data-rating]")
    try:
        rating = float(caps["data-rating"]) if caps else None
    except ValueError:
        return None
    return rating or None   # 0 means no ratings yet


def parse_beer_page(html: str) -> dict | None:
    """{"rating", "style", "abv", "ibu"} from a beer's own page; None if the page doesn't look like
    a beer page at all (changed markup, a login wall, a page that slipped past Cloudflare detection) --
    checked via the beer name heading, the one element every real beer page must have."""
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one("div.name h1") is None:
        return None
    stats_text = _text(soup.select_one("div.details"))
    abv, ibu = ABV_RE.search(stats_text), IBU_RE.search(stats_text)
    return {
        "style": _text(soup.select_one("div.name p.style")) or None,
        "abv": float(abv.group(1)) if abv else None,
        "ibu": round(float(ibu.group(1))) if ibu else None,
        "rating": _rating(soup),
    }
