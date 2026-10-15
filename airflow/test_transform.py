"""Offline tests for explicit-input transformation and embedding contract validation."""
import csv
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent / 'dags'))
from radiofrance_catalogue import transform
import radiofrance_transform as dag


class TransformTests(unittest.TestCase):
    def test_embedding_request_and_normalization(self) -> None:
        response = MagicMock()
        response.__enter__.return_value = io.BytesIO(json.dumps({
            'data': [{'index': 0, 'embedding': [2.0] + [0.0] * 383}],
        }).encode())
        tokens = MagicMock()
        tokens.__enter__.return_value = io.BytesIO(json.dumps({'tokens': [0, 42, 2]}).encode())
        with patch.dict(os.environ, {'RF_EMBEDDING_URL': 'http://localhost/v1/embeddings'}), patch.object(transform, 'urlopen', side_effect=[tokens, response]) as request:
            vector = transform.embedding('bonjour')
        self.assertEqual(vector, [1.0] + [0.0] * 383)
        payload = json.loads(request.call_args.args[0].data)
        self.assertEqual(payload['input'], [0, 42, 2])
        token_payload = json.loads(request.call_args_list[0].args[0].data)
        self.assertEqual(token_payload['content'], 'passage: bonjour')
        self.assertEqual(payload['model'], 'intfloat/multilingual-e5-small')

    def test_invalid_vectors_rejected(self) -> None:
        for vector in ([1.0], [0.0] * 384, [float('nan')] * 384):
            response = MagicMock()
            response.__enter__.return_value = io.BytesIO(json.dumps({'data': [{'index': 0, 'embedding': vector}]}).encode())
            tokens = MagicMock()
            tokens.__enter__.return_value = io.BytesIO(json.dumps({'tokens': [0, 42, 2]}).encode())
            with patch.dict(os.environ, {'RF_EMBEDDING_URL': 'http://localhost/v1/embeddings'}), patch.object(transform, 'urlopen', side_effect=[tokens, response]):
                with self.assertRaises(ValueError):
                    transform.embedding('bonjour')

    def test_transform_preserves_formats_and_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = {'RF_S3_BUCKET': 'test-bucket', 'AIRFLOW_HOME': directory}
            events = MagicMock()

            def aws(*args: str) -> None:
                if args[2].endswith('.json'):
                    Path(args[3]).write_text(json.dumps({'transcript': [{'start': 0, 'end': 1, 'text': 'Bonjour monde'}]}))
                elif args[2].startswith('s3://'):
                    Path(args[3]).write_bytes(next(Path(directory).rglob('*.chunks.csv')).read_bytes())

            with patch.dict(os.environ, env), patch.object(dag, 'aws', side_effect=aws) as storage, patch.object(transform, 'embedding', return_value=[1.0] + [0.0] * 383):
                state = dag.chunk_transcript.function('s3://test-bucket/data/transcripts/test_1.json', 'run', events)
                dag.embed_chunks.function(state, 'run', events)
            self.assertEqual(storage.call_count, 4)
            chunks = next(Path(directory).rglob('*.chunks.csv'))
            embeddings = next(Path(directory).rglob('*.embeddings.csv'))
            with chunks.open() as file:
                row = next(csv.reader(file))
            self.assertEqual(row[0], 'test_1')
            self.assertEqual(row[6], 'Bonjour monde')
            with embeddings.open() as file:
                row = next(csv.reader(file))
            self.assertEqual(row[:2], ['test_1', '0'])
            self.assertEqual(len(json.loads(row[2])), 384)
            calls = events.__getitem__.return_value.add.call_args_list
            self.assertEqual(calls[0].kwargs['extra']['source_uri'], 's3://test-bucket/data/transcripts/test_1.json')
            self.assertEqual(calls[1].kwargs['extra']['source_uri'], 's3://test-bucket/data/chunks/test_1.csv')

    def test_input_list_validation(self) -> None:
        with patch.dict(os.environ, {'RF_S3_BUCKET': 'test-bucket', 'RF_EMBEDDING_URL': 'http://localhost/v1/embeddings'}):
            self.assertEqual(dag.inputs.function({'transcripts': []}), [])
            source = 's3://test-bucket/data/transcripts/test.json'
            self.assertEqual(dag.inputs.function({'transcripts': [source, source]}), [source])
            for bad in ('s3://other/data/transcripts/test.json', 's3://test-bucket/data/transcripts/../test.json'):
                with self.assertRaises(ValueError):
                    dag.inputs.function({'transcripts': [bad]})


    def test_truncation_retains_eos(self) -> None:
        tokens = MagicMock()
        tokens.__enter__.return_value = io.BytesIO(json.dumps({'tokens': [0] + [42] * 600 + [2]}).encode())
        response = MagicMock()
        response.__enter__.return_value = io.BytesIO(json.dumps({'data': [{'index': 0, 'embedding': [1.0] + [0.0] * 383}]}).encode())
        with patch.dict(os.environ, {'RF_EMBEDDING_URL': 'http://localhost/v1/embeddings'}), patch.object(transform, 'urlopen', side_effect=[tokens, response]) as request:
            transform.embedding('long passage')
        payload = json.loads(request.call_args.args[0].data)
        self.assertEqual(len(payload['input']), 512)
        self.assertEqual(payload['input'][-1], 2)
