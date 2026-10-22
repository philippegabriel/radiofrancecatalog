"""Initialize SimpleAuthManager's password file from explicit UI credentials."""

import json
import os
from pathlib import Path
import re
import tempfile


def configure_ui(path: Path, username: str, password: str) -> None:
    """Atomically write the configured UI login with mode 0600, without logging credentials.

    Run before Airflow starts. Replacing the file on each start applies intentional
    .env changes and avoids generating new credentials when the volume is recreated.
    """
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', username) or not password:
        raise ValueError('Set a nonempty AIRFLOW_UI_PASSWORD and a simple AIRFLOW_UI_USERNAME')
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump({username: password}, stream)
            stream.write('\n')
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    """Write the UI password file, then replace this process with Airflow standalone."""
    configure_ui(Path(os.environ['AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_PASSWORDS_FILE']),
        os.environ.get('AIRFLOW_UI_USERNAME', 'admin'), os.environ.get('AIRFLOW_UI_PASSWORD', ''))
    os.execvp('airflow', ['airflow', 'standalone'])


if __name__ == '__main__':
    main()
