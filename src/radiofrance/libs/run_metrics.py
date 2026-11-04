"""Read a pipeline run's manifests and embedding sidecars without modifying artifacts."""

import json
from pathlib import Path
from typing import TypedDict, cast

from radiofrance.jobs.chunk import ChunkManifest
from radiofrance.jobs.download import DownloadManifest
from radiofrance.libs.progress import EmbeddingProgress
from radiofrance.libs.runs import run_folder
from radiofrance.libs.storage import CHUNK_DIRECTORY, EMBEDDING_DIRECTORY


class ShowMetrics(TypedDict):
    episodes: int | None
    transcripts: int | None
    chunks: int | None
    embeddings: EmbeddingProgress | None


def show_metrics(root: Path, run_id: str, show: str) -> ShowMetrics:
    """Read published stage records; absent metrics remain unknown rather than zero."""
    folder = run_folder(run_id)
    result: ShowMetrics = {'episodes': None, 'transcripts': None, 'chunks': None, 'embeddings': None}
    download = root / 'runs' / folder / 'download' / show / 'manifest.json'
    if download.is_file():
        source = cast(DownloadManifest, json.loads(download.read_text()))
        result['episodes'] = source['new_episode_count']
        result['transcripts'] = len(source['transcripts'])
    chunks = root / CHUNK_DIRECTORY / 'runs' / f'{folder}--{show}' / 'manifest.json'
    if chunks.is_file():
        chunk = cast(ChunkManifest, json.loads(chunks.read_text()))
        result['chunks'] = chunk['chunk_count']
    progress = root / EMBEDDING_DIRECTORY / 'runs' / f'{folder}--{show}.progress.json'
    if progress.is_file():
        result['embeddings'] = cast(EmbeddingProgress, json.loads(progress.read_text()))
    return result


def describe_metrics(show: str, metrics: ShowMetrics) -> str:
    """Format one show for the unified Airflow log, including measured rate and ETA."""
    text = (f"[{show}] episodes={metrics['episodes']}, transcripts={metrics['transcripts']}, "
            f"chunks={metrics['chunks']}")
    progress = metrics['embeddings']
    if progress is not None:
        text += (f"; embedding {progress['phase']}: {progress['generated']} generated, "
                 f"{progress['cached']} cached, {progress['total']} total")
        rate, remaining = progress['rate'], progress['remaining_seconds']
        if rate is not None:
            text += f"; {rate:.2f} embeddings/s (encoding only)"
            if progress['provisional']:
                text += ' [provisional]'
        if remaining is not None:
            text += f'; estimated encoding time remaining {remaining:.0f}s'
        if progress['phase'] == 'completed':
            text += f"; encoding {progress['encoding_seconds']:.1f}s, job elapsed {progress['elapsed_seconds']:.1f}s"
    return text


class RunTotals(TypedDict):
    episodes: int | None
    transcripts: int | None
    chunks: int | None
    embeddings: int | None
    generated: int | None
    cached: int | None


def run_totals(shows: dict[str, ShowMetrics]) -> RunTotals:
    """Sum known counts across shows; any unreported input makes its total unknown."""
    episodes = [item['episodes'] for item in shows.values()]
    transcripts = [item['transcripts'] for item in shows.values()]
    chunks = [item['chunks'] for item in shows.values()]
    progress = [item['embeddings'] for item in shows.values()]
    generated = sum(item['generated'] for item in progress if item is not None) if all(item is not None for item in progress) else None
    cached = sum(item['cached'] for item in progress if item is not None) if all(item is not None for item in progress) else None
    return {'episodes': sum(value for value in episodes if value is not None) if None not in episodes else None,
        'transcripts': sum(value for value in transcripts if value is not None) if None not in transcripts else None,
        'chunks': sum(value for value in chunks if value is not None) if None not in chunks else None,
        'embeddings': generated + cached if generated is not None and cached is not None else None,
        'generated': generated, 'cached': cached}
