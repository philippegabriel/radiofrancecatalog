#!/usr/bin/env python3
"""Render display CSVs as HTML tables without third-party dependencies."""
import csv
from html import escape, unescape
import re
import sys
from typing import TextIO
from urllib.parse import urlsplit


def render_cell(value: str, column: str) -> str:
    """Escape text, allowing only the HTTP(S) links emitted in URL columns."""
    if column.casefold().endswith('url'):
        link = re.fullmatch(r'<a href="([^"]*)">([^<]*)</a>', value)
        if link:
            address, label = (unescape(part) for part in link.groups())
            if urlsplit(address).scheme.casefold() in ('http', 'https'):
                return f'<a href="{escape(address, quote=True)}">{escape(label)}</a>'
    return escape(value)


def render_csv(source: TextIO, destination: TextIO) -> None:
    """Write a styled HTML table from CSV, preserving the existing row index.

    Read headers and rows from source and write HTML to destination. Empty CSV
    fields remain empty. Escape ordinary text and preserve the SQL-generated
    HTTP(S) links in URL columns. Reject rows with inconsistent field counts.
    """
    rows = csv.reader(source)
    headers = next(rows, [])
    destination.write('<table border="1" class="dataframe">\n  <thead>\n    <tr>\n      <th></th>\n')
    for header in headers:
        destination.write(f'      <th>{escape(header)}</th>\n')
    destination.write('    </tr>\n  </thead>\n  <tbody>\n')
    for index, row in enumerate(rows):
        if len(row) != len(headers):
            raise ValueError(f'CSV row {rows.line_num} has {len(row)} fields; expected {len(headers)}')
        destination.write(f'    <tr>\n      <th>{index}</th>\n')
        for header, value in zip(headers, row, strict=True):
            destination.write(f'      <td>{render_cell(value, header)}</td>\n')
        destination.write('    </tr>\n')
    destination.write('  </tbody>\n</table>')


if __name__ == '__main__':
    render_csv(sys.stdin, sys.stdout)
