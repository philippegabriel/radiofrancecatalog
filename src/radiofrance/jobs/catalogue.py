"""Export the committed PostgreSQL catalogue as a self-contained static HTML site."""

import argparse
from contextlib import closing
from html import escape
from importlib.resources import files
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import TypedDict
from urllib.parse import urlsplit

import psycopg2


class Episode(TypedDict):
    episode_id: str
    show: str
    date: str
    title: str
    description: str
    url: str
    show_url: str


class CatalogueResult(TypedDict):
    episode_count: int
    shows: dict[str, int]


def page(title: str, body: str) -> str:
    """Wrap escaped content with the original catalogue stylesheet and local logo."""
    return ('<!doctype html>\n<html lang="fr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{escape(title)}</title><link rel="stylesheet" href="index.css"></head><body>'
        '<img src="Logo_Radio_France.svg.webp" alt="Radio France">'
        f'<h1>{escape(title)}</h1>{body}</body></html>\n')


def episode_row(episode: Episode, number: int) -> str:
    """Render numbered metadata columns, escaping text and restricting links to HTTP(S)."""
    title = escape(episode['title'])
    if urlsplit(episode['url']).scheme.lower() in ('http', 'https'):
        title = f'<a href="{escape(episode["url"], quote=True)}">{title}</a>'
    return (f'<tr data-episode-id="{escape(episode["episode_id"], quote=True)}">'
        f'<th scope="row">{number}</th><td>{escape(episode["date"])}</td>'
        f'<td>{title}</td>'
        f'<td>{escape(episode["description"])}</td></tr>\n')


def export_catalogue(output_dir: Path, dsn: str = '', replace: bool = False) -> CatalogueResult:
    """Read a consistent, read-only DB snapshot and atomically publish a new site directory.

    Use libpq environment variables unless a DSN is supplied. Export every show,
    including episodes without transcripts. Refuse to overwrite an existing run;
    a failed export leaves no partial site at the requested output path. With
    replace=True, refresh individual pages atomically and publish the completion
    manifest last; a failure during replacement requires rerunning the export.
    """
    if output_dir.exists() and not replace:
        raise FileExistsError(output_dir)
    with closing(psycopg2.connect(dsn, connect_timeout=10)) as connection:
        connection.set_session(readonly=True, isolation_level='REPEATABLE READ')
        with connection, connection.cursor() as cursor:
            cursor.execute(files('radiofrance.sql').joinpath('catalogue_html.sql').read_text())
            episodes: list[Episode] = [{'episode_id': row[0], 'show': row[1],
                'date': row[2], 'title': row[3], 'description': row[4], 'url': row[5], 'show_url': row[6]}
                for row in cursor.fetchall()]
    grouped: dict[str, list[Episode]] = {}
    for episode in episodes:
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', episode['show']):
            raise ValueError(f'Unsupported show slug: {episode["show"]}')
        grouped.setdefault(episode['show'], []).append(episode)
    result: CatalogueResult = {'episode_count': len(episodes),
        'shows': {show: len(rows) for show, rows in grouped.items()}}
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.catalogue-', dir=output_dir.parent))
    try:
        for asset in ('index.css', 'Logo_Radio_France.svg.webp'):
            (temporary / asset).write_bytes(files('radiofrance').joinpath('assets', asset).read_bytes())
        links: list[str] = []
        for show, rows in grouped.items():
            # podcast_title may contain an episode title, so it cannot name the show.
            title = show
            body = ('<p><a href="index.html">Tous les programmes</a></p><table border="1" class="dataframe">'
                f'<caption>{len(rows)} épisodes, du plus récent au plus ancien</caption>'
                '<thead><tr><th></th><th>date</th><th>title</th><th>description</th></tr></thead><tbody>'
                + ''.join(episode_row(row, number) for number, row in enumerate(rows)) + '</tbody></table>')
            (temporary / f'{show}.html').write_text(page(title, body), encoding='utf-8')
            show_link = escape(title)
            if urlsplit(rows[0]['show_url']).scheme.lower() in ('http', 'https'):
                show_link = f'<a href="{escape(rows[0]["show_url"], quote=True)}">{show_link}</a>'
            links.append(f'<tr><td>{show_link}</td><td><a href="{show}.html">Episodes</a> ({len(rows)})</td></tr>')
        (temporary / 'index.html').write_text(page('Catalogue Radio France',
            f'<p>{len(episodes)} épisodes</p><table border="1" class="dataframe">'
            '<thead><tr><th>Podcast URLs</th><th>Metadata index</th></tr></thead>'
            f'<tbody>{"".join(links)}</tbody></table>' ), encoding='utf-8')
        (temporary / 'manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temporary.chmod(0o755)
        if replace:
            output_dir.mkdir(parents=True, exist_ok=True)
            previous = output_dir / 'manifest.json'
            old = json.loads(previous.read_text())['shows'] if previous.is_file() else {}
            for generated in temporary.iterdir():
                if generated.name != 'manifest.json':
                    generated.replace(output_dir / generated.name)
            for show in old:
                if re.fullmatch(r'[a-z0-9][a-z0-9-]*', show) and show not in grouped:
                    (output_dir / f'{show}.html').unlink(missing_ok=True)
            (temporary / 'manifest.json').replace(previous)
        else:
            temporary.rename(output_dir)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return result


def main() -> None:
    """Export a new HTML directory using application PostgreSQL connection settings."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--replace', action='store_true', help='Refresh the shared catalogue, publishing its count manifest last')
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export_catalogue(args.output_dir, replace=args.replace), ensure_ascii=False))


if __name__ == '__main__':
    main()
