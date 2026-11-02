"""Offline checks for title prompts, immutable inputs and description checkpoints."""

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from radiofrance.jobs import episode_descriptions as job


def vector(texts: list[str]) -> list[list[float]]:
    """Return valid deterministic vectors without a model runtime."""
    return [[1.0] + [0.0] * 255 for _ in texts]


class DescriptionTests(unittest.TestCase):
    def selection(self, root: Path) -> Path:
        """Write two distinct episodes, including one with no title."""
        selection: job.Selection = {'version': 1, 'episodes': [
            {'episode_id': i, 'external_id': f'11111111-1111-4111-8111-{i:012d}_5',
             'title': ' Un titre ' if i == 1 else None, 'description': ' Une description. '}
            for i in (1, 2)]}
        path = root / 'selection.json'
        path.write_text(json.dumps(selection))
        return path

    def test_prompts_publication_and_resume(self) -> None:
        """Publish FP32 rows with real titles, then resume without encoding again."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self.selection(root)
            prompts: list[str] = []

            def encode(texts: list[str]) -> list[list[float]]:
                """Capture exactly what reaches the encoder."""
                prompts.extend(texts)
                return vector(texts)

            report = job.run_embedding(path, root / 'out', root / 'unused', encode=encode)
            self.assertEqual(prompts, ['title: Un titre | text: Une description.',
                                      'title: none | text: Une description.'])
            self.assertEqual(report['newly_encoded'], 2)
            for artifact in (root / 'out').glob('*.npy'):
                matrix = np.load(artifact, allow_pickle=False)
                self.assertEqual(matrix.shape, (1, 256))
                self.assertEqual(matrix.dtype, np.dtype('float32'))
            report = job.run_embedding(path, root / 'out', root / 'unused', encode=encode)
            self.assertEqual(report['resumed'], 2)
            self.assertEqual(len(prompts), 2)
            selection = json.loads(path.read_text())
            selection['episodes'][0]['description'] = 'Changed'
            path.write_text(json.dumps(selection))
            with self.assertRaisesRegex(ValueError, 'Metadata or model changed'):
                job.run_embedding(path, root / 'out', root / 'unused', encode=encode)

    def test_failure_resume_and_corruption(self) -> None:
        """Keep complete episodes after a failed batch and reject malformed checkpoints."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self.selection(root)
            calls = 0

            def failing(texts: list[str]) -> list[list[float]]:
                """Fail on the second episode after the first checkpoint is durable."""
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError('interrupted')
                return vector(texts)

            with self.assertRaises(RuntimeError):
                job.run_embedding(path, root / 'out', root / 'unused', batch_size=1, encode=failing)
            self.assertEqual(len(list((root / 'out').glob('*.npy'))), 1)
            self.assertFalse((root / 'out/manifest.json').exists())
            report = job.run_embedding(path, root / 'out', root / 'unused', encode=vector)
            self.assertEqual((report['newly_encoded'], report['resumed']), (1, 1))
            artifact = next((root / 'out').glob('*.npy'))
            np.save(artifact, np.zeros((1, 256), dtype=np.float32), allow_pickle=False)
            with self.assertRaisesRegex(ValueError, 'unit-normalized'):
                job.run_embedding(path, root / 'out', root / 'unused', encode=vector)

    def test_blank_and_duplicate_metadata(self) -> None:
        """Reject blank descriptions and duplicate selection rows before creating output."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self.selection(root)
            selection = json.loads(path.read_text())
            selection['episodes'][0]['description'] = ' \n '
            path.write_text(json.dumps(selection))
            with self.assertRaisesRegex(ValueError, 'Invalid, empty or duplicate'):
                job.run_embedding(path, root / 'out', root / 'unused', encode=vector)
            self.assertFalse((root / 'out').exists())
            path = self.selection(root)
            selection = json.loads(path.read_text())
            selection['episodes'].append(selection['episodes'][0])
            path.write_text(json.dumps(selection))
            with self.assertRaises(ValueError):
                job.run_embedding(path, root / 'out', root / 'unused', encode=vector)
