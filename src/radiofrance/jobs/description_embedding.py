"""Generate description vectors from a download manifest, without database access."""

import argparse
from collections.abc import Callable
import json
from pathlib import Path
import re
import tempfile
import time
from typing import TypedDict, cast

import numpy as np

from radiofrance.jobs.chunk import contained_file, validate_source
from radiofrance.jobs.embedding import (DIMENSIONS, MAX_TOKENS, MODEL_ID, MODEL_REVISION,
    QUERY_PREFIX, SentenceEncoder, cached_snapshot, default_model_cache, readable_tree,
    read_vectors, verify_model, write_json)
from radiofrance.libs.catalogue import read_rows
from radiofrance.libs.progress import EmbeddingReporter
from radiofrance.libs.storage import artifact_root

DIRECTORY = f'embeddings/episode-descriptions/{MODEL_REVISION}/runs'
TEMPLATE = 'title: {title} | text: {description}'
type Encode = Callable[[list[str]], list[list[float]]]


class Configuration(TypedDict):
    model_id: str
    revision: str
    runtime: str
    precision: str
    dimensions: int
    normalized: bool
    document_template: str
    query_prefix: str
    max_tokens: int


class Description(TypedDict):
    episode_id: str
    title: str
    description: str
    path: str


class Manifest(TypedDict):
    version: int
    stage: str
    show: str
    source_run: str
    configuration: Configuration
    episode_count: int
    descriptions: list[Description]


class Artifact(TypedDict):
    path: str


class Metadata(TypedDict):
    episodes: Artifact


def configuration() -> Configuration:
    """Identify the same normalized 256D titled-description model used by search."""
    return {'model_id': MODEL_ID, 'revision': MODEL_REVISION, 'runtime': 'sentence-transformers',
        'precision': 'float32', 'dimensions': DIMENSIONS, 'normalized': True,
        'document_template': TEMPLATE, 'query_prefix': QUERY_PREFIX, 'max_tokens': MAX_TOKENS}


def selection(source_manifest: Path) -> tuple[str, str, list[Description]]:
    """Read selected metadata and transcripts; return nonblank descriptions with external IDs."""
    source_manifest = source_manifest.resolve()
    source = validate_source(source_manifest)
    metadata = cast(Metadata, json.loads(source_manifest.read_text()))
    root = artifact_root(source_manifest.parent)
    rows = read_rows(contained_file(root, metadata['episodes']['path']), source['show'])
    ids = {row['id'] for row in rows}
    if len(rows) != source['new_episode_count'] or ids != {item['episode_id'] for item in source['transcripts']}:
        raise ValueError('Description/download membership mismatch')
    descriptions: list[Description] = []
    for row in rows:
        if not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f-]{27}_[0-9]+', row['id']):
            raise ValueError('Invalid description episode ID')
        if row['description'].strip():
            descriptions.append({'episode_id': row['id'], 'title': row['title'],
                'description': row['description'], 'path': row['id'] + '.npy'})
    source_run = (str(source_manifest.parent.relative_to(root)) if root != source_manifest.parent
        else source_manifest.parent.name)
    return source['show'], source_run, descriptions


def verify_output(output: Path, source_manifest: Path | None = None) -> Manifest:
    """Validate the complete description set, model, snapshots and one FP32 vector per episode."""
    manifest = cast(Manifest, json.loads((output / 'manifest.json').read_text()))
    if (manifest['version'] != 1 or manifest['stage'] != 'description-embedding'
            or manifest['configuration'] != configuration()):
        raise ValueError('Unexpected description embedding model or manifest')
    descriptions = manifest['descriptions']
    if manifest['episode_count'] != len(descriptions) or len({item['episode_id'] for item in descriptions}) != len(descriptions):
        raise ValueError('Description embedding count/identity mismatch')
    for item in descriptions:
        if (not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f-]{27}_[0-9]+', item['episode_id'])
                or not isinstance(item['title'], str) or not item['description'].strip()
                or item['path'] != item['episode_id'] + '.npy'):
            raise ValueError('Invalid description snapshot or path')
        read_vectors(contained_file(output, item['path']), item['episode_id'], 1)
    if source_manifest is not None:
        show, source, expected = selection(source_manifest)
        if manifest['show'] != show or manifest['source_run'] != source or descriptions != expected:
            raise ValueError('Description/download lineage or snapshot mismatch')
    return manifest


def run_embedding(source_manifest: Path, output: Path, cache: Path, batch_size: int = 4,
                  threads: int = 4, encode: Encode | None = None) -> Manifest:
    """Atomically publish offline description NPY files and a run manifest before DB loading.

    Load the model only for nonempty selections, once per job. No database client,
    credentials, download APIs or network access are required. Stage all outputs;
    failed jobs leave no completion directory. A sibling progress JSON feeds the
    parent monitor. Empty selections publish a verified empty manifest.
    """
    if output.exists():
        raise FileExistsError('Use a fresh description output directory')
    if batch_size < 1 or threads < 1:
        raise ValueError('Use positive batch and thread counts')
    show, source, descriptions = selection(source_manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    progress = EmbeddingReporter(output, len(descriptions), 0)
    with tempfile.TemporaryDirectory(prefix='.description-job-', dir=output.parent) as directory:
        staging = Path(directory)
        if descriptions and encode is None:
            progress.publish('loading_model')
            verify_model(cache)
            encoder = SentenceEncoder(cached_snapshot(cache), batch_size, threads)
            encode = encoder.encode_formatted
            progress.publish('encoding')
        for offset in range(0, len(descriptions), batch_size):
            batch = descriptions[offset:offset + batch_size]
            documents = [TEMPLATE.format(title=item['title'].strip() or 'none',
                description=item['description'].strip()) for item in batch]
            assert encode is not None
            started = time.monotonic()
            vectors = encode(documents)
            duration = time.monotonic() - started
            if len(vectors) != len(batch):
                raise ValueError('Encoder returned the wrong number of description vectors')
            for item, vector in zip(batch, vectors, strict=True):
                path = staging / item['path']
                np.save(path, np.asarray([vector], dtype=np.float32), allow_pickle=False)
                read_vectors(path, item['episode_id'], 1)
            progress.encoded(len(batch), duration)
        manifest: Manifest = {'version': 1, 'stage': 'description-embedding', 'show': show,
            'source_run': source, 'configuration': configuration(), 'episode_count': len(descriptions),
            'descriptions': descriptions}
        write_json(staging / 'manifest.json', manifest)
        verify_output(staging, source_manifest)
        readable_tree(staging)
        staging.rename(output)
    progress.publish('completed')
    return manifest


def main() -> None:
    """Run the database-free description embedding container against explicit artifacts."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download-manifest', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--model-cache', type=Path, default=default_model_cache())
    parser.add_argument('--batch-size', type=int, default=4)
    parser.add_argument('--threads', type=int, default=4)
    args = parser.parse_args()
    manifest = run_embedding(args.download_manifest, args.output_dir, args.model_cache, args.batch_size, args.threads)
    print(f"Published {manifest['episode_count']} description embeddings", flush=True)


if __name__ == '__main__':
    main()
