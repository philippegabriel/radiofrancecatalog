import csv
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / 'dags'))
from radiofrance_catalogue.catalogue import FIELDS, cutoff, merge, catalogue_session, CatalogueRow

class CatalogueTests(unittest.TestCase):
    def test_cutoff_merge_and_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            d = Path(directory)
            def row(identifier: str, timestamp: int, title: str = 'test') -> CatalogueRow:
                return CatalogueRow(
                    show='affaires-sensibles', published_iso='',
                    published_ts=str(timestamp), title=title, description='',
                    web_url='', podcast_title='', podcast_url='', player_url='',
                    id=identifier,
                )
            def write(path: Path, rows: list[CatalogueRow]) -> None:
                with path.open('w', newline='') as stream:
                    writer = csv.DictWriter(stream, fieldnames=FIELDS)
                    writer.writeheader()
                    writer.writerows(rows)
            write(d/'old.csv', [row('a', 100), row('b', 200)])
            with catalogue_session(d/'old.csv', 'affaires-sensibles') as cursor:
                cursor.execute("SELECT relpersistence, relnamespace = pg_my_temp_schema() FROM pg_class WHERE oid = 'rf'::regclass")
                self.assertEqual(cursor.fetchone(), ('t', True))
                with catalogue_session(d/'old.csv', 'affaires-sensibles') as other:
                    other.execute("SELECT COUNT(*) FROM rf")
                    result = other.fetchone()
                    self.assertIsNotNone(result)
                    assert result is not None
                    self.assertEqual(result[0], 2)
            write(d/'updates.csv', [row('b', 200, 'changed'), row('c', 300)])
            self.assertEqual(cutoff(d/'old.csv', 'affaires-sensibles'), '1970-01-01T00:03:20+00')
            self.assertEqual(merge(d/'old.csv', d/'updates.csv', d/'merged.csv', 'affaires-sensibles'), 3)
            with (d/'merged.csv').open() as stream:
                self.assertEqual(list(csv.DictReader(stream))[1]['title'], 'changed')
            write(d/'empty.csv', [])
            self.assertEqual(cutoff(d/'empty.csv', 'affaires-sensibles'), '1900-01-01T00:00:00+00:00')
            write(d/'duplicates.csv', [row('a', 100), row('a', 200)])
            with self.assertRaises(ValueError):
                cutoff(d/'duplicates.csv', 'affaires-sensibles')
            with self.assertRaises(ValueError):
                cutoff(d/'old.csv', 'wrong-show')

if __name__ == '__main__':
    unittest.main()
