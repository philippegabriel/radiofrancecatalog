"""Publish cumulative show catalogues and transcripts for newly fetched episodes."""
import hashlib
import json
import os
import subprocess
import shutil
import sys
from pathlib import Path
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Protocol, TypedDict

from airflow.sdk import Asset, AssetAlias, DAG, Param, task
from airflow.sdk.definitions.param import ParamsDict
from radiofrance_catalogue.catalogue import SHOWS, merge, read_rows
from radiofrance_catalogue.episodes import EPISODES, EpisodeEvent, InletEvents, record_episode_rows
from radiofrance_catalogue.storage import PROJECT, aws, uri, s3_uri


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


class TranscriptEvent(TypedDict):
    show: str
    episode_id: str
    sha256: str


class AssetEventEmitter(Protocol):
    def add(self, asset: Asset, *, extra: CatalogueEvent | TranscriptEvent | EpisodeEvent) -> None: ...


class OutletEvents(Protocol):
    def __getitem__(self, asset: AssetAlias) -> AssetEventEmitter: ...

sys.path.insert(0, str(PROJECT))
from fetchtranscript import aod_uuid, fetch_transcript
CATALOGUES = AssetAlias('radiofrance-show-catalogues')
TRANSCRIPTS = AssetAlias('radiofrance-transcripts')


with DAG(
    dag_id='radiofrance_download',
    description='Update the cumulative show catalogue and download new episode transcripts',
    doc_md="""Retrieve the existing show CSV and register its episode references. Read the
maximum episode publication timestamp from Airflow asset events as the cutoff,
then fetch newer episodes,
merge the new rows into the cumulative CSV, and publish it to S3. Then download
and publish transcripts for the episodes in this run's updates.csv, emitting
one asset event per transcript. Register new episode references after catalogue
publication, so they remain discoverable even if transcript downloading fails.

Run through GitHub Actions or trigger manually with `show`.
Configure RF_S3_BUCKET, AWS credentials, RADIOFRANCE_API_KEY. Cutoff reads use the supported inlet_events API;
no catalogue scratch database is required by this DAG.
RF_TRANSCRIPT_DIR optionally selects the local JSON directory (default transcripts/).
There are no transcript limits, cache scans, or automatic recovery of earlier
missed downloads. A future manually invoked DAG will handle recovery.
See airflow/README.md for CLI examples and required permissions.
""",
    schedule=None,
    catchup=False,
    max_active_runs=1,
    params=ParamsDict({
        'show': Param('affaires-sensibles', type='string', enum=list(SHOWS)),
    }),
    tags=['radiofrance', 'download'],
):
    @task
    def retrieve_catalogue(params: Mapping[str, str], run_id: str) -> CatalogueState:
        """Copy the show's existing CSV into a workspace specific to this run.

        Use RF_CATALOGUE_BASELINE_DIR when supplied; otherwise download from S3.
        A missing baseline fails the task. Return the show and workspace path
        for subsequent tasks.
        """
        show = params['show']
        if not isinstance(show, str) or show not in SHOWS:
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

    @task(inlets=list(EPISODES.values()), outlets=list(EPISODES.values()))
    def record_episodes(
        state: CatalogueState,
        filename: str,
        inlet_events: InletEvents,
        outlet_events: OutletEvents,
    ) -> CatalogueState:
        """Register catalogue episodes as reference assets with publication dates.

        Read the selected run CSV and emit an event only for episodes whose ID
        and publication timestamp are not already recorded for this show.
        Baseline registration seeds existing episodes; updates registration
        records new episodes after S3 catalogue publication. A reference event
        records catalogue membership, not transcript availability.
        """
        alias = EPISODES[state['show']]
        rows = read_rows(Path(state['workspace']) / filename, state['show'])
        record_episode_rows(state['show'], rows, inlet_events[alias], outlet_events[alias])
        return state

    @task(inlets=list(EPISODES.values()))
    def derive_cutoff(state: CatalogueState, inlet_events: InletEvents) -> FetchState:
        """Read the show's maximum publication timestamp from episode events.

        Use Airflow's supported inlet_events accessor. Publication timestamps,
        rather than event creation times, determine the cutoff. A show with no
        episode events starts at 1900-01-01 UTC. Return the cutoff for rf_dump.py.
        """
        timestamp = max(
            (int(event.extra['published_ts']) for event in inlet_events[EPISODES[state['show']]]),
            default=None,
        )
        since = datetime.fromtimestamp(timestamp, timezone.utc).isoformat() if timestamp is not None else '1900-01-01T00:00:00+00:00'
        return {**state, 'since': since}

    @task
    def fetch_updates(state: FetchState) -> FetchState:
        """Fetch episodes published strictly after the baseline cutoff.

        Invoke rf_dump.py with the selected Python executable and an API key
        supplied through its environment. Publish updates.csv in the workspace
        only after the command succeeds, then pass the task state onward.
        """
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
        """Merge baseline and downloaded episodes into a cumulative show CSV.

        Deduplicate by episode ID, prefer downloaded rows, and sort by
        publication timestamp and ID. Return the output path and total episode
        count alongside the existing task state.
        """
        workspace = Path(state['workspace'])
        output = workspace / f"{state['show']}.csv"
        count = merge(workspace / 'baseline.csv', workspace / 'updates.csv', output, state['show'])
        return {**state, 'output': str(output), 'episode_count': count}

    @task(outlets=[CATALOGUES])
    def publish_catalogue(state: PublishedState, outlet_events: OutletEvents) -> None:
        """Upload the cumulative CSV to S3 and record its Airflow asset event.

        When RF_CATALOGUE_OUTPUT_DIR is set, also publish a local copy for
        subsequent steps such as building GitHub Pages. Record the show, episode
        count, content hash, and cutoff used after successful publication.
        """
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

    @task(outlets=[TRANSCRIPTS])
    def download_transcripts(state: PublishedState, outlet_events: OutletEvents) -> None:
        """Download and publish transcripts for this run's newly fetched episodes.

        Read IDs from updates.csv, fetch each transcript using the existing API
        helper, and write JSONs to RF_TRANSCRIPT_DIR (default transcripts/).
        Upload each JSON to data/transcripts/<episode-id>.json and emit an asset
        event with its show, episode ID and content hash. Errors fail the task;
        previous missing downloads are left to a future manual recovery DAG.
        """
        directory = Path(os.environ.get('RF_TRANSCRIPT_DIR', str(PROJECT / 'transcripts')))
        directory.mkdir(parents=True, exist_ok=True)
        rows = read_rows(Path(state['workspace']) / 'updates.csv', state['show'])
        for row in rows:
            episode_id = row['id']
            transcript = fetch_transcript(aod_uuid(episode_id))
            if transcript is None:
                raise RuntimeError(f'Transcript unavailable for {episode_id}')
            path = directory / f'{episode_id}.json'
            temporary = path.with_suffix('.json.tmp')
            temporary.write_text(json.dumps(transcript, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            temporary.replace(path)
            destination = s3_uri(f'data/transcripts/{episode_id}.json')
            aws('s3', 'cp', str(path), destination)
            outlet_events[TRANSCRIPTS].add(
                Asset(uri=destination, name=f'{episode_id}_transcript'),
                extra={'show': state['show'], 'episode_id': episode_id,
                       'sha256': hashlib.sha256(path.read_bytes()).hexdigest()},
            )

    # Airflow injects context arguments and resolves XComArg values at runtime;
    # its decorator annotations retain the underlying Python function signature.
    baseline = record_episodes(retrieve_catalogue(), 'baseline.csv')  # pyright: ignore[reportCallIssue, reportArgumentType]
    catalogue = merge_catalogue(fetch_updates(derive_cutoff(baseline)))  # pyright: ignore[reportCallIssue, reportArgumentType]
    published = publish_catalogue(catalogue)  # pyright: ignore[reportCallIssue, reportArgumentType]
    references = record_episodes.override(task_id='record_new_episodes')(catalogue, 'updates.csv')  # pyright: ignore[reportCallIssue, reportArgumentType]
    _ = published >> references
    transcripts = download_transcripts(catalogue)  # pyright: ignore[reportCallIssue, reportArgumentType]
    _ = references >> transcripts
