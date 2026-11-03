"""Run the existing download image from Airflow, then verify its artifacts."""

from datetime import timedelta
from collections.abc import Mapping
import re
import json
import os
from pathlib import Path
from typing import TypedDict, cast

from radiofrance.libs.runs import run_folder
from radiofrance.libs.storage import artifact_root
from radiofrance.libs.catalogue import SHOWS, read_rows
from radiofrance.libs.programmes import show_url
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
OUTPUT = Path('/opt/radiofrance/data/runs')
HOST = os.environ.get('RF_PROJECT_ROOT') or '/not-configured'
NETWORK = os.environ.get('RF_DOCKER_NETWORK', 'radiofrance_default')
DATABASE_ENV: DatabaseEnvironment = {'PGHOST': os.environ.get('RF_DATABASE_HOST', 'postgres'),
    'PGPORT': os.environ.get('RF_DATABASE_PORT', '5432'), 'PGDATABASE': 'radiofrance', 'PGUSER': 'radiofrance'}


def verify_artifacts(directory: Path, expected_show: str = SHOW) -> None:
    """Check the show, JSON/transcript identities and file presence; reject escaping paths."""
    source = validate_source(directory / 'manifest.json')
    manifest = cast(Manifest, json.loads((directory / 'manifest.json').read_text()))
    if manifest['show'] != expected_show:
        raise ValueError('Unexpected show in download manifest')
    root = artifact_root(directory)
    for artifact in [manifest['episodes'], *manifest['transcripts']]:
        path = (root / artifact['path']).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError('Artifact must be a file inside its run directory')
    episodes = read_rows(root / manifest['episodes']['path'], expected_show)
    if (len(episodes) != source['new_episode_count'] or
            {row['id'] for row in episodes} != {item['episode_id'] for item in source['transcripts']}):
        raise ValueError('Episode metadata does not match the transcript manifest')



with DAG(
    dag_id='radiofrance_download',
    description='Read the committed database cutoff, download new episodes and verify JSON artifacts.',
    doc_md="""
## Purpose and flow
Standalone download stage: read the committed per-show PostgreSQL cutoff, fetch
new episode metadata, reuse or acquire immutable transcripts, and verify the manifest.
Only radiofrance_pipeline coordinates all stages; this DAG performs acquisition only.

## Configuration and running
Build the download image and follow airflow/DEPLOYMENT.md. Parent conf selects show,
show_url and output_folder; RF_SHOW is a standalone legacy default only;
.env supplies the host project path, UID/GID, Docker network and private database
password. The API-key file is a read-only secret mount. RF_EPISODE_SELECTION optionally
selects the fixed integration fixture on an empty database. Trigger without parameters.

## Outputs and recovery
Write episodes.json and manifest.json under data/runs/<UTC timestamp>/download/<show>.
Raw transcripts accumulate under data/transcripts/<episode-id>.json and are referenced
explicitly by the manifest. No database writes occur. A failed downstream stage leaves
the cutoff unchanged; another pipeline run reuses existing transcripts. Shared files
may survive a failed run, but incomplete run metadata is never treated as complete.
Existing download records are never overwritten. Automatic retries are disabled.
""",
    schedule=None,
    catchup=False,
    max_active_runs=1,
    default_args={'retries': 0},
    tags=['radiofrance', 'download'],
) as dag:
    @task
    def prepare_download(run_id: str, params: Mapping[str, str] | None = None) -> str:
        """Validate mounts and return an output-folder name derived from run_id.

        Validate selected show/URL/output references and read mount metadata; create no files or network
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
        options = params or {}
        show = options.get('show', SHOW)
        url = options.get('show_url') or f'https://www.radiofrance.fr/{SHOWS.get(show, "")}/podcasts/{show}'
        if show_url(url)['slug'] != show:
            raise ValueError('Programme URL does not match selected show')
        folder = options.get('output_folder') or f'{run_folder(run_id)}/download/{show}'
        if not re.fullmatch(r'[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*', folder):
            raise ValueError('Invalid download output folder')
        return folder

    @task
    def verify_download(folder: str, params: Mapping[str, str] | None = None) -> None:
        """Read and verify completed files in the shared output mount; log their path.

        Input is the folder name from prepare_download. No files are modified;
        missing or corrupt artifacts fail the task. No value is returned.
        """
        directory = OUTPUT / folder
        verify_artifacts(directory, (params or {}).get('show', SHOW))
        print(f'Verified download: data/runs/{folder}/manifest.json')

    folder = prepare_download()  # pyright: ignore[reportCallIssue]
    download = DockerOperator(
        task_id='download',
        image='radiofrance-download:dev',
        docker_url='unix://var/run/docker.sock',
        api_version='auto',
        command=['--show', "{{ params.get('show', '" + SHOW + "') }}",
                 '--show-url', "{{ params.get('show_url') or 'https://www.radiofrance.fr/" + SHOWS.get(SHOW, '') + '/podcasts/' + SHOW + "' }}",
                 '--output-dir', "/data/runs/{{ ti.xcom_pull(task_ids='prepare_download') }}",
                 '--data-root', '/data',
                 '--api-key-file', '/run/secrets/radiofrance-api-key',
                 *(['--episode-selection', '/input/episode-selection.json'] if SELECTION else [])],
        mounts=[
            Mount('/data', f'{HOST}/data', type='bind'),
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
