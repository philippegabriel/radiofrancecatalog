"""Run the existing download image from Airflow, then verify its artifacts."""

from datetime import timedelta
import json
import os
from pathlib import Path
from typing import TypedDict, cast

from radiofrance.libs.runs import run_folder
from radiofrance.libs.catalogue import read_rows
from radiofrance.jobs.chunk import validate_source

from airflow.sdk import DAG, task
from airflow.providers.docker.operators.docker import DockerOperator
from docker.types import Mount


class Artifact(TypedDict):
    path: str


class Manifest(TypedDict):
    show: str
    episodes: Artifact
    transcripts: list[Artifact]


class DatabaseEnvironment(TypedDict):
    PGHOST: str
    PGPORT: str
    PGDATABASE: str
    PGUSER: str


SHOW = os.environ.get('RF_SHOW', 'mecaniques-du-journalisme')
SELECTION = os.environ.get('RF_EPISODE_SELECTION', '')
OUTPUT = Path('/opt/radiofrance/output')
HOST = os.environ.get('RF_PROJECT_ROOT') or '/not-configured'
NETWORK = os.environ.get('RF_DOCKER_NETWORK', 'radiofrance_default')
DATABASE_ENV: DatabaseEnvironment = {'PGHOST': os.environ.get('RF_DATABASE_HOST', 'postgres'),
    'PGPORT': os.environ.get('RF_DATABASE_PORT', '5432'), 'PGDATABASE': 'radiofrance', 'PGUSER': 'radiofrance'}


def verify_artifacts(directory: Path) -> None:
    """Check the show, JSON/transcript identities and file presence; reject escaping paths."""
    source = validate_source(directory / 'manifest.json')
    manifest = cast(Manifest, json.loads((directory / 'manifest.json').read_text()))
    if manifest['show'] != SHOW:
        raise ValueError('Unexpected show in download manifest')
    for artifact in [manifest['episodes'], *manifest['transcripts']]:
        path = (directory / artifact['path']).resolve()
        if not path.is_relative_to(directory.resolve()) or not path.is_file():
            raise ValueError('Artifact must be a file inside its run directory')
    episodes = read_rows(directory / manifest['episodes']['path'], SHOW)
    if (len(episodes) != source['new_episode_count'] or
            {row['id'] for row in episodes} != {item['episode_id'] for item in source['transcripts']}):
        raise ValueError('Episode metadata does not match the transcript manifest')



with DAG(
    dag_id='radiofrance_download',
    description='Read the committed database cutoff, download new episodes and verify JSON artifacts.',
    doc_md="""
## Purpose and execution flow
Download catalogue episodes and transcripts for RF_SHOW (default mecaniques-du-journalisme).
`prepare_download` checks configuration and chooses a run-specific folder.
`download` launches the existing image using the host Docker daemon, then
`verify_download` checks the completed manifest and artifact files and counts.

## Configuration
Run `bash airflow/configure` on the host, build `radiofrance-download:pilot`,
and rebuild/restart Compose as described in airflow/DEPLOYMENT.md. Host paths
and UID/GID come from .env; .OpenAPIKey is mounted read-only into the child
container. The Airflow container needs the host Docker socket's group permission.
No API secret is stored in Airflow variables, logs or XCom.
RF_EPISODE_SELECTION optionally names a project-relative JSON fixture. With an
empty show in PostgreSQL, download only its fixed IDs and require their transcript counts to match. An absent/changed selected episode fails without replacement.

## Running and outputs
Enable this DAG in the UI and trigger it manually. The download child connects
on RF_DOCKER_NETWORK to the application database using RF_DATABASE_HOST,
RF_DATABASE_PORT and the privately supplied RF_DATABASE_PASSWORD. It reads the
show's maximum committed publication timestamp and IDs at that boundary in one
read-only query. The application database must already be initialized. Database
failure stops the run; it never means an empty catalogue. An empty show downloads
from the beginning. Boundary-second episodes are included, skipping committed IDs.
Outputs under data/download-pilot/airflow-<run-id>/ contain episodes.json (only the
new metadata), raw transcripts and a version-2 manifest. No baseline or cumulative
catalogue CSV is read or written. Only the final database stage commits records;
failed acquisition or downstream work leaves the cutoff unchanged. There are no
S3 calls or asset events. Check the task log and manifest. Trigger a new DAG run after a
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

        Read only configuration and mount metadata; create no files or network
        connections. The returned name is a small XCom reference, never secret data.
        """
        if not os.environ.get('RF_PROJECT_ROOT') or not Path(HOST).is_absolute():
            raise ValueError('Run bash airflow/configure and recreate the Airflow container')
        if SELECTION and (Path(SELECTION).is_absolute() or '..' in Path(SELECTION).parts):
            raise ValueError('RF_EPISODE_SELECTION must be a project-relative fixture path')
        if not OUTPUT.is_dir():
            raise ValueError('Download output mount is missing')
        if not os.environ.get('RF_DATABASE_PASSWORD'):
            raise ValueError('RF_DATABASE_PASSWORD is required for the application database')
        return run_folder(run_id)

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
        command=['--show', SHOW,
                 '--output-dir', "/output/{{ ti.xcom_pull(task_ids='prepare_download') }}",
                 '--api-key-file', '/run/secrets/radiofrance-api-key',
                 *(['--episode-selection', '/input/episode-selection.json'] if SELECTION else [])],
        mounts=[
            Mount('/output', f'{HOST}/data/download-pilot', type='bind'),
            Mount('/run/secrets/radiofrance-api-key', f'{HOST}/.OpenAPIKey', type='bind', read_only=True),
            *([Mount('/input/episode-selection.json', f'{HOST}/{SELECTION}', type='bind', read_only=True)] if SELECTION else []),
        ],
        user=f"{os.environ.get('RF_UID', '1000')}:{os.environ.get('RF_GID', '1000')}",
        mount_tmp_dir=False,
        environment=dict(DATABASE_ENV),
        private_environment={'PGPASSWORD': os.environ.get('RF_DATABASE_PASSWORD', '')},
        network_mode=NETWORK,
        mem_limit='1g',
        auto_remove='force',
        execution_timeout=timedelta(hours=1),
        do_xcom_push=False,
        doc_md='Run the download image on the Compose network. Read the committed application database watermark in a read-only transaction, then fetch the delta via the Radio France APIs and publish JSON metadata/transcripts. The API-key file is mounted read-only; the database password is private and never placed in XCom. No database writes occur; nonzero exit fails the task.',
    )
    checked = verify_download(folder)  # pyright: ignore[reportArgumentType]
    _ = folder >> download >> checked
