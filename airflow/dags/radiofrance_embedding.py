"""Embed a completed chunk run with offline SentenceTransformers FP32."""

from collections.abc import Mapping
from datetime import timedelta
import hashlib
import os
from pathlib import Path
import re
from typing import TypedDict

from airflow.sdk import DAG, Param, task
from airflow.sdk.definitions.param import ParamsDict
from airflow.providers.docker.operators.docker import DockerOperator
from docker.types import Mount

from chunk_job import sha256
from embedding_job import validate_input, verify_output


class EmbeddingRun(TypedDict):
    chunk_run: str
    output_folder: str
    source_sha256: str


CHUNKS = Path('/opt/radiofrance/chunks')
OUTPUTS = Path('/opt/radiofrance/embeddings')
CACHE_HOST = os.environ.get('RF_HF_HUB_CACHE', '')
HOST = os.environ.get('RF_PROJECT_ROOT') or '/not-configured'


with DAG(
    dag_id='radiofrance_embedding',
    description='Generate and verify normalized 256D EmbeddingGemma 2 vectors from a chunk run.',
    doc_md="""
## Purpose and flow
`prepare_embedding` verifies the selected chunk manifest and cache mount configuration.
`embed` runs the SentenceTransformers CPU container, loading Google's text-only
EmbeddingGemma 2 once in FP32 and publishing normalized 256-dimensional vectors.
`verify_embeddings` checks output hashes, row identities, vector norms, totals,
and source/model provenance. There are no database writes or cloud dependencies.

## Configuration and running
Follow embedding/README.md: build radiofrance-embedding:pilot, prepare the pinned
Google snapshot with hf download in the standard Hugging Face cache, create
 data/embedding-pilot, and recreate Compose. The host Docker socket and UID/GID
settings are shared with the download/chunk stages. Trigger this DAG manually
for RF_SHOW (default mecaniques-du-journalisme), using the same deployment setting.
with `chunk_run`, the basename of a completed data/chunk-pilot/airflow-<hash> run.
RF_HF_HUB_CACHE in .env gives the absolute host Hub cache path. No API key or Airflow model dependencies are required. The processing container
has no network, and input/model mounts are read-only. Only the host hf download command needs internet access. The container rejects missing
or corrupt cached snapshots before loading the model; Airflow does not mount the user cache. Container memory is limited to 4 GiB.

## Outputs and recovery
Per-episode JSONL vectors and a completion manifest are published atomically to
 data/embedding-pilot/airflow-<run-hash>/. Existing output is never overwritten.
Empty chunk runs publish empty output without loading the model. Trigger a new
DAG run after processing failure; automatic retries are disabled. Verification
alone can be cleared and retried against an existing completed output.
A model checksum inventory is published with the output, without writing to the HF cache. The manifest records model revision, FP32, prefixes, normalization, dimensions,
package versions, code checksum, model checksum and chunk-source checksums.
""",
    schedule=None, catchup=False, max_active_runs=1, default_args={'retries': 0},
    params=ParamsDict({'chunk_run': Param('', type='string', description='Completed chunk folder: airflow-<64-character hash>')}),
    tags=['radiofrance', 'embedding'],
) as dag:
    @task(multiple_outputs=False)
    def prepare_embedding(params: Mapping[str, str], run_id: str) -> EmbeddingRun:
        """Read the chunk manifest, check cache mount configuration, and return small XCom references.

        Validate the chosen run, output mount and absolute host Hub cache path. No files are
        written and no model is loaded into Airflow. Fail before launching Docker
        if input configuration or source artifacts are invalid. The worker validates model files.
        """
        if not os.environ.get('RF_PROJECT_ROOT') or not Path(HOST).is_absolute():
            raise ValueError('Configure the host path before triggering this DAG')
        name = params['chunk_run']
        if not re.fullmatch(r'airflow-[a-f0-9]{64}', name):
            raise ValueError('chunk_run must be the airflow-<hash> chunk directory name')
        if not OUTPUTS.is_dir():
            raise ValueError('Create data/embedding-pilot and recreate Compose')
        source_path = CHUNKS / name / 'manifest.json'
        source = validate_input(source_path)
        if source['show'] != os.environ.get('RF_SHOW', 'mecaniques-du-journalisme'):
            raise ValueError('Chunk show does not match configured RF_SHOW')
        if not CACHE_HOST or not Path(CACHE_HOST).is_absolute():
            raise ValueError('Set RF_HF_HUB_CACHE in .env to the absolute host Hugging Face hub cache path')
        return {'chunk_run': name, 'output_folder': 'airflow-' + hashlib.sha256(run_id.encode()).hexdigest(),
            'source_sha256': sha256(source_path)}

    @task
    def verify_embeddings(state: EmbeddingRun) -> None:
        """Verify all published JSONL vectors and their selected chunk/model lineage.

        Read artifacts from read-only mounts; fail on corrupt/missing files,
        identities, dimensions, normalization or provenance. Write nothing and
        return no value. Log the completed output manifest path on success.
        """
        manifest = verify_output(OUTPUTS / state['output_folder'], CHUNKS / state['chunk_run'] / 'manifest.json')
        if manifest['source_manifest_sha256'] != state['source_sha256']:
            raise ValueError('Embedding output does not match the selected chunk/model provenance')
        print(f"Verified {manifest['chunk_count']} embeddings: data/embedding-pilot/{state['output_folder']}/manifest.json")

    state = prepare_embedding()  # pyright: ignore[reportCallIssue]
    embed = DockerOperator(
        task_id='embed', image='radiofrance-embedding:pilot',
        docker_url='unix://var/run/docker.sock', api_version='auto',
        command=['--chunk-manifest', "/input/{{ ti.xcom_pull(task_ids='prepare_embedding')['chunk_run'] }}/manifest.json",
            '--output-dir', "/output/{{ ti.xcom_pull(task_ids='prepare_embedding')['output_folder'] }}",
            '--batch-size', '1', '--threads', '4'],
        mounts=[Mount('/input', f'{HOST}/data/chunk-pilot', type='bind', read_only=True),
            Mount('/output', f'{HOST}/data/embedding-pilot', type='bind'),
            Mount('/home/embedding/.cache/huggingface/hub', CACHE_HOST or '/not-configured-hf-cache', type='bind', read_only=True)],
        environment={'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'},
        user=f"{os.environ.get('RF_UID', '1000')}:{os.environ.get('RF_GID', '1000')}",
        mount_tmp_dir=False, network_mode='none', mem_limit='4g', auto_remove='force',
        execution_timeout=timedelta(hours=2), do_xcom_push=False,
        doc_md='Load the pinned Google model once on CPU in FP32; read verified chunk CSVs, produce normalized 256D JSONL and a completion manifest. No network, credentials or database access.',
    )
    checked = verify_embeddings(state)  # pyright: ignore[reportArgumentType]
    _ = state >> embed >> checked
