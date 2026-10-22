"""Prepare and drive a disposable GitHub-hosted Airflow integration environment."""

import argparse
import csv
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
from typing import TypedDict, cast
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from radiofrance.libs.episode_selection import read_selection

FIXTURE = ROOT / 'ci/fixtures/le-cours-de-l-histoire.json'
SHOW = 'le-cours-de-l-histoire'
DAGS = ('radiofrance_download', 'radiofrance_chunk', 'radiofrance_embedding',
        'radiofrance_database', 'radiofrance_pipeline')
FIELDS = ['show', 'published_iso', 'published_ts', 'title', 'description',
          'web_url', 'podcast_title', 'podcast_url', 'player_url', 'id']


class IntegrationSettings(TypedDict):
    RF_PROJECT_ROOT: str
    RF_UID: str
    RF_GID: str
    RF_DOCKER_GID: str
    RF_SHOW: str
    RF_EPISODE_SELECTION: str
    RF_HF_HUB_CACHE: str
    AIRFLOW_UI_USERNAME: str
    AIRFLOW_UI_PASSWORD: str


class IntegrationSummary(TypedDict):
    pipeline_run_id: str
    show: str
    episode_count: int
    episode_ids: list[str]
    status: str


def prepare() -> None:
    """Create runner-only settings, empty baseline and API-key file; never overwrite an installation."""
    if os.environ.get('GITHUB_ACTIONS') != 'true' or (ROOT / '.env').exists():
        raise ValueError('Integration preparation requires a fresh GitHub Actions checkout without .env')
    key = os.environ.get('RADIOFRANCE_API_KEY', '').strip()
    if not key:
        raise ValueError('Repository secret OPENAPIKEY is required')
    selection = read_selection(FIXTURE, SHOW)
    if len(selection['episodes']) != 10:
        raise ValueError('Integration fixture must contain exactly ten episodes')
    for directory in ('download-pilot', 'chunk-pilot', 'embedding-pilot'):
        (ROOT / 'data' / directory).mkdir(parents=True, exist_ok=True)
    with (ROOT / 'data' / f'{SHOW}.csv').open('w', newline='') as stream:
        csv.writer(stream).writerow(FIELDS)
    secret = ROOT / '.OpenAPIKey'
    with secret.open('x') as stream:
        secret.chmod(0o600)
        stream.write(key)
    cache = Path.home() / '.cache/huggingface/hub'
    cache.mkdir(parents=True, exist_ok=True)
    settings: IntegrationSettings = {'RF_PROJECT_ROOT': str(ROOT), 'RF_UID': str(os.getuid()), 'RF_GID': str(os.getgid()),
        'RF_DOCKER_GID': str(Path('/var/run/docker.sock').stat().st_gid), 'RF_SHOW': SHOW,
        'RF_EPISODE_SELECTION': 'ci/fixtures/le-cours-de-l-histoire.json', 'RF_HF_HUB_CACHE': str(cache),
        'AIRFLOW_UI_USERNAME': 'admin', 'AIRFLOW_UI_PASSWORD': os.environ['AIRFLOW_UI_PASSWORD']}
    with (ROOT / '.env').open('x') as stream:
        os.chmod(ROOT / '.env', stat.S_IRUSR | stat.S_IWUSR)
        for name, value in settings.items():
            assert isinstance(value, str)
            if "'" in value or '\n' in value or '\r' in value:
                raise ValueError(f'Unsupported settings characters in {name}')
            stream.write(f"{name}='{value}'\n")
    print('Prepared the fixed ten-episode integration sample; no credentials printed')


class AirflowClient:
    """Authenticate to the runner's Airflow API; refresh expiring tokens without logging them."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip('/')
        self.token = ''

    def request(self, method: str, path: str, body: object = None, refresh: bool = True) -> dict[str, object]:
        """Send a JSON API request and return an object, refreshing authentication once on 401."""
        headers = {'Content-Type': 'application/json'}
        if self.token:
            headers['Authorization'] = f'Bearer {self.token}'
        request = Request(self.base_url + path, data=json.dumps(body).encode() if body is not None else None,
            headers=headers, method=method)
        try:
            with urlopen(request, timeout=30) as response:
                return cast(dict[str, object], json.load(response))
        except HTTPError as error:
            if error.code == 401 and refresh and path != '/auth/token':
                self.login()
                return self.request(method, path, body, refresh=False)
            raise RuntimeError(f'Airflow API {method} {path} returned HTTP {error.code}') from None

    def login(self) -> None:
        """Use the runner's ephemeral UI login without exposing credentials or JWTs."""
        response = self.request('POST', '/auth/token', {'username': 'admin', 'password': os.environ['AIRFLOW_UI_PASSWORD']}, refresh=False)
        self.token = str(response['access_token'])


def run_pipeline(run_id: str, evidence: Path, timeout_seconds: int = 5400) -> None:
    """Unpause maintained stages, trigger the parent once, poll completion and verify exact DB membership."""
    evidence.mkdir(parents=True, exist_ok=True)
    client = AirflowClient('http://127.0.0.1:8082')
    client.login()
    discovery_deadline = time.monotonic() + 180
    for dag in DAGS:
        while True:
            try:
                client.request('PATCH', f'/api/v2/dags/{dag}', {'is_paused': False})
                break
            except RuntimeError:
                if time.monotonic() >= discovery_deadline:
                    raise
                time.sleep(5)
    client.request('POST', '/api/v2/dags/radiofrance_pipeline/dagRuns',
        {'dag_run_id': run_id, 'logical_date': None, 'conf': {}})
    path = '/api/v2/dags/radiofrance_pipeline/dagRuns/' + quote(run_id, safe='')
    deadline = time.monotonic() + timeout_seconds
    previous = ''
    while time.monotonic() < deadline:
        result = client.request('GET', path)
        state = str(result['state'])
        (evidence / 'pipeline-status.json').write_text(json.dumps(result, indent=2) + '\n')
        if state != previous:
            print(f'Pipeline state: {state}', flush=True)
            previous = state
        if state == 'failed':
            raise RuntimeError('Airflow pipeline failed; inspect the uploaded task logs')
        if state == 'success':
            break
        time.sleep(15)
    else:
        raise TimeoutError('Airflow pipeline exceeded its integration deadline')
    selection = read_selection(FIXTURE, SHOW)
    sql = (ROOT / 'ci/verify-integration.sql').read_text()
    checked = subprocess.run(['docker', 'compose', 'exec', '-T', '-e', 'PGPASSWORD=radiofrance-dev',
        'postgres', 'psql', '-h', '127.0.0.1', '-U', 'radiofrance', '-d', 'radiofrance',
        '-v', 'ON_ERROR_STOP=1', '-v', 'fixture=' + json.dumps(selection)],
        input=sql, text=True, capture_output=True, check=False)
    (evidence / 'database-verification.log').write_text(checked.stdout + checked.stderr)
    if checked.returncode:
        raise RuntimeError('Database integration assertions failed; inspect database-verification.log')
    summary: IntegrationSummary = {'pipeline_run_id': run_id, 'show': SHOW, 'episode_count': len(selection['episodes']),
        'episode_ids': [e['episode_id'] for e in selection['episodes']], 'status': 'verified'}
    (evidence / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print('Verified all ten fixed episode IDs, transcript counts and nonempty 256D embeddings')


def main() -> None:
    """Prepare a fresh hosted checkout, or drive its Airflow pipeline and verify results."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'run'])
    parser.add_argument('--run-id', default='integration')
    parser.add_argument('--evidence', type=Path, default=Path('/tmp/radiofrance-integration-evidence'))
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare()
    else:
        run_pipeline(args.run_id, args.evidence)


if __name__ == '__main__':
    main()
