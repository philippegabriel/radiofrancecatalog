#!/usr/bin/env bash
# Measure CI metadata; optionally retain a private dump for snapshot publication.
set -euo pipefail

phase=${1:?Supply a measurement label}
: "${PG_CONTAINER:?Supply the PostgreSQL service container ID}"
: "${RUNNER_TEMP:?Supply a temporary directory}"
: "${GITHUB_STEP_SUMMARY:?Supply the job summary path}"

report=$(mktemp "$RUNNER_TEMP/airflow-size-report.XXXXXX")
backup=${2:-$(mktemp "$RUNNER_TEMP/airflow-size-backup.XXXXXX")}
keep_backup=${2:-}
cleanup() {
    rm -f "$report"
    if [[ -z "$keep_backup" ]]; then rm -f "$backup"; fi
}
trap cleanup EXIT

{
    echo "### Airflow database size: $phase"
    echo
    echo '```text'
    docker exec -i "$PG_CONTAINER" psql -X -U airflow -d airflow -v ON_ERROR_STOP=1 <<'SQL'
SELECT pg_database_size(current_database()) AS database_bytes,
       pg_size_pretty(pg_database_size(current_database())) AS database_size;

SELECT relname AS table_name,
       pg_total_relation_size(relid) AS total_bytes,
       pg_table_size(relid) AS table_bytes,
       pg_indexes_size(relid) AS index_bytes
FROM pg_stat_user_tables
ORDER BY pg_total_relation_size(relid) DESC
LIMIT 15;
SQL
    echo '```'
    echo
    start=$(date +%s)
    docker exec "$PG_CONTAINER" pg_dump -U airflow -d airflow \
        --format=custom --compress=6 --no-owner --no-acl > "$backup"
    elapsed=$(( $(date +%s) - start ))
    echo "Compressed custom-format pg_dump (compression level 6): $(stat -c %s "$backup") bytes"
    echo
    echo "Backup creation: $elapsed seconds"
    echo
    if [[ -n "${2:-}" ]]; then
        echo 'Backup retained temporarily for private S3 snapshot publication.'
    else
        echo 'The backup is deleted after measurement; only these statistics are published.'
    fi
    echo
} > "$report"

cat "$report"
cat "$report" >> "$GITHUB_STEP_SUMMARY"

if [[ -n "${RF_METRICS_REPORT:-}" ]]; then cat "$report" >> "$RF_METRICS_REPORT"; fi
