"""Verify a local pipeline artifact chain and load it transactionally into PostgreSQL."""

import argparse
import csv
import json
import math
import os
from pathlib import Path
import re
from typing import TypedDict, cast

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import cursor as Cursor

from radiofrance.jobs.chunk import ChunkManifest, DownloadManifest, contained_file, validate_source, verify_output as verify_chunks
from radiofrance.jobs.embedding import EmbeddingManifest, EmbeddingRow, verify_output as verify_embeddings
from radiofrance.libs.types import TranscriptSegment
from radiofrance.libs.transcript_json import read_segments

from radiofrance.libs.catalogue import CatalogueRow, read_rows

from importlib.resources import files

SQL = files('radiofrance.sql')


class Artifact(TypedDict):
    path: str


class CatalogueManifest(DownloadManifest):
    catalogue: Artifact
    updates: Artifact
    catalogue_episode_count: int


class Episode(TypedDict):
    external_id: str
    segments: list[TranscriptSegment]
    chunks: list[list[str]]
    embeddings: list[EmbeddingRow]


class Prepared(TypedDict):
    embedding_run: str
    chunk_run: str
    download_run: str
    catalogue: list[CatalogueRow]
    episodes: list[Episode]
    chunks: ChunkManifest
    embeddings: EmbeddingManifest


class LoadResult(TypedDict):
    already_imported: bool
    catalogue_episode_count: int
    transcript_count: int
    chunk_count: int
    embedding_run: str


def canonical(value: object) -> str:
    """Serialize configuration deterministically for stable registry identities."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def execute(cursor: Cursor, name: str, parameters: tuple[object, ...] = ()) -> None:
    """Execute parameterized SQL from the checked-in loader query directory."""
    cursor.execute(SQL.joinpath(f'{name}.sql').read_text(), parameters)


def source_run(root: Path, name: str) -> Path:
    """Resolve only a single run-directory basename inside its explicit artifact root."""
    if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
        raise ValueError('Source run must be a directory basename')
    return contained_file(root, f'{name}/manifest.json')


def prepare(embedding_manifest: Path, chunk_root: Path, download_root: Path) -> Prepared:
    """Read and validate the full lineage, catalogue, chunk text and vectors without DB access."""
    if embedding_manifest.name != 'manifest.json':
        raise ValueError('Expected embedding manifest.json')
    embedding_run = embedding_manifest.parent.name
    embeddings = verify_embeddings(embedding_manifest.parent)
    chunk_path = source_run(chunk_root, embeddings['source_run'])
    chunk_run = chunk_path.parent.name
    chunks = verify_chunks(chunk_path.parent)
    verify_embeddings(embedding_manifest.parent, chunk_path)
    download_path = source_run(download_root, chunks['source_run'])
    download_run = download_path.parent.name
    download = cast(CatalogueManifest, validate_source(download_path))
    if chunks['show'] != download['show']:
        raise ValueError('Chunk/download lineage mismatch')
    catalogue = read_rows(contained_file(download_path.parent, download['catalogue']['path']), download['show'])
    updates = read_rows(contained_file(download_path.parent, download['updates']['path']), download['show'])
    ids = {row['id'] for row in catalogue}
    for identity in ids:
        if not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}_(0|[1-9][0-9]*)', identity):
            raise ValueError('Expected canonical UUID_suffix catalogue identity')
    transcripts = {item['episode_id']: item for item in download['transcripts']}
    chunk_files = {item['episode_id']: item for item in chunks['chunks']}
    embedding_files = {item['episode_id']: item for item in embeddings['embeddings']}
    if (len(catalogue) != download['catalogue_episode_count'] or set(transcripts) != set(chunk_files)
            or set(transcripts) != {row['id'] for row in updates} or not set(transcripts) <= ids):
        raise ValueError('Catalogue/transcript/chunk membership mismatch')
    episodes: list[Episode] = []
    for identity, item in transcripts.items():
        transcript_path = contained_file(download_path.parent, item['path'])
        chunk_item, embedding_item = chunk_files[identity], embedding_files[identity]
        chunk_file = contained_file(chunk_path.parent, chunk_item['path'])
        embedding_file = contained_file(embedding_manifest.parent, embedding_item['path'])
        segments = read_segments(transcript_path)
        if any(not math.isfinite(s[key]) for s in segments for key in ('start', 'end')):
            raise ValueError('Nonfinite transcript time')
        with chunk_file.open(newline='', encoding='utf-8') as stream:
            rows = list(csv.reader(stream))
        for row in rows:
            start, end = int(row[2]), int(row[3])
            if not 0 <= start <= end < len(segments):
                raise ValueError('Invalid chunk boundaries')
            text = ' '.join(s['text'].strip() for s in segments[start:end+1] if s['text'].strip())
            if (row[6] != text or float(row[4]) != segments[start]['start']
                    or float(row[5]) != segments[end]['end']):
                raise ValueError('Chunk text/timestamps do not match transcript')
        vectors = [cast(EmbeddingRow, json.loads(line)) for line in embedding_file.read_text().splitlines()]
        episodes.append({'external_id': identity,
            'segments': segments, 'chunks': rows, 'embeddings': vectors})
    return {'embedding_run': embedding_run, 'chunk_run': chunk_run,
        'download_run': download_run, 'catalogue': catalogue, 'episodes': episodes,
        'chunks': chunks, 'embeddings': embeddings}


def register(cursor: Cursor, table: str, configuration: object) -> int:
    """Reuse an exact JSON configuration or register a new integer identity."""
    content = canonical(configuration)
    query = sql.SQL(SQL.joinpath('configuration.sql').read_text()).format(table=sql.Identifier(table))
    cursor.execute(query, (content,))
    row = cursor.fetchone()
    assert row is not None
    if canonical(row[1]) != content:
        raise ValueError('Configuration conflict')
    return int(row[0])


def load_episode(cursor: Cursor, episode: Episode, identity: int, strategy: int, model: int,
                 verify_only: bool = False) -> None:
    """Insert immutable transcript/chunk/embedding sets or reject conflicting existing artifacts."""
    segments, chunks, embeddings = episode['segments'], episode['chunks'], episode['embeddings']
    expected_transcript = (len(segments),)
    execute(cursor, 'transcript_lookup', (identity,))
    existing = cursor.fetchone()
    if existing is None and verify_only:
        raise ValueError('Previously imported transcript is missing')
    if existing is None:
        execute(cursor, 'transcript', (identity, *expected_transcript))
        for seq, segment in enumerate(segments):
            execute(cursor, 'segment', (identity, seq, segment['start'], segment['end'], segment.get('speaker'), segment['text']))
    elif tuple(existing) != expected_transcript:
        raise ValueError('Conflicting immutable transcript')
    if existing is not None:
        cursor.execute('SELECT seq,start_time,end_time,speaker,text FROM transcript_segment WHERE episode_id=%s ORDER BY seq', (identity,))
        expected_segments = [(seq, item['start'], item['end'], item.get('speaker'), item['text']) for seq, item in enumerate(segments)]
        if cursor.fetchall() != expected_segments:
            raise ValueError('Conflicting immutable transcript')
    expected_chunks = (len(chunks),)
    execute(cursor, 'chunk_set_lookup', (identity, strategy))
    existing = cursor.fetchone()
    if existing is None and verify_only:
        raise ValueError('Previously imported chunk set is missing')
    if existing is None:
        execute(cursor, 'chunk_set', (identity, strategy, *expected_chunks))
        for row in chunks:
            execute(cursor, 'chunk', (identity, strategy, int(row[1]), int(row[2]), int(row[3]), float(row[4]), float(row[5])))
    elif tuple(existing) != expected_chunks:
        raise ValueError('Conflicting immutable chunks')
    expected_embeddings = (len(embeddings),)
    execute(cursor, 'embedding_set_lookup', (identity, strategy, model))
    existing = cursor.fetchone()
    if existing is None and verify_only:
        raise ValueError('Previously imported embedding set is missing')
    if existing is None:
        execute(cursor, 'embedding_set', (identity, strategy, model, *expected_embeddings))
        for row in embeddings:
            execute(cursor, 'embedding', (identity, strategy, model, row['chunk_index'], canonical(row['embedding'])))
    elif tuple(existing) != expected_embeddings:
        raise ValueError('Conflicting immutable embeddings')
    execute(cursor, 'verify', (identity, identity, strategy, identity, strategy, model))
    if tuple(cursor.fetchone() or ()) != (len(segments), len(chunks), len(embeddings)):
        raise ValueError('Database row counts do not match source artifacts')


def verify_committed(cursor: Cursor, prepared: Prepared) -> None:
    """Read committed provenance, configurations, catalogue membership and per-episode counts.

    The caller supplies a transaction snapshot. No records are created or repaired;
    missing completion, mismatched configurations and missing child rows fail.
    """
    execute(cursor, 'run_lookup', (prepared['embedding_run'],))
    previous = cursor.fetchone()
    expected = (prepared['chunk_run'], prepared['download_run'], prepared['embeddings']['show'],
        len(prepared['catalogue']), len(prepared['episodes']), prepared['embeddings']['chunk_count'])
    if previous is None or tuple(previous[:6]) != expected:
        raise ValueError('Committed import is missing or has different provenance')
    strategy, model = int(previous[6]), int(previous[7])
    execute(cursor, 'verify_configuration', (strategy, model))
    configuration = cursor.fetchone()
    if (configuration is None or canonical(configuration[0]) != canonical(prepared['chunks']['strategy'])
            or canonical(configuration[1]) != canonical(prepared['embeddings']['configuration'])):
        raise ValueError('Committed strategy/model configuration mismatch')
    execute(cursor, 'verify_catalogue', ([row['id'] for row in prepared['catalogue']], prepared['embeddings']['show']))
    if tuple(cursor.fetchone() or ()) != (len(prepared['catalogue']),):
        raise ValueError('Imported catalogue episodes are missing')
    for episode in prepared['episodes']:
        execute(cursor, 'identity_lookup', (episode['external_id'],))
        identity = cursor.fetchone()
        if identity is None:
            raise ValueError('Previously imported episode is missing')
        load_episode(cursor, episode, int(identity[0]), strategy, model, verify_only=True)


def verify_database(prepared: Prepared, dsn: str) -> LoadResult:
    """Verify a committed import in a read-only repeatable-read snapshot; never write DB/files."""
    connection = psycopg2.connect(dsn)
    try:
        connection.set_session(readonly=True, isolation_level='REPEATABLE READ')
        with connection:
            if connection.info.dbname != 'radiofrance':
                raise ValueError('Refusing verification outside the radiofrance database')
            with connection.cursor() as cursor:
                verify_committed(cursor, prepared)
    finally:
        connection.close()
    return {'already_imported': True, 'catalogue_episode_count': len(prepared['catalogue']),
        'transcript_count': len(prepared['episodes']), 'chunk_count': prepared['embeddings']['chunk_count'],
        'embedding_run': prepared['embedding_run']}


def load(prepared: Prepared, dsn: str) -> LoadResult:
    """Commit one verified pipeline run atomically; repeat imports are a no-op.

    Serialize imports with a transaction-scoped advisory lock. Any conflict, SQL
    error or invalid input rolls back catalogue updates and all imported records.
    Only committed import_run rows represent successful loads, including empty runs.
    """
    result: LoadResult = {'already_imported': False, 'catalogue_episode_count': len(prepared['catalogue']),
        'transcript_count': len(prepared['episodes']), 'chunk_count': prepared['embeddings']['chunk_count'],
        'embedding_run': prepared['embedding_run']}
    connection = psycopg2.connect(dsn)
    try:
        with connection:
            if connection.info.dbname != 'radiofrance':
                raise ValueError('Refusing imports outside the radiofrance database')
            with connection.cursor() as cursor:
                execute(cursor, 'lock')
                row = cursor.fetchone()
                if row is None or row[0] is None:
                    raise ValueError('Application tables are missing; initialize a fresh database volume with the current init scripts')
                execute(cursor, 'run_lookup', (prepared['embedding_run'],))
                previous = cursor.fetchone()
                expected = (prepared['chunk_run'], prepared['download_run'], prepared['embeddings']['show'],
                    len(prepared['catalogue']), len(prepared['episodes']), prepared['embeddings']['chunk_count'])
                if previous is not None:
                    if tuple(previous[:6]) != expected:
                        raise ValueError('Import completion provenance conflict')
                    verify_committed(cursor, prepared)
                    result['already_imported'] = True
                else:
                    strategy = register(cursor, 'chunk_strategy', prepared['chunks']['strategy'])
                    model = register(cursor, 'embedding_model', prepared['embeddings']['configuration'])
                    identities: dict[str, int] = {}
                    for entry in prepared['catalogue']:
                        uuid, suffix = entry['id'].split('_')
                        execute(cursor, 'identity', (uuid, int(suffix)))
                        identity_row = cursor.fetchone()
                        assert identity_row is not None
                        identity = int(identity_row[0])
                        identities[entry['id']] = identity
                        execute(cursor, 'catalogue', (identity, entry['show'], entry['published_iso'] or None,
                            int(entry['published_ts']), entry['title'], entry['description'], entry['web_url'],
                            entry['podcast_title'], entry['podcast_url'], entry['player_url']))
                    for episode in prepared['episodes']:
                        load_episode(cursor, episode, identities[episode['external_id']], strategy, model)
                    execute(cursor, 'run', (prepared['embedding_run'], prepared['chunk_run'], prepared['download_run'],
                        prepared['embeddings']['show'], strategy, model, len(prepared['catalogue']),
                        len(prepared['episodes']), prepared['embeddings']['chunk_count']))
    finally:
        connection.close()
    return result


def main() -> None:
    """Load a selected manifest using environment-only DB credentials."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--embedding-manifest', type=Path, required=True)
    parser.add_argument('--chunk-root', type=Path, default=Path('data/chunk-pilot'))
    parser.add_argument('--download-root', type=Path, default=Path('data/download-pilot'))
    parser.add_argument('--verify-only', action='store_true', help='Read-only verification of an already committed import')
    args = parser.parse_args()
    dsn = os.environ.get('RF_DATABASE_DSN', '')
    prepared = prepare(args.embedding_manifest, args.chunk_root, args.download_root)
    operation = verify_database if args.verify_only else load
    print(json.dumps(operation(prepared, dsn)))


if __name__ == '__main__':
    main()
