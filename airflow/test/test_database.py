"""Offline checks for database DAG wiring, selection boundaries and read-only verification."""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / 'dags'))
import radiofrance_database as dag_module
import database.test.test_database_job as fixtures


class DatabaseDagTests(unittest.TestCase):
    def test_worker_boundaries_and_flow(self) -> None:
        """Use only application credentials, read-only artifacts and the Compose network."""
        self.assertEqual(dag_module.dag.task_ids, ['prepare_load', 'load_database', 'verify_database'])
        worker = dag_module.load_database
        self.assertEqual(worker.upstream_task_ids, {'prepare_load'})
        self.assertEqual(worker.downstream_task_ids, {'verify_database'})
        self.assertEqual(worker.image, 'radiofrance-database:pilot')
        self.assertEqual(worker.network_mode, dag_module.NETWORK)
        self.assertEqual([m['ReadOnly'] for m in worker.mounts], [True, True, True])
        self.assertEqual(worker.environment['PGUSER'], 'radiofrance')
        self.assertEqual(worker.environment['PGDATABASE'], 'radiofrance')
        self.assertNotIn('PGPASSWORD', worker.environment)
        self.assertNotIn('private_environment', worker.template_fields)
        assert isinstance(worker.command, list)
        self.assertFalse(worker.mount_tmp_dir)
        self.assertFalse(worker.do_xcom_push)
        self.assertTrue(dag_module.dag.doc_md)
        self.assertTrue(dag_module.prepare_load.__doc__)
        self.assertTrue(dag_module.verify_database.__doc__)

    def test_selected_run_and_changed_manifest(self) -> None:
        """Keep XCom small and reject traversal, escaping links and changed selections."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = fixtures.DatabaseJobTests()
            fixture.fixture(root)
            with patch.dict(os.environ, {'RF_DATABASE_PASSWORD': 'test-password'}), \
                    patch.object(dag_module, 'HOST', '/host/project'), \
                    patch.object(dag_module, 'EMBEDDINGS', root), \
                    patch.object(dag_module, 'CHUNKS', root / 'chunks'), \
                    patch.object(dag_module, 'DOWNLOADS', root / 'downloads'):
                state = dag_module.prepare_load.function({'embedding_run': 'embeddings-run'})
                with patch.object(dag_module, 'verify_import') as verify:
                    verify.return_value = {'embedding_run': state['embedding_run'],
                        'catalogue_episode_count': 1, 'transcript_count': 1, 'chunk_count': 1}
                    dag_module.verify_database.function(state)
                    self.assertIn('dbname=radiofrance', verify.call_args.args[1])
                with self.assertRaisesRegex(ValueError, 'basename'):
                    dag_module.prepare_load.function({'embedding_run': '../escape'})
                (root / 'escape').symlink_to(root.parent, target_is_directory=True)
                with self.assertRaises(ValueError):
                    dag_module.prepare_load.function({'embedding_run': 'escape'})
                with patch.dict(os.environ, {'RF_DATABASE_PASSWORD': ''}):
                    with self.assertRaisesRegex(ValueError, 'password'):
                        dag_module.prepare_load.function({'embedding_run': 'embeddings-run'})


if __name__ == '__main__':
    unittest.main()
