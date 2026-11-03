"""Readable names for Airflow's artifact directories."""

import re
from datetime import datetime, timezone


def run_folder(run_id: str) -> str:
    """Convert an Airflow run ID into a readable single-directory name."""
    match = re.search(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})', run_id)
    if match:
        return datetime.fromisoformat(match[0]).astimezone(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S-%fZ')
    return 'airflow-' + re.sub(r'[^A-Za-z0-9_-]', '_', run_id)
