"""Style of a shop beer guessed from its own name: a small table of style words, for display only.

The guess never reaches state, the digest or the pair keys; the site marks it as inferred (site_data.py).
"""
import re

from taps.model import normalize_base

# (label, pattern) over normalize_base(name): lower case, accents dropped, words split by single spaces.
_NAMED = (
    ("IPA", r"\bipa\b|\bindian? pale ale\b"),
    ("Pale Ale", r"\bapa\b|(?<!india )(?<!indian )\bpale ale\b"),
    ("Pilsner", r"\bpils(?:ner|ener)?\b"),
    ("Stout", r"\bstout\b"),
    ("Porter", r"\bporter\b"),
    ("Dunkel", r"\bdunkel\b"),
    # The shops write "Hell", but a bare "hell" is also an English word ("Road to Hell", "Hell's Kitchen"):
    # it counts only right before "lager" / "non-filtered".
    ("Helles", r"\bhelles\b|\bhell(?= (?:lager|non filtered|unfiltered)\b)"),
    ("Tripel", r"\btripel\b"),
    ("Dubbel", r"\bdubbel\b"),
    ("Kriek", r"\bkriek\b"),
    ("Gose", r"\bgose\b"),
    ("Sour", r"\bsour\b"),
    ("Witbier", r"\bwit(?:bier)?\b|\bblanche\b"),
    ("Blonde", r"\bblonde?\b"),
    ("Amber", r"\bamber\b"),
    ("Saison", r"\bsaison\b"),
    ("Radler", r"\bradler\b"),
    ("Bock", r"\bbock\b|(?:doppel|weizen|eis|mai)bock\b"),
    ("Märzen", r"\bmarzen\b"),
    ("Kellerbier", r"\bkellerbier\b"),
    ("Cider", r"\bcider\b"),
)
# Broad families: counted only when the name holds no named style ("Super Wit wheat" is a Witbier).
_GENERIC = (
    ("Wheat Beer", r"weizen\b|\bhefe|weiss(?:e|bier)\b|\bwheat\b"),
    ("Lager", r"\blager\b"),
)
_WHEAT = re.compile(_GENERIC[0][1])
_WHEAT_MODIFIERS = {"Helles", "Dunkel"}   # "Weissbier Dunkel" is a wheat beer, not a Dunkel
_TABLES = tuple(tuple((label, re.compile(pattern)) for label, pattern in table) for table in (_NAMED, _GENERIC))


def guess_style(name: str) -> str | None:
    """The style `name` states, or None when it states none or several different ones."""
    text = normalize_base(name)
    for table in _TABLES:
        found = {label for label, pattern in table if pattern.search(text)}
        if found:
            if found <= _WHEAT_MODIFIERS and _WHEAT.search(text):
                return "Wheat Beer"
            return found.pop() if len(found) == 1 else None
    return None
