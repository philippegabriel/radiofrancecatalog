"""Retrieval validation and real SQL checks on an explicitly disposable database."""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
from typing import cast

import psycopg2
from psycopg2.extensions import make_dsn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retrieval import CorpusHit, Retrieval, combine_hits
from radiofrance.jobs.embedding import DOCUMENT_PREFIX, MODEL_ID, MODEL_REVISION, QUERY_PREFIX


class ValidationTests(unittest.TestCase):
    def test_invalid_requests_do_not_encode(self) -> None:
        encoder = Mock()
        retrieval = Retrieval(encoder)
        for call in (lambda: retrieval.search(''), lambda: retrieval.search('x', limit=21),
                     lambda: retrieval.search('x', date_from='2026-02-30'),
                     lambda: retrieval.search('x', date_from='2026-02-02', date_to='2026-01-01')):
            with self.assertRaises(ValueError):
                call()
        with self.assertRaises(ValueError):
            retrieval.passage('x', 1, 0, 6)
        encoder.encode_formatted.assert_not_called()

    def test_progress_tracks_selected_sources_and_stops_on_failure(self) -> None:
        """Only report work actually selected, and never claim success after SQL errors."""
        encoder = Mock()
        encoder.encode_formatted.return_value = [[1.0] + [0.0] * 255]
        retrieval = Retrieval(encoder)
        retrieval.rows = Mock(return_value=[])
        updates: list[tuple[int, int, str]] = []

        def report(step: int, total: int, message: str) -> None:
            """Capture stages without an MCP transport."""
            updates.append((step, total, message))

        retrieval.search('test', source='descriptions', progress=report)
        self.assertEqual([step for step, _, _ in updates], [0, 1, 2, 3])
        self.assertTrue(all(total == 3 for _, total, _ in updates))
        self.assertNotIn('transcripts', ' '.join(message for _, _, message in updates))
        updates.clear()
        retrieval.rows.side_effect = RuntimeError('Database unavailable')
        with self.assertRaisesRegex(RuntimeError, 'Database unavailable'):
            retrieval.search('test', progress=report)
        self.assertEqual([step for step, _, _ in updates], [0, 1])
        self.assertFalse(any('complete' in message for _, _, message in updates))


class FusionTests(unittest.TestCase):
    def hit(self, identity: str, text: str, transcript: bool = False) -> CorpusHit:
        """Construct a source hit with a deliberately unrelated cosine score."""
        return {'episode_id': identity, 'show': 'test', 'title': identity,
                'web_url': None, 'published': None, 'similarity': .1 if transcript else .99,
                'duplicate_count': 1, 'text': text, 'strategy_id': 1 if transcript else None,
                'chunk_no': 2 if transcript else None, 'start_time': 10.0 if transcript else None,
                'end_time': 20.0 if transcript else None}

    def test_overlap_outranks_single_sources_and_retains_both_snippets(self) -> None:
        results = combine_hits([
            ('descriptions', [self.hit('a', 'description A'), self.hit('b', 'description B')]),
            ('transcripts', [self.hit('c', 'passage C', True), self.hit('b', 'passage B', True)])
        ], 3, mixed=True)
        self.assertEqual([hit['episode_id'] for hit in results], ['b', 'a', 'c'])
        top = results[0]
        self.assertEqual(top['source'], 'both')
        self.assertAlmostEqual(top['fusion_score'] or 0, 2 / 62)
        self.assertIsNone(top['similarity'])
        self.assertEqual(top['text'], 'passage B')
        self.assertEqual(top['chunk_no'], 2)
        self.assertEqual([entry['text'] for entry in top['evidence']], ['description B', 'passage B'])
        self.assertEqual([entry['rank'] for entry in top['evidence']], [2, 2])
        self.assertEqual(results[1]['source'], 'descriptions')
        self.assertIsNone(results[1]['chunk_no'])
        self.assertEqual(results[2]['source'], 'transcripts')

    def test_missing_corpus_limits_and_single_source_scores(self) -> None:
        hits = [self.hit('a', 'A'), self.hit('b', 'B')]
        self.assertEqual(combine_hits([('transcripts', []), ('descriptions', [])], 3, True), [])
        mixed = combine_hits([('transcripts', []), ('descriptions', hits)], 1, True)
        self.assertEqual(len(mixed), 1)
        self.assertEqual(mixed[0]['episode_id'], 'a')
        single = combine_hits([('descriptions', hits)], 2, False)
        self.assertEqual(single[0]['similarity'], .99)
        self.assertIsNone(single[0]['fusion_score'])


@unittest.skipUnless(os.environ.get('RF_VALIDATE_MCP') == '1', 'use bash mcp/test/run-tests')
class DatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        """Seed only the disposable test cluster with repeated and distinct documents."""
        configuration = {'model_id': MODEL_ID, 'revision': MODEL_REVISION,
            'precision': 'float32', 'dimensions': 256, 'normalized': True, 'query_prefix': QUERY_PREFIX}
        cls.vector = [1.0] + [0.0] * 255
        with psycopg2.connect('') as connection:
            with connection.cursor() as cursor:
                cursor.execute('INSERT INTO chunk_strategy(configuration) VALUES (%s) RETURNING id', ('{}',))
                cls.strategy = cursor.fetchone()[0]
                models: list[int] = []
                for extra in ({'document_prefix': DOCUMENT_PREFIX},
                              {'document_template': 'title: {title} | text: {description}'}):
                    cursor.execute('INSERT INTO embedding_model(configuration) VALUES (%s) RETURNING id',
                                   (json.dumps(configuration | extra),))
                    models.append(cursor.fetchone()[0])
                cls.ids: list[str] = []
                for index in range(3):
                    uuid = f'11111111-1111-4111-8111-{index:012d}'
                    cls.ids.append(uuid + '_5')
                    cursor.execute('INSERT INTO episode_identity(source_uuid, source_suffix) VALUES (%s,5) RETURNING id', (uuid,))
                    episode = cursor.fetchone()[0]
                    text = 'Même passage.' if index < 2 else 'Autre passage.'
                    cursor.execute('INSERT INTO rf(episode_id,show,published_ts,published_iso,title,description,web_url) '
                                   'VALUES (%s,%s,1,%s,%s,%s,%s)',
                                   (episode, 'histoire' if index < 2 else 'science', '2026-10-01T12:00:00Z',
                                    'Titre', 'Description', '' if index == 0 else 'https://example.test/episode'))
                    cursor.execute('INSERT INTO transcript VALUES (%s,3)', (episode,))
                    for seq, content in enumerate(('Avant.', text, 'Après.')):
                        cursor.execute('INSERT INTO transcript_segment VALUES (%s,%s,%s,%s,NULL,%s)',
                                       (episode, seq, float(seq), float(seq + 1), content))
                    cursor.execute('INSERT INTO chunk_set VALUES (%s,%s,1)', (episode, cls.strategy))
                    cursor.execute('INSERT INTO semantic_chunk VALUES (%s,%s,0,1,1,1,2)', (episode, cls.strategy))
                    cursor.execute('INSERT INTO embedding_set VALUES (%s,%s,%s,1)', (episode, cls.strategy, models[0]))
                    cursor.execute('INSERT INTO chunk_embedding VALUES (%s,%s,%s,0,%s::vector)',
                                   (episode, cls.strategy, models[0], json.dumps(cls.vector)))
                    cursor.execute('INSERT INTO episode_embedding(episode_id,model_id,source_title,source_description,embedding) '
                                   'VALUES (%s,%s,%s,%s,%s::vector)',
                                   (episode, models[1], 'Titre', text, json.dumps(cls.vector)))
        cls.reader_dsn = cast(str, make_dsn('', user='radiofrance_mcp', password='mcp-test-password'))

    def setUp(self) -> None:
        self.encoder = Mock()
        self.encoder.encode_formatted.return_value = [self.vector]
        self.retrieval = Retrieval(self.encoder, self.reader_dsn)

    def test_search_deduplicates_and_preserves_evidence_identity(self) -> None:
        for source in ('transcripts', 'descriptions'):
            result = self.retrieval.search('histoire', source=source, show='histoire')
            self.assertEqual(len(result['results']), 1)
            hit = result['results'][0]
            self.assertEqual(hit['episode_id'], self.ids[1])
            self.assertEqual(hit['duplicate_count'], 2)
        self.encoder.encode_formatted.assert_called_with([QUERY_PREFIX + 'histoire'])
        passage = self.retrieval.passage(self.ids[1], self.strategy, 0, 1)
        self.assertEqual(passage['text'], 'Même passage.')
        self.assertEqual([s['text'] for s in passage['segments']], ['Avant.', 'Même passage.', 'Après.'])
        self.assertTrue(self.retrieval.episode(self.ids[1])['has_transcript'])

    def test_filters_and_unknown_identity(self) -> None:
        self.assertEqual(self.retrieval.search('x', date_from='2026-10-02')['results'], [])
        self.assertEqual(len(self.retrieval.search('x', date_to='2026-10-01')['results']), 2)
        self.assertEqual(self.retrieval.search('x', show="' OR true --")['results'], [])
        with self.assertRaisesRegex(ValueError, 'not found'):
            self.retrieval.episode('missing')
        with self.assertRaisesRegex(ValueError, 'not found'):
            self.retrieval.passage(self.ids[1], self.strategy, 42)

    def test_both_uses_one_encoding_and_real_filtered_corpora(self) -> None:
        result = self.retrieval.search('histoire', show='histoire',
                                       date_from='2026-10-01', date_to='2026-10-01')
        self.encoder.encode_formatted.assert_called_once_with([QUERY_PREFIX + 'histoire'])
        self.assertEqual(result['source'], 'both')
        self.assertEqual(len(result['results']), 1)
        hit = result['results'][0]
        self.assertEqual(hit['episode_id'], self.ids[1])
        self.assertEqual(hit['source'], 'both')
        self.assertEqual({entry['source'] for entry in hit['evidence']}, {'transcripts', 'descriptions'})
        self.assertEqual(hit['strategy_id'], self.strategy)
        self.assertIsNone(hit['similarity'])

    def test_role_cannot_write_even_with_readonly_disabled(self) -> None:
        connection = psycopg2.connect(self.reader_dsn)
        try:
            connection.set_session(readonly=False)
            with connection.cursor() as cursor:
                with self.assertRaises(psycopg2.ProgrammingError) as denied:
                    cursor.execute("UPDATE rf SET title = 'changed'")
                self.assertEqual(denied.exception.pgcode, '42501')
        finally:
            connection.close()
        with self.assertRaises(psycopg2.OperationalError):
            psycopg2.connect(make_dsn(self.reader_dsn, dbname='airflow'))


if __name__ == '__main__':
    unittest.main()
