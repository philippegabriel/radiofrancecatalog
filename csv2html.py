#!/usr/bin/env python3
"""Render a display CSV as an HTML table; CLI reads stdin and writes stdout."""
import sys
import pandas as pd


def render_csv(source, destination):
    pd.read_csv(source).to_html(escape=False, buf=destination)


if __name__ == '__main__':
    render_csv(sys.stdin, sys.stdout)
