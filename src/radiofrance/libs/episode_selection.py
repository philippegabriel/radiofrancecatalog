"""Read an explicit, checksum-pinned episode selection for bounded integration runs."""

import json
from pathlib import Path
import re
from typing import TypedDict, cast


class SelectedEpisode(TypedDict):
    episode_id: str
    published_ts: int
    published_iso: str
    title: str
    transcript_sha256: str
    segment_count: int


class EpisodeSelection(TypedDict):
    version: int
    show: str
    episodes: list[SelectedEpisode]


def read_selection(path: Path, show: str) -> EpisodeSelection:
    """Validate nonempty fixed membership, canonical IDs, timestamps and fingerprints."""
    selection = cast(EpisodeSelection, json.loads(path.read_text(encoding='utf-8')))
    if selection['version'] != 1 or selection['show'] != show or not selection['episodes']:
        raise ValueError('Invalid episode selection version, show or empty sample')
    seen: set[str] = set()
    for item in selection['episodes']:
        identity = item['episode_id']
        uuid = identity.rsplit('_', 1)[0]
        if (not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}_(0|[1-9][0-9]*)', identity)
                or uuid in seen or type(item['published_ts']) is not int or item['published_ts'] <= 0
                or type(item['segment_count']) is not int or item['segment_count'] <= 0
                or not re.fullmatch(r'[0-9a-f]{64}', item['transcript_sha256'])):
            raise ValueError('Invalid or duplicate selected episode')
        seen.add(uuid)
    return selection
