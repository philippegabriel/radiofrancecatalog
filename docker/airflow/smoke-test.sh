#!/usr/bin/env bash
# Verify the dependency environment without credentials or a metadata database.
set -euo pipefail
python -c 'import sys; assert sys.version_info[:2] == (3, 14), sys.version'
python -c 'from importlib.metadata import version; assert version("apache-airflow") == "3.3.2"'
python -c 'import pandas, psycopg2, requests, graphviz; from airflow.sdk import Asset, AssetAlias, DAG, task'
pip check
aws --version
psql --version | grep -E '^psql \(PostgreSQL\) 17\.'
pg_dump --version | grep -E '^pg_dump \(PostgreSQL\) 17\.'
pg_restore --version | grep -E '^pg_restore \(PostgreSQL\) 17\.'
dot -V
