"""Manually register existing S3 catalogues and artifacts in Airflow metadata."""
import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal, Protocol, TypedDict, cast

from airflow.sdk import Asset, AssetAlias, DAG, task
from radiofrance_catalogue.catalogue import SHOWS, read_rows
from radiofrance_catalogue.episodes import EPISODES, EpisodeEvent, record_episode_rows
from radiofrance_catalogue.storage import StoredObject, aws, list_objects, s3_uri, uri


type ArtifactKind = Literal['transcript', 'chunks', 'embeddings']


class ArtifactSpec(TypedDict):
    prefix: str
    suffix: str
    alias: AssetAlias


CATALOGUES = AssetAlias('radiofrance-show-catalogues')
ARTIFACTS: dict[ArtifactKind, ArtifactSpec] = {
    'transcript': {'prefix': 'data/transcripts/', 'suffix': '.json', 'alias': AssetAlias('radiofrance-transcripts')},
    'chunks': {'prefix': 'data/chunks/', 'suffix': '.csv', 'alias': AssetAlias('radiofrance-chunks')},
    'embeddings': {'prefix': 'data/embeddings/', 'suffix': '.csv', 'alias': AssetAlias('radiofrance-embeddings')},
}


class RegistrationEvent(TypedDict, total=False):
    status: str
    artifact_type: str
    episode_id: str
    show: str
    size_bytes: int
    source_uri: str


class AssetIdentity(Protocol):
    @property
    def uri(self) -> str: ...


class RegisteredEvent(Protocol):
    @property
    def asset(self) -> AssetIdentity: ...

    @property
    def extra(self) -> Mapping[str, str | int]: ...


class InletEvents(Protocol):
    def __getitem__(self, asset: AssetAlias) -> Sequence[RegisteredEvent]: ...


class EventEmitter(Protocol):
    def add(self, asset: Asset, *, extra: EpisodeEvent | RegistrationEvent) -> None: ...


class OutletEvents(Protocol):
    def __getitem__(self, asset: AssetAlias) -> EventEmitter: ...


with DAG(
    dag_id='radiofrance_register',
    description='Register existing S3 catalogues, episode references, transcripts, chunks and embeddings',
    doc_md="""Manually initialize or reconcile Airflow's inventory from authoritative S3.
Read the six catalogue CSVs to register episode references with publication dates.
List transcript JSONs, chunk CSVs and embedding CSVs; record existing files as
`discovered_existing`, skipping URIs already present in each alias's event history.
Basenames establish episode identity and source relationships.

Set RF_S3_BUCKET and AWS credentials with listing access to the artifact prefixes
and read access to data/episodes/*.csv. RF_S3_REGION defaults to eu-west-2;
the ignored local aws/cache-config.json is also supported. AWS CLI must be on PATH.
Trigger with `airflow/run dags trigger radiofrance_register` or run through dags test.
This DAG writes Airflow metadata only. It does not upload S3 objects, download
artifact bodies, generate transformations, or import the Radio France database.
CI can supply RF_REGISTRATION_INVENTORY_DIR containing JSON listings prepared
by airflow/prepare_registration.py to time S3 operations separately from metadata
registration. RF_CATALOGUE_BASELINE_DIR similarly supplies downloaded catalogues.
It populates whichever Airflow metadata database the current installation uses.
See airflow/README.md for the CLI and prefix layout.
""",
    schedule=None,
    catchup=False,
    max_active_runs=1,
    tags=['radiofrance', 'register'],
) as dag:
    @task(inlets=[CATALOGUES, *EPISODES.values()], outlets=[CATALOGUES, *EPISODES.values()])
    def register_catalogues(inlet_events: InletEvents, outlet_events: OutletEvents) -> int:
        """Read existing show CSVs and register catalogue and episode references.

        Reuse RF_CATALOGUE_BASELINE_DIR when supplied; otherwise download the
        six catalogues into a temporary directory removed on completion.
        Record missing episode/date pairs using the
        shared Download DAG helper. Emit discovered-existing catalogue events
        only for URIs not already registered. Return the new reference count.
        """
        known = {event.asset.uri for event in inlet_events[CATALOGUES]}
        count = 0
        with tempfile.TemporaryDirectory(prefix='radiofrance-register-') as directory:
            for show in SHOWS:
                path = Path(directory) / f'{show}.csv'
                destination = uri(show)
                baselines = os.environ.get('RF_CATALOGUE_BASELINE_DIR')
                if baselines:
                    shutil.copyfile(Path(baselines) / path.name, path)
                else:
                    aws('s3', 'cp', destination, str(path))
                alias = EPISODES[show]
                rows = read_rows(path, show)
                count += record_episode_rows(show, rows, inlet_events[alias], outlet_events[alias])
                if destination not in known:
                    outlet_events[CATALOGUES].add(
                        Asset(uri=destination, name=f'{show}_catalogue'),
                        extra={'status': 'discovered_existing', 'artifact_type': 'catalogue',
                               'show': show, 'size_bytes': path.stat().st_size},
                    )
        return count

    @task(inlets=[spec['alias'] for spec in ARTIFACTS.values()], outlets=[spec['alias'] for spec in ARTIFACTS.values()])
    def register_files(kind: ArtifactKind, inlet_events: InletEvents, outlet_events: OutletEvents) -> int:
        """Register listed S3 files of one artifact type without reading bodies.

        Skip folder markers, other file extensions, and URIs already recorded
        in this type's alias. Use the basename as the episode ID and preserve
        the actual S3 URI. Record the size and inferred source URI for chunks
        and embeddings; these links do not assert that the source file exists.
        Return the number of newly registered files.
        When RF_REGISTRATION_INVENTORY_DIR is supplied, read that run's local
        S3 listing snapshot instead of calling S3 inside this task.
        """
        spec = ARTIFACTS[kind]
        alias = spec['alias']
        known = {event.asset.uri for event in inlet_events[alias]}
        count = 0
        inventory = os.environ.get('RF_REGISTRATION_INVENTORY_DIR')
        objects = (cast(list[StoredObject], json.loads(
            (Path(inventory) / f'{kind}.json').read_text()))
            if inventory else list_objects(spec['prefix']))
        for stored in objects:
            key = stored['Key']
            if not key.endswith(spec['suffix']):
                continue
            path = Path(key)
            episode_id = path.stem
            destination = s3_uri(key)
            if destination in known:
                continue
            metadata: RegistrationEvent = {
                'status': 'discovered_existing', 'artifact_type': kind,
                'episode_id': episode_id, 'size_bytes': stored['Size'],
            }
            if kind == 'chunks':
                metadata['source_uri'] = s3_uri(f'data/transcripts/{episode_id}.json')
            elif kind == 'embeddings':
                metadata['source_uri'] = s3_uri(f'data/chunks/{episode_id}.csv')
            name = f'{episode_id}_{kind}'
            if str(path.parent) + '/' != spec['prefix']:
                name += '_' + hashlib.sha256(key.encode()).hexdigest()[:12]
            outlet_events[alias].add(Asset(uri=destination, name=name), extra=metadata)
            known.add(destination)
            count += 1
        return count

    catalogues = register_catalogues()  # pyright: ignore[reportCallIssue]
    artifacts = register_files.expand(kind=list(ARTIFACTS))
    _ = catalogues >> artifacts
