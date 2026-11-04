"""Verify live run reporting, early estimates, cache accounting and terminal failures."""

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / 'dags'))
import radiofrance_pipeline as parent
from radiofrance.libs.progress import EmbeddingReporter
from radiofrance.libs.run_metrics import show_metrics, describe_metrics, run_totals
from radiofrance.libs.storage import CHUNK_DIRECTORY, EMBEDDING_DIRECTORY


class RunMetricsTests(unittest.TestCase):
    def test_first_ten_then_hundreds_and_cached_completion(self) -> None:
        """Exclude cached vectors from measured rate and estimate only pending work."""
        with tempfile.TemporaryDirectory() as temporary, redirect_stdout(StringIO()):
            reporter = EmbeddingReporter(Path(temporary) / 'run', 250, 50)
            reporter.encoded(9, 4.5)
            self.assertIsNone(json.loads(reporter.path.read_text())['rate'])
            reporter.encoded(1, .5)
            first = json.loads(reporter.path.read_text())
            self.assertEqual(first['rate'], 2)
            self.assertEqual(first['remaining_seconds'], 95)
            self.assertTrue(first['provisional'])
            reporter.encoded(89, 44.5)
            self.assertEqual(json.loads(reporter.path.read_text())['generated'], 10)
            reporter.encoded(1, .5)
            self.assertFalse(json.loads(reporter.path.read_text())['provisional'])
            reporter.encoded(100, 50)
            completed = reporter.publish('completed')
            self.assertEqual(completed['generated'], 200)
            self.assertEqual(completed['cached'], 50)
            self.assertEqual(completed['remaining_seconds'], 0)
            self.assertEqual(reporter.path.stat().st_mode & 0o777, 0o644)

    def test_manifests_are_scoped_to_parent_and_missing_is_unknown(self) -> None:
        """Read run-specific stage counts and preserve unknown values before publication."""
        run = 'manual__2026-11-04T12:01:16.365773+00:00'
        folder = '2026-11-04_12-01-16-365773Z'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            empty = show_metrics(root, run, 'test-show')
            self.assertIsNone(empty['episodes'])
            self.assertIsNone(run_totals({'show': empty})['episodes'])
            download = root / 'runs' / folder / 'download/test-show/manifest.json'
            download.parent.mkdir(parents=True)
            download.write_text(json.dumps({'new_episode_count': 3, 'transcripts': [{}, {}]}))
            chunk = root / CHUNK_DIRECTORY / 'runs' / f'{folder}--test-show/manifest.json'
            chunk.parent.mkdir(parents=True)
            chunk.write_text(json.dumps({'chunk_count': 200}))
            with redirect_stdout(StringIO()):
                reporter = EmbeddingReporter(root / EMBEDDING_DIRECTORY / 'runs' / f'{folder}--test-show', 200, 0)
                reporter.encoded(10, 5)
            metrics = show_metrics(root, run, 'test-show')
            self.assertEqual((metrics['episodes'], metrics['transcripts'], metrics['chunks']), (3, 2, 200))
            self.assertIn('2.00 embeddings/s', describe_metrics('test-show', metrics))
            self.assertIn('[provisional]', describe_metrics('test-show', metrics))
            self.assertEqual(run_totals({'one': metrics, 'two': metrics})['chunks'], 400)
            self.assertEqual(run_totals({'one': metrics})['generated'], 10)
            self.assertIsNone(show_metrics(root, run, 'other-show')['chunks'])

    def test_parent_monitor_reschedules_and_stops_after_failure(self) -> None:
        """Keep observation separate from catalogue dependencies and report terminal failures."""
        operator = parent.dag.get_task('monitor_run')
        self.assertEqual(operator.upstream_task_ids, {'select_shows'})
        self.assertEqual(operator.mode, 'reschedule')  # pyright: ignore[reportAttributeAccessIssue]
        self.assertEqual(parent.catalogue.upstream_task_ids, {'process_shows'})
        run = 'manual__test'
        ti = MagicMock()
        ti.xcom_pull.return_value = None
        ti.get_task_states.return_value = {run: {'process_shows_0': 'deferred', 'catalogue': None}}
        requests: list[parent.ShowRequest] = [{'trigger_run_id': 'child', 'conf': {'show': 'test-show', 'show_url': 'https://example.org', 'from_date': '2026-10-01', 'to_date': '2026-10-31'}}]
        with patch.object(parent, 'get_current_context', return_value={'ti': ti}), patch.object(parent, 'show_metrics', return_value={'episodes': None, 'transcripts': None, 'chunks': None, 'embeddings': None}) as read, redirect_stdout(StringIO()) as output:
            self.assertFalse(parent.monitor_run.function(requests, run).is_done)
            self.assertIn('Selected shows (1): test-show', output.getvalue())
            self.assertIn('Date range (Europe/Paris): 2026-10-01 to 2026-10-31', output.getvalue())
            self.assertEqual(read.call_args.args[1], 'child')
            ti.get_task_states.return_value = {run: {'process_shows_0': 'failed', 'catalogue': 'upstream_failed'}}
            result = parent.monitor_run.function(requests, run)
            self.assertTrue(result.is_done)
            self.assertIn('failed', output.getvalue())
            self.assertIn('Final metrics', output.getvalue())
