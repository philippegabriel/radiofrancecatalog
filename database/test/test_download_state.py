"""Real database watermark checks on the explicitly disposable test cluster."""
from contextlib import closing
import os
import unittest

import psycopg2

from radiofrance.libs.download_state import read_download_state


@unittest.skipUnless('RF_TEST_DATABASE_DSN' in os.environ, 'use bash database/test/test-loader')
class DownloadStateTests(unittest.TestCase):
    def setUp(self) -> None:
        """Reset only the disposable application database used by the test harness."""
        self.dsn = os.environ['RF_TEST_DATABASE_DSN']
        with closing(psycopg2.connect(self.dsn)) as connection, connection:
            with connection.cursor() as cursor:
                cursor.execute('TRUNCATE import_run, episode_identity, chunk_strategy, embedding_model RESTART IDENTITY CASCADE')

    def test_empty_show_and_latest_boundary_are_isolated(self) -> None:
        """MAX is per show, includes all tied identities, and is visible only after commit."""
        self.assertEqual(read_download_state('affaires-sensibles', self.dsn),
                         {'cutoff_timestamp': 0, 'episode_count': 0, 'existing_ids': []})
        with closing(psycopg2.connect(self.dsn)) as connection:
            with connection.cursor() as cursor:
                for index, (show, timestamp) in enumerate((('affaires-sensibles', 100),
                        ('affaires-sensibles', 200), ('affaires-sensibles', 200),
                        ('le-cours-de-l-histoire', 500))):
                    cursor.execute('INSERT INTO episode_identity(source_uuid,source_suffix) VALUES (%s,5) RETURNING id',
                                   (f'11111111-1111-4111-8111-{index:012d}',))
                    identity = cursor.fetchone()[0]
                    cursor.execute('INSERT INTO episodes(episode_id,show_id,published_ts) VALUES (%s,(SELECT id FROM shows WHERE slug=%s),%s)', (identity, show, timestamp))
            self.assertEqual(read_download_state('affaires-sensibles', self.dsn)['cutoff_timestamp'], 0)
            connection.commit()
        state = read_download_state('affaires-sensibles', self.dsn)
        self.assertEqual(state['cutoff_timestamp'], 200)
        self.assertEqual(state['episode_count'], 3)
        self.assertEqual(state['existing_ids'], ['11111111-1111-4111-8111-000000000001_5',
                                                '11111111-1111-4111-8111-000000000002_5'])
        historical = read_download_state('affaires-sensibles', self.dsn, include_all_ids=True)
        self.assertEqual(len(historical['existing_ids']), 3)
        self.assertIn('11111111-1111-4111-8111-000000000000_5', historical['existing_ids'])
        self.assertEqual(historical['cutoff_timestamp'], 200)
        self.assertEqual(read_download_state('le-cours-de-l-histoire', self.dsn)['cutoff_timestamp'], 500)
        self.assertEqual(read_download_state("' OR true --", self.dsn)['episode_count'], 0)


if __name__ == '__main__':
    unittest.main()
