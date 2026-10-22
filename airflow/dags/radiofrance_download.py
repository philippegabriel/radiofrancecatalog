"""Run the existing download image from Airflow, then verify its artifacts."""

from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
from typing import TypedDict, cast

from airflow.sdk import DAG, task
from airflow.providers.docker.operators.docker import DockerOperator
from docker.types import Mount


class Artifact(TypedDict):
    path: str
    sha256: str


class Manifest(TypedDict):
    show: str
    baseline_sha256: str
    updates: Artifact
    catalogue: Artifact
    transcripts: list[Artifact]


SHOW = 'mecaniques-du-journalisme'
OUTPUT = Path('/opt/radiofrance/output')
HOST = os.environ.get('RF_PROJECT_ROOT') or '/not-configured'


def verify_artifacts(directory: Path) -> None:
    """Check the completion manifest's show and artifact hashes; reject escaping paths."""
    manifest = cast(Manifest, json.loads((directory / 'manifest.json').read_text()))
    if manifest['show'] != SHOW:
        raise ValueError('Unexpected show in download manifest')
    baseline: Artifact = {'path': 'baseline.csv', 'sha256': manifest['baseline_sha256']}
    for artifact in [baseline, manifest['updates'], manifest['catalogue'], *manifest['transcripts']]:
        path = (directory / artifact['path']).resolve()
        if not path.is_relative_to(directory.resolve()):
            raise ValueError('Artifact path escapes its run directory')
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != artifact['sha256']:
            raise ValueError(f'Checksum mismatch for {path.name}')


with DAG(
    dag_id='radiofrance_download',
    description='Run the pilot download container and verify its completed output.',
    doc_md="""
## Purpose and execution flow
Manually download new `mecaniques-du-journalisme` episodes and their transcripts.
`prepare_download` checks configuration and chooses a run-specific folder.
`download` launches the existing image using the host Docker daemon, then
`verify_download` checks the completed manifest and artifact checksums.

## Configuration
Run `bash airflow/configure` on the host, build `radiofrance-download:pilot`,
and rebuild/restart Compose as described in airflow/DEPLOYMENT.md. Host paths
and UID/GID come from .env; .OpenAPIKey is mounted read-only into the child
container. The Airflow container needs the host Docker socket's group permission.
No API secret is stored in Airflow variables, logs or XCom.

## Running and outputs
Enable this DAG in the UI and trigger it manually. It uses the existing
baseline CSV and publishes under data/download-pilot/airflow-<run-hash>/.
The baseline is never overwritten, and there are no database imports, S3 calls
or asset events. Check the task log and manifest. Trigger a new DAG run after a
failure; automatic retries are disabled. Clearing a completed download task
will fail rather than overwrite its existing output.
""",
    schedule=None,
    catchup=False,
    max_active_runs=1,
    default_args={'retries': 0},
    tags=['radiofrance', 'download'],
) as dag:
    @task
    def prepare_download(run_id: str) -> str:
        """Validate mounts and return an output-folder name derived from run_id.

        Read only configuration and baseline metadata; create no files or network
        connections. The returned name is a small XCom reference, never secret data.
        """
        if not os.environ.get('RF_PROJECT_ROOT') or not Path(HOST).is_absolute():
            raise ValueError('Run bash airflow/configure and recreate the Airflow container')
        if not Path('/opt/radiofrance/input/baseline.csv').is_file() or not OUTPUT.is_dir():
            raise ValueError('Pilot baseline or output mount is missing')
        return 'airflow-' + hashlib.sha256(run_id.encode()).hexdigest()

    @task
    def verify_download(folder: str) -> None:
        """Read and verify completed files in the shared output mount; log their path.

        Input is the folder name from prepare_download. No files are modified;
        missing or corrupt artifacts fail the task. No value is returned.
        """
        directory = OUTPUT / folder
        verify_artifacts(directory)
        print(f'Verified download: data/download-pilot/{folder}/manifest.json')

    folder = prepare_download()  # pyright: ignore[reportCallIssue]
    download = DockerOperator(
        task_id='download',
        image='radiofrance-download:pilot',
        docker_url='unix://var/run/docker.sock',
        api_version='auto',
        command=['--show', SHOW, '--baseline', '/input/baseline.csv',
                 '--output-dir', "/output/{{ ti.xcom_pull(task_ids='prepare_download') }}",
                 '--api-key-file', '/run/secrets/radiofrance-api-key'],
        mounts=[
            Mount('/input/baseline.csv', f'{HOST}/data/{SHOW}.csv', type='bind', read_only=True),
            Mount('/output', f'{HOST}/data/download-pilot', type='bind'),
            Mount('/run/secrets/radiofrance-api-key', f'{HOST}/.OpenAPIKey', type='bind', read_only=True),
        ],
        user=f"{os.environ.get('RF_UID', '1000')}:{os.environ.get('RF_GID', '1000')}",
        mount_tmp_dir=False,
        network_mode='bridge',
        mem_limit='1g',
        auto_remove='force',
        execution_timeout=timedelta(hours=1),
        do_xcom_push=False,
        doc_md='Launch the download image with read-only baseline/key mounts and a writable output directory. API calls and output writes happen in this child container; nonzero exit fails the task.',
    )
    checked = verify_download(folder)  # pyright: ignore[reportArgumentType]
    _ = folder >> download >> checked
