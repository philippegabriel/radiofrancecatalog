# Compact PostgreSQL schema

This schema is for a fresh database, not an in-place upgrade. Keep existing
databases and source CSV files until the rebuilt database has been verified.
It requires PostgreSQL with pgvector 0.7 or newer (`halfvec` support).

`episode_identity` maps an identity-generated integer to a UUID and integer
suffix. The pair is unique; `episode_identity_external` reconstructs the
canonical Radio France ID. `rf.episode_id` references this integer and the
segment/chunk foreign keys reference `rf`, so catalogue records must exist
before transcript data can be imported. Internal keys are local to each rebuild;
use external IDs for files, assets, exports and cross-database references.

`rf_external` preserves the original ten-column catalogue CSV layout and textual
`id`. `rf_html`, catalogue exports and `make dumpids` use external IDs. Airflow's
temporary CSV catalogue remains independent of the persistent compact schema
and does not require pgvector.

## Rebuild from existing CSVs

Create a **new database** and use its connection details for every command:

```bash
createdb -U pgabriel radiofrance_compact
psql -X -v ON_ERROR_STOP=1 -U pgabriel -d radiofrance_compact -f schema.sql
psql -X -v ON_ERROR_STOP=1 -U pgabriel -d radiofrance_compact -f importcatalogue.sql < data/affaires-sensibles.csv
```

Repeat the catalogue import for all shows, then load transcript, chunk and
embedding CSVs in that order. For each episode, pass its external ID:

```bash
psql -X -v ON_ERROR_STOP=1 -U pgabriel -d radiofrance_compact -v episode_id=UUID_SUFFIX -f inserttranscript.sql < data/transcripts/UUID_SUFFIX.csv
psql -X -v ON_ERROR_STOP=1 -U pgabriel -d radiofrance_compact -v episode_id=UUID_SUFFIX -f insertchunks.sql < data/chunks/UUID_SUFFIX.csv
psql -X -v ON_ERROR_STOP=1 -U pgabriel -d radiofrance_compact -v episode_id=UUID_SUFFIX -f insertembeddings.sql < data/embeddings/UUID_SUFFIX.csv
```

CSV layouts stay unchanged, including the chunk text needed by the embedding
generator. Import staging tables are transaction-local: chunk text is discarded
after import, and embeddings are converted to `halfvec(384)`. Missing episodes,
wrong episode IDs, unreconstructible chunk text/boundaries and embeddings for missing chunks fail rather than being
silently skipped. Catalogue imports upsert metadata while preserving internal
keys; transcript/chunk imports replace one episode atomically.

Chunk boundaries are inclusive: `(episode_id, start_seq)` and
`(episode_id, end_seq)` are foreign keys to `transcript_segment(episode_id, seq)`.
A check requires `start_seq <= end_seq`. The foreign keys are deferred until
commit so transcript replacement can delete and reload segments in one
transaction. Replacement fails and rolls back if a referenced boundary is
missing at commit. These constraints enforce endpoints, not every intermediate
sequence value or the validity of existing embeddings after transcript edits.

`query.sql` ranks half-precision embeddings and reconstructs only the selected
chunks from their inclusive segment ranges, using the chunk generator's
whitespace rules. Half precision rounds vector components; compare representative
search rankings before switching production consumers.

When using Make, override `PSQL` to target the new database and remove/rebuild
only the import marker files for that rebuild. Existing `.db` markers indicate
previous imports and are not tied to a database name. `resetdb` is destructive;
do not use it against the original database.
Make imports transcripts before chunks and chunks before embeddings, including
with parallel execution. Catalogue import rules are restricted to the show
catalogues so they cannot consume transcript/chunk/embedding CSVs.

## Verification

Run `bash tests/test_compact_schema.sh` with PostgreSQL available and `PGUSER`
set to a role that can create databases and install pgvector. It creates and
removes an isolated test database, checks CSV round trips, integer references,
half-precision storage, reconstructed search text and failed-import rollback.
Run `venv/airflow/bin/pyright` after Python changes.
