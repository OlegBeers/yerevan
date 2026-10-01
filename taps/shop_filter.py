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
CIDER_COCKTAIL_RE = re.compile(r"(cider|cidre|сидр|cocktail|коктейл|սիդր|կոկտեյլ)", re.I)


def brand_matches(text: str, brand: str) -> bool:
    """True if brand occurs in text as whole tokens, ignoring case and accents."""
    needle = normalize_base(brand)
    return bool(needle) and f" {needle} " in f" {normalize_base(text)} "


def classify(title: str, brand: str | None, not_craft: Sequence[str], category: str | None = None) -> tuple[bool, str]:
    """(keep, reason); reason is cider_cocktail | nonalc | style | not_craft | keep.
    Radlers and lemon/grapefruit beer drinks are beer and pass like any other (Oleg, 2026-10-01);
    a mass brand's radler is still hidden by not_craft."""
    if CIDER_COCKTAIL_RE.search(category or "") or CIDER_COCKTAIL_RE.search(title):
        return False, "cider_cocktail"
    if NONALC_RE.search(title):
        return False, "nonalc"
    if STYLE_RE.search(title):
        return True, "style"
    for name in not_craft:
        if brand_matches(title, name) or (brand is not None and brand_matches(brand, name)):
            return False, "not_craft"
    return True, "keep"
