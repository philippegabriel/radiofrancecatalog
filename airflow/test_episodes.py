"""Episode-reference registration and publication-based cutoff checks."""
import csv
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent / 'dags'))
import radiofrance_download as download_dag
from radiofrance_catalogue.catalogue import FIELDS


class EpisodeTests(unittest.TestCase):
    def test_registration_is_idempotent_and_records_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'baseline.csv'
            with path.open('w') as stream:
                writer = csv.DictWriter(stream, fieldnames=FIELDS)
                writer.writeheader()
                for episode_id, timestamp in [('existing_5', '100'), ('new_5', '200')]:
                    writer.writerow(dict.fromkeys(FIELDS, '') | {
                        'show': 'affaires-sensibles', 'id': episode_id, 'published_ts': timestamp,
                    })
            state: download_dag.CatalogueState = {'show': 'affaires-sensibles', 'workspace': directory}
            inlets = MagicMock()
            inlets.__getitem__.return_value = [SimpleNamespace(extra={'episode_id': 'existing_5', 'published_ts': 100})]
            outlets = MagicMock()
            download_dag.record_episodes.function(state, 'baseline.csv', inlets, outlets)
            emitter = outlets.__getitem__.return_value
            emitter.add.assert_called_once()
            event = emitter.add.call_args
            self.assertEqual(event.args[0].uri, 'x-radiofrance://episodes/affaires-sensibles/new_5')
            self.assertEqual(event.kwargs['extra']['published_iso'], '1970-01-01T00:03:20+00:00')
            self.assertEqual(event.kwargs['extra']['episode_id'], 'new_5')
            inlets.__getitem__.return_value.append(SimpleNamespace(extra=event.kwargs['extra']))
            emitter.reset_mock()
            download_dag.record_episodes.function(state, 'baseline.csv', inlets, outlets)
            emitter.add.assert_not_called()

    def test_cutoff_uses_publication_not_event_order(self) -> None:
        inlets = MagicMock()
        inlets.__getitem__.return_value = [
            SimpleNamespace(extra={'published_ts': 200}),
            SimpleNamespace(extra={'published_ts': 100}),
        ]
        state: download_dag.CatalogueState = {'show': 'affaires-sensibles', 'workspace': '/unused'}
        result = download_dag.derive_cutoff.function(state, inlets)
        self.assertEqual(result['since'], '1970-01-01T00:03:20+00:00')
        inlets.__getitem__.assert_called_with(download_dag.EPISODES['affaires-sensibles'])
        inlets.__getitem__.return_value = []
        self.assertEqual(download_dag.derive_cutoff.function(state, inlets)['since'], '1900-01-01T00:00:00+00:00')


if __name__ == '__main__':
    unittest.main()
