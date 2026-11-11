"""Exercise description snapshots and metadata-change guards on a disposable DB."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from radiofrance.jobs import description_embedding

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


@unittest.skipUnless('RF_TEST_DATABASE_DSN' in os.environ, 'requires a disposable database')
class UnifiedEmbeddingLoadTests(unittest.TestCase):
    def setUp(self) -> None:
        """Prepare both artifact sets without writing anything to the application tables."""
        fixture = fixtures.DatabaseJobTests()
        fixture.setUp()
        self.dsn = fixture.dsn
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        fixture.fixture(self.root)
        source = self.root / 'downloads/run/manifest.json'
        output = self.root / 'description-run'
        description_embedding.run_embedding(source, output, self.root / 'unused', encode=vector)
        self.prepared = database.prepare(self.root / 'embeddings-run/manifest.json',
            self.root / 'chunks', self.root / 'downloads', output / 'manifest.json')

    def test_description_failure_rolls_back_the_entire_import(self) -> None:
        """A description write failure must leave no catalogue, transcript, vector or completion rows."""
        execute = database.execute

        def failing(cursor: database.Cursor, name: str, parameters: tuple[object, ...] = ()) -> None:
            """Simulate a database failure late in the single transaction."""
            if name == 'description_upsert':
                raise ValueError('Simulated description write failure')
            execute(cursor, name, parameters)

        with patch.object(database, 'execute', side_effect=failing), self.assertRaisesRegex(ValueError, 'Simulated'):
            database.load(self.prepared, self.dsn)
        with psycopg2.connect(self.dsn) as connection, connection.cursor() as cursor:
            cursor.execute('SELECT (SELECT count(*) FROM episodes), (SELECT count(*) FROM transcript), '
                '(SELECT count(*) FROM chunk_embedding), (SELECT count(*) FROM episode_embedding), '
                '(SELECT count(*) FROM import_run)')
            self.assertEqual(cursor.fetchone(), (0, 0, 0, 0, 0))
        database.load(self.prepared, self.dsn)
        database.verify_database(self.prepared, self.dsn)
        self.assertTrue(database.load(self.prepared, self.dsn)['already_imported'])
        with psycopg2.connect(self.dsn) as connection, connection.cursor() as cursor:
            cursor.execute('SELECT count(*) FROM episode_embedding')
            self.assertEqual(cursor.fetchone(), (1,))
            cursor.execute('DELETE FROM episode_embedding')
        with self.assertRaisesRegex(ValueError, 'missing or stale'):
            database.verify_database(self.prepared, self.dsn)

    def test_preflight_rejects_a_description_snapshot_from_another_download(self) -> None:
        """Both vector kinds must describe the same downloaded metadata, before opening a DB."""
        path = self.root / 'description-run/manifest.json'
        manifest = json.loads(path.read_text())
        manifest['descriptions'][0]['description'] = 'Unrelated source'
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'snapshot mismatch'):
            database.prepare(self.root / 'embeddings-run/manifest.json', self.root / 'chunks',
                self.root / 'downloads', path)
