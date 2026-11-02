"""Export, embed and load episode descriptions independently of transcript chunks."""

import argparse
from collections.abc import Callable
import json
import os
from pathlib import Path
import re
import resource
import time
from typing import TypedDict, cast

import numpy as np
import psycopg2

from radiofrance.jobs.database import canonical, execute, register
from radiofrance.jobs.embedding import (
    DIMENSIONS, MAX_TOKENS, MODEL_ID, MODEL_REVISION, QUERY_PREFIX,
    SentenceEncoder, cached_snapshot, default_model_cache, read_vectors,
    validate_vector, verify_model, write_json,
)

TEMPLATE = 'title: {title} | text: {description}'
type Encode = Callable[[list[str]], list[list[float]]]


class Episode(TypedDict):
    episode_id: int
    external_id: str
    title: str | None
    description: str


class Selection(TypedDict):
    version: int
    episodes: list[Episode]


class Model(TypedDict):
    model_id: str
    revision: str
    runtime: str
    precision: str
    dimensions: int
    normalized: bool
    document_template: str
    query_prefix: str
    max_tokens: int


class Manifest(TypedDict):
    version: int
    stage: str
    configuration: Model
    episodes: list[Episode]


class Benchmark(TypedDict):
    episodes: int
    newly_encoded: int
    resumed: int
    batch_size: int
    threads: int
    device: str
    model_load_seconds: float
    encoding_seconds: float
    elapsed_seconds: float
    peak_process_rss_mib: float


class LoadReport(TypedDict):
    model_id: int
    episodes: int
    written: int
    unchanged: int


def model_configuration() -> Model:
    """Identify the model and titled-description input convention, separate from runtime tuning."""
    return {'model_id': MODEL_ID, 'revision': MODEL_REVISION, 'runtime': 'sentence-transformers',
            'precision': 'float32', 'dimensions': DIMENSIONS, 'normalized': True,
            'document_template': TEMPLATE, 'query_prefix': QUERY_PREFIX, 'max_tokens': MAX_TOKENS}


def validate_selection(selection: Selection) -> None:
    """Require unique identities and nonblank source descriptions before inference or DB writes."""
    if selection['version'] != 1 or not selection['episodes']:
        raise ValueError('Expected a nonempty version-1 episode selection')
    ids: set[int] = set()
    names: set[str] = set()
    for episode in selection['episodes']:
        identity, external = episode['episode_id'], episode['external_id']
        if (type(identity) is not int or identity < 1 or identity in ids or external in names
                or not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f-]{27}_[0-9]+', external)
                or not isinstance(episode['description'], str) or not episode['description'].strip()
                or (episode['title'] is not None and not isinstance(episode['title'], str))):
            raise ValueError('Invalid, empty or duplicate episode metadata')
        ids.add(identity)
        names.add(external)


def export_selection(dsn: str, output: Path, limit: int = 50) -> Selection:
    """Read a balanced deterministic sample from PostgreSQL and save its metadata snapshot."""
    if limit < 1 or output.exists():
        raise ValueError('Use a positive limit and a fresh selection path')
    connection = psycopg2.connect(dsn)
    try:
        connection.set_session(readonly=True, isolation_level='REPEATABLE READ')
        with connection:
            with connection.cursor() as cursor:
                execute(cursor, 'description_selection', (limit,))
                episodes: list[Episode] = [{'episode_id': row[0], 'external_id': row[1],
                    'title': row[2], 'description': row[3]} for row in cursor.fetchall()]
    finally:
        connection.close()
    selection: Selection = {'version': 1, 'episodes': episodes}
    validate_selection(selection)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, selection)
    return selection


def document(episode: Episode) -> str:
    """Render Google's titled-document prompt without adding a second document prefix."""
    title = (episode['title'] or '').strip() or 'none'
    return TEMPLATE.format(title=title, description=episode['description'].strip())


def run_embedding(selection_path: Path, output: Path, cache: Path,
                  batch_size: int = 4, threads: int = 4, encode: Encode | None = None, device: str = 'cpu') -> Benchmark:
    """Resume immutable per-episode NPY checkpoints and publish a completion manifest.

    Uses one offline CPU or CUDA FP32 model per invocation. Metadata snapshots and model
    settings must match on retry. Completed files are validated before being skipped;
    failed batches are not published. Use one writer per output directory.
    """
    started = time.monotonic()
    if batch_size < 1 or threads < 1 or device not in ('cpu', 'cuda'):
        raise ValueError('Use positive batch/thread counts and cpu or cuda')
    selection = cast(Selection, json.loads(selection_path.read_text()))
    validate_selection(selection)
    manifest: Manifest = {'version': 1, 'stage': 'episode-description',
                          'configuration': model_configuration(), 'episodes': selection['episodes']}
    output.mkdir(parents=True, exist_ok=True)
    run = output / 'run.json'
    if run.exists():
        if json.loads(run.read_text()) != manifest:
            raise ValueError('Metadata or model changed; use a fresh output directory')
    elif any(output.iterdir()):
        raise ValueError('Output files exist without run metadata')
    else:
        write_json(run, manifest)
    expected = {episode['external_id'] + '.npy' for episode in manifest['episodes']}
    if {path.name for path in output.glob('*.npy')} - expected:
        raise ValueError('Unexpected episode checkpoint')
    pending: list[Episode] = []
    for episode in manifest['episodes']:
        path = output / (episode['external_id'] + '.npy')
        if path.exists():
            read_vectors(path, episode['external_id'], 1)
        else:
            pending.append(episode)
    loading = time.monotonic()
    if pending and encode is None:
        verify_model(cache)
        runtime = SentenceEncoder(cached_snapshot(cache), batch_size, threads, device)
        encode = runtime.encode_formatted
    load_seconds = time.monotonic() - loading
    encoding_seconds = 0.0
    for offset in range(0, len(pending), batch_size):
        batch = pending[offset:offset + batch_size]
        if encode is None:
            raise RuntimeError('Missing encoder')
        before = time.monotonic()
        vectors = encode([document(episode) for episode in batch])
        encoding_seconds += time.monotonic() - before
        if len(vectors) != len(batch):
            raise ValueError('Encoder returned the wrong number of vectors')
        for vector in vectors:
            validate_vector(vector)
        for episode, vector in zip(batch, vectors, strict=True):
            path = output / (episode['external_id'] + '.npy')
            temporary = path.with_suffix('.npy.tmp')
            with temporary.open('wb') as stream:
                np.save(stream, np.array([vector], dtype=np.float32), allow_pickle=False)
            # Validate the stored FP32 values before publishing this checkpoint.
            validate_vector(np.asarray(vector, dtype=np.float32).tolist())
            temporary.replace(path)
        print(f'Embedded {min(offset + batch_size, len(pending))}/{len(pending)} descriptions', flush=True)
    for episode in manifest['episodes']:
        read_vectors(output / (episode['external_id'] + '.npy'), episode['external_id'], 1)
    write_json(output / 'manifest.json', manifest)
    report: Benchmark = {'episodes': len(manifest['episodes']), 'newly_encoded': len(pending),
        'resumed': len(manifest['episodes']) - len(pending), 'batch_size': batch_size, 'threads': threads, 'device': device,
        'model_load_seconds': load_seconds, 'encoding_seconds': encoding_seconds,
        'elapsed_seconds': time.monotonic() - started,
        'peak_process_rss_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}
    write_json(output / 'benchmark.json', report)
    return report


def load(directory: Path, dsn: str) -> LoadReport:
    """Validate NPY artifacts, reject stale metadata, then atomically upsert description vectors.

    Equal snapshots must reproduce stored vectors; changed metadata can replace an
    older description embedding. Source rows are locked against concurrent edits.
    No transcript/chunk rows are modified. Repeated imports skip unchanged rows.
    """
    manifest = cast(Manifest, json.loads((directory / 'manifest.json').read_text()))
    if (manifest['version'] != 1 or manifest['stage'] != 'episode-description'
            or manifest['configuration'] != model_configuration()):
        raise ValueError('Unexpected description model or manifest')
    validate_selection({'version': 1, 'episodes': manifest['episodes']})
    vectors = {episode['episode_id']: read_vectors(directory / (episode['external_id'] + '.npy'),
               episode['external_id'], 1)[0]['embedding'] for episode in manifest['episodes']}
    connection = psycopg2.connect(dsn)
    written = 0
    try:
        if connection.info.dbname != 'radiofrance':
            raise ValueError('Refusing imports outside radiofrance')
        with connection:
            with connection.cursor() as cursor:
                execute(cursor, 'lock')
                execute(cursor, 'description_sources', (list(vectors),))
                sources = {row[0]: tuple(row[1:]) for row in cursor.fetchall()}
                for episode in manifest['episodes']:
                    if sources.get(episode['episode_id']) != (episode['external_id'], episode['title'], episode['description']):
                        raise ValueError('Episode metadata changed or disappeared since export')
                model = register(cursor, 'embedding_model', manifest['configuration'])
                for episode in manifest['episodes']:
                    identity = episode['episode_id']
                    vector = vectors[identity]
                    execute(cursor, 'description_lookup', (identity, model))
                    previous = cursor.fetchone()
                    if previous is not None and tuple(previous[:2]) == (episode['title'], episode['description']):
                        if not np.allclose(json.loads(previous[2]), vector, rtol=0, atol=1e-7):
                            raise ValueError('Conflicting vector for the same description snapshot')
                        continue
                    execute(cursor, 'description_upsert', (identity, model, episode['title'],
                            episode['description'], canonical(vector)))
                    written += 1
    finally:
        connection.close()
    return {'model_id': model, 'episodes': len(vectors), 'written': written, 'unchanged': len(vectors) - written}


def main() -> None:
    """Run explicit export, offline embedding, or transactional load; no AWS operations."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    export = commands.add_parser('export')
    export.add_argument('--output', type=Path, required=True)
    export.add_argument('--limit', type=int, default=50)
    embed = commands.add_parser('embed')
    embed.add_argument('--selection', type=Path, required=True)
    embed.add_argument('--output-dir', type=Path, required=True)
    embed.add_argument('--model-cache', type=Path, default=default_model_cache())
    embed.add_argument('--batch-size', type=int, default=4)
    embed.add_argument('--threads', type=int, default=4)
    embed.add_argument('--device', choices=('cpu', 'cuda'), default='cpu')
    ingest = commands.add_parser('load')
    ingest.add_argument('--input-dir', type=Path, required=True)
    args = parser.parse_args()
    dsn = os.environ.get('RF_DATABASE_DSN', '')
    if args.command == 'export':
        result: object = export_selection(dsn, args.output, args.limit)
    elif args.command == 'embed':
        result = run_embedding(args.selection, args.output_dir, args.model_cache, args.batch_size, args.threads, device=args.device)
    else:
        result = load(args.input_dir, dsn)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
