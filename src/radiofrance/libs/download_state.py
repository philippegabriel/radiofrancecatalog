"""Read the committed catalogue watermark without modifying application state."""
from contextlib import closing
from importlib.resources import files
from typing import TypedDict

import psycopg2


class DownloadState(TypedDict):
    cutoff_timestamp: int
    episode_count: int
    existing_ids: list[str]


def read_download_state(show: str, dsn: str = '') -> DownloadState:
    """Read MAX(published_ts) and identities at that timestamp for one show.

    Standard libpq environment variables select the application database. Database
    errors propagate; an unavailable database must never imply an empty catalogue.
    The read-only connection closes before HTTP requests or artifact writes begin.
    Boundary IDs let the caller include unseen episodes sharing the latest second.
    """
    with closing(psycopg2.connect(dsn, connect_timeout=10,
                 options='-c statement_timeout=30000')) as connection:
        connection.set_session(readonly=True)
        with connection.cursor() as cursor:
            cursor.execute(files('radiofrance.sql').joinpath('download_state.sql').read_text(), (show, show))
            row = cursor.fetchone()
            assert row is not None
            return {'cutoff_timestamp': int(row[0]), 'episode_count': int(row[1]),
                    'existing_ids': list(row[2])}
