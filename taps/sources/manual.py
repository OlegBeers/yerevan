"""Source ✍️ "со слов": sightings entered by hand in corrections.yaml."""
from datetime import datetime

from taps.config import Config
from taps.corrections import Corrections
from taps.model import Sighting, SourceResult, u_key, untappd_n_key
from taps.timeutil import to_yerevan

MANUAL_KEEP_DAYS = 14


def manual_result(corrections: Corrections, config: Config, now: datetime) -> SourceResult:
    """Entries at enabled places dated 0..MANUAL_KEEP_DAYS Yerevan days ago; future dates wait for their day."""
    today = to_yerevan(now).date()
    sightings = []
    for e in corrections.sightings:
        if e.place not in config.places or not 0 <= (today - e.date).days <= MANUAL_KEEP_DAYS:
            continue
        if e.untappd_id is not None:
            key, url = u_key(e.untappd_id), f"https://untappd.com/beer/{e.untappd_id}"
        else:
            key, url = untappd_n_key(e.brewery, e.beer, corrections.brewery_aliases), None
        name = e.beer or f"Untappd #{e.untappd_id}"
        sightings.append(Sighting(
            place_id=e.place, source="manual", beer_key=key,
            title=" ".join(filter(None, (e.brewery, name))), name=name, seen_at=now,
            brewery=e.brewery, untappd_beer_id=e.untappd_id, url=url,
            manual_id=e.id, manual_by=e.by, manual_date=e.date.isoformat(),
        ))
    return SourceResult(key="manual", source="manual", ok=True, sightings=sightings)
