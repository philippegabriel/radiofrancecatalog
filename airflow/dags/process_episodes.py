"""Serialize one show through its verified processing stage DAGs."""

from datetime import timedelta
from collections.abc import Mapping
import re
from typing import Literal, TypedDict

from radiofrance.libs.date_range import date_range
from radiofrance.libs.runs import run_folder
from radiofrance.libs.programmes import show_url as validate_show_url, read_show_choices, resolve_programmes, registry_dsn

from airflow.sdk import DAG, Param, task
from airflow.sdk.definitions.param import ParamsDict
from airflow.providers.standard.operators.trigger_dagrun import TriggerDagRunOperator


type Stage = Literal['download', 'chunk', 'embedding', 'database']


class StageRun(TypedDict):
    run_id: str
    folder: str


class PipelineRuns(TypedDict):
    download: StageRun
    chunk: StageRun
    embedding: StageRun
    database: StageRun
    show_url: str


class StageConf(TypedDict, total=False):
    from_date: str
    to_date: str
    download_run: str
    chunk_run: str
    embedding_run: str
    show: str
    show_url: str
    output_folder: str


def run_reference(parent_run_id: str, stage: Stage, show: str) -> StageRun:
    """Name a stage run and its artifact folder using the stage jobs' run-ID convention."""
    child_run_id = f'pipeline__{parent_run_id}__{stage}'
    folder = run_folder(parent_run_id)
    return {'run_id': child_run_id, 'folder': (f'{folder}/download/{show}' if stage == 'download'
        else f'{folder}--{show}')}


def stage_operator(stage: Stage, configuration: StageConf | None = None) -> TriggerDagRunOperator:
    """Trigger an existing stage and defer until success; propagate child failure to the pipeline.

    Input is a stage name and small upstream directory references. The operator
    creates an Airflow DAG run, without copying files or creating asset events.
    Waiting uses the triggerer rather than occupying a processing worker slot.
    """
    return TriggerDagRunOperator(
        task_id=stage, trigger_dag_id=f'radiofrance_{stage}',
        trigger_run_id=f"{{{{ ti.xcom_pull(task_ids='prepare_pipeline')['{stage}']['run_id'] }}}}",
        conf=dict(configuration or {}), wait_for_completion=True, deferrable=True,
        fail_when_dag_is_paused=True, reset_dag_run=False, skip_when_already_exists=False,
        allowed_states=['success'], failed_states=['failed'], poke_interval=15,
        execution_timeout=timedelta(hours=4),
        doc_md=f'Trigger radiofrance_{stage} with this pipeline run’s explicit child identity and upstream directory. Wait without holding a worker slot. Continue only after child success; fail if the stage fails or is paused. Existing child runs are never reset automatically.',
    )


SHOW_CHOICES = read_show_choices()


with DAG(
    dag_id='process_episodes',
    description='Internal coordinator: process one explicitly selected show through verified database loading.',
    doc_md="""
## Purpose and flow
Internal single-show coordinator used by radiofrance_pipeline. It runs download,
chunk, embedding and database child DAGs, waiting for each verification. Exactly
one coordinator run may be active, so selected shows process sequentially even
though the parent queues all requests. A failed show does not reset committed
work; other queued shows can still run, and the parent reports any failure.

## Configuration and recovery
The parent supplies show and show_url from the show registry, plus optional
from_date/to_date inclusive Paris broadcast dates. Validate the range before
triggering any stages and pass it only to download; downstream jobs use its manifest. This DAG and
all four stages must be enabled. A bounded read-only registry query populates the
show suggestions during parsing. Suggestions do not restrict newly registered
shows passed by the parent before the next DAG refresh.
For standalone recovery select a registered show; show_url can be left blank
to resolve its registered URL at task execution time.
Host paths, credentials and model cache come from Compose. Parent and child
triggerers must be running. Automatic retries and child resets are disabled.

## Outputs
Download records go under data/runs/<parent UTC timestamp>/download/<show>.
Chunk and embedding completion folders use <timestamp>--<show>. Corpus files
accumulate by episode and configuration. This DAG does not export HTML; the parent
does so once after all selected shows succeed. See docs/storage.md.
""",
    schedule=None, catchup=False, max_active_runs=1,
    dagrun_timeout=timedelta(hours=6), default_args={'retries': 0},
    params=ParamsDict({
        'from_date': Param(None, type=['null', 'string'], format='date', description='Inclusive Paris broadcast date; optional historical selection.'),
        'to_date': Param(None, type=['null', 'string'], format='date', description='Inclusive Paris broadcast date; leave both dates blank for incremental mode.'),
        'show': Param('', type='string', examples=SHOW_CHOICES, title='Show'),
        'show_url': Param(None, type=['null', 'string'], title='Show URL', description='Optional override; leave blank to use the registered show URL.')}),
    tags=['radiofrance', 'internal'],
) as dag:
    @task(multiple_outputs=False)
    def prepare_pipeline(run_id: str, params: Mapping[str, str | None]) -> PipelineRuns:
        """Return stage identities and folder references for this parent run.

        Inputs are Airflow's run_id, show and optional Paris date bounds. Output is a small typed XCom mapping consumed by
        the trigger tasks. Read/write no files, secrets, database data or models.
        Different parent runs receive distinct artifact directories.
        """
        date_range(params.get('from_date'), params.get('to_date'))
        show = params.get('show') or ''
        if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', show):
            raise ValueError('Invalid show slug')
        url = params.get('show_url')
        if not url:
            url = resolve_programmes({'shows': [show], 'all_enabled': False, 'new_show_url': []}, registry_dsn())[0]['url']
        if validate_show_url(url)['slug'] != show:
            raise ValueError('Programme URL does not match show')
        return {'show_url': url, 'download': run_reference(run_id, 'download', show), 'chunk': run_reference(run_id, 'chunk', show),
            'embedding': run_reference(run_id, 'embedding', show), 'database': run_reference(run_id, 'database', show)}

    references = prepare_pipeline()  # pyright: ignore[reportCallIssue]
    download = stage_operator('download', {'from_date': "{{ params.get('from_date') or '' }}",
        'to_date': "{{ params.get('to_date') or '' }}", 'show': '{{ params.show }}', 'show_url': "{{ ti.xcom_pull(task_ids='prepare_pipeline')['show_url'] }}",
        'output_folder': "{{ ti.xcom_pull(task_ids='prepare_pipeline')['download']['folder'] }}"})
    chunk = stage_operator('chunk', {'show': '{{ params.show }}',
        'output_folder': "{{ ti.xcom_pull(task_ids='prepare_pipeline')['chunk']['folder'] }}", 'download_run': "{{ ti.xcom_pull(task_ids='prepare_pipeline')['download']['folder'] }}"})
    embedding = stage_operator('embedding', {'show': '{{ params.show }}',
        'output_folder': "{{ ti.xcom_pull(task_ids='prepare_pipeline')['embedding']['folder'] }}", 'chunk_run': "{{ ti.xcom_pull(task_ids='prepare_pipeline')['chunk']['folder'] }}"})
    database = stage_operator('database', {'embedding_run': "{{ ti.xcom_pull(task_ids='prepare_pipeline')['embedding']['folder'] }}"})
    _ = references >> download >> chunk >> embedding >> database
