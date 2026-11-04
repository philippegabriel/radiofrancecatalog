"""Verify static catalogue exports against the disposable application database."""

from contextlib import closing
import json
import os
from pathlib import Path
import tempfile
import unittest

import psycopg2

from radiofrance.jobs.catalogue import export_catalogue


@unittest.skipUnless('RF_TEST_DATABASE_DSN' in os.environ, 'use bash database/test/test-loader')
class CatalogueTests(unittest.TestCase):
    def setUp(self) -> None:
        """Reset only the disposable database; no production connection is used."""
        self.dsn = os.environ['RF_TEST_DATABASE_DSN']
        with closing(psycopg2.connect(self.dsn)) as connection, connection:
            with connection.cursor() as cursor:
                cursor.execute('TRUNCATE import_run, episode_identity, chunk_strategy, embedding_model RESTART IDENTITY CASCADE')

    def test_snapshot_pages_escape_metadata_and_include_unprocessed_episodes(self) -> None:
        """Export all shows without transcripts; preserve dates, ordering and safe links."""
        with closing(psycopg2.connect(self.dsn)) as connection, connection:
            with connection.cursor() as cursor:
                for number, (show, timestamp, url) in enumerate([
                    ('show-one', 100, 'https://example.org/?a=1&b=2'),
                    ('show-one', 200, 'javascript:alert(1)'), ('show-two', 300, '')]):
                    cursor.execute('INSERT INTO episode_identity(source_uuid,source_suffix) VALUES (%s,5) RETURNING id',
                        (f'11111111-1111-4111-8111-{number:012d}',))
                    identity = cursor.fetchone()[0]
                    cursor.execute('INSERT INTO shows(slug,url) VALUES (%s,%s) ON CONFLICT(slug) DO NOTHING',
                        (show, f'https://www.radiofrance.fr/franceculture/podcasts/{show}'))
                    cursor.execute('INSERT INTO episodes(episode_id,show_id,published_ts,title,description,web_url,podcast_title) '
                        'VALUES (%s,(SELECT id FROM shows WHERE slug=%s),%s,%s,%s,%s,%s)',
                        (identity, show, timestamp, f'Title {number} <script>', 'Été & <img src=x>', url, 'Misleading episode title'))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'site'
            result = export_catalogue(output, self.dsn)
            self.assertEqual(result, {'episode_count': 3, 'shows': {'show-one': 2, 'show-two': 1}})
            self.assertEqual(json.loads((output / 'manifest.json').read_text()), result)
            html = (output / 'show-one.html').read_text()
            self.assertLess(html.index('Title 1'), html.index('Title 0'))
            self.assertIn('1970-01-01', html)
            self.assertIn('Été &amp; &lt;img src=x&gt;', html)
            self.assertNotIn('javascript:', html)
            self.assertNotIn('<script>', html)
            self.assertIn('https://example.org/?a=1&amp;b=2', html)
            index = (output / 'index.html').read_text()
            self.assertIn('<a href="show-two.html">Episodes</a>', index)
            self.assertIn('<a href="show-one.html">Episodes</a>', index)
            self.assertNotIn('Misleading episode title', index)
            self.assertIn('<h1>show-one</h1>', html)
            self.assertNotIn('<th>Web URL</th>', html)
            self.assertIn('class="dataframe"', html)
            self.assertIn('https://www.radiofrance.fr/franceculture/podcasts/show-one', index)
            self.assertTrue((output / 'Logo_Radio_France.svg.webp').is_file())
            self.assertIn('#6495ED', (output / 'index.css').read_text())
            with self.assertRaises(FileExistsError):
                export_catalogue(output, self.dsn)
            self.assertEqual(len(list(output.parent.iterdir())), 1)

    def test_empty_database_produces_valid_index(self) -> None:
        """An empty catalogue still has an index and a zero-count manifest."""
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'site'
            self.assertEqual(export_catalogue(output, self.dsn), {'episode_count': 0, 'shows': {}})
            self.assertIn('0 épisodes', (output / 'index.html').read_text())
            self.assertEqual(export_catalogue(output, self.dsn, replace=True), {'episode_count': 0, 'shows': {}})
