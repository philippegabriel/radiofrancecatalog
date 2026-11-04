"""Embed a completed chunk run with offline SentenceTransformers FP32."""

from collections.abc import Mapping
from datetime import timedelta
import os
from pathlib import Path
import re
from typing import TypedDict

from radiofrance.libs.runs import run_folder
from radiofrance.libs.storage import CHUNK_DIRECTORY, EMBEDDING_DIRECTORY

from airflow.sdk import DAG, Param, task
from airflow.sdk.definitions.param import ParamsDict
from airflow.providers.docker.operators.docker import DockerOperator
from docker.types import Mount

from radiofrance.jobs.embedding import validate_input, verify_output


class EmbeddingRun(TypedDict):
    chunk_run: str
    output_folder: str


CHUNKS = Path('/opt/radiofrance/data') / CHUNK_DIRECTORY / 'runs'
OUTPUTS = Path('/opt/radiofrance/data') / EMBEDDING_DIRECTORY / 'runs'
CACHE_HOST = os.environ.get('RF_HF_HUB_CACHE', '')
HOST = os.environ.get('RF_PROJECT_ROOT') or '/not-configured'


with DAG(
    dag_id='radiofrance_embedding',
    description='Generate and verify normalized 256D EmbeddingGemma 2 vectors from a chunk run.',
    doc_md="""
## Purpose and flow
Standalone embedding recovery stage. Verify a selected chunk manifest, generate
missing FP32 normalized 256D EmbeddingGemma 2 vectors, then verify all listed outputs.
For a full run, trigger radiofrance_pipeline instead.

## Configuration and running
Follow embedding/README.md to build radiofrance-embedding:dev and download the
pinned model into the standard Hugging Face cache. Set RF_HF_HUB_CACHE in .env.
Supply chunk_run as the UTC timestamp under the configured chunk strategy's runs
folder. The parent explicitly passes the show and output folder. The container has no network and the model mount is
read-only. Host UID/GID is used to publish shared data files.

## Outputs and recovery
NPY files accumulate under data/embeddings/embeddinggemma2-fp32-256/
segment-characters-v1-1000-300, one matrix per episode. The runs/<UTC timestamp>
subdirectory contains a completion manifest only. Existing matrices are validated
and reused without model inference. A sibling <run>.progress.json sidecar reports
model loading and inference-only throughput after ten new vectors, then every
hundred. radiofrance_pipeline.monitor_run reads it for unified parent logging;
the final record contains generated/cached counts and duration. Empty runs need no model inference. A new run
can resume from files already present; existing completion records are not overwritten.
""",
    schedule=None, catchup=False, max_active_runs=1, default_args={'retries': 0},
    params=ParamsDict({'chunk_run': Param('', type='string', description='Standalone recovery: UTC timestamp of a completed chunk manifest')}),
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
        if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
            raise ValueError('chunk_run must be the UTC timestamp chunk directory name')
        if not OUTPUTS.is_dir():
            raise ValueError('Create the shared embedding corpus directories and recreate Compose')
        source_path = CHUNKS / name / 'manifest.json'
        source = validate_input(source_path)
        if source['show'] != params.get('show', source['show']):
            raise ValueError('Chunk show does not match selected show')
        if not CACHE_HOST or not Path(CACHE_HOST).is_absolute():
            raise ValueError('Set RF_HF_HUB_CACHE in .env to the absolute host Hugging Face hub cache path')
        output_folder = params.get('output_folder') or run_folder(run_id)
        if not re.fullmatch(r'[A-Za-z0-9_-]+', output_folder):
            raise ValueError('Invalid output folder')
        return {'chunk_run': name, 'output_folder': output_folder}

    @task
    def verify_embeddings(state: EmbeddingRun) -> None:
        """Verify all published FP32 NPY vectors and their selected chunk/model lineage.

        Read artifacts from read-only mounts; fail on corrupt/missing files,
        identities, dimensions, normalization or provenance. Write nothing and
        return no value. Log the completed output manifest path on success.
        """
        manifest = verify_output(OUTPUTS / state['output_folder'], CHUNKS / state['chunk_run'] / 'manifest.json')
        if manifest['source_run'] != state['chunk_run']:
            raise ValueError('Embedding output does not match the selected chunk/model provenance')
        print(f"Verified {manifest['chunk_count']} embeddings: shared embedding manifest: {state['output_folder']}/manifest.json")

    state = prepare_embedding()  # pyright: ignore[reportCallIssue]
    embed = DockerOperator(
        task_id='embed', image='radiofrance-embedding:dev',
        docker_url='unix://var/run/docker.sock', api_version='auto',
        command=['--chunk-manifest', f"/data/{CHUNK_DIRECTORY}/runs/{{{{ ti.xcom_pull(task_ids='prepare_embedding')['chunk_run'] }}}}/manifest.json",
            '--output-dir', f"/data/{EMBEDDING_DIRECTORY}/runs/{{{{ ti.xcom_pull(task_ids='prepare_embedding')['output_folder'] }}}}",
            '--data-root', '/data', '--batch-size', '1', '--threads', '4'],
        mounts=[Mount('/data', f'{HOST}/data', type='bind'),
            Mount('/home/embedding/.cache/huggingface/hub', CACHE_HOST or '/not-configured-hf-cache', type='bind', read_only=True)],
        environment={'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'},
        user=f"{os.environ.get('RF_UID', '1000')}:{os.environ.get('RF_GID', '1000')}",
        mount_tmp_dir=False, network_mode='none', mem_limit='4g', auto_remove='force',
        execution_timeout=timedelta(hours=2), do_xcom_push=False,
        doc_md='Load the pinned Google model once on CPU in FP32; read verified chunk CSVs, produce normalized 256D FP32 NPY and a completion manifest. No network, credentials or database access.',
    )
    checked = verify_embeddings(state)  # pyright: ignore[reportArgumentType]
    _ = state >> embed >> checked
