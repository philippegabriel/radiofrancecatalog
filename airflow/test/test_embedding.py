"""Check offline Airflow embedding orchestration without launching Docker."""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / 'dags'))
import radiofrance_embedding as dag_module
import test_embedding_job as fixtures
from embedding_job import run_embedding


class LocalEmbeddingTests(unittest.TestCase):
    def test_container_is_offline_and_model_is_read_only(self) -> None:
        self.assertEqual(dag_module.dag.task_ids, ['prepare_embedding', 'embed', 'verify_embeddings'])
        self.assertEqual(dag_module.embed.upstream_task_ids, {'prepare_embedding'})
        self.assertEqual(dag_module.embed.downstream_task_ids, {'verify_embeddings'})
        self.assertEqual(dag_module.embed.network_mode, 'none')
        self.assertEqual(dag_module.embed.mem_limit, '4g')
        self.assertFalse(dag_module.embed.do_xcom_push)
        self.assertFalse(dag_module.embed.mount_tmp_dir)
        self.assertEqual([mount['ReadOnly'] for mount in dag_module.embed.mounts], [True, False, True])
        self.assertTrue(dag_module.dag.doc_md)
        self.assertNotIn('API_KEY', str(dag_module.embed.environment))

    def test_prepare_verify_and_wrong_model_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            name = 'airflow-' + 'a' * 64
            chunks = root / 'chunks'
            chunks.mkdir()
            outputs = root / 'outputs'
            outputs.mkdir()
            fixture = fixtures.EmbeddingJobTests()
            source = fixture.source(chunks / name)
            model = fixture.model(root / 'model')
            with patch.dict(os.environ, {'RF_PROJECT_ROOT': '/host/project'}), patch.object(dag_module, 'HOST', '/host/project'), patch.object(dag_module, 'CHUNKS', chunks), patch.object(dag_module, 'OUTPUTS', outputs), patch.object(dag_module, 'CACHE_HOST', str(model)):
                state = dag_module.prepare_embedding.function({'chunk_run': name}, 'manual-test')
                run_embedding(source, outputs / state['output_folder'], model, encoder=fixtures.FakeEncoder())
                dag_module.verify_embeddings.function(state)
                bad: dag_module.EmbeddingRun = {**state, 'source_sha256': 'wrong'}
                with self.assertRaisesRegex(ValueError, 'provenance'):
                    dag_module.verify_embeddings.function(bad)
                with self.assertRaisesRegex(ValueError, 'directory name'):
                    dag_module.prepare_embedding.function({'chunk_run': '../escape'}, 'manual-test')


if __name__ == '__main__':
    unittest.main()
