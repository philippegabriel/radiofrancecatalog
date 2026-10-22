"""Manually chunk one completed download run in an isolated container."""

from collections.abc import Mapping
from datetime import timedelta
import os
from pathlib import Path
import re
from typing import TypedDict

from radiofrance.libs.runs import run_folder

from airflow.sdk import DAG, Param, task
from airflow.sdk.definitions.param import ParamsDict
from airflow.providers.docker.operators.docker import DockerOperator
from docker.types import Mount

from radiofrance.jobs.chunk import validate_source, verify_output


class ChunkRun(TypedDict):
    download_run: str
    output_folder: str


DOWNLOADS = Path('/opt/radiofrance/output')
OUTPUTS = Path('/opt/radiofrance/chunks')
HOST = os.environ.get('RF_PROJECT_ROOT') or '/not-configured'


with DAG(
    dag_id='radiofrance_chunk',
    description='Chunk transcripts from a selected download manifest and verify the results.',
    doc_md="""
## Purpose and flow
Process one completed download run for RF_SHOW (default mecaniques-du-journalisme). `prepare_chunking`
validates its transcript list and files and counts. `chunk` runs the existing character
chunker in a separate, network-disabled container. `verify_chunks` checks the
output manifest, row counts, files and counts and source-manifest provenance.

## Configuration and running
Build `radiofrance-chunk:pilot` from chunking/Dockerfile, create data/chunk-pilot
as the project owner, and recreate Compose. The normal .env supplies the
host path and UID/GID. In the trigger form, supply `download_run`, the basename
of a completed data/download-pilot/airflow-<run-id> directory.
See chunking/README.md for commands. No API key or additional services are needed.

## Inputs, outputs and retries
Only transcripts listed in the download manifest are processed. Output is a
new data/chunk-pilot/airflow-<run-id>/ directory containing per-episode CSVs
and a versioned manifest. Empty downloads succeed with no CSVs. Inputs and the
baseline remain unchanged. No embeddings, database imports, cloud calls or asset
events occur. Trigger a new DAG run to repeat processing; automatic retries are
disabled and existing outputs are not overwritten. A failed verification alone
can be cleared and retried without rerunning the chunk container.
""",
    schedule=None,
    catchup=False,
    max_active_runs=1,
    default_args={'retries': 0},
    params=ParamsDict({'download_run': Param('', type='string', description='Completed download folder: airflow-<run-id>')}),
    tags=['radiofrance', 'chunking'],
) as dag:
    @task(multiple_outputs=False)
    def prepare_chunking(params: Mapping[str, str], run_id: str) -> ChunkRun:
        """Validate the chosen download and return path references and its file presence.

        Read the manifest and listed transcripts; create no files, containers or
        network connections. Store only small references in the returned XCom.
        """
        if not os.environ.get('RF_PROJECT_ROOT') or not Path(HOST).is_absolute():
            raise ValueError('Configure the host path before triggering this DAG')
        name = params['download_run']
        if not re.fullmatch(r'airflow-[A-Za-z0-9_-]+', name):
            raise ValueError('download_run must be the airflow-<run-id> download directory name')
        if not OUTPUTS.is_dir():
            raise ValueError('Create data/chunk-pilot and recreate Compose before running')
        path = DOWNLOADS / name / 'manifest.json'
        source = validate_source(path)
        if source['show'] != os.environ.get('RF_SHOW', 'mecaniques-du-journalisme'):
            raise ValueError('Download show does not match configured RF_SHOW')
        return {'download_run': name, 'output_folder': run_folder(run_id)}

    @task
    def verify_chunks(state: ChunkRun) -> None:
        """Verify the published run and its source identity; log the manifest path.

        Read chunk CSVs and manifests from read-only mounts. Fail on missing,
        corrupt or mismatched artifacts. Modify no files and return no value.
        """
        manifest = verify_output(OUTPUTS / state['output_folder'])
        if manifest['source_run'] != state['download_run']:
            raise ValueError('Chunk output does not match the selected download')
        print(f"Verified {manifest['chunk_count']} chunks: data/chunk-pilot/{state['output_folder']}/manifest.json")

    state = prepare_chunking()  # pyright: ignore[reportCallIssue]
    chunk = DockerOperator(
        task_id='chunk', image='radiofrance-chunk:pilot',
        docker_url='unix://var/run/docker.sock', api_version='auto',
        command=['--download-manifest', "/input/{{ ti.xcom_pull(task_ids='prepare_chunking')['download_run'] }}/manifest.json",
                 '--output-dir', "/output/{{ ti.xcom_pull(task_ids='prepare_chunking')['output_folder'] }}"],
        mounts=[Mount('/input', f'{HOST}/data/download-pilot', type='bind', read_only=True),
                Mount('/output', f'{HOST}/data/chunk-pilot', type='bind')],
        user=f"{os.environ.get('RF_UID', '1000')}:{os.environ.get('RF_GID', '1000')}",
        mount_tmp_dir=False, network_mode='none', mem_limit='1g', auto_remove='force',
        execution_timeout=timedelta(minutes=10), do_xcom_push=False,
        doc_md='Run the existing chunker against the explicit download manifest. Read-only transcript inputs and writable output mount; no network or API key. Publish CSVs and manifest only on success.',
    )
    checked = verify_chunks(state)  # pyright: ignore[reportArgumentType]
    _ = state >> chunk >> checked
