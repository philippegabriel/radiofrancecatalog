"""Offline tests for embedding artifact integrity, lineage and failure isolation."""

import csv
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from radiofrance.jobs.chunk import ChunkManifest
import radiofrance.jobs.embedding as job
class FakeEncoder:
    """Deterministic vectors for testing publication without an ML installation."""

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] + [0.0] * 255 for _ in texts]


class BadEncoder:
    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 256 for _ in texts]


class EmbeddingJobTests(unittest.TestCase):
    def source(self, root: Path, empty: bool = False) -> Path:
        """Create a completed chunk run containing two real-format CSV rows."""
        root.mkdir()
        path = root / 'episode_5.csv'
        with path.open('w', newline='', encoding='utf-8') as stream:
            csv.writer(stream).writerows([] if empty else [
                ['episode_5', 0, 0, 1, 0.0, 1.0, 'Un texte français'],
                ['episode_5', 1, 2, 3, 1.0, 2.0, 'Une autre phrase'],
            ])
        manifest: ChunkManifest = {'version': 1, 'stage': 'chunking', 'show': 'mecaniques-du-journalisme',
            'source_run': 'download-run',
            'strategy': {'name': 'segment-characters', 'version': 1, 'target_size': 1000, 'min_size': 300},
            'episode_count': 1, 'chunk_count': 0 if empty else 2,
            'chunks': [{'episode_id': 'episode_5', 'path': path.name,
                         'chunk_count': 0 if empty else 2}]}
        job.write_json(root / 'manifest.json', manifest)
        return root / 'manifest.json'

    def model(self, root: Path) -> Path:
        """Create a standard Hub cache fixture with snapshot-to-blob symlinks."""
        repository = root / ('models--' + job.MODEL_ID.replace('/', '--'))
        snapshot = repository / 'snapshots' / job.MODEL_REVISION
        blobs = repository / 'blobs'
        snapshot.mkdir(parents=True)
        blobs.mkdir()
        content = blobs / 'temporary'
        content.write_text('fixture')
        blob = blobs / 'model-fixture'
        content.rename(blob)
        for name in ['model.safetensors', 'config.json', 'tokenizer.json', 'modules.json', 'chat_template.jinja']:
            (snapshot / name).symlink_to(Path('../../blobs') / blob.name)
        return root

    def test_publication_and_provenance(self) -> None:
        """Preserve identities, version configuration, publish readable files and reject overwrite."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.source(root / 'chunks')
            model = self.model(root / 'model')
            (source.parent / 'unlisted.csv').write_text('not a chunk')
            before = source.read_bytes()
            previous = os.umask(0o077)
            try:
                result = job.run_embedding(source, root / 'output', model, encoder=FakeEncoder())
            finally:
                os.umask(previous)
            self.assertEqual(result, job.verify_output(root / 'output', source))
            self.assertEqual(result['chunk_count'], 2)
            self.assertEqual(result['configuration']['precision'], 'float32')
            self.assertEqual(result['configuration']['dimensions'], 256)
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(stat.S_IMODE((root / 'output').stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE((root / 'output/episode_5.jsonl').stat().st_mode), 0o644)
            with self.assertRaises(FileExistsError):
                job.run_embedding(source, root / 'output', model, encoder=FakeEncoder())

    def test_atomic_failure_empty_input_and_missing_model(self) -> None:
        """Bad vectors or missing model files leave no published or staged output; empty runs succeed."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.source(root / 'chunks')
            model = self.model(root / 'model')
            with self.assertRaisesRegex(ValueError, 'unit-normalized'):
                job.run_embedding(source, root / 'failed', model, encoder=BadEncoder())
            self.assertFalse((root / 'failed').exists())
            self.assertEqual(list(root.glob('.embedding-job-*')), [])
            empty = self.source(root / 'empty', empty=True)
            result = job.run_embedding(empty, root / 'empty-output', model)
            self.assertEqual(result['chunk_count'], 0)
            (job.cached_snapshot(model) / 'config.json').unlink()
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                job.run_embedding(source, root / 'failed', model, encoder=FakeEncoder())
            self.assertFalse((root / 'failed').exists())

    def test_verifier_rejects_forged_vectors_paths_and_lineage(self) -> None:
        """Reject malformed vectors, escaping paths and wrong source runs."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.source(root / 'chunks')
            model = self.model(root / 'model')
            output = root / 'output'
            result = job.run_embedding(source, output, model, encoder=FakeEncoder())
            path = output / 'episode_5.jsonl'
            records = [json.loads(line) for line in path.read_text().splitlines()]
            records[0]['embedding'] = [1.0]
            path.write_text(''.join(json.dumps(record) + '\n' for record in records))
            job.write_json(output / 'manifest.json', result)
            with self.assertRaisesRegex(ValueError, '256 finite'):
                job.verify_output(output, source)
            result['embeddings'][0]['path'] = '../outside.jsonl'
            job.write_json(output / 'manifest.json', result)
            with self.assertRaises(ValueError):
                job.verify_output(output, source)
            output2 = root / 'output2'
            result = job.run_embedding(source, output2, model, encoder=FakeEncoder())
            result['source_run'] = 'wrong'
            job.write_json(output2 / 'manifest.json', result)
            with self.assertRaisesRegex(ValueError, 'does not match'):
                job.verify_output(output2, source)

    def test_missing_model_files_and_revision(self) -> None:
        """Accept local cache links and require the pinned revision and necessary model files."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, 'hf download'):
                job.verify_model(root / 'absent')
            model = self.model(root / 'hub')
            job.verify_model(model)
            source = self.source(root / 'chunks')
            output = root / 'output'
            job.run_embedding(source, output, model, encoder=FakeEncoder())
            outside = root / 'outside'
            outside.write_text('fixture')
            path = job.cached_snapshot(model) / 'config.json'
            path.unlink()
            path.symlink_to(root / 'missing-file')
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                job.verify_model(model)

    def test_shared_hub_blobs(self) -> None:
        """Follow repository blob links into the shared Hub content store."""
        with tempfile.TemporaryDirectory() as temporary:
            cache = self.model(Path(temporary) / 'hub')
            snapshot = job.cached_snapshot(cache)
            blob = (snapshot / 'model.safetensors').resolve()
            shared = cache / 'blobs' / 'aa' / ('a' * 64)
            shared.parent.mkdir(parents=True)
            blob.rename(shared)
            blob.symlink_to(Path('../../blobs/aa') / shared.name)
            job.verify_model(cache)


if __name__ == '__main__':
    unittest.main()
