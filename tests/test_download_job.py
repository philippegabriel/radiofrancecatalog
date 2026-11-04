"""Offline checks for database-driven acquisition and atomic JSON publication."""
import csv
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
from radiofrance.libs.catalogue import FIELDS, read_rows
from radiofrance.libs.download_state import DownloadState
from radiofrance.libs.date_range import date_range
from radiofrance.libs.types import GraphQLData
from radiofrance.libs.types import EpisodeRow, Transcript


def episode(episode_id: str, timestamp: int) -> EpisodeRow:
    """Return a minimal valid API catalogue row for an offline test."""
    return {'show': 'affaires-sensibles', 'id': episode_id, 'published_ts': timestamp,
            'published_iso': '', 'title': 'Test', 'description': '', 'web_url': '',
            'podcast_title': '', 'podcast_url': '', 'player_url': ''}


class DownloadJobTests(unittest.TestCase):
    def setUp(self) -> None:
        """Unit tests substitute only the database read and external HTTP clients."""
        self.state: DownloadState = {'cutoff_timestamp': 0, 'episode_count': 0, 'existing_ids': []}
        state = patch.object(job, 'read_download_state', return_value=self.state)
        self.read_state = state.start()
        self.addCleanup(state.stop)

    def test_date_bounds_use_paris_midnights_including_dst(self) -> None:
        """Use calendar boundaries rather than fixed 24-hour days; reject invalid ranges."""
        for day, hours in [('2026-03-29', 23), ('2026-10-25', 25)]:
            bounds = date_range(day, day)
            assert bounds['start_timestamp'] is not None and bounds['end_timestamp'] is not None
            self.assertEqual(bounds['end_timestamp'] - bounds['start_timestamp'], hours * 3600)
        for first, last in [('2026-02-30', None), ('2026-2-01', None), ('2026-11-02', '2026-11-01')]:
            with self.assertRaises(ValueError):
                date_range(first, last)
        self.assertIsNone(date_range('', None)['start_timestamp'])
        self.assertIsNone(date_range(None, '2026-10-01')['start_timestamp'])
        self.assertIsNone(date_range('2026-10-01', None)['end_timestamp'])

    def test_historical_range_ignores_latest_cutoff_and_skips_committed_ids(self) -> None:
        """Select only unimported episodes in inclusive dates before requesting transcripts."""
        bounds = date_range('2026-10-01', '2026-10-01')
        start, end = bounds['start_timestamp'], bounds['end_timestamp']
        assert start is not None and end is not None
        self.state.update(cutoff_timestamp=end + 100, episode_count=2, existing_ids=['old_5', 'future_5'])
        rows = [episode('future_5', end + 100), episode('outside_5', end),
                episode('last_5', end - 1), episode('old_5', start + 1),
                episode('first_5', start), episode('before_5', start - 1)]
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(job, 'fetch_all', return_value=iter(rows)) as fetch, patch.object(job, 'fetch_transcript', return_value={'transcript': []}) as transcripts:
                manifest = job.run_download('affaires-sensibles', Path(directory) / 'range', 'test', 0,
                    from_date='2026-10-01', to_date='2026-10-01')
            self.assertEqual(manifest['new_episode_count'], 2)
            self.assertEqual(manifest.get('date_range'), bounds)
            self.assertEqual([x['episode_id'] for x in manifest['transcripts']], ['first_5', 'last_5'])
            self.assertEqual(transcripts.call_count, 2)
            self.assertEqual(fetch.call_args.kwargs, {'since_ts': start - 1, 'until_ts': end})
            self.read_state.assert_called_with('affaires-sensibles', '', include_all_ids=True)

    def test_open_ended_dates_and_fixed_selection_conflict(self) -> None:
        """One bound selects explicit mode; invalid/conflicting ranges fail before API calls."""
        with tempfile.TemporaryDirectory() as directory, patch.object(job, 'fetch_all', return_value=iter([])) as fetch:
            root = Path(directory)
            job.run_download('affaires-sensibles', root / 'upper', 'test', to_date='2026-10-01')
            self.assertEqual(fetch.call_args.kwargs['since_ts'], 0)
            job.run_download('affaires-sensibles', root / 'lower', 'test', from_date='2026-10-01')
            self.assertIsNone(fetch.call_args.kwargs['until_ts'])
            with self.assertRaisesRegex(ValueError, 'fixed episode selection'):
                job.run_download('affaires-sensibles', root / 'conflict', 'test', episode_selection=root / 'fixture.json', from_date='2026-10-01')
            with self.assertRaises(ValueError):
                job.run_download('affaires-sensibles', root / 'invalid', 'test', from_date='2026-10-02', to_date='2026-10-01')
            self.assertEqual(fetch.call_count, 2)

    def test_api_paginates_past_upper_bound_and_stops_at_lower_bound(self) -> None:
        """Skip newer pages while retaining both boundary seconds of the requested interval."""
        first: GraphQLData = {'diffusionsOfShowByUrl': {'edges': [
            {'cursor': 'next-page', 'node': {'id': 'new_5', 'published_date': 400}}]}}
        second: GraphQLData = {'diffusionsOfShowByUrl': {'edges': [
            {'cursor': 'a', 'node': {'id': 'last_5', 'published_date': 399}},
            {'cursor': 'b', 'node': {'id': 'first_5', 'published_date': 200}},
            {'cursor': 'c', 'node': {'id': 'old_5', 'published_date': 199}}]}}
        with patch.object(rf_dump, 'gql_post', side_effect=[first, second]) as post:
            rows = list(rf_dump.fetch_all('test', 'https://www.radiofrance.fr/franceinter/podcasts/affaires-sensibles',
                since_ts=199, until_ts=400, sleep_sec=0))
        self.assertEqual([row['id'] for row in rows], ['last_5', 'first_5'])
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args.args[3]['after'], 'next-page')

    def test_fixed_selection_enforces_membership_and_counts(self) -> None:
        """Keep exact IDs on an empty database; reject unavailable or changed transcripts."""
        from radiofrance.libs.episode_selection import EpisodeSelection, read_selection
        identity = '11111111-1111-4111-8111-111111111111_5'
        transcript: Transcript = {'transcript': [{'start': 0, 'end': 1, 'text': 'Français'}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selection: EpisodeSelection = {'version': 1, 'show': 'affaires-sensibles', 'episodes': [{
                'episode_id': identity, 'published_ts': 200, 'published_iso': '', 'title': 'Test', 'segment_count': 1}]}
            fixture = root / 'selection.json'
            fixture.write_text(json.dumps(selection))
            with patch.object(job, 'fetch_all', return_value=iter([episode('unselected_5', 300), episode(identity, 200)])) as fetch, patch.object(job, 'fetch_transcript', return_value=transcript):
                result = job.run_download('affaires-sensibles', root / 'success', 'test', 0, fixture)
            self.assertEqual(result['new_episode_count'], 1)
            self.assertEqual(result['transcripts'][0]['episode_id'], identity)
            self.assertEqual(fetch.call_args.kwargs['since_ts'], 199)
            with patch.object(job, 'fetch_all', return_value=iter([])), self.assertRaisesRegex(ValueError, 'unavailable'):
                job.run_download('affaires-sensibles', root / 'missing', 'test', 0, fixture)
            with patch.object(job, 'fetch_all', return_value=iter([episode(identity, 200)])), patch.object(job, 'fetch_transcript', return_value={'transcript': []}), self.assertRaisesRegex(ValueError, 'changed'):
                job.run_download('affaires-sensibles', root / 'changed', 'test', 0, fixture)
            self.assertFalse((root / 'changed').exists())
            self.assertFalse((root / 'missing').exists())
            self.state['episode_count'] = 1
            with self.assertRaisesRegex(ValueError, 'empty show'):
                job.run_download('affaires-sensibles', root / 'populated', 'test', 0, fixture)
            selection['episodes'].append(selection['episodes'][0])
            fixture.write_text(json.dumps(selection))
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                read_selection(fixture, 'affaires-sensibles')

    def test_original_clis_keep_sorting_columns_and_json_format(self) -> None:
        """Standalone CSV export remains available independently of the pipeline."""
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
            self.assertEqual((root / 'transcripts/episode_5.json').read_text(), json.dumps(transcript, ensure_ascii=False, indent=2))

    def test_published_artifacts_are_readable_by_other_container_users(self) -> None:
        """Publish readable JSON under umask 077 without widening key-file permissions."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            key = root / 'key'
            key.write_text('test-key')
            key.chmod(0o600)
            previous_umask = os.umask(0o077)
            try:
                with patch.object(job, 'fetch_all', return_value=iter([episode('new_5', 200)])), patch.object(job, 'fetch_transcript', return_value={'transcript': []}):
                    job.run_download('affaires-sensibles', root / 'output', 'test', 0)
            finally:
                os.umask(previous_umask)
            output = root / 'output'
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o755)
            for path in output.rglob('*'):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o755 if path.is_dir() else 0o644)
            self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o600)
            self.assertEqual(list(output.glob('*.csv')), [])

    def test_runtime_key_file_takes_precedence(self) -> None:
        """Read an explicitly mounted key without using the environment value."""
        with tempfile.TemporaryDirectory() as temporary:
            key = Path(temporary) / 'key'
            key.write_text('file-key')
            arguments = ['radiofrance.jobs.download', '--show', 'mecaniques-du-journalisme',
                         '--output-dir', 'output', '--api-key-file', str(key)]
            with patch('sys.argv', arguments), patch.dict(os.environ, {'RADIOFRANCE_API_KEY': 'environment-key'}), patch.object(job, 'run_download', return_value={}) as run, patch('builtins.print'):
                job.main()
            self.assertEqual(run.call_args.args[2], 'file-key')

    def test_boundary_empty_updates_and_failed_transcript(self) -> None:
        """Retain unseen IDs sharing the watermark second; failures leave the cutoff unchanged."""
        self.state.update(cutoff_timestamp=100, episode_count=10, existing_ids=['old_5'])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(job, 'fetch_all', return_value=iter([episode('new_5', 200), episode('old_5', 100), episode('tie_5', 100)])) as fetch, patch.object(job, 'fetch_transcript', return_value={'transcript': []}) as transcript:
                manifest = job.run_download('affaires-sensibles', root / 'success', 'test', 0)
            self.assertEqual(fetch.call_args.kwargs['since_ts'], 99)
            self.assertEqual([call.args[0] for call in transcript.call_args_list], ['tie', 'new'])
            self.assertEqual(manifest['new_episode_count'], 2)
            self.assertEqual(manifest['database_episode_count'], 10)
            self.assertEqual(manifest['cutoff_timestamp'], 100)
            published = root / 'success'
            self.assertEqual(json.loads((published / 'manifest.json').read_text()), manifest)
            self.assertEqual([r['id'] for r in read_rows(published / 'episodes.json', 'affaires-sensibles')], ['tie_5', 'new_5'])
            with self.assertRaises(FileExistsError):
                job.run_download('affaires-sensibles', published, 'test')
            with patch.object(job, 'fetch_all', return_value=iter([episode('old_5', 100)])), patch.object(job, 'fetch_transcript') as transcript:
                empty = job.run_download('affaires-sensibles', root / 'empty', 'test')
                self.assertEqual(empty['new_episode_count'], 0)
                transcript.assert_not_called()
            with patch.object(job, 'fetch_all', return_value=iter([episode('new_5', 200)])), patch.object(job, 'fetch_transcript', return_value=None):
                with self.assertRaises(RuntimeError):
                    job.run_download('affaires-sensibles', root / 'failed', 'test')
            self.assertFalse((root / 'failed').exists())
            self.assertEqual(list(root.glob('.download-job-*')), [])
            self.assertEqual(self.state['cutoff_timestamp'], 100)

    def test_database_failure_never_starts_full_download(self) -> None:
        """An unavailable database fails before HTTP or artifact publication."""
        self.read_state.side_effect = RuntimeError('Database unavailable')
        with tempfile.TemporaryDirectory() as temporary, patch.object(job, 'fetch_all') as fetch:
            output = Path(temporary) / 'run'
            with self.assertRaisesRegex(RuntimeError, 'Database unavailable'):
                job.run_download('affaires-sensibles', output, 'test')
            fetch.assert_not_called()
            self.assertFalse(output.exists())

    def test_metadata_rejects_duplicates_and_invalid_fields(self) -> None:
        """Reject malformed deltas before requesting any transcripts."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bad = episode('a_5', 100)
            bad['published_ts'] = None
            for index, rows in enumerate(([episode('a_5', 100), episode('a_5', 100)], [bad])):
                with patch.object(job, 'fetch_all', return_value=iter(rows)), patch.object(job, 'fetch_transcript') as fetch:
                    with self.assertRaises(ValueError):
                        job.run_download('affaires-sensibles', root / str(index), 'test', 0)
                    fetch.assert_not_called()
                    self.assertFalse((root / str(index)).exists())


if __name__ == '__main__':
    unittest.main()
