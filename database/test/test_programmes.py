"""Runtime show registration and selection checks on the disposable database."""

from contextlib import closing
import os
import unittest
from unittest.mock import patch

import psycopg2

from radiofrance.libs.programmes import ProgrammeSelection, show_url, resolve_programmes, read_show_choices


class ProgrammeUrlTests(unittest.TestCase):
    def test_only_canonical_show_urls_are_accepted(self) -> None:
        """Normalize trailing slashes and reject wrong origins, paths and query strings."""
        self.assertEqual(show_url(' https://www.radiofrance.fr/franceculture/podcasts/new-show/ ')['slug'], 'new-show')
        for url in ('http://www.radiofrance.fr/franceculture/podcasts/new-show',
                    'https://example.org/franceculture/podcasts/new-show',
                    'https://user@www.radiofrance.fr/franceculture/podcasts/new-show',
                    'https://www.radiofrance.fr/franceculture/podcasts/new-show?x=1',
                    'https://www.radiofrance.fr/franceculture/podcasts/new-show/episode'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                show_url(url)


@unittest.skipUnless('RF_TEST_DATABASE_DSN' in os.environ, 'use bash database/test/test-loader')
class ProgrammeRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        """Reset only the isolated test registry; episode records are independent."""
        self.dsn = os.environ['RF_TEST_DATABASE_DSN']
        with closing(psycopg2.connect(self.dsn)) as connection, connection:
            with connection.cursor() as cursor:
                cursor.execute('TRUNCATE shows CASCADE')
                cursor.execute('INSERT INTO shows(slug,url,enabled) VALUES (%s,%s,true),(%s,%s,false)',
                    ('known-show', 'https://www.radiofrance.fr/franceculture/podcasts/known-show',
                     'disabled-show', 'https://www.radiofrance.fr/franceinter/podcasts/disabled-show'))

    def test_dropdown_reads_current_registry_without_writing(self) -> None:
        """Return stored suggestions, including explicit disabled choices, without changing rows."""
        self.assertEqual(read_show_choices(self.dsn), ['disabled-show', 'known-show'])
        with patch('radiofrance.libs.programmes.psycopg2.connect', side_effect=psycopg2.OperationalError('private connection details')):
            with self.assertLogs('radiofrance.libs.programmes', level='WARNING') as captured:
                self.assertEqual(read_show_choices(self.dsn), [])
        self.assertNotIn('private connection details', ''.join(captured.output))

    def test_registration_selection_and_enabled_snapshot(self) -> None:
        """Deduplicate selected/new shows and reuse registrations without enabling disabled rows."""
        selection: ProgrammeSelection = {'shows': ['known-show', 'known-show'], 'all_enabled': True,
            'new_show_url': ['https://www.radiofrance.fr/franceculture/podcasts/new-show/']}
        resolved = resolve_programmes(selection, self.dsn)
        self.assertEqual([row['slug'] for row in resolved], ['known-show', 'new-show'])
        self.assertEqual(resolve_programmes(selection, self.dsn), resolved)
        explicit = resolve_programmes({'shows': ['disabled-show'], 'all_enabled': False,
            'new_show_url': []}, self.dsn)
        self.assertFalse(explicit[0]['enabled'])

    def test_invalid_selection_rolls_back_registrations(self) -> None:
        """Unknown slugs and slug/URL conflicts leave no partially registered shows."""
        with self.assertRaisesRegex(ValueError, 'Unknown programme'):
            resolve_programmes({'shows': ['unknown'], 'all_enabled': False,
                'new_show_url': ['https://www.radiofrance.fr/franceculture/podcasts/new-show']}, self.dsn)
        with self.assertRaisesRegex(ValueError, 'another URL'):
            resolve_programmes({'shows': [], 'all_enabled': False,
                'new_show_url': ['https://www.radiofrance.fr/franceinter/podcasts/known-show']}, self.dsn)
        resolved = resolve_programmes({'shows': [], 'all_enabled': True, 'new_show_url': []}, self.dsn)
        self.assertEqual([row['slug'] for row in resolved], ['known-show'])
        with self.assertRaisesRegex(ValueError, 'Select shows'):
            resolve_programmes({'shows': [], 'all_enabled': False, 'new_show_url': []}, self.dsn)
