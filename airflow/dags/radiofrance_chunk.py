"""Manually chunk one completed download run in an isolated container."""

from collections.abc import Mapping
from datetime import timedelta
import os
from pathlib import Path
import re
from typing import TypedDict

from radiofrance.libs.runs import run_folder
from radiofrance.libs.storage import CHUNK_DIRECTORY

from airflow.sdk import DAG, Param, task
from airflow.sdk.definitions.param import ParamsDict
from airflow.providers.docker.operators.docker import DockerOperator
from docker.types import Mount

from radiofrance.jobs.chunk import validate_source, verify_output


class ChunkRun(TypedDict):
    download_run: str
    output_folder: str


DOWNLOADS = Path('/opt/radiofrance/data/runs')
OUTPUTS = Path('/opt/radiofrance/data') / CHUNK_DIRECTORY / 'runs'
HOST = os.environ.get('RF_PROJECT_ROOT') or '/not-configured'


with DAG(
    dag_id='radiofrance_chunk',
    description='Chunk transcripts from a selected download manifest and verify the results.',
    doc_md="""
## Purpose and flow
Standalone chunk recovery stage. prepare_chunking verifies an explicit download;
chunk writes/reuses per-episode CSVs; verify_chunks checks counts and source lineage.
For a full run, trigger radiofrance_pipeline instead.

## Configuration and running
Build radiofrance-chunk:dev and follow airflow/DEPLOYMENT.md. Supply download_run
as <UTC timestamp>/download, relative to data/runs. The parent explicitly passes the show and output folder.
The host UID/GID and project path come from .env. The processing container has no
network or credentials. It reads/writes the shared data mount.

## Outputs and recovery
CSVs accumulate in data/chunks/segment-characters-v1-1000-300, one file per episode.
Only a completion manifest is written under its runs/<UTC timestamp>/ directory.
No duplicate corpus files are kept in that directory. Existing equal CSVs are reused;
conflicting content fails. Empty downloads produce empty manifests. A new pipeline
run can reuse files left by an earlier failure. Verification can be retried alone.
""",
    schedule=None,
    catchup=False,
    max_active_runs=1,
    default_args={'retries': 0},
    params=ParamsDict({'download_run': Param('', type='string', description='Standalone recovery: <UTC timestamp>/download under data/runs')}),
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
        if not re.fullmatch(r'[A-Za-z0-9_-]+(?:/download(?:/[a-z0-9-]+)?)?', name) or '..' in name:
            raise ValueError('download_run must be the <UTC timestamp>/download directory name')
        if not OUTPUTS.is_dir():
            raise ValueError('Create the shared chunk corpus directories and recreate Compose before running')
        path = DOWNLOADS / name / 'manifest.json'
        source = validate_source(path)
        if source['show'] != params.get('show', source['show']):
            raise ValueError('Download show does not match selected show')
        output_folder = params.get('output_folder') or run_folder(run_id)
        if not re.fullmatch(r'[A-Za-z0-9_-]+', output_folder):
            raise ValueError('Invalid output folder')
        return {'download_run': name, 'output_folder': output_folder}

    @task
    def verify_chunks(state: ChunkRun) -> None:
        """Verify the published run and its source identity; log the manifest path.

        Read chunk CSVs and manifests from the read-only Airflow data mount. Fail on missing,
        corrupt or mismatched artifacts. Modify no files and return no value.
        """
        manifest = verify_output(OUTPUTS / state['output_folder'])
        if manifest['source_run'] != ('runs/' + state['download_run'] if manifest.get('artifact_root') else state['download_run']):
            raise ValueError('Chunk output does not match the selected download')
        print(f"Verified {manifest['chunk_count']} chunks: shared chunk manifest: {state['output_folder']}/manifest.json")

    state = prepare_chunking()  # pyright: ignore[reportCallIssue]
    chunk = DockerOperator(
        task_id='chunk', image='radiofrance-chunk:dev',
        docker_url='unix://var/run/docker.sock', api_version='auto',
        command=['--download-manifest', "/data/runs/{{ ti.xcom_pull(task_ids='prepare_chunking')['download_run'] }}/manifest.json",
                 '--data-root', '/data', '--output-dir', f"/data/{CHUNK_DIRECTORY}/runs/{{{{ ti.xcom_pull(task_ids='prepare_chunking')['output_folder'] }}}}"],
        mounts=[Mount('/data', f'{HOST}/data', type='bind')],
        user=f"{os.environ.get('RF_UID', '1000')}:{os.environ.get('RF_GID', '1000')}",
        mount_tmp_dir=False, network_mode='none', mem_limit='1g', auto_remove='force',
        execution_timeout=timedelta(minutes=10), do_xcom_push=False,
        doc_md='Run the existing chunker against the explicit download manifest. Shared corpus input/output mount; no network or API key. Publish CSVs and manifest only on success.',
    )
    checked = verify_chunks(state)  # pyright: ignore[reportArgumentType]
    _ = state >> chunk >> checked
