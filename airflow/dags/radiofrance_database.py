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

from database_job import Prepared, prepare, verify_database as verify_import
from chunk_job import contained_file


class DatabaseRun(TypedDict):
    embedding_run: str
    manifest_sha256: str


HOST = os.environ.get('RF_PROJECT_ROOT', '')
DOWNLOADS = Path('/opt/radiofrance/output')
CHUNKS = Path('/opt/radiofrance/chunks')
EMBEDDINGS = Path('/opt/radiofrance/embeddings')
NETWORK = os.environ.get('RF_DOCKER_NETWORK', 'radiofrance_default')
DATABASE_ENV = {'PGHOST': os.environ.get('RF_DATABASE_HOST', 'postgres'),
    'PGPORT': os.environ.get('RF_DATABASE_PORT', '5432'), 'PGDATABASE': 'radiofrance', 'PGUSER': 'radiofrance'}


def selected_run(state: DatabaseRun) -> Prepared:
    """Revalidate the selected file chain and reject a manifest changed since preflight."""
    manifest = contained_file(EMBEDDINGS, f"{state['embedding_run']}/manifest.json")
    prepared = prepare(manifest, CHUNKS, DOWNLOADS)
    if prepared['embedding_hash'] != state['manifest_sha256']:
        raise ValueError('Selected embedding manifest changed after preflight')
    return prepared


with DAG(
    dag_id='radiofrance_database',
    description='Load a selected embedding run transactionally and verify its committed database records.',
    doc_md="""
## Purpose and execution
Manually load one completed embedding run and its exact download/chunk ancestors.
`prepare_load` validates artifacts and configuration and returns only the selected
folder and manifest SHA256. `load_database` starts the database-loader image on
the Compose network. It verifies the expected manifest and commits the import in
one transaction. `verify_database` independently checks the completion record,
configuration, catalogue membership, source fingerprints and per-episode counts
using a read-only repeatable-read transaction. No models or downloads are run.

## Configuration and running
Follow database/README.md: initialize PostgreSQL through the first-start scripts,
build radiofrance-database:pilot and recreate the Airflow service. The Compose
setup provides RF_PROJECT_ROOT, RF_DOCKER_NETWORK, RF_DATABASE_HOST/PORT and
RF_DATABASE_PASSWORD. The host socket launches a sibling container; all three
artifact roots are mounted read-only. Only the radiofrance application role is
used. The password is passed through the operator's private environment, never
XCom or command arguments. No Airflow metadata credentials reach the loader.
Trigger with `embedding_run`, a directory basename under data/embedding-pilot,
for example airflow-<hash> or a manually generated run name. Ancestor run
folders must still exist. There is no automatic selection of the newest run.

## Results and recovery
Database changes and the import_run completion record commit together. No output
files are created. Repeating the same import is safe and verifies existing records.
Failures roll back uncommitted writes. If loading committed but verification failed,
clear verification alone or trigger the same selected run again. No data reset,
new artifact directory, schema update or re-embedding is required. Automatic retries
are disabled for this manually operated pilot. Database counts are scoped to the
selected run and configuration, not global totals across all imported shows/models.
""",
    schedule=None, catchup=False, max_active_runs=1, default_args={'retries': 0},
    params=ParamsDict({'embedding_run': Param('', type='string',
        description='Completed directory under data/embedding-pilot, containing manifest.json')}),
    tags=['radiofrance', 'database'],
) as dag:
    @task(multiple_outputs=False)
    def prepare_load(params: Mapping[str, str]) -> DatabaseRun:
        """Validate the chosen artifact chain and host/DB settings without database writes.

        Input is the embedding_run parameter. Read source manifests and artifacts;
        return only a directory basename and its manifest checksum through XCom.
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
        prepared = prepare(manifest, CHUNKS, DOWNLOADS)
        return {'embedding_run': name, 'manifest_sha256': prepared['embedding_hash']}

    @task
    def verify_database(state: DatabaseRun) -> None:
        """Read the selected artifacts and verify their committed database records.

        Input is preflight's small XCom reference. Recheck the manifest checksum,
        connect as radiofrance and read a consistent database snapshot; log verified
        counts on success. Never insert, update or repair database rows or files.
        """
        prepared = selected_run(state)
        password = os.environ.get('RF_DATABASE_PASSWORD', '')
        if not password:
            raise ValueError('Configure RF_DATABASE_PASSWORD in Compose')
        dsn = make_dsn(host=DATABASE_ENV['PGHOST'], port=DATABASE_ENV['PGPORT'],
            dbname=DATABASE_ENV['PGDATABASE'], user=DATABASE_ENV['PGUSER'], password=password)
        result = verify_import(prepared, dsn)
        print(f"Verified import {result['embedding_manifest_sha256']}: "
            f"{result['catalogue_episode_count']} catalogue entries, "
            f"{result['transcript_count']} transcripts, {result['chunk_count']} embeddings")

    state = prepare_load()  # pyright: ignore[reportCallIssue]
    load_database = DockerOperator(
        task_id='load_database', image='radiofrance-database:pilot',
        docker_url='unix://var/run/docker.sock', api_version='auto',
        command=['--embedding-manifest', "/input/embeddings/{{ ti.xcom_pull(task_ids='prepare_load')['embedding_run'] }}/manifest.json",
            '--chunk-root', '/input/chunks', '--download-root', '/input/downloads',
            '--expected-manifest-sha256', "{{ ti.xcom_pull(task_ids='prepare_load')['manifest_sha256'] }}"],
        mounts=[Mount('/input/downloads', f'{HOST or "/not-configured"}/data/download-pilot', type='bind', read_only=True),
            Mount('/input/chunks', f'{HOST or "/not-configured"}/data/chunk-pilot', type='bind', read_only=True),
            Mount('/input/embeddings', f'{HOST or "/not-configured"}/data/embedding-pilot', type='bind', read_only=True)],
        environment=DATABASE_ENV,
        private_environment={'PGPASSWORD': os.environ.get('RF_DATABASE_PASSWORD', '')},
        user=f"{os.environ.get('RF_UID', '1000')}:{os.environ.get('RF_GID', '1000')}",
        network_mode=NETWORK, mount_tmp_dir=False, auto_remove='force', mem_limit='1g',
        execution_timeout=timedelta(minutes=30), do_xcom_push=False,
        doc_md='Verify the selected manifest SHA256 and load its artifact chain in one transaction as the radiofrance application role. Uses read-only file mounts, the Compose database network and private password environment. No schema changes or model inference.',
    )
    checked = verify_database(state)  # pyright: ignore[reportArgumentType]
    _ = state >> load_database >> checked
