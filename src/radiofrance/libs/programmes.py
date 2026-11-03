"""Validate programme URLs and resolve a run's registered programme selection."""

from contextlib import closing
from importlib.resources import files
import re
from typing import TypedDict
from urllib.parse import urlsplit

import psycopg2


class Programme(TypedDict):
    slug: str
    url: str
    enabled: bool


class ProgrammeSelection(TypedDict):
    shows: list[str]
    all_enabled: bool
    new_show_url: list[str]


def show_url(url: str) -> Programme:
    """Accept only canonical HTTPS Radio France station/podcasts/slug URLs.

    Validate syntax locally; actual programme existence is checked by acquisition.
    Refuse credentials, alternate hosts, queries, fragments and ambiguous slugs.
    """
    parsed = urlsplit(url.strip())
    match = re.fullmatch(r'/([a-z0-9]+(?:-[a-z0-9]+)*)/podcasts/([a-z0-9]+(?:-[a-z0-9]+)*)/?', parsed.path)
    if (parsed.scheme != 'https' or parsed.netloc != 'www.radiofrance.fr'
            or parsed.query or parsed.fragment or not match):
        raise ValueError('Use https://www.radiofrance.fr/<station>/podcasts/<show>')
    return {'slug': match[2], 'url': 'https://www.radiofrance.fr' + parsed.path.rstrip('/'), 'enabled': True}


def resolve_programmes(selection: ProgrammeSelection, dsn: str = '') -> list[Programme]:
    """Register valid URLs and freeze a deduplicated selection in one DB transaction.

    Inputs come from the trigger form; outputs contain public slugs and URLs only.
    Unknown selected slugs or conflicting URLs roll back all registrations. All
    enabled includes newly registered programmes; explicitly selected disabled
    programmes can still be processed. This resolver runs at task execution time.
    """
    new = [show_url(url) for url in selection['new_show_url']]
    if not selection['shows'] and not selection['all_enabled'] and not new:
        raise ValueError('Select shows, enable all_enabled, or enter new_show_url')
    if any(not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', slug) for slug in selection['shows']):
        raise ValueError('Show selections must be programme slugs')
    sql = files('radiofrance.sql')
    with closing(psycopg2.connect(dsn, connect_timeout=10)) as connection, connection:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = '30s'")
            for item in new:
                cursor.execute(sql.joinpath('programme_register.sql').read_text(), (item['slug'], item['url']))
                registered = cursor.fetchone()
                if registered is None or registered[1] != item['url']:
                    raise ValueError(f'Programme slug already has another URL: {item["slug"]}')
            cursor.execute(sql.joinpath('programme_list.sql').read_text())
            programmes: list[Programme] = [{'slug': row[0], 'url': row[1], 'enabled': row[2]} for row in cursor.fetchall()]
            known = {item['slug']: item for item in programmes}
            selected = set(selection['shows']) | {item['slug'] for item in new}
            unknown = selected - known.keys()
            if unknown:
                raise ValueError(f'Unknown programme slugs: {sorted(unknown)}; provide their URLs to register them')
            if selection['all_enabled']:
                selected.update(item['slug'] for item in programmes if item['enabled'])
            if not selected:
                raise ValueError('No enabled programmes are registered')
            return [known[slug] for slug in sorted(selected)]


def registry_dsn() -> str:
    """Build the application DSN from Compose settings, or use native libpq defaults."""
    import os
    from psycopg2.extensions import make_dsn
    if not os.environ.get('RF_DATABASE_PASSWORD'):
        return ''
    return make_dsn(host=os.environ.get('RF_DATABASE_HOST', 'postgres'),
        port=os.environ.get('RF_DATABASE_PORT', '5432'), dbname='radiofrance',
        user='radiofrance', password=os.environ['RF_DATABASE_PASSWORD']) or ''


def read_show_choices(dsn: str | None = None) -> list[str]:
    """Read registry slugs for trigger-form suggestions with bounded read-only access.

    Called while parsing DAGs. Database outages or a missing registry log a generic
    warning and return no suggestions, so the DAG remains usable with explicit
    inputs or all_enabled. Runtime selection still validates the current registry.
    No URLs or credentials are written and no database records are modified.
    """
    import logging
    try:
        with closing(psycopg2.connect(registry_dsn() if dsn is None else dsn, connect_timeout=2,
                options='-c statement_timeout=1000')) as connection:
            connection.set_session(readonly=True)
            with connection, connection.cursor() as cursor:
                cursor.execute(files('radiofrance.sql').joinpath('programme_list.sql').read_text())
                return [row[0] for row in cursor.fetchall()]
    except psycopg2.Error:
        logging.getLogger(__name__).warning('Show dropdown unavailable: registry read failed; runtime selection remains available')
        return []
