"""Snapshot S3 listings locally so CI can time S3 and metadata work separately."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'dags'))

from radiofrance_register import ARTIFACTS
from radiofrance_catalogue.storage import list_objects


def main() -> None:
    """Write one listing per artifact type, without downloading artifact bodies."""
    directory = Path(sys.argv[1])
    directory.mkdir(parents=True, exist_ok=True)
    for kind, spec in ARTIFACTS.items():
        objects = list_objects(spec['prefix'])
        (directory / f'{kind}.json').write_text(json.dumps(objects))
        print(f'{kind}: {len(objects)} objects listed')


if __name__ == '__main__':
    main()
