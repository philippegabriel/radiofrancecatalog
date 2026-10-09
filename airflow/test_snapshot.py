"""Check snapshot isolation, integrity guards, and atomic pointer publication."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import snapshot


class SnapshotTests(unittest.TestCase):
    def test_client_routing_for_host_and_job_container(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(snapshot.postgres_command('pg_restore'), ['pg_restore'])
        with patch.dict(os.environ, {'PG_CONTAINER': 'service'}):
            self.assertEqual(snapshot.postgres_command('psql'),
                             ['docker', 'exec', '-i', 'service', 'psql'])

    def test_verified_snapshot_restores_without_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dump = Path(directory) / 'metadata.dump'
            prefix = 'data/airflow/github-download/v2/airflow-3.3.2/postgres-17'
            record = snapshot.Snapshot(format_version=1, airflow_version='3.3.2', postgres_major='17',
                                       key=prefix + '/snapshots/1.dump',
                                       sha256=hashlib.sha256(b'abc').hexdigest(), bytes=3)
            def download(*args: str) -> None:
                Path(args[3]).write_bytes(json.dumps(record).encode() if args[2].endswith('.json') else b'abc')
            with patch.dict(os.environ, {'RF_SNAPSHOT_SCOPE': 'v2', 'RF_S3_BUCKET': 'test', 'PG_CONTAINER': 'test'}), patch.object(sys, 'argv', ['snapshot.py', 'restore', str(dump)]), patch.object(snapshot, 'version', return_value='3.3.2'), patch.object(snapshot, 'postgres_major', return_value='17'), patch.object(snapshot, 'aws', side_effect=download), patch.object(snapshot, 'report'), patch.object(snapshot.subprocess, 'run') as restore:
                snapshot.main()
                restore.assert_called_once()
                self.assertIn('pg_restore', restore.call_args.args[0])
                self.assertTrue(restore.call_args.kwargs['check'])
            self.assertFalse(dump.exists())

    def test_rejects_wrong_version_and_namespace(self) -> None:
        record = snapshot.Snapshot(format_version=1, airflow_version='3.3.2', postgres_major='17',
                                   key='prefix/snapshots/1.dump', sha256='hash', bytes=10)
        snapshot.validate(record, 'prefix', '3.3.2', '17')
        with self.assertRaises(ValueError):
            snapshot.validate(record, 'prefix', '3.4.0', '17')
        with self.assertRaises(ValueError):
            snapshot.validate(record, 'another-branch', '3.3.2', '17')

    def test_restore_rejects_corruption_before_postgres(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dump = Path(directory) / 'metadata.dump'
            prefix = 'data/airflow/github-download/v2/airflow-3.3.2/postgres-17'
            record = snapshot.Snapshot(format_version=1, airflow_version='3.3.2', postgres_major='17',
                                       key=prefix + '/snapshots/1.dump', sha256='wrong', bytes=3)
            def download(*args: str) -> None:
                Path(args[3]).write_bytes(json.dumps(record).encode() if args[2].endswith('.json') else b'abc')
            with patch.dict(os.environ, {'RF_SNAPSHOT_SCOPE': 'v2', 'RF_S3_BUCKET': 'test', 'PG_CONTAINER': 'test'}), patch.object(sys, 'argv', ['snapshot.py', 'restore', str(dump)]), patch.object(snapshot, 'version', return_value='3.3.2'), patch.object(snapshot, 'postgres_major', return_value='17'), patch.object(snapshot, 'aws', side_effect=download), patch.object(snapshot.subprocess, 'run') as restore:
                with self.assertRaisesRegex(ValueError, 'checksum'):
                    snapshot.main()
                restore.assert_not_called()

    def test_failed_pointer_upload_retains_local_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dump = Path(directory) / 'metadata.dump'
            dump.write_bytes(b'abc')
            destinations: list[str] = []
            def upload(*args: str) -> None:
                destinations.append(args[3])
                if args[3].endswith('current.json'):
                    record = json.loads(Path(args[2]).read_text())
                    self.assertEqual(record['sha256'], hashlib.sha256(b'abc').hexdigest())
                    raise subprocess.CalledProcessError(1, 'aws')
            with patch.dict(os.environ, {'RF_SNAPSHOT_SCOPE': 'v2', 'RF_S3_BUCKET': 'test', 'GITHUB_RUN_ID': '1', 'GITHUB_RUN_ATTEMPT': '1'}), patch.object(sys, 'argv', ['snapshot.py', 'save', str(dump)]), patch.object(snapshot, 'version', return_value='3.3.2'), patch.object(snapshot, 'postgres_major', return_value='17'), patch.object(snapshot, 'aws', side_effect=upload):
                with self.assertRaises(subprocess.CalledProcessError):
                    snapshot.main()
            self.assertTrue(destinations[0].endswith('/snapshots/1-1.dump'))
            self.assertTrue(destinations[1].endswith('/current.json'))
            self.assertTrue(dump.exists())


if __name__ == '__main__':
    unittest.main()
