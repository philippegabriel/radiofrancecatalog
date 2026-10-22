"""Offline checks for processing container wiring and manifest validation."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / 'dags'))
import radiofrance_download as dag_module


class LocalDownloadTests(unittest.TestCase):
    def test_dag_uses_sibling_container_and_file_secret(self) -> None:
        """Check dependencies, runtime mounts, no secret values and no container-log XCom."""
        self.assertEqual(dag_module.dag.task_ids, ['prepare_download', 'download', 'verify_download'])
        operator = dag_module.download
        self.assertEqual(operator.upstream_task_ids, {'prepare_download'})
        self.assertEqual(operator.downstream_task_ids, {'verify_download'})
        self.assertFalse(operator.do_xcom_push)
        self.assertTrue(dag_module.dag.doc_md)
        assert isinstance(dag_module.download.command, list)
        self.assertIn('--api-key-file', dag_module.download.command)
        self.assertFalse(dag_module.download.mount_tmp_dir)
        self.assertEqual(dag_module.download.retries, 0)
        self.assertEqual(dag_module.download.environment, {})
        mounts = {mount['Target']: mount for mount in dag_module.download.mounts}
        self.assertTrue(mounts['/run/secrets/radiofrance-api-key']['ReadOnly'])
        self.assertTrue(mounts['/input/baseline.csv']['ReadOnly'])
        self.assertFalse(mounts['/output']['ReadOnly'])

    def test_verification_detects_corruption_and_traversal(self) -> None:
        """Verify artifact bytes against the manifest and reject paths outside the output."""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'run'
            directory.mkdir()
            artifact: dag_module.Artifact = {'path': 'catalogue.csv'}
            (directory / 'baseline.csv').write_bytes(b'csv')
            manifest: dag_module.Manifest = {'show': dag_module.SHOW,
                                       'updates': artifact, 'catalogue': artifact, 'transcripts': []}
            (directory / 'catalogue.csv').write_bytes(b'csv')
            path = directory / 'manifest.json'
            path.write_text(json.dumps(manifest))
            dag_module.verify_artifacts(directory)
            (directory / 'catalogue.csv').unlink()
            with self.assertRaisesRegex(ValueError, 'must be a file'):
                dag_module.verify_artifacts(directory)
            artifact['path'] = '../outside.csv'
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'must be a file'):
                dag_module.verify_artifacts(directory)

    def test_preflight_does_not_accept_missing_configuration(self) -> None:
        """Fail before container creation when the host path was not configured."""
        with patch.dict(os.environ, {'RF_PROJECT_ROOT': ''}):
            with self.assertRaisesRegex(ValueError, 'configure'):
                dag_module.prepare_download.function('manual-run')


if __name__ == '__main__':
    unittest.main()
