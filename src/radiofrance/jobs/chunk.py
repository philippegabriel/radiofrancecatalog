"""Chunk the explicit transcript set from a completed local download manifest."""

import argparse
import csv
import json
from pathlib import Path
import re
import tempfile
from typing import TypedDict, cast


class TranscriptArtifact(TypedDict):
    episode_id: str
    path: str


class DownloadManifest(TypedDict):
    version: int
    show: str
    new_episode_count: int
    transcripts: list[TranscriptArtifact]


class ChunkArtifact(TranscriptArtifact):
    chunk_count: int


class ChunkStrategy(TypedDict):
    name: str
    version: int
    target_size: int
    min_size: int


class ChunkManifest(TypedDict):
    version: int
    stage: str
    show: str
    source_run: str
    strategy: ChunkStrategy
    episode_count: int
    chunk_count: int
    chunks: list[ChunkArtifact]



def contained_file(root: Path, relative: str) -> Path:
    """Resolve a manifest path, rejecting absolute paths and escaping symlinks."""
    if Path(relative).is_absolute():
        raise ValueError('Artifact paths must be relative')
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('Artifact must be a file inside its run directory')
    return path


def validate_source(path: Path) -> DownloadManifest:
    """Validate download completion and each listed transcript's identity and existence.

    Only transcripts in the manifest become work; unrelated files are ignored.
    This function uses local files only and can run in Airflow's preflight task.
    """
    raw: object = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(raw, dict) or raw.get('version') != 1 or not isinstance(raw.get('show'), str):
        raise ValueError('Expected a version-1 download manifest')
    if not isinstance(raw.get('transcripts'), list):
        raise ValueError('Expected a transcript list')
    manifest = cast(DownloadManifest, raw)
    if type(manifest.get('new_episode_count')) is not int or manifest['new_episode_count'] != len(manifest['transcripts']):
        raise ValueError('Episode count does not match the transcript list')
    seen: set[str] = set()
    for item in manifest['transcripts']:
        if not isinstance(item, dict) or not all(isinstance(item.get(key), str) for key in ('episode_id', 'path')):
            raise ValueError('Invalid transcript artifact')
        episode = item['episode_id']
        if not re.fullmatch(r'[A-Za-z0-9_-]+', episode) or episode in seen:
            raise ValueError('Invalid or duplicate episode ID')
        seen.add(episode)
        source = contained_file(path.parent, item['path'])
        if source.name != f'{episode}.json':
            raise ValueError(f'Transcript identity mismatch: {episode}')
    return manifest


def verify_output(directory: Path) -> ChunkManifest:
    """Read a completed chunk run and verify file presence, row identities and counts."""
    manifest = cast(ChunkManifest, json.loads((directory / 'manifest.json').read_text(encoding='utf-8')))
    if manifest['version'] != 1 or manifest['stage'] != 'chunking':
        raise ValueError('Expected a version-1 chunk manifest')
    strategy = manifest['strategy']
    manifest['strategy'] = {
        'name': strategy['name'], 'version': strategy['version'],
        'target_size': strategy['target_size'], 'min_size': strategy['min_size']}
    total = 0
    seen: set[str] = set()
    for item in manifest['chunks']:
        if item['episode_id'] in seen:
            raise ValueError('Duplicate output episode')
        seen.add(item['episode_id'])
        path = contained_file(directory, item['path'])
        with path.open(encoding='utf-8', newline='') as stream:
            rows = list(csv.reader(stream))
        if len(rows) != item['chunk_count'] or any(
            len(row) != 7 or row[0] != item['episode_id'] or row[1] != str(index)
            for index, row in enumerate(rows)
        ):
            raise ValueError(f'Invalid chunk rows: {path.name}')
        total += len(rows)
    if manifest['episode_count'] != len(seen) or manifest['chunk_count'] != total:
        raise ValueError('Output manifest counts do not match its files')
    return manifest


def run_chunking(source_manifest: Path, output: Path) -> ChunkManifest:
    """Reuse the current chunker and atomically publish CSVs and versioned provenance.

    Inputs are immutable local transcript artifacts identified by a download
    manifest. No cloud, network, model or database access occurs. An empty
    transcript list publishes an empty successful manifest. Existing outputs
    are never overwritten; failures remove staging and leave no final directory.
    """
    # Airflow imports the verification helpers without needing the chunker installed.
    from radiofrance.chunk_json_to_csv import MIN_SIZE, TARGET_SIZE, transcript_to_csv

    if output.exists():
        raise FileExistsError(f'Use a fresh output directory: {output}')
    source = validate_source(source_manifest)
    strategy: ChunkStrategy = {'name': 'segment-characters', 'version': 1,
                              'target_size': TARGET_SIZE, 'min_size': MIN_SIZE}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.chunk-job-', dir=output.parent) as temporary:
        staging = Path(temporary)
        artifacts: list[ChunkArtifact] = []
        for item in source['transcripts']:
            transcript = contained_file(source_manifest.parent, item['path'])
            target = staging / f"{item['episode_id']}.csv"
            with target.open('w', encoding='utf-8', newline='') as stream:
                chunk_count = transcript_to_csv(transcript, stream)
            artifacts.append({'episode_id': item['episode_id'], 'path': target.name,
                              'chunk_count': chunk_count})
        manifest: ChunkManifest = {
            'version': 1, 'stage': 'chunking', 'show': source['show'],
            'source_run': source_manifest.parent.name,
            'strategy': strategy, 'episode_count': len(artifacts),
            'chunk_count': sum(item['chunk_count'] for item in artifacts), 'chunks': artifacts,
        }
        (staging / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        verify_output(staging)
        for path in staging.iterdir():
            path.chmod(0o644)
        staging.chmod(0o755)
        staging.rename(output)
    return manifest


def main() -> None:
    """Chunk one completed download run and print its successful manifest."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download-manifest', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run_chunking(args.download_manifest, args.output_dir), indent=2))


if __name__ == '__main__':
    main()
