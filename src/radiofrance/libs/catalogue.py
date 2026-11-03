"""Catalogue metadata shared by download artifacts and the database loader."""
import json
from pathlib import Path
from typing import TypedDict, cast


class CatalogueRow(TypedDict):
    show: str
    published_iso: str
    published_ts: int
    title: str
    description: str
    web_url: str
    podcast_title: str
    podcast_url: str
    player_url: str
    id: str


FIELDS: list[str] = list(CatalogueRow.__annotations__)
SHOWS: dict[str, str] = {
    'affaires-sensibles': 'franceinter',
    **dict.fromkeys(['lsd-la-serie-documentaire', 'les-nuits-de-france-culture', 'les-pieds-sur-terre',
                    'le-cours-de-l-histoire', 'mecaniques-du-journalisme'], 'franceculture'),
}


def read_rows(path: str | Path, show: str) -> list[CatalogueRow]:
    """Read a JSON metadata delta; reject missing fields, invalid timestamps and duplicate IDs."""
    raw: object = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(raw, list):
        raise ValueError('Expected a JSON episode list')
    seen: set[str] = set()
    for row in raw:
        if (not isinstance(row, dict) or set(row) != set(FIELDS)
                or any(not isinstance(row[key], str) for key in FIELDS if key != 'published_ts')
                or type(row['published_ts']) is not int or row['published_ts'] < 0):
            raise ValueError('Invalid episode metadata fields or publication timestamp')
        if row['show'] != show or not row['id'] or row['id'] in seen:
            raise ValueError('Invalid show or duplicate episode ID')
        seen.add(row['id'])
    return cast(list[CatalogueRow], raw)
