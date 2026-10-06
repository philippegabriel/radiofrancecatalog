"""A manually triggered smoke test; no transcript or database imports."""
from pathlib import Path
from collections.abc import Iterator
import json
import os
from datetime import datetime, timezone

from airflow.sdk import Asset, DAG, Metadata, task

TEST_OUTPUT = Asset(
    uri="x-radiofrance://test/hello.json",
    name="radiofrance_test_output",
)

with DAG(
    dag_id="radiofrance_test",
    description="Write a greeting file and record its production as an asset event.",
    schedule=None,
    catchup=False,
    tags=["radiofrance", "test"],
):
    @task(outlets=[TEST_OUTPUT])
    def write_greeting() -> Iterator[Metadata]:
        output = Path(os.environ["AIRFLOW_HOME"]) / "test-output" / "hello.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        produced_at = datetime.now(timezone.utc).isoformat()
        temporary = output.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps({"message": "Hello from Radio France Airflow!", "produced_at": produced_at}, indent=2)
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(output)
        print(f"Created {output}")
        yield Metadata(TEST_OUTPUT, {"path": str(output), "produced_at": produced_at})

    write_greeting()
