# Download stage

The download job uses PostgreSQL as the catalogue source of truth. It reuses
`radiofrance.fetch_episodes.fetch_all` and
`radiofrance.fetch_transcripts.fetch_transcript` for acquisition. No baseline CSV,
cumulative catalogue CSV, temporary catalogue table or CSV merge is involved.
The standalone episode CLI can still export CSV when explicitly requested.

## Cutoff and publication contract

Inputs are a supported show, the application database connection (standard
`PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` variables), an API key
and a new output directory. Initialize the application database before running.
The API key comes from `RADIOFRANCE_API_KEY` or an explicit runtime-mounted
`--api-key-file`; the file takes precedence. Secrets are never written into artifacts.

One read-only SQL statement reads `MAX(published_ts)`, the show's committed
row count and episode IDs at that timestamp. The connection closes before API
requests begin. An empty show uses cutoff zero and downloads from the beginning;
a database error fails the job and never causes an implicit full download.
The API request includes the cutoff second, then skips the already-committed
IDs at that boundary. New episodes sharing that second are retained.

Only the final database-loading stage commits new episodes. Failed acquisition,
chunking, embedding or an uncommitted load leaves the cutoff unchanged, so a
new pipeline run retries from the database's committed state. Successfully
committed episodes are skipped on the next run. This is incremental acquisition
by publication timestamp, not reconciliation of backdated episodes or older
missing transcripts. Per-show execution remains serialized by the pipeline DAG.

Outputs:

```text
OUTPUT/
  episodes.json                # new episode metadata only; [] for no new work
  transcripts/EPISODE_ID.json   # raw transcript JSON for each new episode
  manifest.json                # version 2; show, DB cutoff/count, episode count and paths
```

`episodes.json` is a JSON array with the existing catalogue fields. `published_ts`
is an integer; the other fields are strings. The metadata IDs must match the
manifest's transcript IDs exactly. Chunk CSVs and embedding NPY files belong to
later stages and keep their existing formats.

Publication is atomic. Missing transcripts or changed fixed-sample counts fail
the job; failed staging directories are removed. Existing completed directories
are never overwritten. Completed directories use mode 0755 and files 0644 so
Airflow's separate container user can verify them. No database writes, S3 calls
or Airflow asset events occur in this job.

## Running through Airflow

Follow [Airflow deployment](../airflow/DEPLOYMENT.md). The download child runs on
`RF_DOCKER_NETWORK` and connects to the application database using
`RF_DATABASE_HOST`, `RF_DATABASE_PORT` and the privately supplied
`RF_DATABASE_PASSWORD`. The API-key file is mounted read-only from `.OpenAPIKey`.
The Airflow container no longer needs a baseline mount.

After upgrading an existing checkout, rebuild the affected images and recreate
Airflow without deleting the database volume:

```bash
sudo docker build -f download/Dockerfile -t radiofrance-download:pilot .
sudo docker build -f chunking/Dockerfile -t radiofrance-chunk:pilot .
sudo docker compose build airflow database-loader
sudo docker compose up -d --wait --wait-timeout 300 airflow
```

Download manifests are now version 2. New download runs must be used with the
updated chunk and database images; old CSV-based download runs are not supported.
Embedding/chunk output formats and the application schema are unchanged.
No database migration, re-embedding or volume reset is required.

## Running directly

Set the libpq connection variables for the existing application database, keeping
the password out of command arguments. For host execution the default Compose
address is 127.0.0.1:55432. For a container use the Compose network and hostname
`postgres:5432`.

```bash
export PGHOST=127.0.0.1 PGPORT=55432 PGUSER=radiofrance PGDATABASE=radiofrance
read -rsp 'Database password: ' PGPASSWORD; export PGPASSWORD
venv/airflow/bin/python -m radiofrance.jobs.download \
  --show mecaniques-du-journalisme \
  --api-key-file .OpenAPIKey \
  --output-dir data/download-pilot/run-new
```

The default delay between transcript requests is one second. Use
`--transcript-delay` to change it. `PGPASSWORD` is consumed by libpq; the command
has no database-credential argument to appear in the process list.

## Fixed integration sample

`--episode-selection PATH` selects the committed episode IDs and transcript
segment counts from a fixture. It requires the selected show to be empty in the
application database. Metadata still comes from the live API; unavailable IDs or
changed segment counts fail the run without replacement. GitHub integration uses
its fresh disposable database and no catalogue CSV files.

## Validation

```bash
venv/airflow/bin/pyright
bash ci/run-tests
```

Offline tests cover database failure, empty state, same-timestamp IDs, retries,
JSON validation, atomic publication, container permissions, the full artifact
chain, database cutoff advancement on commit and a subsequent no-op download.
Live API and real-model validation use the manual pipeline-integration workflow.
