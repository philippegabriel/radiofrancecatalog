# PostgreSQL — first-start initialization

One PostgreSQL 17 container hosts `airflow` and `radiofrance` in the Compose
`radiofrance-db-volume` volume (the exact Docker name, without a project prefix).
The pgvector image runs the numbered SQL files in `database/init/` from
`/docker-entrypoint-initdb.d/` when the PostgreSQL data directory is empty.
There is no separate setup service or schema initialization command.
`10-radiofrance.sql` creates the database, role and pgvector;
`20-application-schema.sql` creates the application tables as the `radiofrance`
role. The loader described below only imports data.

## Fresh start

After normal Airflow configuration and image build, run from the project root:

```bash
sudo docker compose pull postgres
sudo docker compose up -d --wait --wait-timeout 300
```

PostgreSQL creates the Airflow database/admin from its environment, then runs the
initialization SQL to create RadioFrance and its application tables. Airflow initializes its own tables.
Initialization must succeed before the database is ready for Airflow's connection.

## Reset existing Airflow state

This deliberately discards both the PostgreSQL state (including any RadioFrance
data in this container) and Airflow's home state: history, UI login file, logs
and configuration. The following names correspond to this repository's unchanged
Compose project name, `radiofrance`:

```bash
sudo docker compose down --remove-orphans
sudo docker volume rm radiofrance-db-volume radiofrance-airflow-volume
sudo docker compose pull postgres
sudo docker compose up -d --wait --wait-timeout 300
```

The Airflow UI login is restored from `.env` on startup; follow the login instructions
in the [Airflow guide](../airflow/DEPLOYMENT.md). Preserve `.env`. These commands
do not remove host files under `data/download-pilot`, `data/chunk-pilot`,
`data/embedding-pilot`, or the Hugging Face cache. No host PostgreSQL service or
host data directory is modified. Other Docker volumes are not selected.

## Connections

| Purpose | Database | Login | default password |
| --- | --- | --- | --- |
| Airflow/bootstrap administrator | `airflow` | `airflow` | `airflow-dev` |
| Application | `radiofrance` | `radiofrance` | `radiofrance-dev` |

Set `RF_DATABASE_PASSWORD` in the Git-ignored `.env` before first startup to
customize the application password. Changing it after initialization does not
change an existing database password. These defaults are for development.

Host tools use `127.0.0.1:55432`; containers use `postgres:5432`. Override the
host port with `RF_DATABASE_PORT` in `.env` if needed.

```bash
psql -h 127.0.0.1 -p 55432 -U radiofrance -d radiofrance -W
```

The application login is not a superuser, cannot create roles/databases, and is
denied connection to Airflow's database. The existing bootstrap `airflow` login
remains a superuser for compatibility and can administer both databases.

## Persistence and limitations

Normal restarts/recreation preserve the volume; initialization does not rerun.
The SQL intentionally targets an empty installation rather than handling existing
roles and databases. If initialization fails partway through, fix the cause and
reset the disposable state again. Later application schema changes require
explicit migrations; they do not belong in first-start scripts alone.

Verify the extension and restart persistence:

```bash
sudo docker compose exec -T postgres psql -U airflow -d radiofrance \
  -c "SELECT current_database(), extversion FROM pg_extension WHERE extname = 'vector';"
sudo docker compose restart postgres
```

## Developer checks

```bash
bash -n database/test/test-setup
docker compose config --quiet
bash database/test/test-setup
```

The test requires installed PostgreSQL binaries and pgvector. It creates a private
temporary cluster under `/tmp`, applies the first-start SQL, restarts, and checks
persistence, quoted-password login, vector(256) access and denial of Airflow
connections. It removes only that temporary cluster. Set `PG_BINDIR` to select
installed server binaries. It never connects to the host service or Docker volume.
The native check uses installed PostgreSQL 18; Docker PostgreSQL 17 execution
still requires access to the Docker daemon.

References: [pgvector images](https://github.com/pgvector/pgvector#docker),
[PostgreSQL image initialization](https://hub.docker.com/_/postgres).

## Schema and loader — increment 2

This stage reads existing output; it does not rerun downloads, chunking or models.
Airflow integration is described in increment 3 below. Start the existing database, build
only the small loader image:

```bash
sudo docker compose up -d --wait --wait-timeout 300 postgres
sudo docker compose build database-loader
```

If the volume was initialized before the application-table script was added,
follow the reset section above once. After that, loading requires no reset.
The `database-loader` service is a one-off processing container under the `jobs`
profile; normal `compose up` does not start it. It has application credentials and
read-only artifact mounts, and installs only psycopg2, not model libraries.
Compose passes credentials from `RF_DATABASE_PASSWORD` (or the default),
so sudo does not need to preserve shell secrets.

Choose your completed embedding directory under `data/embedding-pilot`. For
example, the existing Airflow pilot can be loaded with:

```bash
sudo docker compose run --rm database-loader \
  --embedding-manifest /input/embeddings/airflow-ccd46aa7980644d8f007ab96f6761145425b9bd40170f8d66dedd7ccabdeeff0/manifest.json \
  --chunk-root /input/chunks --download-root /input/downloads
```

Replace the directory basename if you selected a different run. The loader uses
`source_run` and checksums to find its exact chunk and download ancestors in the
explicit roots. It does not scan for the newest files. Their original run
folders must still be available. Only downloaded transcripts listed in that
chain are loaded: the merged catalogue also includes baseline episodes which
may not yet have transcripts.

### Tables

| Tables | Meaning |
| --- | --- |
| `episode_identity`, `rf` | Compact integer identities and merged catalogue metadata |
| `transcript`, `transcript_segment` | Immutable source checksums and ordered transcript segments |
| `chunk_strategy`, `chunk_set`, `semantic_chunk` | Versioned chunking configuration, per-episode completion and segment boundaries |
| `embedding_model`, `embedding_set`, `chunk_embedding` | Versioned encoder configuration, per-episode completion and normalized `vector(256)` values |
| `import_run` | Completed import, source manifest hashes and counts |

Chunk text is reconstructed from transcript segments; preflight rejects CSV text
or timestamps that do not match those segments. Anonymous speaker labels remain
unchanged. Configurations are fingerprinted from canonical JSON, including their
implementation/runtime provenance. In this first implementation, the full encoder
configuration includes batch size and thread count, so changing either also
registers a separate configuration. Different strategies and models can coexist.
A successful empty transcript/chunk set has an explicit completion record.

The canonical application schema is `database/init/20-application-schema.sql`.
The former root `schema.sql` has been removed. Older E5/Make import scripts are
not maintained for this schema. First-start scripts target an empty volume and
do not update an existing database.

### Transaction and retry behavior

Preflight verifies the source chain, checksums, catalogue IDs, chunk boundaries,
text and vectors before database access. Each import uses one transaction and a
transaction-scoped advisory lock, serializing pilot imports. Catalogue rows are
upserted; immutable transcript, chunk and embedding records are reused only if
their source/artifact checksums match. A conflict or SQL failure rolls back all
changes, including catalogue updates and the completion record.

Repeat the exact same command safely: it reports `already_imported: true` after
checking completion provenance and per-episode counts. It does not reapply older
catalogue metadata. The loader rechecks source files before commit and never
modifies the input directories. Manual database edits are not covered by a full
byte-for-byte integrity audit; completion/checksum records assume loader-owned data.

The loader checks that application tables exist and rejects databases named other
than `radiofrance`. There is no migration registry in this initial implementation.
Future schema evolution will need an explicit update procedure; changing init SQL
does not modify an existing volume. Connections use standard `PG*` settings, with
optional `RF_DATABASE_DSN` for native execution; credentials are not command arguments.
For example, native commands use `venv/airflow/bin/python -m radiofrance.jobs.database` with
the same flags and host connection settings (`PGHOST=127.0.0.1`, `PGPORT=55432`).

### Inspect the imported data

Connect as shown above, then:

```sql
\dt
SELECT count(*) FROM rf;
SELECT count(*) FROM transcript;
SELECT count(*) FROM semantic_chunk;
SELECT count(*) FROM chunk_embedding;
SELECT show, catalogue_episode_count, transcript_count, chunk_count, completed_at
FROM import_run ORDER BY completed_at DESC;
```

Counts in `import_run` describe the selected import's scope, not necessarily new
rows inserted, because artifacts can be reused across runs/configurations. No
approximate vector index is added yet; retrieval benchmarking can guide that later.

### Loader validation

```bash
venv/airflow/bin/pyright
bash database/test/test-loader
RF_TEST_PILOT_MANIFEST=data/embedding-pilot/hf-cache-validation-001/manifest.json \
  bash database/test/test-loader
```

The test harness provisions a disposable PostgreSQL cluster, not your
container database. Tests cover first-start schema/repeat imports, conflicting immutable
data, transaction rollback, strategy/model coexistence, empty imports, changed
files, invalid lineage and unrecoverable chunk text. The optional pilot test
reads existing artifacts without model inference. The native PostgreSQL 18 check
loaded 90 catalogue entries, 12 transcripts and 253 vectors and repeated without
duplication. Docker image execution on PostgreSQL 17 remains a host-side check.

The pilot loads one run in memory and performs straightforward inserts in one
transaction. Corpus-scale batching and more granular concurrency can follow after
measurement; they are not hidden behind this initial implementation.

## Airflow loading — increment 3

No schema update or volume reset is needed. Rebuild the loader image to include
its read-only verifier and recreate Airflow to apply the new helper mounts and
application connection settings:

```bash
sudo docker compose build database-loader
sudo docker compose up -d --wait --wait-timeout 300 airflow
```

Enable and trigger **radiofrance_database** in the UI with:

```json
{"embedding_run": "airflow-ccd46aa7980644d8f007ab96f6761145425b9bd40170f8d66dedd7ccabdeeff0"}
```

Choose the basename of your completed directory in `data/embedding-pilot`.
Manually generated names such as `run-001` also work. The flow is:

```text
prepare_load → load_database → verify_database
```

Preparation validates the full artifact chain and returns only the selected
folder and manifest checksum through XCom. The loader runs on the Compose
network with all artifact roots mounted read-only and no model dependencies.
It checks the expected manifest checksum before writing. Its password is passed
through DockerOperator's private environment, not its command or XCom.

The final task rechecks the selected artifacts and opens a **read-only,
repeatable-read transaction** as `radiofrance`. It checks the committed import
record and source hashes, exact chunking/model configuration, catalogue membership,
and per-episode segment/chunk/vector counts and artifact fingerprints. Counts
are scoped to this import's configurations, allowing unrelated runs to coexist.
It never inserts, repairs or deletes database records. The existing limitation
on manual edits still applies: this is not a byte-for-byte vector integrity audit.

Compose supplies `RF_DATABASE_HOST=postgres`, `RF_DATABASE_PORT=5432` and
`RF_DATABASE_PASSWORD` to Airflow. The internal port stays 5432 even when the
published host port is customized. `RF_DOCKER_NETWORK` controls both the actual
Compose network name and the sibling worker's network (default `radiofrance_default`).
The loader receives only the application account, not Airflow's metadata credentials.

If you already imported the chosen run manually, the DAG verifies and reuses it
without duplicates. If the loader commits but the final task fails, clear only
`verify_database` to retry the read, or trigger the same selected run again.
Automatic retries remain disabled. You do not need a new embedding run or reset.

The CLI also supports independent read-only verification using the same input
arguments plus `--verify-only`. Orchestration additionally supplies
`--expected-manifest-sha256` to protect the selected run across task boundaries.

Offline DAG checks:

```bash
venv/airflow/bin/python -m unittest airflow/test/test_database.py
venv/airflow/bin/pyright
```

The database integration suite also checks missing completion, missing vectors,
changed configuration and enforcement of the read-only transaction. Its optional
253-vector pilot is verified after loading. DockerOperator execution must be
checked on a host with access to the Docker daemon.
