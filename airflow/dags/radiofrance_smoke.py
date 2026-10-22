"""Manual scheduling and persistence check for the Compose installation."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import TypedDict

from airflow.sdk import DAG, task


class Greeting(TypedDict):
    message: str
    produced_at: str


with DAG(
    dag_id='radiofrance_smoke',
    description='Verify task execution by writing a greeting to the Airflow volume.',
    doc_md="""
## Purpose
Check that the Compose scheduler can execute a task and persist its output.
This DAG performs no downloads, creates no asset events, and opens no database
connections itself. Airflow records its task/run history in the metadata database.

## Execution flow
The single `write_greeting` task writes `$AIRFLOW_HOME/smoke-output/hello.json`
atomically. Repeating the run replaces the greeting with the current timestamp.

## Configuration and running
No API keys or additional configuration are required. Start the repository's
Compose stack, sign in to the UI, enable this DAG and trigger it manually.
Inspect the task log, then use `docker compose exec airflow cat
/opt/airflow/smoke-output/hello.json` to inspect the persistent output. It remains
in the Airflow named volume across container restarts.
""",
    schedule=None,
    catchup=False,
    max_active_runs=1,
    tags=['radiofrance', 'smoke'],
) as dag:
    @task
    def write_greeting() -> None:
        """Write a timestamped greeting with no inputs or return value.

        Create the smoke-output directory under AIRFLOW_HOME and atomically
        replace hello.json in its persistent volume. Log the path for inspection;
        no external service or application database is accessed.
        """
        output = Path(os.environ['AIRFLOW_HOME']) / 'smoke-output' / 'hello.json'
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix('.json.tmp')
        greeting: Greeting = {
            'message': 'Hello from Radio France Airflow!',
            'produced_at': datetime.now(timezone.utc).isoformat(),
        }
        temporary.write_text(json.dumps(greeting, indent=2) + '\n', encoding='utf-8')
        temporary.replace(output)
        print(f'Created {output}')

    write_greeting()
