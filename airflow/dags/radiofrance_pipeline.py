"""Run the download-to-database pilot by coordinating the existing stage DAGs."""

from datetime import timedelta
import hashlib
from typing import Literal, TypedDict

from airflow.sdk import DAG, task
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


class StageConf(TypedDict, total=False):
    download_run: str
    chunk_run: str
    embedding_run: str


def run_reference(parent_run_id: str, stage: Stage) -> StageRun:
    """Name a child run and its artifact folder using the stage jobs' run-ID convention."""
    token = hashlib.sha256(parent_run_id.encode()).hexdigest()
    child_run_id = f'pipeline__{token}__{stage}'
    return {'run_id': child_run_id, 'folder': 'airflow-' + hashlib.sha256(child_run_id.encode()).hexdigest()}


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


with DAG(
    dag_id='radiofrance_pipeline',
    description='Run the mecaniques-du-journalisme pilot from download through verified database loading.',
    doc_md="""
## Purpose and flow
One manually triggered pipeline run coordinates the existing radiofrance_download,
radiofrance_chunk, radiofrance_embedding and radiofrance_database DAGs in that order.
Each stage must finish successfully, including its verification task, before the
next starts. Processing logic stays in those DAGs and their container jobs.

`prepare_pipeline` returns explicit child run IDs and artifact folder names. Each
stage receives the exact folder produced by its predecessor, never the newest
folder found on disk. The child jobs already derive output directories from their
run IDs; this DAG uses the same convention. Metadata XCom contains references only.

## Configuration and running
Follow airflow/DEPLOYMENT.md and build the four processing images. The API key,
model cache, baseline, application database and host mounts are unchanged. Enable
this DAG AND all four child DAGs in the UI, then trigger radiofrance_pipeline
without parameters. A running scheduler and triggerer are required. Deferrable
waits release worker slots so child tasks can run with the current parallelism.
This remains the mecaniques-du-journalisme pilot; it does not generalize show
selection or change the fixed baseline CSV.

## Outputs and recovery
Artifacts remain in the normal download-pilot, chunk-pilot and embedding-pilot
run directories. The final child imports their complete lineage transactionally
and verifies the committed database records. No extra copies or asset events are
created. Empty updates flow through all stages as successful empty work.
Automatic retries and child resets are disabled. If a stage fails, downstream
stages do not start. Inspect the linked child run. A fresh pipeline run creates
fresh child IDs/folders and is the simplest whole-pipeline retry. Do not clear a
completed parent trigger to rerun an immutable producing stage: it intentionally
refuses an existing child run. Individual stage DAGs remain available for manual
recovery using their documented explicit run parameters. If a pipeline times out,
check child status before retrying; a parent timeout does not cancel child work.
""",
    schedule=None, catchup=False, max_active_runs=1,
    dagrun_timeout=timedelta(hours=6), default_args={'retries': 0},
    tags=['radiofrance', 'pipeline'],
) as dag:
    @task(multiple_outputs=False)
    def prepare_pipeline(run_id: str) -> PipelineRuns:
        """Return child run identities and folder references for this parent run.

        Input is Airflow's run_id. Output is a small typed XCom mapping consumed by
        the trigger tasks. Read/write no files, secrets, database data or models.
        Different parent runs receive distinct artifact directories.
        """
        return {'download': run_reference(run_id, 'download'), 'chunk': run_reference(run_id, 'chunk'),
            'embedding': run_reference(run_id, 'embedding'), 'database': run_reference(run_id, 'database')}

    references = prepare_pipeline()  # pyright: ignore[reportCallIssue]
    download = stage_operator('download')
    chunk = stage_operator('chunk', {'download_run': "{{ ti.xcom_pull(task_ids='prepare_pipeline')['download']['folder'] }}"})
    embedding = stage_operator('embedding', {'chunk_run': "{{ ti.xcom_pull(task_ids='prepare_pipeline')['chunk']['folder'] }}"})
    database = stage_operator('database', {'embedding_run': "{{ ti.xcom_pull(task_ids='prepare_pipeline')['embedding']['folder'] }}"})
    _ = references >> download >> chunk >> embedding >> database
