"""Build a complete public catalogue site from downloaded show CSVs."""
import csv
from io import StringIO
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / 'dags'))
from radiofrance_catalogue.catalogue import SHOWS, query_catalogue
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from csv2html import render_csv


def build(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    project = Path(__file__).resolve().parent.parent
    for name in ['index.html', 'index.css', 'Logo_Radio_France.svg.webp']:
        shutil.copyfile(project / name, destination / name)
    (destination / '.nojekyll').touch()
    for show in SHOWS:
        catalogue = source / f'{show}.csv'
        headers, rows = query_catalogue(catalogue, show, 'emithtml.sql')
        shutil.copyfile(catalogue, destination / catalogue.name)
        display = StringIO()
        writer = csv.writer(display)
        writer.writerow(headers)
        writer.writerows(rows)
        display.seek(0)
        with (destination / f'{show}.html').open('w', encoding='utf-8') as output:
            output.write('<meta charset="utf-8">\n<link rel="stylesheet" href="index.css">\n<img src="Logo_Radio_France.svg.webp" alt="Radio France">\n')
            render_csv(display, output)



if __name__ == '__main__':
    build(Path(sys.argv[1]), Path(sys.argv[2]))
