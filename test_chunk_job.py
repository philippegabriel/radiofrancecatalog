"""Offline chunking checks using real transcripts, CSVs and the existing chunker."""

import csv
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

import chunk_job as job


class ChunkJobTests(unittest.TestCase):
    def source(self, root: Path, empty: bool = False) -> Path:
        """Create a valid local download manifest and transcript test fixture."""
        root.mkdir()
        path = root / 'episode_5.json'
        path.write_text(json.dumps({'transcript': [
            {'text': '', 'start': 0, 'end': 1},
            {'text': 'Texte français ' * 80, 'start': 1, 'end': 10},
            {'text': 'Fin', 'start': 10, 'end': 11},
        ]}), encoding='utf-8')
        transcripts: list[job.TranscriptArtifact] = [] if empty else [
            {'episode_id': 'episode_5', 'path': path.name, 'sha256': job.sha256(path)}]
        manifest: job.DownloadManifest = {'version': 1, 'show': 'mecaniques-du-journalisme',
                                          'new_episode_count': len(transcripts), 'transcripts': transcripts}
        output = root / 'manifest.json'
        output.write_text(json.dumps(manifest), encoding='utf-8')
        return output

    def test_preserves_existing_chunk_format_and_records_provenance(self) -> None:
        """Match existing chunker output, preserve numbering and publish readable files."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.source(root / 'download')
            before = source.read_bytes()
            # Unlisted artifacts must not be picked up by a directory scan.
            (source.parent / 'unlisted.json').write_text('invalid JSON')
            previous = os.umask(0o077)
            try:
                manifest = job.run_chunking(source, root / 'chunks')
            finally:
                os.umask(previous)
            self.assertEqual(manifest, job.verify_output(root / 'chunks'))
            self.assertEqual(manifest['episode_count'], 1)
            self.assertEqual(manifest['source_manifest_sha256'], job.sha256(source))
            self.assertEqual(manifest['strategy']['target_size'], 1000)
            self.assertEqual(manifest['strategy']['min_size'], 300)
            self.assertEqual(len(manifest['strategy']['code_sha256']), 64)
            expected = subprocess.check_output([sys.executable, 'chunk_json_to_csv.py', str(source.parent / 'episode_5.json')])
            output = root / 'chunks/episode_5.csv'
            self.assertEqual(output.read_bytes(), expected)
            with output.open() as stream:
                self.assertEqual(next(csv.reader(stream))[2:4], ['1', '2'])
            self.assertEqual(stat.S_IMODE((root / 'chunks').stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o644)
            self.assertEqual(source.read_bytes(), before)
            with self.assertRaises(FileExistsError):
                job.run_chunking(source, root / 'chunks')
            output.write_text('corrupt')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                job.verify_output(root / 'chunks')

    def test_empty_input_and_failure_do_not_publish_partial_results(self) -> None:
        """Accept an empty download, but reject corrupt transcripts and malformed JSON."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.source(root / 'empty', empty=True)
            manifest = job.run_chunking(source, root / 'empty-chunks')
            self.assertEqual(manifest['chunks'], [])
            self.assertEqual(manifest['chunk_count'], 0)
            source = self.source(root / 'bad')
            transcript = source.parent / 'episode_5.json'
            transcript.write_text('invalid JSON')
            with self.assertRaises(ValueError):
                job.run_chunking(source, root / 'failed')
            data = job.validate_source(self.source(root / 'valid'))
            data['transcripts'][0]['sha256'] = job.sha256(transcript)
            source.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                job.run_chunking(source, root / 'failed')
            self.assertFalse((root / 'failed').exists())
            self.assertEqual(list(root.glob('.chunk-job-*')), [])

    def test_escaping_input_paths_are_rejected(self) -> None:
        """Reject a transcript path that reaches outside its download directory."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.source(root / 'download')
            data = job.validate_source(source)
            outside = root / 'episode_5.json'
            outside.write_text('{}')
            data['transcripts'][0]['path'] = '../episode_5.json'
            data['transcripts'][0]['sha256'] = job.sha256(outside)
            source.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'inside'):
                job.run_chunking(source, root / 'chunks')


if __name__ == '__main__':
    unittest.main()
