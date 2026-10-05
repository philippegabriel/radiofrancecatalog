import csv
import os
from contextlib import contextmanager
import psycopg2
from pathlib import Path

FIELDS = ['show', 'published_iso', 'published_ts', 'title', 'description', 'web_url', 'podcast_title', 'podcast_url', 'player_url', 'id']
SHOWS = {
    'affaires-sensibles': 'franceinter',
    **dict.fromkeys(['lsd-la-serie-documentaire', 'les-nuits-de-france-culture', 'les-pieds-sur-terre', 'le-cours-de-l-histoire', 'mecaniques-du-journalisme'], 'franceculture'),
}


def read_rows(path, show):
    with Path(path).open(encoding='utf-8', newline='') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != FIELDS:
            raise ValueError(f'Unexpected catalogue columns in {path}')
        rows = list(reader)
    seen = set()
    for row in rows:
        if row['show'] != show or not row['id'] or row['id'] in seen or None in row.values() or None in row:
            raise ValueError(f'Invalid or duplicate episode in {path}')
        int(row['published_ts'])
        seen.add(row['id'])
    return rows


PROJECT = Path(__file__).resolve().parents[3]


@contextmanager
def catalogue_session(path, show):
    rows = read_rows(path, show)
    # An explicit separate DSN prevents using Airflow metadata as scratch space.
    connection = psycopg2.connect(os.environ.get('RF_CATALOGUE_DSN', 'dbname=radiofrance user=pgabriel'))
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL search_path TO pg_temp")
            cursor.execute("SET LOCAL TIME ZONE 'UTC'")
            schema = (PROJECT / 'schema.sql').read_text()
            start = schema.index('CREATE TABLE IF NOT EXISTS rf (')
            end = schema.index(';', start) + 1
            cursor.execute(schema[start:end].replace('CREATE TABLE IF NOT EXISTS rf', 'CREATE TEMP TABLE rf'))
            start = schema.index('CREATE OR REPLACE VIEW rf_html AS')
            end = schema.index(';', start) + 1
            cursor.execute(schema[start:end].replace('CREATE OR REPLACE VIEW', 'CREATE TEMP VIEW'))
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


def query_catalogue(path, show, sql_file):
    query = (PROJECT / sql_file).read_text().replace(":'show'", '%s')
    with catalogue_session(path, show) as cursor:
        cursor.execute(query, (show,))
        headers = [column.name for column in cursor.description]
        return headers, cursor.fetchall()


def cutoff(path, show):
    return query_catalogue(path, show, 'cutoffdate.sql')[1][0][0]


def merge(baseline, updates, output, show):
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
