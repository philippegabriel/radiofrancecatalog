"""Exercise migration and rollback on a populated disposable database."""

from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import psycopg2

import database.test.test_database_job as job_fixtures
from radiofrance.jobs.database import load

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless('RF_TEST_DATABASE_DSN' in os.environ, 'use bash database/test/test-loader')
class ShowIdMigrationTests(unittest.TestCase):
    def script(self, filename: str) -> subprocess.CompletedProcess[str]:
        """Apply a psql migration to the explicitly disposable database only."""
        psql = str(Path(os.environ['PG_BINDIR']) / 'psql') if 'PG_BINDIR' in os.environ else shutil.which('psql')
        assert psql is not None
        if filename == 'rollback_show_id.sql':
            result = self.script('rollback_catalogue_names.sql')
            self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([psql, '-X', '--dbname', self.dsn, '-f', str(ROOT / 'database' / filename)],
                              text=True, capture_output=True)
        if filename == 'migrate_show_id.sql' and result.returncode == 0:
            return self.script('rename_catalogue_tables.sql')
        return result

    def has_integer_key(self) -> bool:
        """Inspect the current test schema to ensure cleanup restores the new version."""
        with closing(psycopg2.connect(self.dsn)) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT EXISTS (SELECT FROM information_schema.columns WHERE table_name='episodes' AND column_name='show_id')")
            return bool(cursor.fetchone()[0])

    def restore_new_schema(self) -> None:
        """Restore a fresh-schema-compatible fixture after testing the old schema."""
        if not self.has_integer_key():
            with closing(psycopg2.connect(self.dsn)) as connection, connection:
                with connection.cursor() as cursor:
                    cursor.execute("UPDATE rf SET show='mecaniques-du-journalisme' WHERE show='missing-show'")
            result = self.script('migrate_show_id.sql')
            self.assertEqual(result.returncode, 0, result.stderr)

    def setUp(self) -> None:
        """Create real catalogue/transcript/vector rows, then restore the old text schema."""
        self.dsn = os.environ['RF_TEST_DATABASE_DSN']
        with closing(psycopg2.connect(self.dsn)) as connection, connection:
            with connection.cursor() as cursor:
                cursor.execute('TRUNCATE import_run, episode_identity, chunk_strategy, embedding_model RESTART IDENTITY CASCADE')
                cursor.execute('INSERT INTO shows(slug,url) VALUES (%s,%s) ON CONFLICT(slug) DO NOTHING',
                    ('mecaniques-du-journalisme', 'https://www.radiofrance.fr/franceculture/podcasts/mecaniques-du-journalisme'))
        with tempfile.TemporaryDirectory() as directory:
            load(job_fixtures.DatabaseJobTests().fixture(Path(directory)), self.dsn)
        with closing(psycopg2.connect(self.dsn)) as connection, connection.cursor() as cursor:
            cursor.execute('SELECT row_to_json(r) FROM episodes_external r')
            self.before = json.dumps(cursor.fetchall(), sort_keys=True)
            cursor.execute("SELECT 'episodes_external'::regclass::oid")
            self.view_oid = cursor.fetchone()[0]
        self.addCleanup(self.restore_new_schema)
        result = self.script('rollback_show_id.sql')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_migrate_rollback_preserves_data_view_and_relationships(self) -> None:
        """Preserve external rows/vectors, enforce FK constraints, and support reversal."""
        result = self.script('migrate_show_id.sql')
        self.assertEqual(result.returncode, 0, result.stderr)
        with closing(psycopg2.connect(self.dsn)) as connection, connection.cursor() as cursor:
            cursor.execute('SELECT row_to_json(r) FROM episodes_external r')
            self.assertEqual(json.dumps(cursor.fetchall(), sort_keys=True), self.before)
            cursor.execute("SELECT 'episodes_external'::regclass::oid, to_regclass('episodes_show_id_idx') IS NOT NULL")
            self.assertEqual(cursor.fetchone(), (self.view_oid, True))
            cursor.execute("SELECT to_regclass('public.rf'), to_regclass('public.rf_external'), to_regclass('public.show')")
            self.assertEqual(cursor.fetchone(), (None, None, None))
            cursor.execute("SELECT confrelid='episodes'::regclass FROM pg_constraint WHERE conrelid='transcript'::regclass AND contype='f'")
            self.assertTrue(cursor.fetchone()[0])
            cursor.execute('SELECT (SELECT count(*) FROM transcript_segment), (SELECT count(*) FROM chunk_embedding)')
            self.assertEqual(cursor.fetchone(), (1, 1))
            cursor.execute((ROOT / 'inventory.sql').read_text())
            self.assertEqual(cursor.fetchall(), [('mecaniques-du-journalisme', 1)])
        with self.assertRaises(psycopg2.IntegrityError):
            with closing(psycopg2.connect(self.dsn)) as connection, connection:
                with connection.cursor() as cursor:
                    cursor.execute('UPDATE episodes SET show_id=-1')
        result = self.script('rollback_show_id.sql')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.has_integer_key())
        with closing(psycopg2.connect(self.dsn)) as connection, connection.cursor() as cursor:
            cursor.execute('SELECT row_to_json(r) FROM rf_external r')
            self.assertEqual(json.dumps(cursor.fetchall(), sort_keys=True), self.before)

    def test_rename_roundtrip_preserves_relation_identity_and_sequence(self) -> None:
        """Rename existing relations without replacing data, grants or foreign keys."""
        result = self.script('migrate_show_id.sql')
        self.assertEqual(result.returncode, 0, result.stderr)
        with closing(psycopg2.connect(self.dsn)) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT oid, relacl::text FROM pg_class WHERE oid IN ('episodes'::regclass, 'shows'::regclass, 'episodes_external'::regclass, 'shows_id_seq'::regclass) ORDER BY oid")
            before = cursor.fetchall()
        for filename in ('rollback_catalogue_names.sql', 'rename_catalogue_tables.sql'):
            result = self.script(filename)
            self.assertEqual(result.returncode, 0, result.stderr)
        with closing(psycopg2.connect(self.dsn)) as connection, connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT oid, relacl::text FROM pg_class WHERE oid IN ('episodes'::regclass, 'shows'::regclass, 'episodes_external'::regclass, 'shows_id_seq'::regclass) ORDER BY oid")
                self.assertEqual(cursor.fetchall(), before)
                cursor.execute("SELECT pg_get_serial_sequence('shows','id')")
                self.assertEqual(cursor.fetchone()[0], 'public.shows_id_seq')
                cursor.execute("INSERT INTO shows(slug,url) VALUES ('rename-probe','https://example.org/rename-probe') RETURNING id")
                self.assertGreater(cursor.fetchone()[0], 0)
                cursor.execute("DELETE FROM shows WHERE slug='rename-probe'")

    def test_unknown_slug_aborts_without_partial_schema_changes(self) -> None:
        """Reject unmatched old text values and leave the old schema/data intact."""
        with closing(psycopg2.connect(self.dsn)) as connection, connection:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE rf SET show='missing-show'")
        result = self.script('migrate_show_id.sql')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unregistered rf.show slug', result.stderr)
        self.assertFalse(self.has_integer_key())
        with closing(psycopg2.connect(self.dsn)) as connection, connection.cursor() as cursor:
            cursor.execute('SELECT show FROM rf')
            self.assertEqual(cursor.fetchone()[0], 'missing-show')
            cursor.execute("SELECT to_regclass('public.show_set'), to_regclass('public.shows'), to_regclass('public.episodes_external')")
            self.assertEqual(cursor.fetchone(), (None, None, None))
