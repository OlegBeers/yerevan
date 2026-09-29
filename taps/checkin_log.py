"""data/checkins.json: a compact rolling log of Untappd check-ins at Armenian venues, for stats.html.

Privacy (spec): only id, time, venue id, username, beer id, brewery name and the check-in's own rating
are stored -- no display names, avatars, photos, comments or per-user venue history. corrections.yaml
hide_users opts a username out entirely: never stored, and purged from an existing log."""
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Collection

from taps.sources.untappd_checkins import Checkin
from taps.timeutil import age_days, iso, parse_iso

LOG_KEEP_DAYS = 120   # a check-in older than this is pruned every run
USER_URL_RE = re.compile(r"^https?://(?:www\.)?untappd\.com/user/([^/?#]+)", re.IGNORECASE)


def normalize_username(raw: str) -> str:
    """Case/format-insensitive comparison key for a hide_users entry or a parsed check-in username:
    strips a untappd.com/user/ profile URL down to the handle, drops a leading '@', and casefolds."""
    text = raw.strip()
    if m := USER_URL_RE.match(text):
        text = m.group(1)
    return text.lstrip("@").strip().casefold()


@dataclass(frozen=True)
class CheckinLogEntry:
    id: int
    time: str            # iso; the check-in's own time, not when it was read
    venue_id: int
    username: str
    beer_id: int
    brewery: str
    rating: float | None = None   # this check-in's own rating, never the beer's average


def load_checkin_log(path: Path) -> list[CheckinLogEntry]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [CheckinLogEntry(**json.loads(line)) for line in lines if line.strip()]


def save_checkin_log(path: Path, entries: list[CheckinLogEntry]) -> None:
    """One compact JSON record per line, in id order, for small git diffs."""
    ordered = sorted(entries, key=lambda e: e.id)
    text = "".join(json.dumps(asdict(e), ensure_ascii=False, sort_keys=True) + "\n" for e in ordered)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _entry(c: Checkin) -> CheckinLogEntry:
    return CheckinLogEntry(id=c.checkin_id, time=iso(c.created_at), venue_id=c.venue_id,
                           username=c.username, beer_id=c.beer_id, brewery=c.brewery, rating=c.rating)


def record_checkins(log: list[CheckinLogEntry], checkins: Collection[Checkin], armenia_venue_ids: Collection[int],
                    hidden_users: Collection[str], now: datetime) -> list[CheckinLogEntry]:
    """Merge newly seen check-ins into the log: kept only at an Armenian venue, with a username, not
    at-home, and not from an opted-out user (corrections.yaml hide_users) -- which also purges that
    user's existing entries. Deduped by check-in id (across venue and brewery-page sources), pruned to
    LOG_KEEP_DAYS, sorted by id (spec: small git diffs)."""
    armenia = set(armenia_venue_ids)
    hidden = {normalize_username(u) for u in hidden_users}
    by_id = {e.id: e for e in log if normalize_username(e.username) not in hidden}
    for c in checkins:
        if c.venue_id is None or c.at_home or c.username is None:
            continue
        if c.venue_id not in armenia or normalize_username(c.username) in hidden:
            continue
        by_id[c.checkin_id] = _entry(c)
    kept = [e for e in by_id.values() if age_days(parse_iso(e.time), now) <= LOG_KEEP_DAYS]
    return sorted(kept, key=lambda e: e.id)
