"""Source ✍️ "со слов": sightings entered by hand in corrections.yaml."""
from dataclasses import replace
from datetime import datetime

from taps.config import Config
from taps.corrections import Corrections, ManualEntry
from taps.model import Serving, Sighting, SourceResult, u_key, untappd_n_key, with_servings
from taps.state import resolve_alias
from taps.timeutil import to_yerevan

MANUAL_KEEP_DAYS = 14
DETAIL_FIELDS = ("brewery", "beer", "untappd_id", "style", "abv", "ibu")   # a later entry for the beer may add these


def manual_result(corrections: Corrections, config: Config, now: datetime) -> SourceResult:
    """Entries at enabled places dated 0..MANUAL_KEEP_DAYS Yerevan days ago; future dates wait for their day.

    Entries for one beer at one place (an alias counts as the same beer) are servings of one sighting: the
    first entry gives the beer and the announcement (manual_id/by/date), every entry gives a serving."""
    today = to_yerevan(now).date()
    entries: dict[tuple[str, str], list[ManualEntry]] = {}
    for e in corrections.sightings:
        if e.place not in config.places or not 0 <= (today - e.date).days <= MANUAL_KEEP_DAYS:
            continue
        if e.untappd_id is not None:
            key = u_key(e.untappd_id)
        else:
            key = untappd_n_key(e.brewery, e.beer, corrections.brewery_aliases)
        entries.setdefault((e.place, resolve_alias(key, corrections.aliases)), []).append(e)
    sightings = []
    for (place, key), beer_entries in entries.items():
        e = replace(beer_entries[0], **{
            f: next((v for x in beer_entries if (v := getattr(x, f)) is not None), None) for f in DETAIL_FIELDS})
        url = f"https://untappd.com/beer/{e.untappd_id}" if e.untappd_id is not None else None
        name = e.beer or f"Untappd #{e.untappd_id}"
        ids = tuple(dict.fromkeys(x.id for x in beer_entries))   # every merged entry counts as announced with the pair
        sightings.append(with_servings(Sighting(
            place_id=place, source="manual", beer_key=key,
            title=" ".join(filter(None, (e.brewery, name))), name=name, seen_at=now,
            brewery=e.brewery, untappd_beer_id=e.untappd_id, url=url,
            manual_id=e.id, manual_by=e.by, manual_date=e.date.isoformat(), manual_ids=ids if len(ids) > 1 else (),
            style=e.style, abv=e.abv, ibu=e.ibu,
        ), (Serving(x.container, x.price_amd) for x in beer_entries)))
    return SourceResult(key="manual", source="manual", ok=True, sightings=sightings)
