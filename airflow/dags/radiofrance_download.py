"""Download and publish cumulative show catalogues, using only temporary catalogue tables in Radio France PostgreSQL."""
import hashlib
import json
import os
import subprocess
import shutil
from pathlib import Path
from collections.abc import Mapping
from typing import Protocol, TypedDict, cast

from airflow.sdk import Asset, AssetAlias, DAG, Param, task
from airflow.sdk.definitions.param import ParamsDict
from radiofrance_catalogue.catalogue import SHOWS, cutoff, merge


class CatalogueState(TypedDict):
    show: str
    workspace: str


class FetchState(CatalogueState):
    since: str


class PublishedState(FetchState):
    output: str
    episode_count: int


class CatalogueEvent(TypedDict):
    show: str
    episode_count: int
    sha256: str
    cutoff_used: str


class CacheConfig(TypedDict, total=False):
    bucket: str
    region: str


class AssetEventEmitter(Protocol):
    def add(self, asset: Asset, *, extra: CatalogueEvent) -> None: ...


class OutletEvents(Protocol):
    def __getitem__(self, asset: AssetAlias) -> AssetEventEmitter: ...

PROJECT = Path(__file__).resolve().parents[2]
CATALOGUES = AssetAlias('radiofrance-show-catalogues')


def cache_config() -> CacheConfig:
    # Local-only fallback; CI uses environment values from repository secrets.
    path = PROJECT / 'aws/cache-config.json'
    return cast(CacheConfig, json.loads(path.read_text())) if path.exists() else {}


def aws(*arguments: str) -> None:
    region = os.environ.get('RF_S3_REGION') or cache_config().get('region', 'eu-west-2')
    subprocess.run(['aws', *arguments, '--region', region], check=True)


def uri(show: str) -> str:
    bucket = os.environ.get('RF_S3_BUCKET') or cache_config().get('bucket')
    if not bucket:
        raise ValueError('Set RF_S3_BUCKET or supply local aws/cache-config.json')
    return f's3://{bucket}/data/episodes/{show}.csv'


with DAG(
    dag_id='radiofrance_download',
    description='S3 catalogue → temporary PostgreSQL cutoff → Radio France updates → cumulative S3 CSV',
    schedule=None,
    catchup=False,
    max_active_runs=1,
    params=ParamsDict({'show': Param('affaires-sensibles', type='string', enum=list(SHOWS))}),
    tags=['radiofrance', 'download'],
):
    @task
    def retrieve_catalogue(params: Mapping[str, str], run_id: str) -> CatalogueState:
        show = params['show']
        if show not in SHOWS:
            raise ValueError('Unsupported show')
        workspace = Path(os.environ['AIRFLOW_HOME']) / 'downloads' / hashlib.sha256(run_id.encode()).hexdigest() / show
        workspace.mkdir(parents=True, exist_ok=True)
        baseline = workspace / 'baseline.csv'
        temporary = workspace / 'baseline.csv.tmp'
        local_baselines = os.environ.get('RF_CATALOGUE_BASELINE_DIR')
        if local_baselines:
            shutil.copyfile(Path(local_baselines) / f'{show}.csv', temporary)
        else:
            aws('s3', 'cp', uri(show), str(temporary))
        # Missing baselines fail instead of silently starting a full catalogue fetch.
        temporary.replace(baseline)
        return {'show': show, 'workspace': str(workspace)}

    @task
    def derive_cutoff(state: CatalogueState) -> FetchState:
        workspace = Path(state['workspace'])
        return {**state, 'since': cutoff(workspace / 'baseline.csv', state['show'])}

    @task
    def fetch_updates(state: FetchState) -> FetchState:
        key = os.environ.get('RADIOFRANCE_API_KEY')
        if not key:
            key_file = Path(os.environ.get('RF_API_KEY_FILE', str(PROJECT / '.OpenAPIKey')))
            key = key_file.read_text().strip()
        if not key:
            raise ValueError('Radio France API key is empty')
        env = {**os.environ, 'RADIOFRANCE_API_KEY': key}
        temporary = Path(state['workspace']) / 'updates.csv.tmp'
        subprocess.run([
            os.environ.get('RF_DOWNLOAD_PYTHON', str(PROJECT / 'venv/airflow/bin/python')),
            str(PROJECT / 'rf_dump.py'), '--show-url',
            f"https://www.radiofrance.fr/{SHOWS[state['show']]}/podcasts/{state['show']}",
            '--since', state['since'], '--out', str(temporary),
        ], check=True, env=env, cwd=PROJECT)
        temporary.replace(Path(state['workspace']) / 'updates.csv')
        return state

    @task
    def merge_catalogue(state: FetchState) -> PublishedState:
        workspace = Path(state['workspace'])
        output = workspace / f"{state['show']}.csv"
        count = merge(workspace / 'baseline.csv', workspace / 'updates.csv', output, state['show'])
        return {**state, 'output': str(output), 'episode_count': count}

    @task(outlets=[CATALOGUES])
    def publish_catalogue(state: PublishedState, outlet_events: OutletEvents) -> None:
        output = Path(state['output'])
        destination = uri(state['show'])
        aws('s3', 'cp', str(output), destination)
        local_outputs = os.environ.get('RF_CATALOGUE_OUTPUT_DIR')
        if local_outputs:
            target = Path(local_outputs)
            target.mkdir(parents=True, exist_ok=True)
            temporary = target / (output.name + '.tmp')
            shutil.copyfile(output, temporary)
            temporary.replace(target / output.name)
        outlet_events[CATALOGUES].add(
            Asset(uri=destination, name=f"{state['show']}_catalogue"),
            extra={'show': state['show'], 'episode_count': state['episode_count'],
                   'sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'cutoff_used': state['since']},
        )

    # Airflow injects context arguments and resolves XComArg values at runtime;
    # its decorator annotations retain the underlying Python function signature.
    publish_catalogue(merge_catalogue(fetch_updates(derive_cutoff(retrieve_catalogue()))))  # pyright: ignore[reportCallIssue, reportArgumentType]
