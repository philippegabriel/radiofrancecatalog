"""Shared episode-reference identity and registration for catalogue DAGs."""
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Protocol, TypedDict
from airflow.sdk import Asset, AssetAlias
from radiofrance_catalogue.catalogue import SHOWS, CatalogueRow

EPISODES: dict[str, AssetAlias] = {show: AssetAlias(f'radiofrance-episodes-{show}') for show in SHOWS}


class EpisodeEvent(TypedDict):
    show: str
    episode_id: str
    published_iso: str
    published_ts: int


class RecordedEpisode(Protocol):
    @property
    def extra(self) -> Mapping[str, str | int]: ...


class InletEvents(Protocol):
    def __getitem__(self, asset: AssetAlias) -> Sequence[RecordedEpisode]: ...


class EpisodeEmitter(Protocol):
    def add(self, asset: Asset, *, extra: EpisodeEvent) -> None: ...


def record_episode_rows(show: str, rows: list[CatalogueRow], events: Sequence[RecordedEpisode], emitter: EpisodeEmitter) -> int:
    """Emit reference events for episode/date pairs not yet registered."""
    recorded = {(event.extra['episode_id'], int(event.extra['published_ts'])) for event in events}
    count = 0
    for row in rows:
        timestamp = int(row['published_ts'])
        if (row['id'], timestamp) in recorded:
            continue
        published_iso = datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
        metadata: EpisodeEvent = {
            'show': show, 'episode_id': row['id'],
            'published_ts': timestamp, 'published_iso': published_iso,
        }
        emitter.add(
            Asset(uri=f"x-radiofrance://episodes/{show}/{row['id']}", name=f"{show}_{row['id']}_episode",
                  extra={'show': show, 'episode_id': row['id'], 'published_ts': timestamp, 'published_iso': published_iso}),
            extra=metadata,
        )
        count += 1
    return count
