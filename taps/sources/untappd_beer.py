"""An Untappd beer's own page (v1.1 §2): rating/style/abv/ibu, the label image and the country.

Checked against a real page saved from a browser (tests/fixtures/untappd/beer_page_real.html, trimmed
and free of user data): div.name h1 / p.style, div.details p.abv/p.ibu and div.caps[data-rating], the
label in div.basic a.label img, and the country in <meta name="keywords"> -- "<name>, <brewery>,
<style>, <country>[, <region>]". tests/fixtures/untappd/beer_page.html is an older synthetic page."""
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


LABEL_PREFIX = "https://assets.untappd.com/"


def _logo(soup: BeautifulSoup) -> str | None:
    img = soup.select_one("div.basic a.label img[src]")
    src = img["src"] if img else ""
    return src if src.startswith(LABEL_PREFIX) else None


def _country(soup: BeautifulSoup, style: str | None) -> str | None:
    """The keywords list is "<name>, <brewery>, <style>, <country>[, <region>]": the part right after
    the style (names can hold commas, so count from the style, not from the start)."""
    meta = soup.find("meta", attrs={"name": "keywords"})
    if not meta or not style:
        return None
    parts = [p.strip() for p in (meta.get("content") or "").split(", ")]
    for i in range(len(parts) - 1, 0, -1):
        if parts[i] == style and i + 1 < len(parts):
            return parts[i + 1] or None
    return None


def parse_beer_page(html: str) -> dict | None:
    """{"rating", "style", "abv", "ibu", "logo", "country"} from a beer's own page; None if the page doesn't look like
    a beer page at all (changed markup, a login wall, a page that slipped past Cloudflare detection) --
    checked via the beer name heading, the one element every real beer page must have."""
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one("div.name h1") is None:
        return None
    stats_text = _text(soup.select_one("div.details"))
    abv, ibu = ABV_RE.search(stats_text), IBU_RE.search(stats_text)
    style = _text(soup.select_one("div.name p.style")) or None
    return {
        "style": style,
        "abv": float(abv.group(1)) if abv else None,
        "ibu": round(float(ibu.group(1))) if ibu else None,
        "rating": _rating(soup),
        "logo": _logo(soup),
        "country": _country(soup, style),
    }
