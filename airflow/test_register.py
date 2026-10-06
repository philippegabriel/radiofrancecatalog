"""Offline checks for registration of existing S3 artifacts."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent / 'dags'))
import radiofrance_register as register_dag


class RegisterTests(unittest.TestCase):
    def test_prepared_inventory_does_not_access_s3(self) -> None:
        inlets = MagicMock()
        inlets.__getitem__.return_value = []
        outlets = MagicMock()
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'transcript.json').write_text(
                '[{"Key":"data/transcripts/episode_5.json","Size":12}]')
            with patch.dict('os.environ', {
                'RF_S3_BUCKET': 'test-bucket',
                'RF_REGISTRATION_INVENTORY_DIR': directory,
            }), patch.object(register_dag, 'list_objects') as listing:
                self.assertEqual(register_dag.register_files.function('transcript', inlets, outlets), 1)
            listing.assert_not_called()
            outlets.__getitem__.return_value.add.assert_called_once()

    def test_existing_transcripts_skip_registered_uris(self) -> None:
        inlets = MagicMock()
        inlets.__getitem__.return_value = [SimpleNamespace(asset=SimpleNamespace(uri='s3://test-bucket/data/transcripts/old_5.json'))]
        outlets = MagicMock()
        listing = [
            {'Key': 'data/transcripts/', 'Size': 0},
            {'Key': 'data/transcripts/old_5.json', 'Size': 20},
            {'Key': 'data/transcripts/new_5.json', 'Size': 30},
            {'Key': 'data/transcripts/new_5.csv', 'Size': 40},
        ]
        with patch.dict('os.environ', {'RF_S3_BUCKET': 'test-bucket'}), patch.object(register_dag, 'list_objects', return_value=listing):
            count = register_dag.register_files.function('transcript', inlets, outlets)
        self.assertEqual(count, 1)
        emitter = outlets.__getitem__.return_value
        emitter.add.assert_called_once()
        event = emitter.add.call_args
        self.assertEqual(event.args[0].name, 'new_5_transcript')
        self.assertEqual(event.kwargs['extra']['status'], 'discovered_existing')
        self.assertEqual(event.kwargs['extra']['size_bytes'], 30)
        inlets.__getitem__.return_value.append(SimpleNamespace(asset=event.args[0]))
        emitter.reset_mock()
        with patch.dict('os.environ', {'RF_S3_BUCKET': 'test-bucket'}), patch.object(register_dag, 'list_objects', return_value=listing):
            self.assertEqual(register_dag.register_files.function('transcript', inlets, outlets), 0)
        emitter.add.assert_not_called()

    def test_chunk_and_embedding_sources_follow_basename(self) -> None:
        cases: list[tuple[register_dag.ArtifactKind, str, str]] = [
            ('chunks', 'data/chunks/', 'data/transcripts/episode_5.json'),
            ('embeddings', 'data/embeddings/', 'data/chunks/episode_5.csv'),
        ]
        for kind, prefix, source in cases:
            inlets = MagicMock()
            inlets.__getitem__.return_value = []
            outlets = MagicMock()
            with patch.dict('os.environ', {'RF_S3_BUCKET': 'test-bucket'}), patch.object(register_dag, 'list_objects', return_value=[{'Key': prefix + 'episode_5.csv', 'Size': 50}]):
                self.assertEqual(register_dag.register_files.function(kind, inlets, outlets), 1)
            self.assertEqual(outlets.__getitem__.return_value.add.call_args.kwargs['extra']['source_uri'], 's3://test-bucket/' + source)

    def test_catalogue_registration_uses_shared_episode_logic(self) -> None:
        inlets = MagicMock()
        inlets.__getitem__.return_value = []
        outlets = MagicMock()
        with patch.dict('os.environ', {'RF_S3_BUCKET': 'test-bucket'}), patch.object(register_dag, 'aws') as access, patch.object(register_dag, 'read_rows', return_value=[]), patch.object(register_dag, 'record_episode_rows', return_value=2) as record:
            # Supply a file as the S3 copy would, so catalogue size can be recorded.
            def copy_file(*arguments: str) -> None:
                Path(arguments[3]).write_text('show,id\n')
            access.side_effect = copy_file
            self.assertEqual(register_dag.register_catalogues.function(inlets, outlets), 2 * len(register_dag.SHOWS))
            self.assertEqual(record.call_count, len(register_dag.SHOWS))
            self.assertTrue(all(call.args[:2] == ('s3', 'cp') for call in access.call_args_list))


if __name__ == '__main__':
    unittest.main()
