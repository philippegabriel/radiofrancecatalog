"""Resolve registered/new shows at trigger time, process them serially, then export HTML."""

from collections.abc import Mapping
from datetime import timedelta
import os
from pathlib import Path
from typing import TypedDict, cast, NotRequired

from airflow.sdk import DAG, Param, task, PokeReturnValue, get_current_context
from airflow.sdk.definitions.param import ParamsDict
from airflow.providers.standard.operators.trigger_dagrun import TriggerDagRunOperator
from airflow.providers.docker.operators.docker import DockerOperator
from docker.types import Mount
from psycopg2.extensions import make_dsn

from radiofrance.libs.date_range import date_range
from radiofrance.libs.run_metrics import ShowMetrics, show_metrics, describe_metrics, run_totals

from radiofrance.libs.programmes import ProgrammeSelection, resolve_programmes, read_show_choices


class ShowConf(TypedDict):
    from_date: NotRequired[str]
    to_date: NotRequired[str]
    show: str
    show_url: str


class ShowRequest(TypedDict):
    trigger_run_id: str
    conf: ShowConf


SHOW_CHOICES = read_show_choices()


with DAG(
    dag_id='radiofrance_pipeline',
    description='Select registered shows or add programme URLs, process each show, then refresh HTML.',
    doc_md="""
## Trigger form
Select one or more slugs in shows (an array of strings), choose all_enabled, or
provide new_show_url (an array of HTTPS Radio France programme URLs).
Optional from_date/to_date (YYYY-MM-DD) select inclusive Europe/Paris broadcast
dates for every show. With either bound supplied, ignore the latest database
watermark and skip all already committed episode IDs, allowing historical backfills.
Leave both dates blank for normal incremental acquisition. Reversed/invalid ranges
fail before show registration. Bounds are included in the unified monitor log.
The selection is their union. At least one option is required. The initial task
validates URLs and registers missing shows transactionally in shows,
then freezes the exact selection in XCom. A short read-only registry query populates
the multi-select suggestions during parsing; failures leave the DAG available.
Suggestions are refreshed on the next parse and runtime selection is revalidated.

## Flow and configuration
select_shows queues one process_episodes run per selected show.
That coordinator has max_active_runs=1, so only one show's processing chain can
be active at once. Enable it, the four stage DAGs and this parent. Apply
 the upgrades in database/CATALOGUE_NAMES_MIGRATION.md to existing databases and rebuild
the affected images. Fresh databases create the registry before episodes.
Compose provides application DB credentials and Docker/model settings.

## Outputs and recovery
Each show has data/runs/<UTC timestamp>/download/<show> metadata and explicit shared
corpus references. Chunk/embedding completion directories end in --<show>.
Successful shows commit independently; a failed show leaves those commits intact
and does not prevent other already queued shows from running. The parent fails
if any show fails. catalogue runs once only if all selected shows succeed, using
all committed episodes. A new parent run reuses files and database cutoffs.
Registered URLs persist even if acquisition later fails. URL syntax is validated
before registration; the live API determines whether a programme actually exists.
Automatic retries and child resets are disabled. See docs/storage.md.

## Unified live progress
Open monitor_run > Logs in this parent run for all selected shows. It polls
published manifests and atomic embedding progress every ten seconds, rescheduling
between polls so it does not hold a worker slot. Missing stage counts are unknown,
not zero. The first encoding-rate estimate appears after ten new vectors, then
every hundred; model loading and cached vectors are excluded from encoding rate.
The completion XCom contains per-show metrics and terminal processing states,
including failed or skipped work. The monitor observes but does not hide failures.

""",
    params=ParamsDict({
        'from_date': Param(None, type=['null', 'string'], format='date', title='From date',
            description='Optional inclusive broadcast date, Europe/Paris. Explicit dates bypass the latest database cutoff.'),
        'to_date': Param(None, type=['null', 'string'], format='date', title='To date',
            description='Optional inclusive broadcast date, Europe/Paris. Leave both dates blank for incremental processing.'),
        'shows': Param([], type='array', items={'type': 'string'}, examples=SHOW_CHOICES,
            title='Shows',
            description='Select one or more registered shows. Choices refresh when Airflow reloads this DAG.'),
        'all_enabled': Param(False, type='boolean', description='Process all enabled shows in the registry.'),
        'new_show_url': Param([], type='array', items={'type': 'string'}, title='New show URLs',
            description='Register and process new shows: https://www.radiofrance.fr/<station>/podcasts/<show>'),
    }),
    schedule=None, catchup=False, max_active_runs=1,
    default_args={'retries': 0}, tags=['radiofrance', 'pipeline'],
) as dag:
    @task(multiple_outputs=False)
    def select_shows(params: Mapping[str, object], run_id: str) -> list[ShowRequest]:
        """Validate trigger options, register new shows, and return frozen child requests.

        Inputs are the show selection, optional inclusive Paris dates and parent
        run ID. Writes only the application show registry in one transaction.
        Returns public slugs, URLs and explicit child IDs; no credentials or model
        data enter XCom. Empty/unknown selections fail before downloads start.
        """
        bounds = date_range(cast(str | None, params.get('from_date')), cast(str | None, params.get('to_date')))
        password = os.environ.get('RF_DATABASE_PASSWORD', '')
        if not password:
            raise ValueError('Configure RF_DATABASE_PASSWORD in Compose')
        dsn = make_dsn(host=os.environ.get('RF_DATABASE_HOST', 'postgres'),
            port=os.environ.get('RF_DATABASE_PORT', '5432'), dbname='radiofrance',
            user='radiofrance', password=password)
        if dsn is None:
            raise ValueError('Could not construct application database connection')
        selection: ProgrammeSelection = {'shows': cast(list[str], params['shows']),
            'all_enabled': cast(bool, params['all_enabled']),
            'new_show_url': cast(list[str], params['new_show_url'])}
        programmes = resolve_programmes(selection, dsn)
        print('Selected shows: ' + ', '.join(item['slug'] for item in programmes))
        requests: list[ShowRequest] = []
        for programme in programmes:
            conf: ShowConf = {'show': programme['slug'], 'show_url': programme['url']}
            if bounds['from_date']:
                conf['from_date'] = bounds['from_date']
            if bounds['to_date']:
                conf['to_date'] = bounds['to_date']
            requests.append({'trigger_run_id': f"shows__{run_id}__{programme['slug']}", 'conf': conf})
        return requests

    requests = select_shows()  # pyright: ignore[reportCallIssue]
    shows = TriggerDagRunOperator.partial(
        task_id='process_shows', trigger_dag_id='process_episodes',
        wait_for_completion=True, deferrable=True, fail_when_dag_is_paused=True,
        reset_dag_run=False, skip_when_already_exists=False,
        allowed_states=['success'], failed_states=['failed'], poke_interval=15,
        doc_md='Queue one explicit single-show coordinator per frozen registry selection. '
            'The coordinator max_active_runs=1 serializes full show chains. Defer while waiting; '
            'any failure fails the parent, and catalogue runs only after all successes.',
    ).expand_kwargs(requests)
    catalogue = DockerOperator(
        task_id='catalogue', image='radiofrance-database:dev',
        entrypoint=['python', '-m', 'radiofrance.jobs.catalogue'],
        command=['--output-dir', '/data/catalogue', '--replace'],
        docker_url='unix://var/run/docker.sock', api_version='auto',
        mounts=[Mount('/data', f'{os.environ.get("RF_PROJECT_ROOT") or "/not-configured"}/data', type='bind')],
        environment={'PGHOST': os.environ.get('RF_DATABASE_HOST', 'postgres'),
            'PGPORT': os.environ.get('RF_DATABASE_PORT', '5432'),
            'PGDATABASE': 'radiofrance', 'PGUSER': 'radiofrance'},
        private_environment={'PGPASSWORD': os.environ.get('RF_DATABASE_PASSWORD', '')},
        user=f"{os.environ.get('RF_UID', '1000')}:{os.environ.get('RF_GID', '1000')}",
        network_mode=os.environ.get('RF_DOCKER_NETWORK', 'radiofrance_default'),
        mount_tmp_dir=False, auto_remove='force', do_xcom_push=False, mem_limit='1g',
        execution_timeout=timedelta(minutes=15),
        doc_md='After verified database loading, read all committed episode metadata in a read-only snapshot. '
            'Refresh the shared static HTML catalogue and count manifest under data/catalogue, '
            'using the host UID/GID. No database writes, model inference or web publication. '
            'A failure leaves the successful database import intact; a fresh pipeline run can regenerate the site.',
    )
    @task.sensor(poke_interval=10, timeout=7 * 24 * 3600, mode='reschedule')
    def monitor_run(requests: list[ShowRequest], run_id: str) -> PokeReturnValue:
        """Publish one live log for selected shows while child DAGs and catalogue run.

        Read run-scoped manifests/sidecars from the read-only data mount and task
        states through Airflow's SDK. Persist the last printed messages in XCom
        between reschedules, logging the selected shows first and then only changes. Return final per-show metrics
        and terminal states in XCom; failed stages remain failures in the parent.
        No application DB access, model loading or artifact writes occur here.
        """
        ti = get_current_context().get('ti')
        if ti is None:
            raise RuntimeError('Airflow task context is required')
        previous = cast(dict[str, str], ti.xcom_pull(task_ids='monitor_run', key='last_messages') or {})
        metrics: dict[str, ShowMetrics] = {}
        messages: dict[str, str] = {'selection': f'Selected shows ({len(requests)}): '
            + ', '.join(request['conf']['show'] for request in requests)}
        if requests:
            conf = requests[0]['conf']
            messages['date_range'] = ('Date range (Europe/Paris): '
                + f"{conf.get('from_date') or 'unbounded'} to {conf.get('to_date') or 'unbounded'}"
                if conf.get('from_date') or conf.get('to_date') else 'Date selection: incremental database cutoff')
        for request in requests:
            show = request['conf']['show']
            metrics[show] = show_metrics(Path('/opt/radiofrance/data'), request['trigger_run_id'], show)
            messages[show] = describe_metrics(show, metrics[show])
        all_states = ti.get_task_states(dag_id='radiofrance_pipeline', run_ids=[run_id],
            task_ids=['process_shows', 'catalogue'])
        states = cast(dict[str, str | None], all_states.get(run_id, {}))
        messages['states'] = 'Processing states: ' + str(states)
        for key, message in messages.items():
            if previous.get(key) != message:
                print(message, flush=True)
        ti.xcom_push(key='last_messages', value=messages)
        terminal = {'success', 'failed', 'upstream_failed', 'skipped', 'removed'}
        expected = ['catalogue', *(f'process_shows_{index}' for index in range(len(requests)))]
        done = all(states.get(key) in terminal for key in expected)
        if done:
            print('Run complete. Final metrics (unreported stages remain unknown):', flush=True)
            for show, values in metrics.items():
                print(describe_metrics(show, values), flush=True)
            print('Run totals: ' + str(run_totals(metrics)), flush=True)
            print('Final processing states: ' + str(states), flush=True)
        return PokeReturnValue(is_done=done, xcom_value={'shows': metrics, 'totals': run_totals(metrics), 'states': states} if done else None)

    monitoring = monitor_run(requests)  # pyright: ignore[reportArgumentType, reportCallIssue]
    _ = shows >> catalogue
