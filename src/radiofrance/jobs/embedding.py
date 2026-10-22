"""Generate immutable, normalized 256D EmbeddingGemma 2 artifacts from a chunk run."""

import argparse
import csv
import hashlib
import importlib
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Protocol, TypedDict, cast

from radiofrance.jobs.chunk import ChunkManifest, contained_file, sha256, verify_output as verify_chunks

MODEL_ID = 'google/embeddinggemma-2'
MODEL_REVISION = '914f7f89142e33e77833254d9c9b90c3cef7303b'
DIMENSIONS = 256
DOCUMENT_PREFIX = 'title: none | text: '
QUERY_PREFIX = 'task: search result | query: '
MAX_TOKENS = 8192


class FileArtifact(TypedDict):
    path: str
    sha256: str


class ModelManifest(TypedDict):
    version: int
    model_id: str
    revision: str
    files: list[FileArtifact]


class Configuration(TypedDict):
    model_id: str
    revision: str
    runtime: str
    precision: str
    dimensions: int
    normalized: bool
    document_prefix: str
    query_prefix: str
    max_tokens: int
    batch_size: int
    threads: int
    model_manifest_sha256: str
    code_sha256: str
    packages: dict[str, str]


class EmbeddingRow(TypedDict):
    episode_id: str
    chunk_index: int
    embedding: list[float]


class EmbeddingArtifact(FileArtifact):
    episode_id: str
    source_sha256: str
    chunk_count: int


class EmbeddingManifest(TypedDict):
    version: int
    stage: str
    show: str
    source_run: str
    source_manifest_sha256: str
    configuration: Configuration
    episode_count: int
    chunk_count: int
    embeddings: list[EmbeddingArtifact]


class Encoder(Protocol):
    """Minimal interface used to test publication independently of model downloads."""

    def encode(self, texts: list[str]) -> list[list[float]]: ...


def write_json(path: Path, value: object) -> None:
    """Write UTF-8 metadata without accepting nonfinite JSON values."""
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def readable_tree(root: Path) -> None:
    """Publish artifacts readable by the distinct Airflow UID, even under umask 077."""
    for path in root.rglob('*'):
        path.chmod(0o755 if path.is_dir() else 0o644)
    root.chmod(0o755)


def default_model_cache() -> Path:
    """Return the standard Hub cache, honoring HF_HUB_CACHE, HF_HOME and XDG_CACHE_HOME."""
    home = Path(os.environ.get('HF_HOME', str(Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'huggingface')))
    return Path(os.environ.get('HF_HUB_CACHE', str(home / 'hub'))).expanduser()


def cached_snapshot(cache: Path) -> Path:
    """Find the exact revision downloaded by hf, without downloading or writing cache data."""
    repository = cache / ('models--' + MODEL_ID.replace('/', '--'))
    snapshot = repository / 'snapshots' / MODEL_REVISION
    if not snapshot.is_dir() or not snapshot.resolve().is_relative_to(repository.resolve()):
        raise ValueError(f'Model revision is not cached in {cache}. Run hf download {MODEL_ID} --revision {MODEL_REVISION}')
    return snapshot


def verify_model(cache: Path) -> ModelManifest:
    """Read a standard Hub snapshot and fingerprint files without modifying the cache.

    Snapshot symlinks may use repository blobs backed by the shared Hub store. Reject
    escaping/broken links and verify content-addressed blob names against bytes.
    Return provenance to publish with the output, rather than writing in HF's cache.
    """
    snapshot = cached_snapshot(cache)
    repository = snapshot.parent.parent.resolve()
    files: list[FileArtifact] = []
    for path in sorted(snapshot.rglob('*')):
        if path.is_symlink() and not path.is_file():
            raise ValueError('Model snapshot contains a broken cache link; run hf download again')
        if not path.is_file():
            continue
        target = path.resolve()
        if not (target.is_relative_to(repository) or target.is_relative_to(cache.resolve() / 'blobs')):
            raise ValueError('Model cache link escapes its repository')
        checksum = sha256(path)
        if path.is_symlink():
            link = path.parent / path.readlink()
            if link.parent.resolve() != repository / 'blobs':
                raise ValueError('Model cache link escapes its repository blobs')
            blob_name = link.name
            if re.fullmatch(r'[0-9a-f]{64}', blob_name):
                if checksum != blob_name:
                    raise ValueError('Corrupt model cache blob')
            elif re.fullmatch(r'[0-9a-f]{40}', blob_name):
                digest = hashlib.sha1(usedforsecurity=False)
                digest.update(f'blob {path.stat().st_size}\0'.encode())
                with path.open('rb') as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(block)
                if digest.hexdigest() != blob_name:
                    raise ValueError('Corrupt model cache blob')
            else:
                raise ValueError('Unexpected model cache blob name')
        files.append({'path': str(path.relative_to(snapshot)), 'sha256': checksum})
    if not {'model.safetensors', 'config.json', 'tokenizer.json', 'modules.json', 'chat_template.jinja'} <= {item['path'] for item in files}:
        raise ValueError('Model snapshot is incomplete; run hf download again')
    return {'version': 1, 'model_id': MODEL_ID, 'revision': MODEL_REVISION, 'files': files}


def model_digest(manifest: ModelManifest) -> str:
    """Hash the canonical model inventory serialized alongside embedding outputs."""
    content = json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def validate_input(path: Path) -> ChunkManifest:
    """Validate only the chunk artifacts listed by the supplied completion manifest."""
    source = verify_chunks(path.parent)
    if path.name != 'manifest.json':
        raise ValueError('Expected a chunk-run manifest.json')
    for item in source['chunks']:
        if not re.fullmatch(r'[A-Za-z0-9_-]+', item['episode_id']):
            raise ValueError('Invalid episode ID')
    return source


def validate_vector(vector: list[float]) -> None:
    """Reject nonfinite, wrongly sized, zero or unnormalized output vectors."""
    if len(vector) != DIMENSIONS or any(type(x) not in (int, float) or not math.isfinite(x) for x in vector):
        raise ValueError('Expected 256 finite embedding coordinates')
    if abs(sum(x*x for x in vector) - 1) > 1e-4:
        raise ValueError('Embedding is not unit-normalized')


class SentenceEncoder:
    """Load Google's text-only FP32 CPU model once, with no network access."""

    def __init__(self, directory: Path, batch_size: int, threads: int) -> None:
        torch = importlib.import_module('torch')
        sentence = importlib.import_module('sentence_transformers')
        torch.set_num_threads(threads)
        torch.set_num_interop_threads(1)
        self.model = sentence.SentenceTransformer(str(directory), device='cpu', local_files_only=True,
            config_kwargs={'vision_config': None, 'audio_config': None}, model_kwargs={'dtype': torch.float32})
        self.batch_size = batch_size
        self.model.max_seq_length = MAX_TOKENS

    def encode(self, texts: list[str]) -> list[list[float]]:
        """Apply the document prefix, reject overlength inputs, truncate and normalize."""
        formatted = [DOCUMENT_PREFIX + text for text in texts]
        lengths = [len(tokens) for tokens in self.model.tokenizer(formatted, truncation=False)['input_ids']]
        if any(length > MAX_TOKENS for length in lengths):
            raise ValueError('Chunk exceeds model token limit; refusing silent truncation')
        return cast(list[list[float]], self.model.encode(formatted, batch_size=self.batch_size,
            truncate_dim=DIMENSIONS, normalize_embeddings=True, show_progress_bar=False).tolist())


def verify_output(directory: Path, source_manifest: Path | None = None) -> EmbeddingManifest:
    """Verify output identities, checksums, 256D norms, counts and optional source lineage."""
    manifest = cast(EmbeddingManifest, json.loads((directory / 'manifest.json').read_text()))
    config = manifest['configuration']
    if manifest['version'] not in (1, 2) or manifest['stage'] != 'embedding':
        raise ValueError('Expected a version-1 or version-2 embedding manifest')
    if (config['model_id'], config['revision'], config['runtime'], config['precision'], config['dimensions'], config['normalized'],
        config['document_prefix'], config['query_prefix'], config['max_tokens']) != (
        MODEL_ID, MODEL_REVISION, 'sentence-transformers', 'float32', DIMENSIONS, True,
        DOCUMENT_PREFIX, QUERY_PREFIX, MAX_TOKENS):
        raise ValueError('Unexpected embedding configuration')
    if manifest['version'] == 2:
        inventory_path = contained_file(directory, 'model-manifest.json')
        inventory = cast(ModelManifest, json.loads(inventory_path.read_text()))
        if (sha256(inventory_path) != config['model_manifest_sha256']
                or inventory['version'] != 1 or inventory['model_id'] != MODEL_ID or inventory['revision'] != MODEL_REVISION):
            raise ValueError('Model inventory does not match embedding provenance')
    seen: set[str] = set()
    paths: set[str] = set()
    total = 0
    for item in manifest['embeddings']:
        episode = item['episode_id']
        if not re.fullmatch(r'[A-Za-z0-9_-]+', episode) or episode in seen or item['path'] in paths:
            raise ValueError('Invalid or duplicate embedding episode or path')
        seen.add(episode)
        paths.add(item['path'])
        path = contained_file(directory, item['path'])
        if path.name != f'{episode}.jsonl' or sha256(path) != item['sha256']:
            raise ValueError('Embedding identity or checksum mismatch')
        count = 0
        with path.open(encoding='utf-8') as stream:
            for index, line in enumerate(stream):
                row = cast(EmbeddingRow, json.loads(line))
                if row['episode_id'] != episode or type(row['chunk_index']) is not int or row['chunk_index'] != index:
                    raise ValueError('Embedding row identity or index mismatch')
                validate_vector(row['embedding'])
                count += 1
        if count != item['chunk_count']:
            raise ValueError('Embedding row count mismatch')
        total += count
    if manifest['episode_count'] != len(seen) or manifest['chunk_count'] != total:
        raise ValueError('Embedding manifest totals mismatch')
    if source_manifest is not None:
        source = validate_input(source_manifest)
        expected = {item['episode_id']: (item['sha256'], item['chunk_count']) for item in source['chunks']}
        actual = {item['episode_id']: (item['source_sha256'], item['chunk_count']) for item in manifest['embeddings']}
        if (manifest['source_manifest_sha256'] != sha256(source_manifest) or manifest['source_run'] != source_manifest.parent.name
                or manifest['show'] != source['show'] or expected != actual):
            raise ValueError('Embedding output does not match its chunk source')
    return manifest


def run_embedding(source_manifest: Path, output: Path, model_cache: Path, batch_size: int = 1,
                  threads: int = 4, encoder: Encoder | None = None) -> EmbeddingManifest:
    """Embed a completed chunk run and atomically publish immutable per-episode JSONL.

    Input/model mounts may be read-only. Load once per job, then reuse across all
    episodes. Empty runs publish without importing ML libraries. No database or
    network access occurs. Failures remove staging; existing outputs are rejected.
    The optional encoder is an offline-test seam, never a CLI substitution.
    """
    if output.exists():
        raise FileExistsError(f'Use a fresh output directory: {output}')
    if batch_size < 1 or threads < 1:
        raise ValueError('Batch size and threads must be positive')
    source = validate_input(source_manifest)
    source_hash = sha256(source_manifest)
    model_inventory = verify_model(model_cache)
    model_hash = model_digest(model_inventory)
    config: Configuration = {'model_id': MODEL_ID, 'revision': MODEL_REVISION,
        'runtime': 'sentence-transformers', 'precision': 'float32', 'dimensions': DIMENSIONS,
        'normalized': True, 'document_prefix': DOCUMENT_PREFIX, 'query_prefix': QUERY_PREFIX,
        'max_tokens': MAX_TOKENS, 'batch_size': batch_size, 'threads': threads,
        'model_manifest_sha256': model_hash, 'code_sha256': sha256(Path(__file__)),
        'packages': {name: version(name) for name in ('torch', 'transformers', 'sentence-transformers')} if encoder is None and source['chunk_count'] else {}}
    if source['chunk_count'] and encoder is None:
        encoder = SentenceEncoder(cached_snapshot(model_cache), batch_size, threads)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.embedding-job-', dir=output.parent) as temporary:
        staging = Path(temporary)
        artifacts: list[EmbeddingArtifact] = []
        for item in source['chunks']:
            path = contained_file(source_manifest.parent, item['path'])
            with path.open(encoding='utf-8', newline='') as stream:
                rows = list(csv.reader(stream))
            target = staging / f"{item['episode_id']}.jsonl"
            with target.open('w', encoding='utf-8') as stream:
                for offset in range(0, len(rows), batch_size):
                    if encoder is None:
                        raise RuntimeError('Missing encoder for nonempty source')
                    batch = rows[offset:offset+batch_size]
                    vectors = encoder.encode([row[-1] for row in batch])
                    if len(vectors) != len(batch):
                        raise ValueError('Model returned the wrong number of vectors')
                    for index, vector in enumerate(vectors, start=offset):
                        validate_vector(vector)
                        record: EmbeddingRow = {'episode_id': item['episode_id'], 'chunk_index': index, 'embedding': vector}
                        stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n')
            if sha256(path) != item['sha256']:
                raise ValueError('Chunk source changed during embedding')
            artifacts.append({'episode_id': item['episode_id'], 'path': target.name,
                'sha256': sha256(target), 'source_sha256': item['sha256'], 'chunk_count': len(rows)})
            print(f"Embedded {len(rows)} chunks: {item['episode_id']}", flush=True)
        manifest: EmbeddingManifest = {'version': 2, 'stage': 'embedding', 'show': source['show'],
            'source_run': source_manifest.parent.name, 'source_manifest_sha256': source_hash,
            'configuration': config, 'episode_count': len(artifacts),
            'chunk_count': sum(item['chunk_count'] for item in artifacts), 'embeddings': artifacts}
        write_json(staging / 'model-manifest.json', model_inventory)
        write_json(staging / 'manifest.json', manifest)
        verify_output(staging, source_manifest)
        if model_digest(verify_model(model_cache)) != model_hash:
            raise ValueError('Model cache changed during embedding')
        readable_tree(staging)
        staging.rename(output)
    return manifest


def main() -> None:
    """Embed one chunk run offline using the pinned revision in the standard HF cache."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-cache', type=Path, default=default_model_cache(), help='Hub cache root (default: HF_HUB_CACHE or ~/.cache/huggingface/hub)')
    parser.add_argument('--chunk-manifest', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--batch-size', type=int, default=1)
    parser.add_argument('--threads', type=int, default=4)
    args = parser.parse_args()
    result = run_embedding(args.chunk_manifest, args.output_dir, args.model_cache, args.batch_size, args.threads)
    print(f"Published {result['chunk_count']} normalized 256D embeddings: {args.output_dir}/manifest.json")


if __name__ == '__main__':
    main()
