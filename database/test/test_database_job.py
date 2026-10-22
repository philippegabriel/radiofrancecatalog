"""Real PostgreSQL loader checks against an explicitly supplied disposable database."""

import csv
import json
import os
import sys
import subprocess
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import psycopg2

from radiofrance.jobs.chunk import run_chunking
import radiofrance.jobs.database as job
from radiofrance.jobs.embedding import run_embedding, write_json
from tests import test_embedding_job as fixtures
from radiofrance.libs.catalogue import FIELDS
from radiofrance.libs.types import Transcript


@unittest.skipUnless('RF_TEST_DATABASE_DSN' in os.environ, 'requires an explicit disposable database; use bash database/test/test-loader')
class DatabaseJobTests(unittest.TestCase):
    def setUp(self) -> None:
        """Clear only this explicitly disposable, preinitialized test database."""
        self.dsn = os.environ['RF_TEST_DATABASE_DSN']
        connection = psycopg2.connect(self.dsn)
        try:
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute('TRUNCATE import_run, episode_identity, chunk_strategy, embedding_model RESTART IDENTITY CASCADE')
        finally:
            connection.close()

    def fixture(self, root: Path, name: str = 'run', text: str = 'Texte français.', empty: bool = False) -> job.Prepared:
        """Build real download/chunk/embedding artifacts with deterministic test vectors."""
        downloads, chunks = root / 'downloads', root / 'chunks'
        download = downloads / name
        download.mkdir(parents=True)
        identity = '11111111-1111-4111-8111-111111111111_5'
        fields = ['mecaniques-du-journalisme', '2026-10-01T12:00:00+00:00', '1790856000',
                  text, 'description', 'https://example.test/episode', 'podcast', '', '', identity]
        for filename in ('catalogue.csv', 'updates.csv'):
            with (download / filename).open('w', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(FIELDS)
                if not empty:
                    writer.writerow(fields)
        transcript = download / f'{identity}.json'
        write_json(transcript, {'transcript': [{'start': 0.0, 'end': 1.0, 'text': text}]})
        write_json(download / 'manifest.json', {'version': 1, 'show': 'mecaniques-du-journalisme',
            'catalogue_episode_count': 0 if empty else 1, 'new_episode_count': 0 if empty else 1,
            'catalogue': {'path': 'catalogue.csv'},
            'updates': {'path': 'updates.csv'},
            'transcripts': [] if empty else [{'episode_id': identity, 'path': transcript.name}]})
        run_chunking(download / 'manifest.json', chunks / name)
        cache = root / f'cache-{name}'
        fixtures.EmbeddingJobTests().model(cache)
        output = root / f'embeddings-{name}'
        run_embedding(chunks / name / 'manifest.json', output, cache, encoder=fixtures.FakeEncoder())
        return job.prepare(output / 'manifest.json', chunks, downloads)

    def counts(self) -> tuple[int, int, int]:
        """Read committed completion, catalogue and vector counts from the test database."""
        connection = psycopg2.connect(self.dsn)
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT (SELECT count(*) FROM import_run), (SELECT count(*) FROM rf), (SELECT count(*) FROM chunk_embedding)')
                return cast(tuple[int, int, int], cursor.fetchone())
        finally:
            connection.close()

    def test_ten_episode_integration_assertions(self) -> None:
        """Run the artifact chain with fake inference, then exercise CI's real SQL assertions."""
        import radiofrance.jobs.download as download_job
        from radiofrance.libs.episode_selection import read_selection
        from tests.test_download_job import episode
        project = Path(__file__).resolve().parents[2]
        selection = read_selection(project / 'ci/fixtures/le-cours-de-l-histoire.json', 'le-cours-de-l-histoire')
        transcript: Transcript = {'transcript': [{'start': 0.0, 'end': 1.0, 'text': 'Texte français.'}]}
        rows = []
        for item in selection['episodes']:
            item['segment_count'] = 1
            row = episode(item['episode_id'], item['published_ts'])
            row['show'] = selection['show']
            row['published_iso'] = item['published_iso']
            rows.append(row)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / 'baseline.csv'
            with baseline.open('w', newline='') as stream:
                csv.writer(stream).writerow(FIELDS)
            chosen = root / 'selection.json'
            chosen.write_text(json.dumps(selection))
            download = root / 'downloads/run'
            with patch.object(download_job, 'fetch_all', return_value=iter(rows)), patch.object(download_job, 'fetch_transcript', return_value=transcript):
                download_job.run_download(selection['show'], baseline, download, 'test', 0, chosen)
            run_chunking(download / 'manifest.json', root / 'chunks/run')
            cache = root / 'cache'
            fixtures.EmbeddingJobTests().model(cache)
            run_embedding(root / 'chunks/run/manifest.json', root / 'embeddings', cache, encoder=fixtures.FakeEncoder())
            job.load(job.prepare(root / 'embeddings/manifest.json', root / 'chunks', root / 'downloads'), self.dsn)
            psql = str(Path(os.environ['PG_BINDIR']) / 'psql') if 'PG_BINDIR' in os.environ else shutil.which('psql')
            assert psql is not None
            arguments = [psql, self.dsn, '-v', 'ON_ERROR_STOP=1', '-v', 'fixture=' + json.dumps(selection)]
            sql = (project / 'ci/verify-integration.sql').read_text()
            checked = subprocess.run(arguments, input=sql, text=True, capture_output=True)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            connection = psycopg2.connect(self.dsn)
            try:
                with connection:
                    with connection.cursor() as cursor:
                        cursor.execute('DELETE FROM chunk_embedding WHERE episode_id=(SELECT min(episode_id) FROM chunk_embedding)')
            finally:
                connection.close()
            rejected = subprocess.run(arguments, input=sql, text=True, capture_output=True)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn('episode without embeddings', rejected.stderr)

    def test_load_repeat_and_configuration_coexistence(self) -> None:
        """Commit a complete run, repeat without duplication and keep model variants separately."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared = self.fixture(root)
            self.assertFalse(job.load(prepared, self.dsn)['already_imported'])
            self.assertTrue(job.load(prepared, self.dsn)['already_imported'])
            self.assertEqual(self.counts(), (1, 1, 1))
            path = root / 'embeddings-run/manifest.json'
            manifest = json.loads(path.read_text())
            manifest['configuration']['threads'] = 2
            write_json(path, manifest)
            path.parent.rename(root / 'embeddings-variant')
            path = root / 'embeddings-variant/manifest.json'
            variant = job.prepare(path, root / 'chunks', root / 'downloads')
            job.load(variant, self.dsn)
            self.assertEqual(self.counts(), (2, 1, 2))
            chunk_path = root / 'chunks/run/manifest.json'
            chunk_manifest = json.loads(chunk_path.read_text())
            chunk_manifest['strategy']['target_size'] = 2000
            write_json(chunk_path, chunk_manifest)
            write_json(path, manifest)
            path.parent.rename(root / 'embeddings-strategy')
            path = root / 'embeddings-strategy/manifest.json'
            job.load(job.prepare(path, root / 'chunks', root / 'downloads'), self.dsn)
            self.assertEqual(self.counts(), (3, 1, 3))

    def test_conflict_rolls_back_catalogue_and_completion(self) -> None:
        """A changed immutable transcript rolls back earlier catalogue updates in the same transaction."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = self.fixture(root)
            job.load(original, self.dsn)
            conflict = self.fixture(root, 'different', text='Changed transcript and catalogue title')
            with self.assertRaisesRegex(ValueError, 'immutable transcript'):
                job.load(conflict, self.dsn)
            self.assertEqual(self.counts(), (1, 1, 1))
            connection = psycopg2.connect(self.dsn)
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT title FROM rf')
                    self.assertEqual(cursor.fetchone(), ('Texte français.',))
            finally:
                connection.close()

    def test_database_failure_rolls_back_everything(self) -> None:
        """A vector constraint failure after inserting earlier records commits no partial data."""
        with tempfile.TemporaryDirectory() as directory:
            prepared = self.fixture(Path(directory))
            prepared['episodes'][0]['embeddings'][0]['embedding'] = [0.0] * 256
            with self.assertRaises(psycopg2.IntegrityError):
                job.load(prepared, self.dsn)
            self.assertEqual(self.counts(), (0, 0, 0))

    def test_empty_run_is_idempotent(self) -> None:
        """Record successful empty work explicitly and repeat without duplicate records."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared = self.fixture(root, empty=True)
            self.assertEqual(job.load(prepared, self.dsn)['chunk_count'], 0)
            self.assertEqual(self.counts(), (1, 0, 0))
            self.assertTrue(job.load(prepared, self.dsn)['already_imported'])
            self.assertEqual(self.counts(), (1, 0, 0))

    def test_verification_requires_committed_complete_import(self) -> None:
        """Verification never loads missing work or repairs missing vectors."""
        with tempfile.TemporaryDirectory() as directory:
            prepared = self.fixture(Path(directory))
            with self.assertRaisesRegex(ValueError, 'Committed import'):
                job.verify_database(prepared, self.dsn)
            self.assertEqual(self.counts(), (0, 0, 0))
            job.load(prepared, self.dsn)
            self.assertEqual(job.verify_database(prepared, self.dsn)['chunk_count'], 1)
            connection = psycopg2.connect(self.dsn)
            try:
                with connection:
                    with connection.cursor() as cursor:
                        cursor.execute('DELETE FROM chunk_embedding')
            finally:
                connection.close()
            with self.assertRaisesRegex(ValueError, 'row counts'):
                job.verify_database(prepared, self.dsn)
            self.assertEqual(self.counts(), (1, 1, 0))

    def test_verifier_transaction_forbids_writes(self) -> None:
        """PostgreSQL rejects writes even if verification accidentally attempts one."""
        with tempfile.TemporaryDirectory() as directory:
            prepared = self.fixture(Path(directory))
            job.load(prepared, self.dsn)
            def write(cursor: job.Cursor, source: job.Prepared) -> None:
                cursor.execute('DELETE FROM import_run')
            with patch.object(job, 'verify_committed', side_effect=write):
                with self.assertRaises(psycopg2.errors.ReadOnlySqlTransaction):
                    job.verify_database(prepared, self.dsn)
            self.assertEqual(self.counts(), (1, 1, 1))

    def test_verification_rejects_changed_configuration(self) -> None:
        """A completion record cannot conceal modified strategy metadata."""
        with tempfile.TemporaryDirectory() as directory:
            prepared = self.fixture(Path(directory))
            job.load(prepared, self.dsn)
            connection = psycopg2.connect(self.dsn)
            try:
                with connection:
                    with connection.cursor() as cursor:
                        cursor.execute("UPDATE chunk_strategy SET configuration=jsonb_set(configuration, '{target_size}', '12345')")
            finally:
                connection.close()
            with self.assertRaisesRegex(ValueError, 'configuration mismatch'):
                job.verify_database(prepared, self.dsn)
            self.assertEqual(self.counts(), (1, 1, 1))

    def test_bad_lineage_and_unrecoverable_text(self) -> None:
        """Reject a missing source run and CSV text that cannot be reconstructed."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared = self.fixture(root)
            path = root / 'chunks/run/manifest.json'
            manifest = json.loads(path.read_text())
            original = path.read_bytes()
            manifest['source_run'] = 'missing-run'
            write_json(path, manifest)
            write_json(path, manifest)
            with self.assertRaises(ValueError):
                job.prepare(root / 'embeddings-run/manifest.json', root / 'chunks', root / 'downloads')
            self.assertEqual(self.counts(), (0, 0, 0))
            self.assertEqual(prepared['embeddings']['chunk_count'], 1)
            path.write_bytes(original)
            manifest = json.loads(original)
            csv_path = root / 'chunks/run' / manifest['chunks'][0]['path']
            with csv_path.open(newline='') as stream:
                rows = list(csv.reader(stream))
            rows[0][6] = 'Text absent from transcript'
            with csv_path.open('w', newline='') as stream:
                csv.writer(stream).writerows(rows)
            write_json(path, manifest)
            embedding_path = root / 'embeddings-run/manifest.json'
            embedding_manifest = json.loads(embedding_path.read_text())
            write_json(embedding_path, embedding_manifest)
            with self.assertRaisesRegex(ValueError, 'text/timestamps'):
                job.prepare(embedding_path, root / 'chunks', root / 'downloads')

    @unittest.skipUnless(os.environ.get('RF_TEST_PILOT_MANIFEST'), 'optional representative pilot')
    def test_representative_pilot(self) -> None:
        """Load actual previously generated vectors without downloading or running an encoder."""
        prepared = job.prepare(Path(os.environ['RF_TEST_PILOT_MANIFEST']), Path('data/chunk-pilot'), Path('data/download-pilot'))
        result = job.load(prepared, self.dsn)
        self.assertEqual(self.counts(), (1, len(prepared['catalogue']), prepared['embeddings']['chunk_count']))
        self.assertTrue(job.load(prepared, self.dsn)['already_imported'])
        self.assertEqual(job.verify_database(prepared, self.dsn)['chunk_count'], prepared['embeddings']['chunk_count'])
        print('Representative pilot:', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
