"""Save and restore versioned CI metadata snapshots without schema migrations."""
import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import time
from importlib.metadata import version
from pathlib import Path
from typing import TypedDict, cast


class Snapshot(TypedDict):
    format_version: int
    airflow_version: str
    postgres_major: str
    key: str
    sha256: str
    bytes: int


def report(label: str, started: float) -> None:
    """Write timing to the log, job summary, and optional statistics artifact."""
    message = f'{label}: {time.monotonic() - started:.2f} seconds\n'
    print(message, end='')
    for name in ('GITHUB_STEP_SUMMARY', 'RF_METRICS_REPORT'):
        if destination := os.environ.get(name):
            with Path(destination).open('a') as stream:
                stream.write(message + '\n')


def aws(*arguments: str) -> None:
    """Run AWS CLI using the workflow's region and credentials."""
    subprocess.run(['aws', *arguments, '--region', os.environ.get('RF_S3_REGION', 'eu-west-2')], check=True)


def postgres_command(program: str) -> list[str]:
    """Use host Docker clients in Register, or installed clients in a job container."""
    if container := os.environ.get('PG_CONTAINER'):
        return ['docker', 'exec', '-i', container, program]
    return [program]


def postgres_major() -> str:
    """Read the service version through PostgreSQL's supported SQL interface."""
    result = subprocess.run(postgres_command('psql') + [
                             '-XAt', '-U', 'airflow', '-d', 'airflow', '-c',
                             'SHOW server_version_num'], check=True, capture_output=True, text=True)
    return str(int(result.stdout.strip()) // 10000)


def validate(snapshot: Snapshot, prefix: str, airflow_version: str, major: str) -> None:
    """Reject incompatible manifests and object keys outside this namespace."""
    if (snapshot.get('format_version') != 1 or snapshot.get('airflow_version') != airflow_version
            or snapshot.get('postgres_major') != major):
        raise ValueError('Incompatible snapshot: run Register or perform the manual upgrade procedure')
    if (not snapshot['key'].startswith(prefix + '/snapshots/')
            or not snapshot['key'].endswith('.dump') or '..' in snapshot['key'].split('/')):
        raise ValueError('Invalid snapshot object key')
    if not isinstance(snapshot['bytes'], int) or snapshot['bytes'] < 1:
        raise ValueError('Invalid snapshot size')


def main() -> None:
    """Restore a verified dump, or publish a dump then atomically update its pointer.

    Restore targets the empty CI service database. Save retains older immutable
    objects for rollback and deletes the local dump only after publication succeeds.
    Neither operation runs Airflow migrations or modifies Airflow internal tables.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['restore', 'save'])
    parser.add_argument('dump', type=Path)
    args = parser.parse_args()
    dump: Path = args.dump
    airflow_version = version('apache-airflow')
    major = postgres_major()
    scope = os.environ.get('RF_SNAPSHOT_SCOPE', '')
    if scope not in ('main', 'v2'):
        raise ValueError('Snapshot scope must be main or v2')
    prefix = f'data/airflow/github-download/{scope}/airflow-{airflow_version}/postgres-{major}'
    bucket = os.environ['RF_S3_BUCKET']
    pointer = f's3://{bucket}/{prefix}/current.json'
    with tempfile.TemporaryDirectory() as directory:
        manifest = Path(directory) / 'current.json'
        if args.operation == 'restore':
            started = time.monotonic()
            try:
                aws('s3', 'cp', pointer, str(manifest), '--only-show-errors')
            except subprocess.CalledProcessError as error:
                raise RuntimeError('Snapshot unavailable: verify AWS access and run Airflow Register for this branch/version') from error
            snapshot = cast(Snapshot, json.loads(manifest.read_text()))
            validate(snapshot, prefix, airflow_version, major)
            aws('s3', 'cp', f"s3://{bucket}/{snapshot['key']}", str(dump), '--only-show-errors')
            with dump.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if dump.stat().st_size != snapshot['bytes'] or digest != snapshot['sha256']:
                raise ValueError('Snapshot checksum or size mismatch')
            report('S3 snapshot download and verification', started)
            started = time.monotonic()
            with dump.open('rb') as stream:
                subprocess.run(postgres_command('pg_restore') + [
                                '-U', 'airflow', '-d', 'airflow', '--no-owner', '--no-acl',
                                '--exit-on-error'], stdin=stream, check=True)
            report('PostgreSQL snapshot restore', started)
            dump.unlink()
        else:
            identifier = f"{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}"
            key = f'{prefix}/snapshots/{identifier}.dump'
            with dump.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            snapshot = Snapshot(format_version=1, airflow_version=airflow_version,
                                postgres_major=major, key=key, sha256=digest, bytes=dump.stat().st_size)
            manifest.write_text(json.dumps(snapshot))
            started = time.monotonic()
            aws('s3', 'cp', str(dump), f's3://{bucket}/{key}', '--only-show-errors')
            # Updating this single object publishes the complete, verified dump.
            aws('s3', 'cp', str(manifest), pointer, '--only-show-errors')
            report('S3 snapshot publication', started)
            dump.unlink()


if __name__ == '__main__':
    main()
