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

The loader reads a completed embedding run and follows its `source_run` names to
the chunk and download directories. It imports the catalogue, transcripts, chunks
and vectors in a single transaction. It does not run models or initialize tables.

```bash
sudo docker compose build database-loader
sudo docker compose run --rm database-loader \
  --embedding-manifest /input/embeddings/RUN/manifest.json \
  --chunk-root /input/chunks --download-root /input/downloads
```

Replace `RUN` with the selected directory under `data/embedding-pilot`.
Compose supplies the application credentials. The service uses read-only artifact
mounts and installs no model libraries. Run names must be unique across imports;
repeat loading of the same completed run verifies it and creates no duplicates.
For different data or configuration, generate a new run directory.

| Tables | Purpose |
| --- | --- |
| `episode_identity`, `rf` | Integer identities, external episode IDs and catalogue metadata |
| `transcript`, `transcript_segment` | Transcript counts and ordered segments |
| `chunk_strategy`, `chunk_set`, `semantic_chunk` | Configuration JSON, counts and chunk boundaries |
| `embedding_model`, `embedding_set`, `chunk_embedding` | Model configuration and normalized 256D vectors |
| `import_run` | Completed embedding, chunk and download run names and counts |

Configurations are stored directly as unique JSON values with integer primary
keys. Chunking configurations include an explicit name, version and parameters;
embedding configurations include the pinned model revision, runtime and dimensions.
No file digests or source-code fingerprints are calculated or stored.

The proof-of-concept checks file presence, source run names, episode membership,
chunk text/boundaries, counts, vector dimensions and normalization. Foreign keys
protect the relationships. Existing transcript text is compared when reused;
existing chunk/vector sets are reused by episode, configuration and count.
Conflicting input or SQL errors roll back the import. This is not a continuous
byte-for-byte audit of existing database records.

Add `--verify-only` to the same command for read-only verification of a completed
import. It checks configurations, episode membership and row counts without
repairing or importing missing data.

## Airflow loading — increment 3

Enable `radiofrance_database` and select the embedding run's directory basename
in its `embedding_run` parameter. The flow is:

```text
prepare_load → load_database → verify_database
```

Preflight validates the selected chain and passes the run name through XCom.
The Docker task loads it using the application account, and the final task
verifies the committed result with a read-only transaction. Airflow metadata
credentials are not passed to the processing container.

Trigger `radiofrance_pipeline` for automatic download → chunk → embedding →
database handoffs. Stage DAGs remain available for manual use. If the loader
commits but verification fails, retry the verification task for that run.

## Existing populated database

The one-off [host database migration](MIGRATION.md) is complete. The destination
contains catalogue, transcript and chunk data; Gemma embedding generation is
separate. The source database and recovery files remain intact.

For a database created before checksum removal, apply this once as the
application role without resetting the volume:

```bash
psql -h 127.0.0.1 -p 55432 -U radiofrance -d radiofrance -W \
  -v ON_ERROR_STOP=1 -f database/remove_checksums.sql
```

Fresh installations already use the simplified schema. Rebuild all processing
images and Airflow after upgrading the code, then recreate Airflow with Compose.

## Loader tests

```bash
venv/airflow/bin/pyright
bash ci/run-tests
```

Tests use an isolated PostgreSQL cluster and deterministic vectors. They cover
transaction rollback, repeated imports, coexisting configurations, invalid vectors,
missing records and the Airflow container wiring. No model download is required.
