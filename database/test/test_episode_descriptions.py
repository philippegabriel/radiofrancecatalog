"""Exercise description snapshots and metadata-change guards on a disposable DB."""

import json
import os
from pathlib import Path
import tempfile
import unittest

import psycopg2

from radiofrance.jobs import database, episode_descriptions as job
import test_database_job as fixtures
from tests.test_episode_descriptions import vector


@unittest.skipUnless('RF_TEST_DATABASE_DSN' in os.environ, 'requires a disposable database')
class DescriptionDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        """Use a real download/chunk fixture while keeping all tests isolated."""
        fixture = fixtures.DatabaseJobTests()
        fixture.setUp()
        self.dsn = fixture.dsn
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        database.load(fixture.fixture(self.root), self.dsn)

    def sample(self, name: str) -> Path:
        """Export current DB metadata and publish deterministic description vectors."""
        path = self.root / (name + '.json')
        selection = job.export_selection(self.dsn, path)
        self.assertEqual(len(selection['episodes']), 1)
        directory = self.root / name
        job.run_embedding(path, directory, self.root / 'unused', encode=vector)
        return directory

    def test_load_resume_and_metadata_update(self) -> None:
        """Description loading is idempotent and accepts a newly exported metadata update."""
        first = self.sample('first')
        self.assertEqual(job.load(first, self.dsn)['written'], 1)
        self.assertEqual(job.load(first, self.dsn)['unchanged'], 1)
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE episodes SET title='New title'")
        with self.assertRaisesRegex(ValueError, 'metadata changed'):
            job.load(first, self.dsn)
        second = self.sample('second')
        self.assertEqual(job.load(second, self.dsn)['written'], 1)
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT source_title FROM episode_embedding')
                self.assertEqual(cursor.fetchone(), ('New title',))
                cursor.execute('SELECT count(*) FROM chunk_embedding')
                self.assertEqual(cursor.fetchone(), (1,))

    def test_stale_metadata_and_conflicting_vector(self) -> None:
        """Reject stale snapshots or conflicting vectors without modifying existing data."""
        directory = self.sample('first')
        manifest = json.loads((directory / 'manifest.json').read_text())
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE episodes SET description='Changed'")
        with self.assertRaisesRegex(ValueError, 'metadata changed'):
            job.load(directory, self.dsn)
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT count(*) FROM episode_embedding')
                self.assertEqual(cursor.fetchone(), (0,))
                cursor.execute('UPDATE episodes SET description=%s', (manifest['episodes'][0]['description'],))
        job.load(directory, self.dsn)
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE episode_embedding SET embedding=('[0,1,' || repeat('0,',253) || '0]')::vector")
        with self.assertRaisesRegex(ValueError, 'Conflicting vector'):
            job.load(directory, self.dsn)
