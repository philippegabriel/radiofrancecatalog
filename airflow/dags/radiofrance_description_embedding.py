"""Produce and verify description vectors from the selected download artifacts."""

from collections.abc import Mapping
import os
from pathlib import Path
import re
from typing import TypedDict

from airflow.sdk import DAG, Param, task
from airflow.sdk.definitions.param import ParamsDict
from airflow.providers.docker.operators.docker import DockerOperator
from docker.types import Mount

from radiofrance.jobs.description_embedding import selection, verify_output
from radiofrance.libs.runs import run_folder


class DescriptionRun(TypedDict):
    download_run: str
    output_folder: str


HOST = os.environ.get('RF_PROJECT_ROOT') or '/not-configured'
CACHE_HOST = os.environ.get('RF_HF_HUB_CACHE', '')
DOWNLOADS = Path('/opt/radiofrance/data/runs')
OUTPUTS = Path('/opt/radiofrance/data/runs')


with DAG(
    dag_id='radiofrance_description_embedding',
    description='Generate offline 256D description vectors from downloaded metadata, then verify them.',
    doc_md="""
## Purpose and flow
Validate a selected download manifest, encode nonblank episode title/description
pairs into normalized 256D FP32 vectors, and verify the published artifacts.
Like the transcript embedding DAG, this stage reads files and writes files only.
It has no database connection or API credentials. The coordinator invokes it after
transcript embedding to avoid loading two CPU models concurrently; database loading
waits for both embedding stages to finish successfully.

## Configuration and running
Normally trigger radiofrance_pipeline. Enable this DAG alongside the other stage
DAGs. For standalone generation supply download_run (<timestamp>/<show>/download).
Compose provides RF_PROJECT_ROOT, RF_HF_HUB_CACHE and host UID/GID. Build the
existing radiofrance-embedding:dev image and cache the pinned model. Inference has
no network, and the model mount is read-only. Empty selections load no model.

## Outputs and recovery
Publish per-episode NPY files and text snapshots under
 data/embeddings/descriptions/<model-and-revision>/256. The completion manifest
 goes under data/runs/<timestamp>/<show>/description-embedding.
A run-scoped progress.json record feeds radiofrance_pipeline.monitor_run. The manifest
records the download identity and exact title/description snapshots; the database
stage validates these before loading both vector kinds in one transaction.
Existing completion manifests are never overwritten. Validated vectors are cached by episode; exact text snapshots control description reuse. Completion manifests and progress.json remain in the run directory. If a stage fails, a new
pipeline run reuses downloaded transcripts and retries uncommitted work. No schema
migration or backfill tool is involved.
""",
    schedule=None, catchup=False, max_active_runs=1, default_args={'retries': 0},
    params=ParamsDict({'download_run': Param('', type='string', description='Completed download folder: <timestamp>/<show>/download')}),
    tags=['radiofrance', 'embedding'],
) as dag:
    @task(multiple_outputs=False)
    def prepare_descriptions(params: Mapping[str, str], run_id: str) -> DescriptionRun:
        """Validate selected metadata and model/host mounts; return only run references.

        Read and validate the download artifacts, show and output folder. No model
        inference, network, database access or filesystem writes occur here.
        """
        if not os.environ.get('RF_PROJECT_ROOT') or not Path(HOST).is_absolute():
            raise ValueError('Configure RF_PROJECT_ROOT before triggering this DAG')
        if not CACHE_HOST or not Path(CACHE_HOST).is_absolute():
            raise ValueError('Configure the absolute RF_HF_HUB_CACHE path')
        name = params['download_run']
        if not re.fullmatch(r'[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*', name):
            raise ValueError('Invalid download_run')
        show, _, _ = selection(DOWNLOADS / name / 'manifest.json')
        if show != params.get('show', show):
            raise ValueError('Description/download show mismatch')
        output = params.get('output_folder') or f"{run_folder(run_id)}/{show}/description-embedding"
        if not re.fullmatch(r'[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*', output):
            raise ValueError('Invalid description output folder')
        return {'download_run': name, 'output_folder': output}

    @task
    def verify_descriptions(state: DescriptionRun) -> None:
        """Verify all description NPY vectors and source snapshots from read-only mounts.

        Read explicit manifests; reject missing, corrupt or mismatched outputs.
        Log the verified count. No files or database records are modified.
        """
        manifest = verify_output(OUTPUTS / state['output_folder'], DOWNLOADS / state['download_run'] / 'manifest.json')
        print(f"Verified {manifest['episode_count']} description embeddings: {state['output_folder']}/manifest.json")

    state = prepare_descriptions()  # pyright: ignore[reportCallIssue]
    embed = DockerOperator(
        task_id='embed', image='radiofrance-embedding:dev',
        entrypoint=['python', '-m', 'radiofrance.jobs.description_embedding'],
        command=['--download-manifest', "/data/runs/{{ ti.xcom_pull(task_ids='prepare_descriptions')['download_run'] }}/manifest.json",
            '--output-dir', f"/data/runs/{{{{ ti.xcom_pull(task_ids='prepare_descriptions')['output_folder'] }}}}",
            '--data-root', '/data', '--batch-size', '4', '--threads', '4'],
        docker_url='unix://var/run/docker.sock', api_version='auto',
        mounts=[Mount('/data', f'{HOST}/data', type='bind'),
            Mount('/home/embedding/.cache/huggingface/hub', CACHE_HOST or '/not-configured-hf-cache', type='bind', read_only=True)],
        environment={'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'},
        user=f"{os.environ.get('RF_UID', '1000')}:{os.environ.get('RF_GID', '1000')}",
        network_mode='none', mount_tmp_dir=False, auto_remove='force', do_xcom_push=False,
        mem_limit='4g',
        doc_md='Read downloaded episode metadata, load the pinned FP32 model once, and publish '
            'normalized 256D description vectors plus a verified completion manifest. Empty runs '
            'do not load the model. No network, database client or credentials are needed.',
    )
    checked = verify_descriptions(state)  # pyright: ignore[reportArgumentType]
    _ = state >> embed >> checked
