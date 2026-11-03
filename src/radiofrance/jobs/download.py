"""Acquire a show's catalogue delta and transcripts using local-file boundaries."""

import argparse
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import TypedDict
from radiofrance.libs.episode_selection import read_selection

from radiofrance.libs.catalogue import SHOWS, CatalogueRow, read_rows
from radiofrance.libs.download_state import read_download_state
from radiofrance.fetch_transcripts import aod_uuid, fetch_transcript, write_transcript_json
from radiofrance.fetch_episodes import fetch_all


class Artifact(TypedDict):
    path: str


class TranscriptArtifact(Artifact):
    episode_id: str


class DownloadManifest(TypedDict):
    version: int
    show: str
    cutoff_timestamp: int
    new_episode_count: int
    database_episode_count: int
    episodes: Artifact
    transcripts: list[TranscriptArtifact]


def artifact(path: Path, root: Path) -> Artifact:
    """Identify a published file by relative path for later staging."""
    return {'path': str(path.relative_to(root))}


def run_download(show: str, output: Path, api_key: str,
                 transcript_delay: float = 1.0, episode_selection: Path | None = None,
                 dsn: str = '') -> DownloadManifest:
    """Read PostgreSQL's watermark, fetch a metadata delta and download its transcripts.

    The database is read only and closed before acquisition. Include the watermark
    second in API retrieval, then skip its already-committed identities so unseen
    episodes sharing that second are retained. An empty show starts from zero.
    Fixed-selection mode requires an empty show and enforces exact fixture IDs and
    transcript counts. Database failures never fall back to a full download.
    Publish episodes.json, raw transcripts and a version-2 manifest atomically;
    failure leaves the database watermark and final output unchanged. No database
    writes, catalogue CSVs, S3 publication or Airflow asset events occur here.
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
    state = read_download_state(show, dsn)
    cutoff = state['cutoff_timestamp']
    selection = read_selection(episode_selection, show) if episode_selection else None
    if selection and state['episode_count']:
        raise ValueError('Fixed episode selection requires an empty show in the database')
    query_cutoff = min(e['published_ts'] for e in selection['episodes']) - 1 if selection else max(0, cutoff - 1)
    print(f"Database cutoff for {show}: {cutoff}; {state['episode_count']} committed episodes", flush=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.download-job-', dir=output.parent) as staging:
        directory = Path(staging)
        rows = fetch_all(api_key, f'https://www.radiofrance.fr/{SHOWS[show]}/podcasts/{show}', since_ts=query_cutoff)
        expected = {e['episode_id']: e for e in selection['episodes']} if selection else {}
        if selection:
            selected = []
            found: set[str] = set()
            for row in rows:
                if row['id'] in expected and row['id'] not in found:
                    selected.append(row)
                    found.add(row['id'])
                if found == set(expected):
                    break
            if found != set(expected):
                raise ValueError(f'Selected catalogue episodes unavailable: {sorted(set(expected) - found)}')
            rows = selected
        existing = set(state['existing_ids'])
        metadata: list[CatalogueRow] = []
        for row in rows:
            timestamp = row['published_ts']
            if timestamp is None or isinstance(timestamp, bool):
                raise ValueError('Missing or invalid publication timestamp')
            published = int(timestamp)
            if published < cutoff:
                raise ValueError('Download returned an episode before the database cutoff')
            if not selection and published == cutoff and row['id'] in existing:
                continue
            metadata.append({'show': row['show'], 'id': row['id'], 'published_ts': published,
                'published_iso': row['published_iso'], 'title': row['title'],
                'description': row['description'], 'web_url': row['web_url'],
                'podcast_title': row['podcast_title'], 'podcast_url': row['podcast_url'],
                'player_url': row['player_url']})
        metadata.sort(key=lambda row: (row['published_ts'], row['id']))
        episodes = directory / 'episodes.json'
        episodes.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        # Validate metadata before making transcript requests.
        new_rows = read_rows(episodes, show)
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
            if selection:
                target = expected[episode_id]
                if len(transcript['transcript']) != target['segment_count']:
                    raise ValueError(f'Selected transcript changed: {episode_id}')
            transcripts.append({**artifact(path, directory), 'episode_id': episode_id})
        manifest: DownloadManifest = {
            'version': 2, 'show': show, 'cutoff_timestamp': cutoff,
            'new_episode_count': len(new_rows), 'database_episode_count': state['episode_count'],
            'episodes': artifact(episodes, directory),
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
    """Download a show using PostgreSQL connection variables and a runtime API secret."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--show', required=True, choices=list(SHOWS))
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--transcript-delay', type=float, default=1.0)
    parser.add_argument('--api-key-file', type=Path, help='Read a runtime-mounted key file instead of the environment')
    parser.add_argument('--episode-selection', type=Path, help='Fixed episode IDs and segment counts; requires an empty show in PostgreSQL')
    args = parser.parse_args()
    api_key = args.api_key_file.read_text(encoding='utf-8') if args.api_key_file else os.environ.get('RADIOFRANCE_API_KEY', '')
    manifest = run_download(args.show, args.output_dir,
                            api_key, args.transcript_delay, args.episode_selection)
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
