"""Readable names for Airflow's artifact directories."""

import re


def run_folder(run_id: str) -> str:
    """Convert an Airflow run ID into a readable single-directory name."""
    return 'airflow-' + re.sub(r'[^A-Za-z0-9_-]', '_', run_id)
