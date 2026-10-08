# Compact PostgreSQL schema

This schema is for a fresh database, not an in-place upgrade. Keep existing
databases and source JSON and CSV files until the rebuilt database has been verified.
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

## Rebuild from JSON and existing CSVs

Create a **new database** and use its connection details for every command:

```bash
createdb -U pgabriel radiofrance_compact
psql -X -v ON_ERROR_STOP=1 -U pgabriel -d radiofrance_compact -f schema.sql
psql -X -v ON_ERROR_STOP=1 -U pgabriel -d radiofrance_compact -f importcatalogue.sql < data/affaires-sensibles.csv
```

Repeat the catalogue import for all shows, then import transcript JSON, chunk CSVs and
embedding CSVs in that order. For each episode, pass its external ID:

```bash
RF_TRANSCRIPT_JSON=transcripts/UUID_SUFFIX.json psql -X -v ON_ERROR_STOP=1 -U pgabriel -d radiofrance_compact -v episode_id=UUID_SUFFIX -f inserttranscript.sql
psql -X -v ON_ERROR_STOP=1 -U pgabriel -d radiofrance_compact -v episode_id=UUID_SUFFIX -f insertchunks.sql < data/chunks/UUID_SUFFIX.csv
psql -X -v ON_ERROR_STOP=1 -U pgabriel -d radiofrance_compact -v episode_id=UUID_SUFFIX -f insertembeddings.sql < data/embeddings/UUID_SUFFIX.csv
```

Transcript import reads JSON directly; no transcript CSV files are written.
The `import_transcript_json(text, jsonb)` PostgreSQL function validates the source
and replaces that episode atomically, returning the inserted segment count.
The `psql` script reads the client-local file named by `RF_TRANSCRIPT_JSON`
and sends its contents as a quoted JSON value. No Python importer, database
driver, server filesystem access or intermediate transcript CSV is needed.
Install or update the function by running `schema.sql` before importing.
Each invocation imports one episode atomically.
Blank segments retain their original zero-based sequence numbers. PostgreSQL uses array ordinality minus one; chunk generation uses the source
array order through `transcript_json.py`, preserving the same numbering.

Chunk and embedding CSV layouts stay unchanged, including the chunk text needed by the embedding
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

## Independent chunk production

`python chunk_json_to_csv.py transcripts/UUID_SUFFIX.json > data/chunks/UUID_SUFFIX.csv`
reads JSON only and needs no database. Chunk CSV text is retained for embedding
production, then discarded on database import. Segment import and chunk generation
use the same source order and sequence numbering, so changing chunk boundaries or adding
overlap does not require reimporting transcript text.

`make tr` imports JSON directly. `make data/chunks/UUID_SUFFIX.csv` and
`make embeddings` produce artifacts without database access; `make chunks` imports
chunks after transcripts and `make uploadembeds` imports existing embedding CSVs after chunks; empty
transcripts need not have embedding artifacts.
Override `PYTHON` for your interpreter and `PSQL` for connection options, for example:

```bash
make tr PYTHON=venv/airflow/bin/python PSQL="psql -U pgabriel -d radiofrance_compact"
```

Existing transcript CSV files are no longer used. Import markers still require
removal when changing the target database or forcing a reimport.

The importer can also be called directly by SQL clients:

```sql
SELECT import_transcript_json(
    '12345678-1234-4234-8234-123456789abc_5',
    '{"transcript":[{"start":0,"end":1,"text":"Bonjour"}]}'::jsonb
);
```

The function uses ordinary caller permissions and built-in PostgreSQL JSON
support. It needs neither PL/Python nor access to files on the database server.
An empty transcript array removes all segments, subject to existing chunk boundary
constraints. Malformed JSON, invalid segment fields and unknown episodes fail
without retaining partial changes. Concurrent replacements of an episode are
serialized by a catalogue row lock. Transcript edits can invalidate existing
embeddings even when their boundary references remain valid.

Transcript and chunk imports resolve an episode through the existing UUID/suffix
unique index. Embedding joins also use UUID/suffix conditions; PostgreSQL chooses
their join plan based on the staging table estimates.
