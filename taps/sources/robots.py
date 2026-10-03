"""robots.txt of a shop, read on every run: a source never requests a URL the site disallows."""
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from taps.fetch import FetchError, Http


@dataclass(frozen=True)
class Robots:
    disallow: tuple[str, ...]   # Disallow paths of the "User-agent: *" group; neither shop uses Allow lines

    @classmethod
    def parse(cls, text: str) -> "Robots":
        rules: list[str] = []
        agents: list[str] = []
        in_rules = False   # the previous line was a rule, so the next User-agent starts a new group
        for line in text.splitlines():
            field, _, value = line.split("#", 1)[0].partition(":")
            field, value = field.strip().lower(), value.strip()
            if field == "user-agent":
                if in_rules:
                    agents, in_rules = [], False
                agents.append(value)
            elif field in ("disallow", "allow"):
                in_rules = True
                if field == "disallow" and value and "*" in agents:
                    rules.append(value)
        return cls(tuple(rules))

    def allowed(self, url: str) -> bool:
        """Path plus query against every rule: a prefix match where "*" is any text and a final "$" the end."""
        parts = urlsplit(url)
        target = parts.path + (f"?{parts.query}" if parts.query else "")
        return not any(re.match(_pattern(rule), target) for rule in self.disallow)


def _pattern(rule: str) -> str:
    return "".join(".*" if c == "*" else "$" if c == "$" else re.escape(c) for c in rule)


def read_robots(http: Http, base: str) -> Robots:
    """FetchError when robots.txt cannot be read: the run waits rather than guess."""
    return Robots.parse(http.get(f"{base}/robots.txt").text)


def guarded_get(http: Http, robots: Robots, url: str):
    """http.get that never leaves what robots.txt allows: FetchError("blocked") before any request is made."""
    if not robots.allowed(url):
        raise FetchError("blocked", url)
    return http.get(url)
