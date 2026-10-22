"""Acquire a show's catalogue delta and transcripts using local-file boundaries."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from typing import TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parent / 'airflow/dags'))
from radiofrance_catalogue.catalogue import SHOWS, merge, read_rows
from fetchtranscript import aod_uuid, fetch_transcript, write_transcript_json
from rf_dump import fetch_all, write_catalogue_csv


class Artifact(TypedDict):
    path: str
    sha256: str


class TranscriptArtifact(Artifact):
    episode_id: str


class DownloadManifest(TypedDict):
    version: int
    show: str
    cutoff_timestamp: int
    baseline_sha256: str
    new_episode_count: int
    catalogue_episode_count: int
    updates: Artifact
    catalogue: Artifact
    transcripts: list[TranscriptArtifact]


def artifact(path: Path, root: Path) -> Artifact:
    """Identify a published file by relative path and SHA256 for later staging."""
    return {'path': str(path.relative_to(root)),
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def run_download(show: str, baseline: Path, output: Path, api_key: str,
                 transcript_delay: float = 1.0) -> DownloadManifest:
    """Fetch newer episodes, merge a catalogue, and download their raw transcripts.

    Read a required local baseline; its greatest publication timestamp is the
    exclusive cutoff. Reuse the existing Radio France clients and CSV merger.
    Publish a new directory and manifest only after all downloads succeed. Completed
    artifacts are readable across local container users (directories 0755, files
    0644); staging remains private during acquisition. An
    unavailable transcript fails this pilot, matching the existing Download DAG.
    No S3 publication, Airflow events or database writes occur here.
    """
    if show not in SHOWS:
        raise ValueError(f'Unsupported show: {show}')
    if not api_key.strip():
        raise ValueError('RADIOFRANCE_API_KEY is missing or empty. Export it before running the download job.')
    api_key = api_key.strip()
    if transcript_delay < 0:
        raise ValueError('Transcript delay must be nonnegative')
    if output.exists():
        raise FileExistsError(f'Use a fresh output directory: {output}')
    baseline_bytes = baseline.read_bytes()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.download-job-', dir=output.parent) as staging:
        directory = Path(staging)
        # Snapshot the baseline so concurrent local edits cannot change the merge.
        baseline_copy = directory / 'baseline.csv'
        baseline_copy.write_bytes(baseline_bytes)
        baseline_rows = read_rows(baseline_copy, show)
        cutoff = max((int(row['published_ts']) for row in baseline_rows), default=0)
        rows = fetch_all(api_key, f'https://www.radiofrance.fr/{SHOWS[show]}/podcasts/{show}', since_ts=cutoff)
        updates = directory / 'updates.csv'
        with updates.open('w', encoding='utf-8', newline='') as stream:
            write_catalogue_csv(rows, stream)
        # Validate IDs, timestamps, columns and uniqueness before transcript requests.
        new_rows = read_rows(updates, show)
        if any(int(row['published_ts']) <= cutoff for row in new_rows):
            raise ValueError('Download returned episodes at or before the cutoff')
        catalogue = directory / f'{show}.csv'
        count = merge(baseline_copy, updates, catalogue, show)
        transcript_dir = directory / 'transcripts'
        transcript_dir.mkdir()
        transcripts: list[TranscriptArtifact] = []
        for index, row in enumerate(new_rows):
            episode_id = row['id']
            if not re.fullmatch(r'[A-Za-z0-9_-]+', episode_id):
                raise ValueError('Episode ID is not a safe filename')
            if index and transcript_delay:
                time.sleep(transcript_delay)
            transcript = fetch_transcript(aod_uuid(episode_id))
            if transcript is None:
                raise RuntimeError(f'Transcript unavailable for {episode_id}')
            path = transcript_dir / f'{episode_id}.json'
            with path.open('w', encoding='utf-8') as stream:
                write_transcript_json(transcript, stream)
                stream.write('\n')
            transcripts.append({**artifact(path, directory), 'episode_id': episode_id})
        manifest: DownloadManifest = {
            'version': 1, 'show': show, 'cutoff_timestamp': cutoff,
            'baseline_sha256': hashlib.sha256(baseline_bytes).hexdigest(),
            'new_episode_count': len(new_rows), 'catalogue_episode_count': count,
            'updates': artifact(updates, directory), 'catalogue': artifact(catalogue, directory),
            'transcripts': transcripts,
        }
        (directory / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        # TemporaryDirectory starts at 0700. Publish public podcast artifacts with
        # read/traverse permissions for Airflow's separate container user, even
        # under a restrictive umask. The API key is never copied into this tree.
        for path in directory.rglob('*'):
            path.chmod(0o755 if path.is_dir() else 0o644)
        directory.chmod(0o755)
        directory.rename(output)
    return manifest


def main() -> None:
    """Run one explicit show from local files, reading the API secret from the environment."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--show', required=True, choices=list(SHOWS))
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--transcript-delay', type=float, default=1.0)
    parser.add_argument('--api-key-file', type=Path, help='Read a runtime-mounted key file instead of the environment')
    args = parser.parse_args()
    api_key = args.api_key_file.read_text(encoding='utf-8') if args.api_key_file else os.environ.get('RADIOFRANCE_API_KEY', '')
    manifest = run_download(args.show, args.baseline, args.output_dir,
                            api_key, args.transcript_delay)
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
