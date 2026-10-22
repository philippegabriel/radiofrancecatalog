"""Parse all maintained DAGs without starting Airflow services or processing jobs."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'airflow/dags'))

from airflow.dag_processing.dagbag import DagBag


def main() -> None:
    """Fail on import errors, missing maintained DAGs or unexpected retired DAGs."""
    bag = DagBag(dag_folder=ROOT / 'airflow/dags')
    if bag.import_errors:
        for path, error in bag.import_errors.items():
            print(f'{path}:\n{error}', file=sys.stderr)
        raise SystemExit('DAG imports failed')
    expected = {'radiofrance_smoke', 'radiofrance_download', 'radiofrance_chunk',
        'radiofrance_embedding', 'radiofrance_database', 'radiofrance_pipeline'}
    if set(bag.dags) != expected:
        raise SystemExit(f'Unexpected DAG set: {sorted(bag.dags)}; expected {sorted(expected)}')
    print(f'Parsed {len(bag.dags)} maintained DAGs successfully')


if __name__ == '__main__':
    main()
