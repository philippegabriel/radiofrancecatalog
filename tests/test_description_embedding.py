"""Exercise the artifact-only description encoder independently of the backfill job."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from radiofrance.jobs import description_embedding as job
from tests.test_download_job import episode


def source(root: Path) -> Path:
    """Publish two downloaded episode records, one with a blank description."""
    root.mkdir(parents=True)
    first = episode('11111111-1111-4111-8111-111111111111_5', 100)
    first.update(title=' A title ', description=' Some description ')
    second = episode('22222222-2222-4222-8222-222222222222_5', 200)
    second['description'] = '  '
    (root / 'episodes.json').write_text(json.dumps([first, second]))
    for item in (first, second):
        (root / (item['id'] + '.json')).write_text('{"transcript": []}')
    manifest = root / 'manifest.json'
    manifest.write_text(json.dumps({'version': 2, 'show': 'affaires-sensibles', 'new_episode_count': 2,
        'episodes': {'path': 'episodes.json'}, 'transcripts': [
            {'episode_id': item['id'], 'path': item['id'] + '.json'} for item in (first, second)]}))
    return manifest


def vectors(texts: list[str]) -> list[list[float]]:
    """Return normalized vectors without an ML dependency."""
    return [[1.0] + [0.0] * 255 for _ in texts]


class DescriptionEmbeddingTests(unittest.TestCase):
    def test_shared_cache_reuses_exact_text_and_replaces_changed_description(self) -> None:
        """Shared vectors survive runs; a changed snapshot regenerates only that episode."""
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary) / 'data'
            data.mkdir()
            manifest = source(data / 'download')
            first = data / 'runs/first/show/description-embedding'
            job.run_embedding(manifest, first, data / 'unused', encode=vectors, data_root=data)
            second = data / 'runs/second/show/description-embedding'
            with patch.object(job, 'SentenceEncoder', side_effect=AssertionError('cache should skip model')):
                result = job.run_embedding(manifest, second, data / 'unused', data_root=data)
            self.assertEqual(result['episode_count'], 1)
            self.assertEqual(json.loads((second / 'progress.json').read_text())['cached'], 1)
            rows = json.loads((manifest.parent / 'episodes.json').read_text())
            rows[0]['description'] = 'Changed description'
            (manifest.parent / 'episodes.json').write_text(json.dumps(rows))
            with patch('tests.test_description_embedding.vectors', wraps=vectors) as encode:
                third = data / 'runs/third/show/description-embedding'
                job.run_embedding(manifest, third, data / 'unused', encode=encode, data_root=data)
                encode.assert_called_once()
            with self.assertRaisesRegex(ValueError, 'cache snapshot mismatch'):
                job.verify_output(first)
            self.assertEqual(job.verify_output(third, manifest)['episode_count'], 1)

    def test_no_download_database_or_backfill_dependencies(self) -> None:
        """The production encoder must import without database or download packages."""
        result = subprocess.run([sys.executable, '-c',
            "import sys; sys.modules['psycopg2']=None; sys.modules['requests']=None; from radiofrance.jobs import description_embedding; assert 'radiofrance.jobs.episode_descriptions' not in sys.modules"],
            text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_snapshots_prefixes_blank_descriptions_and_integrity(self) -> None:
        """Preserve exact metadata, encode the titled prompt, and enforce source lineage."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = source(root / 'download')
            with patch.object(job, 'SentenceEncoder') as model:
                output = root / 'output'
                result = job.run_embedding(manifest, output, root / 'unused', encode=vectors)
                model.assert_not_called()
            self.assertEqual(result['episode_count'], 1)
            self.assertEqual(result['descriptions'][0]['title'], ' A title ')
            self.assertEqual(job.verify_output(output, manifest), result)
            result['descriptions'][0]['title'] = 'changed'
            (output / 'manifest.json').write_text(json.dumps(result))
            with self.assertRaisesRegex(ValueError, 'snapshot mismatch'):
                job.verify_output(output, manifest)

    def test_empty_and_failed_runs_publish_only_valid_completion(self) -> None:
        """No model is needed for empty metadata and failed inference leaves no output."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = source(root / 'download')
            with self.assertRaises(ValueError):
                job.run_embedding(manifest, root / 'failed', root / 'unused', encode=lambda texts: [])
            self.assertFalse((root / 'failed').exists())
            data = json.loads(manifest.read_text())
            data.update(new_episode_count=0, transcripts=[])
            manifest.write_text(json.dumps(data))
            (manifest.parent / 'episodes.json').write_text('[]')
            with patch.object(job, 'SentenceEncoder', side_effect=AssertionError('Unexpected model')):
                empty = job.run_embedding(manifest, root / 'empty', root / 'unused')
            self.assertEqual(empty['episode_count'], 0)
            self.assertEqual(job.verify_output(root / 'empty', manifest)['descriptions'], [])
