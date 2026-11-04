# Catalogue table names

The already-applied integer show-ID migration is preserved. This follow-up only
renames its relations; it does not copy rows or regenerate embeddings.

| Current name | Final name |
| --- | --- |
| `shows` (episodes) | `episodes` |
| `show_external` | `episodes_external` |
| `show_set` (registry) | `shows` |
| `show_set_id_seq` | `shows_id_seq` |

`episodes.show_id` references `shows.id`. The `episodes_external` view exposes
readable show slugs. Matching constraint/index names are updated too; foreign
keys, relation identities, sequence ownership and existing privileges persist.
Fresh database initialization already creates these final names.

Finish active runs, rebuild consumers, and stop readers/writers before applying:

```bash
sudo docker build -f download/Dockerfile -t radiofrance-download:dev .
sudo docker compose build airflow database-loader mcp
sudo docker compose stop airflow mcp
sudo docker compose exec -T postgres psql -U airflow -d radiofrance \
  -v ON_ERROR_STOP=1 < database/rename_catalogue_tables.sql
```

Run this script once against the intermediate schema, not the original `rf`
schema. It runs transactionally with a five-second lock timeout. Keep your
PostgreSQL container and volume; no reset is needed. Verify in psql:

```sql
SELECT count(*) AS episodes, count(s.id) AS matched_shows
FROM episodes e LEFT JOIN shows s ON s.id=e.show_id;
SELECT show, count(*) FROM episodes_external GROUP BY show ORDER BY show;
SELECT id, slug FROM shows ORDER BY id;
SELECT pg_get_serial_sequence('shows', 'id');
```

Both counts must match, and per-show totals must be unchanged. Restart consumers:

```bash
sudo docker compose up -d --wait --wait-timeout 300 airflow
sudo docker compose --profile mcp up -d --wait --wait-timeout 300 mcp
```

To reverse only this naming change, stop consumers and apply
`database/rollback_catalogue_names.sql` using the same psql command. Restore the
intermediate application code/images before restarting. To reverse the earlier
integer-ID migration as well, apply its rollback only after this naming rollback.
