"""Offline checks for the chunk DAG's container and provenance boundaries."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / 'dags'))
import radiofrance_chunk as dag_module
from radiofrance.jobs.chunk import DownloadManifest, run_chunking


class LocalChunkTests(unittest.TestCase):
    def test_chunk_container_has_no_network_or_secrets(self) -> None:
        """Check task ordering and isolation of the chunk processing container."""
        self.assertEqual(dag_module.dag.task_ids, ['prepare_chunking', 'chunk', 'verify_chunks'])
        self.assertEqual(dag_module.chunk.upstream_task_ids, {'prepare_chunking'})
        self.assertEqual(dag_module.chunk.downstream_task_ids, {'verify_chunks'})
        self.assertEqual(dag_module.chunk.network_mode, 'none')
        self.assertFalse(dag_module.chunk.do_xcom_push)
        self.assertFalse(dag_module.chunk.mount_tmp_dir)
        self.assertTrue(dag_module.chunk.mounts[0]['ReadOnly'])
        self.assertFalse(dag_module.chunk.mounts[1]['ReadOnly'])
        self.assertEqual(len(dag_module.chunk.mounts), 2)
        self.assertTrue(dag_module.dag.doc_md)

    def test_selected_run_and_output_provenance(self) -> None:
        """Exercise preflight and verification with a valid, empty download manifest."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downloads = root / 'downloads'
            chunks = root / 'chunks'
            name = 'airflow-' + 'a' * 64
            source = downloads / name
            source.mkdir(parents=True)
            chunks.mkdir()
            manifest: DownloadManifest = {'version': 1, 'show': 'mecaniques-du-journalisme',
                                          'new_episode_count': 0, 'transcripts': []}
            (source / 'manifest.json').write_text(json.dumps(manifest))
            with patch.dict(os.environ, {'RF_PROJECT_ROOT': '/host/project'}), patch.object(dag_module, 'HOST', '/host/project'), patch.object(dag_module, 'DOWNLOADS', downloads), patch.object(dag_module, 'OUTPUTS', chunks):
                state = dag_module.prepare_chunking.function({'download_run': name}, 'manual-test')
                run_chunking(source / 'manifest.json', chunks / state['output_folder'])
                dag_module.verify_chunks.function(state)
                bad_state: dag_module.ChunkRun = {**state, 'source_sha256': 'wrong'}
                with self.assertRaisesRegex(ValueError, 'does not match'):
                    dag_module.verify_chunks.function(bad_state)
                with self.assertRaisesRegex(ValueError, 'directory name'):
                    dag_module.prepare_chunking.function({'download_run': '../escape'}, 'manual-test')


if __name__ == '__main__':
    unittest.main()
