import csv
import os
from contextlib import contextmanager
import psycopg2
from pathlib import Path
from collections.abc import Iterator
from datetime import date
from typing import TypedDict, cast
from psycopg2.extensions import cursor as PostgreSQLCursor

class CatalogueRow(TypedDict):
    show: str
    published_iso: str
    published_ts: str
    title: str
    description: str
    web_url: str
    podcast_title: str
    podcast_url: str
    player_url: str
    id: str


type SQLValue = str | int | date | None

FIELDS: list[str] = ['show', 'published_iso', 'published_ts', 'title', 'description', 'web_url', 'podcast_title', 'podcast_url', 'player_url', 'id']
SHOWS: dict[str, str] = {
    'affaires-sensibles': 'franceinter',
    **dict.fromkeys(['lsd-la-serie-documentaire', 'les-nuits-de-france-culture', 'les-pieds-sur-terre', 'le-cours-de-l-histoire', 'mecaniques-du-journalisme'], 'franceculture'),
}


def read_rows(path: str | Path, show: str) -> list[CatalogueRow]:
    with Path(path).open(encoding='utf-8', newline='') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != FIELDS:
            raise ValueError(f'Unexpected catalogue columns in {path}')
        rows = cast(list[CatalogueRow], list(reader))
    seen: set[str] = set()
    for row in rows:
        if row['show'] != show or not row['id'] or row['id'] in seen or None in row.values() or None in row:
            raise ValueError(f'Invalid or duplicate episode in {path}')
        int(row['published_ts'])
        seen.add(row['id'])
    return rows


PROJECT = Path(__file__).resolve().parents[3]


@contextmanager
def catalogue_session(path: str | Path, show: str) -> Iterator[PostgreSQLCursor]:
    """Load CSV rows into an isolated temporary catalogue; roll back on exit."""
    rows = read_rows(path, show)
    # An explicit separate DSN prevents using Airflow metadata as scratch space.
    connection = psycopg2.connect(os.environ.get('RF_CATALOGUE_DSN', 'dbname=radiofrance user=pgabriel'))
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL search_path TO pg_temp")
            cursor.execute("SET LOCAL TIME ZONE 'UTC'")
            schema = (PROJECT / 'schema.sql').read_text()
            cursor.execute((PROJECT / 'catalogue_csv_schema.sql').read_text())
            start = schema.index('CREATE OR REPLACE VIEW rf_html AS')
            end = schema.index(';', start) + 1
            cursor.execute(schema[start:end].replace('CREATE OR REPLACE VIEW', 'CREATE TEMP VIEW').replace('FROM rf_external', 'FROM rf'))
            from io import StringIO
            buffer = StringIO()
            writer = csv.DictWriter(buffer, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
            buffer.seek(0)
            cursor.copy_expert('COPY rf FROM STDIN WITH (FORMAT CSV, HEADER TRUE)', buffer)
            yield cursor
    finally:
        connection.rollback()
        connection.close()


def query_catalogue(path: str | Path, show: str, sql_file: str) -> tuple[list[str], list[tuple[SQLValue, ...]]]:
    query = (PROJECT / sql_file).read_text().replace(":'show'", '%s')
    with catalogue_session(path, show) as cursor:
        cursor.execute(query, (show,))
        assert cursor.description is not None
        headers = [column.name for column in cursor.description]
        return headers, cast(list[tuple[SQLValue, ...]], cursor.fetchall())


def cutoff(path: str | Path, show: str) -> str:
    value = query_catalogue(path, show, 'cutoffdate.sql')[1][0][0]
    if not isinstance(value, str):
        raise TypeError('Cutoff SQL must return a timestamp string')
    return value


def merge(baseline: str | Path, updates: str | Path, output: str | Path, show: str) -> int:
    rows = {r['id']: r for r in read_rows(baseline, show)}
    rows.update({r['id']: r for r in read_rows(updates, show)})
    ordered = sorted(rows.values(), key=lambda r: (int(r['published_ts']), r['id']))
    temporary = Path(output).with_suffix('.csv.tmp')
    with temporary.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(ordered)
    temporary.replace(output)
    return len(ordered)
