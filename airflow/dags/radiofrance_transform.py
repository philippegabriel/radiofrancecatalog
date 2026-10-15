"""Transform the explicit S3 transcript list supplied by Download."""
import hashlib
import os
from pathlib import Path
from collections.abc import Mapping
from typing import Protocol, TypedDict

from airflow.sdk import Asset, AssetAlias, DAG, Param, task
from airflow.sdk.definitions.param import ParamsDict
from radiofrance_catalogue.storage import aws, s3_uri
from radiofrance_catalogue.transform import (
    ArtifactEvent, EmbeddingEvent, MODEL, DIMENSIONS, make_chunk_file,
    make_embedding_file, transcript_id,
)

class ChunkInput(TypedDict):
    episode_id: str
    chunk_uri: str


CHUNKS = AssetAlias('radiofrance-chunks')
EMBEDDINGS = AssetAlias('radiofrance-embeddings')


class EventEmitter(Protocol):
    def add(self, asset: Asset, *, extra: ArtifactEvent | EmbeddingEvent) -> None: ...


class OutletEvents(Protocol):
    def __getitem__(self, alias: AssetAlias) -> EventEmitter: ...


with DAG(
    dag_id='radiofrance_transform',
    description='Create chunks and E5 embeddings for the transcript list supplied by Download',
    doc_md="""Accept `transcripts`, an explicit list of S3 transcript JSON URIs.
No discovery or catalogue scanning occurs. Mapped chunk tasks retrieve inputs, run the existing chunker and upload chunk CSVs.
Mapped embedding tasks retrieve those CSVs, request embeddings one chunk at a time
from a persistent server and upload embedding CSVs. Each task emits its own asset event.
The model stays loaded in the server; files need no batching.

Requires AWS credentials and RF_S3_BUCKET. The embedding endpoint defaults to
http://127.0.0.1:8081/v1/embeddings; RF_EMBEDDING_URL overrides the full URL.
RF_EMBEDDING_API_KEY optionally supplies authentication. Server must serve intfloat/multilingual-e5-small
with E5-compatible tokenization and mean pooling, 384 dimensions. Output is L2 normalized.
Use an unquantized model and validate against existing SentenceTransformer vectors
before adding outputs to the existing corpus. A matching model name alone does not
prove numerical compatibility. No model download or server startup is performed.

Download can trigger this DAG with transform=true when a scheduler is running.
For CLI dags test, invoke Transform explicitly with the same transcript list.
See airflow/README.md for configuration and examples. No database import occurs.
""",
    schedule=None, catchup=False, max_active_runs=1, max_active_tasks=1,
    params=ParamsDict({'transcripts': Param([], type='array', items={'type': 'string'})}),
    tags=['radiofrance', 'transform'],
):
    @task
    def inputs(params: Mapping[str, list[str]]) -> list[str]:
        """Validate and deduplicate the supplied S3 transcript URIs; perform no discovery."""
        sources = list(dict.fromkeys(params['transcripts']))
        for source in sources:
            transcript_id(source)
        return sources

    @task(outlets=[CHUNKS])
    def chunk_transcript(source: str, run_id: str, outlet_events: OutletEvents) -> ChunkInput:
        """Retrieve one transcript and publish chunks with their source asset link.

        Write atomic local files in a run-specific workspace; upload the chunk CSV
        and emit its asset event. Return the episode ID and S3 chunk URI so the
        next task can run on another worker without sharing local files.
        """
        episode_id = transcript_id(source)
        workspace = Path(os.environ['AIRFLOW_HOME']) / 'transforms' / hashlib.sha256(run_id.encode()).hexdigest() / episode_id
        workspace.mkdir(parents=True, exist_ok=True)
        transcript = workspace / f'{episode_id}.json'
        temporary = transcript.with_suffix('.json.tmp')
        aws('s3', 'cp', source, str(temporary))
        temporary.replace(transcript)
        chunks = workspace / f'{episode_id}.chunks.csv'
        make_chunk_file(transcript, chunks)
        chunk_uri = s3_uri(f'data/chunks/{episode_id}.csv')
        aws('s3', 'cp', str(chunks), chunk_uri)
        outlet_events[CHUNKS].add(Asset(uri=chunk_uri, name=f'{episode_id}_chunks'), extra={
            'episode_id': episode_id, 'source_uri': source,
            'sha256': hashlib.sha256(chunks.read_bytes()).hexdigest(),
        })
        return {'episode_id': episode_id, 'chunk_uri': chunk_uri}

    @task(outlets=[EMBEDDINGS])
    def embed_chunks(state: ChunkInput, run_id: str, outlet_events: OutletEvents) -> None:
        """Retrieve a chunk CSV and publish its embeddings with model provenance.

        Read the chunk URI returned by chunk_transcript. Request E5 vectors from
        the persistent server, write an atomic CSV, upload it and emit an asset
        event. A failure leaves the already completed chunk task's event intact.
        """
        episode_id = state['episode_id']
        chunk_uri = state['chunk_uri']
        workspace = Path(os.environ['AIRFLOW_HOME']) / 'transforms' / hashlib.sha256(run_id.encode()).hexdigest() / episode_id
        workspace.mkdir(parents=True, exist_ok=True)
        chunks = workspace / f'{episode_id}.chunks.csv'
        temporary = chunks.with_suffix('.csv.tmp')
        aws('s3', 'cp', chunk_uri, str(temporary))
        temporary.replace(chunks)
        embeddings = workspace / f'{episode_id}.embeddings.csv'
        make_embedding_file(chunks, embeddings)
        embedding_uri = s3_uri(f'data/embeddings/{episode_id}.csv')
        aws('s3', 'cp', str(embeddings), embedding_uri)
        outlet_events[EMBEDDINGS].add(Asset(uri=embedding_uri, name=f'{episode_id}_embeddings'), extra={
            'episode_id': episode_id, 'source_uri': chunk_uri,
            'sha256': hashlib.sha256(embeddings.read_bytes()).hexdigest(),
            'model': MODEL, 'dimensions': DIMENSIONS,
        })

    chunk_inputs = chunk_transcript.expand(source=inputs())  # pyright: ignore[reportCallIssue]
    embed_chunks.expand(state=chunk_inputs)
