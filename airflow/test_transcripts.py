"""Offline checks for incremental transcript downloads and asset publication."""
import csv
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent / 'dags'))
from radiofrance_catalogue.catalogue import FIELDS
import radiofrance_download as download_dag


class TranscriptTests(unittest.TestCase):
    def test_downloads_only_current_updates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for filename, episode_ids in [('updates.csv', ['new_5']), ('show.csv', ['old_5', 'new_5'])]:
                with (root / filename).open('w') as stream:
                    writer = csv.DictWriter(stream, fieldnames=FIELDS)
                    writer.writeheader()
                    for episode_id in episode_ids:
                        writer.writerow(dict.fromkeys(FIELDS, '') | {
                            'show': 'affaires-sensibles', 'id': episode_id, 'published_ts': '1',
                        })
            state: download_dag.PublishedState = {
                'show': 'affaires-sensibles', 'workspace': directory,
                'output': str(root / 'show.csv'), 'episode_count': 2, 'since': 'cutoff',
            }
            environment = {'RF_S3_BUCKET': 'test-bucket', 'RF_TRANSCRIPT_DIR': str(root / 'transcripts')}
            events = MagicMock()
            transcript = {'transcript': [{'start': 0, 'end': 1, 'text': 'bonjour'}]}
            with patch.dict(os.environ, environment), patch.object(download_dag, 'fetch_transcript', return_value=transcript) as fetch, patch.object(download_dag, 'aws') as upload:
                download_dag.download_transcripts.function(state, events)
            fetch.assert_called_once_with('new')
            source = root / 'transcripts' / 'new_5.json'
            self.assertEqual(json.loads(source.read_text()), transcript)
            upload.assert_called_once_with('s3', 'cp', str(source), 's3://test-bucket/data/transcripts/new_5.json')
            event = events.__getitem__.return_value.add.call_args
            self.assertEqual(event.args[0].uri, 's3://test-bucket/data/transcripts/new_5.json')
            self.assertEqual(event.kwargs['extra']['sha256'], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertFalse((root / 'transcripts' / 'old_5.json').exists())

    def test_empty_update_downloads_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (root / 'updates.csv').open('w') as stream:
                csv.DictWriter(stream, fieldnames=FIELDS).writeheader()
            state: download_dag.PublishedState = {
                'show': 'affaires-sensibles', 'workspace': directory,
                'output': str(root / 'show.csv'), 'episode_count': 0, 'since': 'cutoff',
            }
            with patch.dict(os.environ, {'RF_TRANSCRIPT_DIR': str(root / 'transcripts')}), patch.object(download_dag, 'fetch_transcript') as fetch, patch.object(download_dag, 'aws') as upload:
                download_dag.download_transcripts.function(state, MagicMock())
            fetch.assert_not_called()
            upload.assert_not_called()


if __name__ == '__main__':
    unittest.main()
