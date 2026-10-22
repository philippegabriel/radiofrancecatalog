"""Offline checks for the download pilot's publication and retry boundaries."""

import csv
import hashlib
import json
import os
import stat
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import radiofrance.jobs.download as job
import radiofrance.fetch_transcripts as fetchtranscript
import radiofrance.fetch_episodes as rf_dump
from radiofrance.libs.catalogue import FIELDS
from radiofrance.libs.types import EpisodeRow, Transcript


def episode(episode_id: str, timestamp: int) -> EpisodeRow:
    """Return a minimal valid API catalogue row for an offline test."""
    return {'show': 'affaires-sensibles', 'id': episode_id, 'published_ts': timestamp,
            'published_iso': '', 'title': 'Test', 'description': '', 'web_url': '',
            'podcast_title': '', 'podcast_url': '', 'player_url': ''}


class DownloadJobTests(unittest.TestCase):
    def test_fixed_selection_enforces_membership_and_fingerprints(self) -> None:
        """Select exact IDs, reject unavailable/changed sources and never publish partial output."""
        from radiofrance.libs.episode_selection import EpisodeSelection, read_selection
        identity = '11111111-1111-4111-8111-111111111111_5'
        transcript: Transcript = {'transcript': [{'start': 0, 'end': 1, 'text': 'Français'}]}
        encoded = (json.dumps(transcript, ensure_ascii=False, indent=2) + '\n').encode()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / 'baseline.csv'
            with baseline.open('w', newline='') as stream:
                csv.writer(stream).writerow(FIELDS)
            selection: EpisodeSelection = {'version': 1, 'show': 'affaires-sensibles', 'episodes': [{
                'episode_id': identity, 'published_ts': 200, 'published_iso': '', 'title': 'Test',
                'transcript_sha256': hashlib.sha256(encoded).hexdigest(), 'segment_count': 1}]}
            fixture = root / 'selection.json'
            fixture.write_text(json.dumps(selection))
            with patch.object(job, 'fetch_all', return_value=iter([episode('unselected_5', 300), episode(identity, 200)])) as fetch, patch.object(job, 'fetch_transcript', return_value=transcript):
                result = job.run_download('affaires-sensibles', baseline, root / 'success', 'test', 0, fixture)
            self.assertEqual(result['catalogue_episode_count'], 1)
            self.assertEqual(result['transcripts'][0]['episode_id'], identity)
            self.assertEqual(fetch.call_args.kwargs['since_ts'], 199)
            with patch.object(job, 'fetch_all', return_value=iter([])), self.assertRaisesRegex(ValueError, 'unavailable'):
                job.run_download('affaires-sensibles', baseline, root / 'missing', 'test', 0, fixture)
            with patch.object(job, 'fetch_all', return_value=iter([episode(identity, 200)])), patch.object(job, 'fetch_transcript', return_value={'transcript': []}), self.assertRaisesRegex(ValueError, 'changed'):
                job.run_download('affaires-sensibles', baseline, root / 'changed', 'test', 0, fixture)
            self.assertFalse((root / 'changed').exists())
            self.assertFalse((root / 'missing').exists())
            selection['episodes'].append(selection['episodes'][0])
            fixture.write_text(json.dumps(selection))
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                read_selection(fixture, 'affaires-sensibles')

    def test_original_clis_keep_sorting_columns_and_json_format(self) -> None:
        """Exercise both existing CLIs offline after sharing their serializers with the job."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / 'catalogue.csv'
            rows = [episode('c_5', 200), episode('b_5', 100), episode('a_5', 100)]
            arguments = ['fetch_episodes.py', '--api-key', 'test', '--out', str(output)]
            with patch('sys.argv', arguments), patch.object(rf_dump, 'fetch_all', return_value=iter(rows)), patch('builtins.print'):
                self.assertEqual(rf_dump.main(), 0)
            with output.open(newline='') as stream:
                reader = csv.DictReader(stream)
                self.assertEqual(reader.fieldnames, FIELDS)
                self.assertEqual([row['id'] for row in reader], ['a_5', 'b_5', 'c_5'])
            ids = root / 'ids.txt'
            ids.write_text('episode_5\n')
            transcript: Transcript = {'transcript': [{'start': 0, 'end': 1, 'text': 'Français'}]}
            arguments = ['fetch_transcripts.py', str(ids), '--output-dir', str(root / 'transcripts'), '--delay', '0']
            with patch('sys.argv', arguments), patch.object(fetchtranscript, 'fetch_transcript', return_value=transcript) as fetch, patch('builtins.print'):
                fetchtranscript.main()
            fetch.assert_called_once_with('episode')
            self.assertEqual((root / 'transcripts/episode_5.json').read_text(),
                             json.dumps(transcript, ensure_ascii=False, indent=2))

    def test_published_artifacts_are_readable_by_other_container_users(self) -> None:
        """Publish traversable outputs under umask 077 without widening source/key permissions."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline = root / 'baseline.csv'
            with baseline.open('w', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=FIELDS)
                writer.writeheader()
            baseline.chmod(0o600)
            key = root / 'key'
            key.write_text('test-key')
            key.chmod(0o600)
            previous_umask = os.umask(0o077)
            try:
                with patch.object(job, 'fetch_all', return_value=iter([episode('new_5', 200)])), patch.object(job, 'fetch_transcript', return_value={'transcript': []}):
                    job.run_download('affaires-sensibles', baseline, root / 'output', 'test', 0)
            finally:
                os.umask(previous_umask)
            output = root / 'output'
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o755)
            for path in output.rglob('*'):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o755 if path.is_dir() else 0o644)
            self.assertEqual(stat.S_IMODE(baseline.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o600)

    def test_runtime_key_file_takes_precedence(self) -> None:
        """Read an explicitly mounted key file without using the environment value."""
        with tempfile.TemporaryDirectory() as temporary:
            key = Path(temporary) / 'key'
            key.write_text('file-key')
            arguments = ['radiofrance.jobs.download', '--show', 'mecaniques-du-journalisme',
                         '--baseline', 'baseline.csv', '--output-dir', 'output', '--api-key-file', str(key)]
            with patch('sys.argv', arguments), patch.dict(os.environ, {'RADIOFRANCE_API_KEY': 'environment-key'}), patch.object(job, 'run_download', return_value={}) as run, patch('builtins.print'):
                job.main()
            self.assertEqual(run.call_args.args[3], 'file-key')

    def test_success_empty_and_failed_transcript(self) -> None:
        """Verify cutoff, merging, checksums, no-update behavior and all-or-nothing publication."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline = root / 'baseline.csv'
            with baseline.open('w', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerow(episode('old_5', 100))
            with patch.object(job, 'fetch_all', return_value=iter([episode('new_5', 200)])) as fetch, patch.object(job, 'fetch_transcript', return_value={'transcript': []}) as transcript:
                manifest = job.run_download('affaires-sensibles', baseline, root / 'success', 'test', 0)
            self.assertEqual(fetch.call_args.kwargs['since_ts'], 100)
            transcript.assert_called_once_with('new')
            self.assertEqual(manifest['catalogue_episode_count'], 2)
            published = root / 'success'
            self.assertEqual(json.loads((published / 'manifest.json').read_text()), manifest)
            source = published / manifest['transcripts'][0]['path']
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), manifest['transcripts'][0]['sha256'])
            with self.assertRaises(FileExistsError):
                job.run_download('affaires-sensibles', baseline, published, 'test')
            with patch.object(job, 'fetch_all', return_value=iter([])), patch.object(job, 'fetch_transcript') as transcript:
                empty = job.run_download('affaires-sensibles', baseline, root / 'empty', 'test')
                self.assertEqual(empty['new_episode_count'], 0)
                transcript.assert_not_called()
            with patch.object(job, 'fetch_all', return_value=iter([episode('new_5', 200)])), patch.object(job, 'fetch_transcript', return_value=None):
                with self.assertRaises(RuntimeError):
                    job.run_download('affaires-sensibles', baseline, root / 'failed', 'test')
            self.assertFalse((root / 'failed').exists())
            self.assertEqual(list(root.glob('.download-job-*')), [])
            self.assertEqual(len(job.read_rows(baseline, 'affaires-sensibles')), 1)


if __name__ == '__main__':
    unittest.main()
