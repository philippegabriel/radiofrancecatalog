"""Check the description DAG follows the same offline stage boundaries as transcript embedding."""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / 'dags'))
import radiofrance_description_embedding as stage
from radiofrance.jobs.description_embedding import run_embedding
from tests.test_description_embedding import source, vectors


class DescriptionDagTests(unittest.TestCase):
    def test_offline_worker_and_verification_flow(self) -> None:
        """Keep credentials and database access out of the embedding stage."""
        self.assertEqual(stage.dag.task_ids, ['prepare_descriptions', 'embed', 'verify_descriptions'])
        self.assertEqual(stage.embed.upstream_task_ids, {'prepare_descriptions'})
        self.assertEqual(stage.embed.downstream_task_ids, {'verify_descriptions'})
        self.assertEqual(stage.embed.network_mode, 'none')
        self.assertNotIn('PGPASSWORD', stage.embed.environment)
        self.assertNotIn('PGHOST', stage.embed.environment)
        self.assertEqual(stage.embed.mounts[1]['ReadOnly'], True)
        self.assertTrue(stage.dag.doc_md)
        self.assertTrue(stage.embed.doc_md)

    def test_explicit_download_and_generated_manifest(self) -> None:
        """Preflight selects files only and verification checks the exact source snapshots."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = source(root / 'downloads/run')
            output = root / 'descriptions'
            with patch.dict(os.environ, {'RF_PROJECT_ROOT': '/host/project'}), patch.object(stage, 'HOST', '/host/project'), \
                    patch.object(stage, 'CACHE_HOST', '/host/cache'), patch.object(stage, 'DOWNLOADS', root / 'downloads'), \
                    patch.object(stage, 'OUTPUTS', output):
                state = stage.prepare_descriptions.function({'download_run': 'run', 'output_folder': 'output'}, 'manual__test')
                run_embedding(manifest, output / 'output', root / 'unused', encode=vectors)
                stage.verify_descriptions.function(state)
                with self.assertRaisesRegex(ValueError, 'download_run'):
                    stage.prepare_descriptions.function({'download_run': '../escape'}, 'manual__test')
