# Radio France Airflow

Airflow 3.3.2 is installed in `venv/airflow` using Python 3.14 and the official versioned constraints. From the repository root:

```bash
airflow/run standalone
```

Open http://localhost:8080 and use the credentials printed by standalone. Stop with Ctrl-C. No Radio France processing DAGs have been added yet.

The launcher works from any directory and accepts all Airflow CLI commands:

```bash
airflow/run db check
airflow/run dags list
```

`airflow.cfg` contains project installation settings. The metadata database is local PostgreSQL database `airflow`, using Unix socket `/var/run/postgresql` and role `pgabriel` (peer authentication). `LocalExecutor` allows up to four tasks concurrently. Example DAGs are disabled. DAGs belong in `airflow/dags/`.

Runtime state and logs live in `data/airflow/`. The launcher generates persistent encryption/signing keys in `data/airflow/secrets.env` with restricted permissions. Keep this file when restoring a metadata backup; it is excluded from Git. Standalone is intended for local development. S3 backups and remote logging are not configured yet.

To reinstall:

```bash
python3.14 -m venv venv/airflow
venv/airflow/bin/python -m pip install -r airflow/requirements.txt
airflow/run db migrate
```

The Airflow environment is separate from the existing transcript/embedding environment.

## Background service

The systemd user service runs the local development installation in the background:

```bash
systemctl --user start radiofrance-airflow
systemctl --user stop radiofrance-airflow
systemctl --user restart radiofrance-airflow
systemctl --user status radiofrance-airflow
journalctl --user -u radiofrance-airflow -f
```

Open http://localhost:8080. The admin password is stored in
`data/airflow/simple_auth_manager_passwords.json.generated`.
Do not run `airflow/run standalone` concurrently with the service.
PostgreSQL must be running before starting Airflow.

To install or update the service from the repository root:

```bash
mkdir -p ~/.config/systemd/user
cp airflow/radiofrance-airflow.service ~/.config/systemd/user/
systemctl --user daemon-reload
```

Optional automatic startup when your user session starts:

```bash
systemctl --user enable radiofrance-airflow
# Disable automatic startup:
systemctl --user disable radiofrance-airflow
```

Automatic startup is not enabled by default. This is a user service; it follows
user-session lifecycle rather than providing an always-on system deployment.

## Run the first test DAG

With the service running, open http://localhost:8080 and find `radiofrance_test`
in the DAG list (discovery can take about a minute). Trigger it manually using
the play/trigger button. It has no schedule and does not process transcripts or
import data into the Radio France database.

Its `write_greeting` task writes `data/airflow/test-output/hello.json` and emits
an event for the `radiofrance_test_output` asset. Inspect the task logs and the
asset event in the UI. Each run overwrites the greeting file and records a new
event.

## Download DAG

`radiofrance_download` is manually triggered and accepts a `show` parameter
(default: `affaires-sensibles`). The six programmes in `psql.mk` are supported.

```bash
airflow/run dags trigger radiofrance_download --conf '{"show":"affaires-sensibles"}'
```

Tasks retrieve `s3://<bucket>/data/episodes/<show>.csv`, register its episode
references in Airflow, derive the maximum publication timestamp from their asset
events, invoke `rf_dump.py`, merge by episode ID, and publish the
validated cumulative CSV back to the same S3 key. A missing baseline fails the
run; new shows must first be configured and supplied with a baseline CSV (a
header-only CSV supports a full initial fetch). This retains the existing strict
cutoff behavior; late/backdated episodes and metadata corrections older than the
cutoff need a future overlap or full refresh policy.

Each episode reference has URI
`x-radiofrance://episodes/<show>/<episode-id>` and records the show, episode ID,
publication timestamp and ISO publication date as asset/event metadata.
`record_episodes` seeds references from the S3 baseline, emitting events only for
unregistered episode/date pairs. `derive_cutoff` reads the selected show's
events using the supported `inlet_events` API and takes the maximum publication
timestamp, independent of registration order. An empty catalogue starts at
1900-01-01 UTC. No direct metadata SQL or radiofrance scratch tables are needed
for this cutoff. Baseline registration reconciles episode metadata with the authoritative S3 catalogue.

After catalogue publication, `record_new_episodes` records references from
`updates.csv` before transcript downloading. These reference events establish
expected work; they do not claim that transcript/chunk/embedding files exist.

`download_transcripts` reads episode IDs from this
run's `updates.csv`, fetches each transcript using the shared API helper in
`fetchtranscript.py`, and uploads its JSON to
`data/transcripts/<episode-id>.json`. It records one Airflow asset event per
transcript with the show, episode ID, and content hash. The occurrence suffix
is removed only for the API request, not for the stored filename.

Local JSONs default to the existing project `transcripts/` directory;
`RF_TRANSCRIPT_DIR` overrides it. There is no batch limit, S3 cache scan,
backlog processing, or automatic transcript retry. Downloads are expected to
succeed; an error, including an unavailable transcript, fails the task.
The cumulative catalogue is already published at that point, so a subsequent
incremental run will not automatically recover missed transcripts. A separate
manually invoked recovery DAG can be added later. No transcript CSVs, chunks or
embeddings are generated here.

S3 CSVs are authoritative. Airflow still requires its own metadata backend.
HTML is built in the Pages workflow using shared SQL and rendering code with
session-private temporary tables in `radiofrance`; permanent tables are untouched.
Transcript transformations and permanent PostgreSQL imports belong to future
Transform and Import DAGs.

AWS CLI must be on PATH. Set `RF_S3_BUCKET` and optionally `RF_S3_REGION`
(default: `eu-west-2`). Local runs can use the ignored `aws/cache-config.json`
as a fallback; this file must not be committed. Credentials use AWS's normal
credential chain, allowing local SSO, GitHub credentials or an EC2 instance role.
Locally refresh expired credentials with `aws sso login --profile default`.
Worker identities require read/write access to `data/episodes/` and write
access to `data/transcripts/*.json`.

The Radio France key comes from `RADIOFRANCE_API_KEY`, or the file named by
`RF_API_KEY_FILE` (default: project `.OpenAPIKey`). It is passed via environment,
not command arguments. `RF_DOWNLOAD_PYTHON` optionally selects another Python
with requests installed. Default: `venv/airflow/bin/python`.

Run files are retained under `data/airflow/downloads/<run-hash>/<show>/` for
inspection. They include the downloaded baseline,
fetched updates and merged output. A task retry replaces its own outputs. Only
one run is active per installation; do not publish the same show concurrently
from different Airflow installations. S3 publication is not a cross-installation
lock. Back up authoritative CSVs or enable S3 versioning before relying on
historical recovery.

The existing S3 catalogues under `data/episodes/` are the baselines. The separate `fc_fictions.csv` uses a different schema and is not
included.

Local validation (requires catalogue PostgreSQL):

```bash
venv/airflow/bin/python -m unittest discover -s airflow -p 'test_catalogue.py'
venv/airflow/bin/python -m unittest discover -s airflow -p 'test_transcripts.py'
venv/airflow/bin/python -m unittest discover -s airflow -p 'test_episodes.py'
```

## Register DAG

`radiofrance_register` is manually invoked to initialize or reconcile the
current Airflow installation's inventory from S3. It never writes S3 objects.

```bash
# Run through the scheduler:
airflow/run dags trigger radiofrance_register

# Or run synchronously without a persistent scheduler:
airflow/run dags test radiofrance_register \
  --dagfile-path "$PWD/airflow/dags/radiofrance_register.py"
```

`register_catalogues` reads the six supported catalogue CSVs and uses the same
episode-reference registration logic as the Download DAG. Publication dates
are retained on episode references for cutoff calculation. It also registers
the catalogue assets if they have no previous events.

`register_files` runs once for each artifact type, listing all pages of S3 keys:

| Artifact | Prefix | Files |
| --- | --- | --- |
| Transcripts | `data/transcripts/` | `.json` |
| Chunks | `data/chunks/` | `.csv` |
| Embeddings | `data/embeddings/` | `.csv` |

Folder markers and other extensions are ignored. Artifact bodies are not
downloaded. Existing-file events use `status=discovered_existing` and record
artifact type, episode basename, and size. Chunks record the inferred transcript
source URI; embeddings record the inferred chunk source URI. These links do not
assert source availability. No production timestamps or unknown model versions
are invented. The event timestamp represents registration.

Previously recorded URIs are skipped, including transcripts published by the
Download DAG, so rerunning Register does not create duplicate availability events.
The immutable, retained-S3 assumption makes those events sufficient for our
inventory. Episode references represent expected work; artifact events represent
observed availability. Repair and transformation DAGs are still separate future
work.

Use the same AWS/environment configuration as Download. Credentials require
`s3:GetObject` for `data/episodes/*.csv` and `s3:ListBucket` for the three artifact
prefixes. Local SSO credentials support this. The separate GitHub Register workflow
initializes the snapshot used by Download. Registration
affects the current metadata database; the persistent local database is separate
from GitHub's restored or rebuilt database.

Offline registration tests:

```bash
venv/airflow/bin/python -m unittest discover -s airflow -p 'test_register.py'
```

## GitHub Actions

`.github/workflows/airflow-download.yml` adds **Airflow Download** with a manual
Run workflow action and show selector. Commit/push the workflow, DAG and helper,
launcher, requirements and Airflow configuration before using it. Do not push
local AWS configuration or policies containing bucket identifiers.

Configure repository settings:

- Secret `RF_S3_BUCKET`: private bucket name (without `s3://` or a path).
- Secret `OPENAPIKEY`: existing Radio France API key.
- Secret `AIRFLOW_FERNET_KEY`: a stable Airflow encryption key. Generate one with
  `venv/airflow/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`.
  Keep the same key across restores; do not commit it.
- Secret `AWS_AIRFLOW_ROLE_ARN`: AWS IAM role trusted by GitHub OIDC for this
  repository and the permitted branch. Allow `s3:GetObject`/`s3:PutObject` on
  `data/episodes/*.csv`, plus
  `s3:PutObject` on `data/transcripts/*.json` and `s3:ListBucket` limited to
  the catalogue, transcript, chunk and embedding prefixes. OIDC trust should restrict the
  repository/ref and audience `sts.amazonaws.com`.

### Register and Download snapshots

Both workflows are manual and share the same concurrency group, preventing
Register and Download from overwriting each other's snapshots. Select main or v2;
each branch has an independent snapshot namespace. Airflow is pinned in
requirements.txt and PostgreSQL is pinned to major version 17.

**Airflow Register** (`.github/workflows/airflow-register.yml`) bootstraps or
rebuilds metadata from authoritative S3 files. It initializes an empty database
with `airflow db migrate`, downloads the six catalogue CSVs, lists all artifact
prefixes, runs the Register DAG, and publishes a compressed PostgreSQL snapshot.
Run it once before the first Download for each branch/version. A rebuild replaces
the current snapshot's history with the reconstructed inventory.

**Airflow Download** restores the matching snapshot into an empty database and
runs `airflow db check-migrations --migration-wait-timeout 0`. This checks
compatibility without running migrations. Missing/incompatible snapshots fail
clearly; the workflow never silently creates an empty inventory. It then runs
the Download DAG, computes cutoff from maximum episode publication timestamps
in asset metadata, and publishes an updated snapshot. Baseline episode references
are reconciled with the existing catalogue; new references and transcript events
are recorded incrementally. The database remains local and persistent for local
DAG runs; GitHub snapshots do not replace the local database.

After a Download task failure, the workflow still attempts to snapshot completed
work and its run history, provided restore and schema checks passed. The failure
remains visible and Pages deployment does not run. Cutoff follows catalogue
progress; missing transcripts are for the future manually invoked repair DAG.
Cancellation or backup/upload failure may leave the previous snapshot current;
Register can rebuild inventory from S3 if necessary.

Snapshots live under
`data/airflow/github-download/<branch>/airflow-<version>/postgres-<major>/`.
Each run writes `snapshots/<run-id>-<attempt>.dump` first, then publishes
`current.json` as a single-object pointer. The manifest records format version,
Airflow version, PostgreSQL major version, checksum, and compressed byte count.
Restore verifies those values before invoking pg_restore. Earlier immutable dumps
are retained for rollback; they are not automatically deleted. Lifecycle cleanup
can be configured later. IAM must allow GetObject and PutObject on
`data/airflow/github-download/*`, not just the legacy `metadata.dump` key.
The bucket is supplied only through RF_S3_BUCKET; no bucket identifier is committed.

Both workflows report S3 operations, DAG timings, database sizes, the largest
fifteen tables/indexes, compressed backup size, and backup creation time.
Download additionally reports restore and schema-check timings. Statistics appear
in the job log and summary, and a small `*-statistics-*` artifact (seven-day
retention). Private dumps/manifests and raw Airflow logs are never public artifacts.
Measurement dumps are deleted unless immediately reused for S3 publication;
published local dumps are then deleted by the snapshot helper.

### Manual Airflow upgrade

Download never runs `airflow db migrate`. On an upgrade, retain the old-version
snapshot, restore a copy into an isolated database using the old version's
manifest/key, install the new pinned Airflow version, run `airflow db migrate`
explicitly, and verify schema compatibility and DAG execution. Back up and publish
the migrated database under the new version namespace using snapshot.py save.
Keep the same AIRFLOW_FERNET_KEY. Rolling back uses the old Airflow version with
its old snapshot. Alternatively, run Register on the new version to reconstruct
inventory from S3, accepting the loss of old task/run history.

The workflow retrieves all six S3 catalogue baselines once before running the
DAG. The DAG reuses the selected local baseline and supplies its successfully
published CSV for Pages, replacing that baseline without another S3 download.
The complete Pages site includes CSV downloads and HTML tables.
A separate deploy job publishes it only when the download/build job succeeds.
Configure Pages to use GitHub Actions; the github-pages environment must allow
deployment from the selected main/v2 branch. Both workflows retain manual invocation on main/v2.

GitHub masks the bucket secret in workflow logs. Raw Airflow logs are not uploaded
as artifacts because secret masking does not apply to artifact contents.
Uploaded working files contain
catalogue data, not the local AWS config; keep secrets out of DAG source.

AWS role `radiofrance-github-airflow` has been created with GitHub OIDC trust for
`philippegabriel/radiofrancecatalog` on `main`, `v2`, and the exact experiment
branch `codex/airflow-register-experiment`. Set its ARN as the
repository secret `AWS_AIRFLOW_ROLE_ARN`. Other branches and pull-request
identities are not trusted. The manual workflow must exist on GitHub's default
branch before the Run workflow UI is available; select main or v2 when launching.

`RF_CATALOGUE_BASELINE_DIR` optionally supplies predownloaded baselines; missing
files fail instead of falling back to S3. `RF_CATALOGUE_OUTPUT_DIR` receives an
atomic local copy of the selected output after successful S3 publication.
Without these options, local DAG runs continue to download directly from S3.

## Catalogue PostgreSQL processing

`RF_CATALOGUE_DSN` selects the catalogue database. Locally it defaults to
`dbname=radiofrance user=pgabriel` via the Unix socket. Airflow metadata still
uses its own connection to `airflow`. GitHub creates a separate lightweight
`radiofrance` database on its PostgreSQL service and sets this DSN explicitly.
EC2 needs a PostgreSQL instance and an empty radiofrance database too.

Each SQL operation loads its CSV into a session-private temporary `rf` table and
`rf_html` view. Only `pg_temp` is on the search path. The connection is rolled
back and closed after querying, so permanent catalogue/transcript/embedding
tables are untouched. No pgvector extension or full corpus is required.
`catalogue_csv_schema.sql` supplies the CSV-shaped temporary rf definition;
`schema.sql` supplies the HTML view, adapted to the temporary table. The compact
persistent schema uses integer episode references independently of this scratch
catalogue. `emithtml.sql` supplies
display columns. `cutoffdate.sql` remains available for the Make workflow;
the Download DAG now derives its cutoff from episode-reference events. `build_pages.py` assembles
the site and calls the shared `csv2html.py` renderer (including its existing
HTML escaping behavior). There is no separate HTML table renderer.

Catalogue tests now require access to radiofrance through `RF_CATALOGUE_DSN` or
the local default. They operate solely on temporary tables.

## GitHub Download container

Download uses the commit-tagged GHCR image defined under `docker/`, with a
separate PostgreSQL 17 service at hostname `postgres`. Local invocations and
Register retain their virtualenv defaults. The job sets `RF_AIRFLOW_PYTHON`,
`RF_AIRFLOW_COMMAND`, and `RF_DOWNLOAD_PYTHON` to the container executables.
`PYTHONUSERBASE=/home/airflow/.local` exposes the image's installed packages to
root job steps. The slim base omits the unused FAB provider; its uninstall
workaround is no longer needed. Download preserves the no-migration
compatibility check.

Measured Download job durations for `affaires-sensibles`:

| Phase | [Virtualenv](https://github.com/philippegabriel/radiofrancecatalog/actions/runs/37809188962) | [Regular image](https://github.com/philippegabriel/radiofrancecatalog/actions/runs/37983338671) | [Slim image](https://github.com/philippegabriel/radiofrancecatalog/actions/runs/37984691302) |
| --- | ---: | ---: | ---: |
| Container startup, including image pulls | 21 s | 61 s | 53 s |
| Python/Airflow setup | 36 s | 0 s | 0 s |
| Remove unused FAB provider | 0 s | 1 s | 0 s |
| Download DAG | 43 s | 54 s | 43 s |
| Complete Download job | 153 s | 176 s | 148 s |

Docker-measured uncompressed image size fell from 2,607,141,053 bytes to
1,229,874,880 bytes (53% smaller). The slim image build and smoke checks passed:
[build run](https://github.com/philippegabriel/radiofrancecatalog/actions/runs/37984332467).

These are GitHub job/step timestamp measurements, excluding the separate Pages
deployment job. They are one run per version with changing source/cache state,
not a controlled benchmark. The slim run was 28 seconds faster than the regular
image and 5 seconds faster than the virtualenv run. Image startup still accounts
for a substantial part of runtime. Download remains manual-only.

## Python type checking

From the project root, install the development tools and run Pyright:

```bash
venv/airflow/bin/python -m pip install -r requirements-dev.txt
venv/airflow/bin/pyright
```

`pyrightconfig.json` checks the project scripts and Airflow code in basic mode
with Python 3.14. It resolves embedding dependencies from `venv/radiofrance`
and Airflow dependencies from `venv/airflow`, using a consistent import search
path to avoid mixing NumPy type definitions. Both environments must
be installed for a complete local check. This is an offline static check;
it does not run downloads, load an embedding model, or access PostgreSQL.
