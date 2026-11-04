> Historical migration: this script produces the intermediate names below.
> After applying it, use [the catalogue naming migration](CATALOGUE_NAMES_MIGRATION.md).
> Fresh initialization now creates the final names directly.

# Integer show identities

The combined migration changes these names and adds the integer relationship:

| Existing name | New name | Purpose |
| --- | --- | --- |
| `"show"` | `show_set` | Registered shows: integer identity ID and unique slug/URL |
| `rf` | `shows` | Episode rows; `show_id` references `show_set.id` |
| `rf_external` | `show_external` | Episode view exposing text show slugs |

`shows.show` is removed after its values are converted to `show_id`. Forms,
file paths and manifests continue to use slugs. `show_external.show` exposes the
slug through a join, retaining the view's existing columns, column order, relation
identity and grants. Search, cutoff and HTML queries use the renamed view.
The loader resolves each metadata slug to an existing registry ID when writing.

Fresh databases initialize `show_set` in `database/init/15-shows.sql`, before
`20-application-schema.sql` creates `shows`. Existing volumes require the combined
migration below. There is no intermediate migration to apply first: it starts
from the original `rf`/`"show"` schema that has not yet been migrated.

## Existing database upgrade

The read-only preflight on 3 November 2026 found 38,107 episodes and six show
slugs, all already registered. The local database has not been migrated by Codex.
Docker daemon access is unavailable to the agent, so image updates and the live
schema change must be coordinated on the host.

Finish any active pipeline runs before these steps. Keep the PostgreSQL container
and its volume; do not reset the database. Rebuild the affected images from the
project root:

```bash
sudo docker build -f download/Dockerfile -t radiofrance-download:dev .
sudo docker compose build airflow database-loader mcp
```

The chunk and embedding jobs do not query PostgreSQL, so this schema change does
not require rebuilding their images or generating any new embeddings.

Stop the application readers/writers while the schema changes:

```bash
sudo docker compose stop airflow mcp
```

Apply the migration once, using the current source file via stdin:

```bash
sudo docker compose exec -T postgres psql -U airflow -d radiofrance \
  -v ON_ERROR_STOP=1 < database/migrate_show_id.sql
```

It takes short exclusive locks on the registry and catalogue, rejects any unmatched
slug, renames the three relations, assigns IDs, backfills the foreign key, and replaces
the view definition before dropping
the old column. A five-second lock timeout prevents waiting indefinitely for a busy
application. Any error rolls back the whole transaction. The MCP role receives
SELECT on show_external if that role exists. Existing transcript/chunk/
embedding data is not rewritten. This is a one-time script, not a repeatable setup
command; do not rerun it after successful migration.

Verify from psql:

```sql
SELECT count(*) AS episodes, count(s.id) AS matched_shows
FROM shows r LEFT JOIN show_set s ON s.id = r.show_id;

SELECT show, count(*) FROM show_external GROUP BY show ORDER BY show;
SELECT id, slug FROM show_set ORDER BY id;
```

Both counts in the first query must match, and the per-show totals must match the
pre-upgrade catalogue. Start the updated Airflow container:

```bash
sudo docker compose up -d --wait --wait-timeout 300 airflow
```

If you use the MCP service, start its rebuilt container too:

```bash
sudo docker compose --profile mcp up -d --wait --wait-timeout 300 mcp
```

Normal show selection, incremental cutoffs, HTML export and query commands keep
their existing external interfaces. To query the base table manually, join the
registry, or use `show_external` to retrieve the text slug directly.

## Rollback

The migration and rollback have been exercised on a populated disposable database,
including an episode with transcript segments and an embedding. Tests also verify
that an unregistered old slug aborts without partial schema changes, the external
view OID/rows remain stable, and invalid show IDs are rejected by the foreign key.

If rollback is needed, stop application containers again and run:

```bash
sudo docker compose stop airflow mcp
sudo docker compose exec -T postgres psql -U airflow -d radiofrance \
  -v ON_ERROR_STOP=1 < database/rollback_show_id.sql
```

This reconstructs text slugs from the registry, restores the old primary key
and the original `rf`, `rf_external` and `"show"` names, and removes the integer
relationship in one transaction. Use the previous
application images/code with that old schema before restarting services. The
rollback does not restore previous Docker images for you. Reapplying the forward
migration after rollback assigns new internal show IDs while preserving slugs.
