"""Load a selected embedding artifact chain and verify the committed PostgreSQL results."""

from collections.abc import Mapping
from datetime import timedelta
import os
from pathlib import Path
import re
from typing import TypedDict

from airflow.sdk import DAG, Param, task
from airflow.sdk.definitions.param import ParamsDict
from airflow.providers.docker.operators.docker import DockerOperator
from docker.types import Mount
from psycopg2.extensions import make_dsn

from radiofrance.libs.storage import CHUNK_DIRECTORY, EMBEDDING_DIRECTORY
from radiofrance.jobs.database import Prepared, prepare, verify_database as verify_import
from radiofrance.jobs.chunk import contained_file


class DatabaseRun(TypedDict):
    embedding_run: str


HOST = os.environ.get('RF_PROJECT_ROOT', '')
DOWNLOADS = Path('/opt/radiofrance/data/runs')
CHUNKS = Path('/opt/radiofrance/data') / CHUNK_DIRECTORY / 'runs'
EMBEDDINGS = Path('/opt/radiofrance/data') / EMBEDDING_DIRECTORY / 'runs'
NETWORK = os.environ.get('RF_DOCKER_NETWORK', 'radiofrance_default')
DATABASE_ENV = {'PGHOST': os.environ.get('RF_DATABASE_HOST', 'postgres'),
    'PGPORT': os.environ.get('RF_DATABASE_PORT', '5432'), 'PGDATABASE': 'radiofrance', 'PGUSER': 'radiofrance'}


def selected_run(state: DatabaseRun) -> Prepared:
    """Revalidate the selected file chain and reject a manifest changed since preflight."""
    manifest = contained_file(EMBEDDINGS, f"{state['embedding_run']}/manifest.json")
    prepared = prepare(manifest, CHUNKS, DOWNLOADS.parent if DOWNLOADS.name == "runs" else DOWNLOADS)
    return prepared


with DAG(
    dag_id='radiofrance_database',
    description='Load a selected embedding run transactionally and verify its committed database records.',
    doc_md="""
## Purpose and flow
Standalone database-load recovery stage. Validate a selected embedding manifest
and its download/chunk lineage, load the episode set transactionally, then independently
verify committed records. For a complete run, use the show selection form in
radiofrance_pipeline instead.

## Configuration and running
Follow database/README.md. Build radiofrance-database:dev; Compose supplies the
application PostgreSQL host, network and private password. Supply embedding_run as
the UTC timestamp under the configured embedding corpus's runs directory. All data
files are mounted read-only. Only application credentials reach the loader.

## Results and recovery
The selected manifests explicitly identify shared transcripts, chunk CSVs and NPY
matrices; filesystem directory scans never select work. Database changes and import
completion commit together. Repeating an import verifies the existing records.
Failures roll back uncommitted writes; if verification fails after commit, retry
verification alone or reload the same timestamp. No schema changes are required.
""",
    schedule=None, catchup=False, max_active_runs=1, default_args={'retries': 0},
    params=ParamsDict({'embedding_run': Param('', type='string',
        description='Standalone recovery: UTC timestamp of a completed embedding stage; use radiofrance_pipeline for a full run')}),
    tags=['radiofrance', 'database'],
) as dag:
    @task(multiple_outputs=False)
    def prepare_load(params: Mapping[str, str]) -> DatabaseRun:
        """Validate the chosen artifact chain and host/DB settings without database writes.

        Input is the embedding_run parameter. Read source manifests and artifacts;
        return only a directory basename and its run name through XCom.
        No credentials or artifact contents are returned, and no files are written.
        """
        if not HOST or not Path(HOST).is_absolute():
            raise ValueError('Configure RF_PROJECT_ROOT and recreate Airflow')
        if not NETWORK or not os.environ.get('RF_DATABASE_PASSWORD'):
            raise ValueError('Configure the database password and Docker network in Compose')
        name = params['embedding_run']
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,150}', name):
            raise ValueError('embedding_run must be a single run directory basename')
        manifest = contained_file(EMBEDDINGS, f'{name}/manifest.json')
        prepared = prepare(manifest, CHUNKS, DOWNLOADS.parent if DOWNLOADS.name == "runs" else DOWNLOADS)
        return {'embedding_run': name}

    @task
    def verify_database(state: DatabaseRun) -> None:
        """Read the selected artifacts and verify their committed database records.

        Input is preflight's small XCom reference. Recheck the run name,
        connect as radiofrance and read a consistent database snapshot; log verified
        counts on success. Never insert, update or repair database rows or files.
        """
        prepared = selected_run(state)
        password = os.environ.get('RF_DATABASE_PASSWORD', '')
        if not password:
            raise ValueError('Configure RF_DATABASE_PASSWORD in Compose')
        dsn = make_dsn(host=DATABASE_ENV['PGHOST'], port=DATABASE_ENV['PGPORT'],
            dbname=DATABASE_ENV['PGDATABASE'], user=DATABASE_ENV['PGUSER'], password=password)
        if dsn is None:
            raise ValueError('Could not construct the application database connection string')
        result = verify_import(prepared, dsn)
        print(f"Verified import {result['embedding_run']}: "
            f"{result['catalogue_episode_count']} catalogue entries, "
            f"{result['transcript_count']} transcripts, {result['chunk_count']} embeddings")

    state = prepare_load()  # pyright: ignore[reportCallIssue]
    load_database = DockerOperator(
        task_id='load_database', image='radiofrance-database:dev',
        docker_url='unix://var/run/docker.sock', api_version='auto',
        command=['--embedding-manifest', f"/data/{EMBEDDING_DIRECTORY}/runs/{{{{ ti.xcom_pull(task_ids='prepare_load')['embedding_run'] }}}}/manifest.json",
            '--chunk-root', f'/data/{CHUNK_DIRECTORY}/runs', '--download-root', '/data'],
        mounts=[Mount('/data', f'{HOST or "/not-configured"}/data', type='bind', read_only=True)],
        environment=DATABASE_ENV,
        private_environment={'PGPASSWORD': os.environ.get('RF_DATABASE_PASSWORD', '')},
        user=f"{os.environ.get('RF_UID', '1000')}:{os.environ.get('RF_GID', '1000')}",
        network_mode=NETWORK, mount_tmp_dir=False, auto_remove='force', mem_limit='1g',
        execution_timeout=timedelta(minutes=30), do_xcom_push=False,
        doc_md='Verify the selected run name and load its artifact chain in one transaction as the radiofrance application role. Uses read-only file mounts, the Compose database network and private password environment. No schema changes or model inference.',
    )
    checked = verify_database(state)  # pyright: ignore[reportArgumentType]
    _ = state >> load_database >> checked
