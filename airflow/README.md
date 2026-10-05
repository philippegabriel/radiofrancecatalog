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

Tasks retrieve `s3://<bucket>/data/episodes/<show>.csv`, load its episode IDs and
publication timestamps into a run-specific PostgreSQL temporary `rf` table in `radiofrance`, derive the maximum
publication timestamp, invoke `rf_dump.py`, merge by episode ID, and publish the
validated cumulative CSV back to the same S3 key. A missing baseline fails the
run; new shows must first be configured and supplied with a baseline CSV (a
header-only CSV supports a full initial fetch). This retains the existing strict
cutoff behavior; late/backdated episodes and metadata corrections older than the
cutoff need a future overlap or full refresh policy.

S3 CSVs are authoritative. Catalogue SQL connects to `radiofrance`, using only session-private temporary
tables; it does not modify the permanent tables. Airflow still requires its own
metadata backend. HTML is built in the Pages workflow using shared SQL and rendering code.
Transcript transformations and permanent PostgreSQL imports belong to future
Transform and Import DAGs.

AWS CLI must be on PATH. Set `RF_S3_BUCKET` and optionally `RF_S3_REGION`
(default: `eu-west-2`). Local runs can use the ignored `aws/cache-config.json`
as a fallback; this file must not be committed. Credentials use AWS's normal
credential chain, allowing local SSO, GitHub credentials or an EC2 instance role.
Locally refresh expired credentials with `aws sso login --profile default`.
Worker identities require read/write access to `data/episodes/`; check write
permissions for the publishing task.

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
  `data/episodes/*.csv` and `data/airflow/github-download/metadata.dump`, plus
  `s3:ListBucket` limited to those prefixes. OIDC trust should restrict the
  repository/ref and audience `sts.amazonaws.com`.

The runner uses temporary PostgreSQL 17 only for Airflow metadata. It restores
`data/airflow/github-download/metadata.dump` from S3 if present, migrates the schema,
runs the DAG with `dags test`, and saves a new dump even after a task failure.
No persistent scheduler or full Radio France corpus is needed. GitHub metadata is
separate from your local Airflow metadata. Catalogue working files are retained as a
GitHub artifact for seven days; credentials and key files are excluded.

Workflow executions are serialized across shows. Avoid simultaneous local/EC2
publication to the same catalogue. A cancelled or forcibly terminated runner may
publish a catalogue before saving metadata; the next run derives its cutoff from
the S3 CSV rather than stale Airflow history. Pin-compatible Airflow/PostgreSQL
versions when reusing snapshots. The workflow retrieves all six S3 catalogue baselines once before running the
DAG. The DAG reuses the selected local baseline and supplies its successfully
published CSV for Pages, replacing that baseline without another S3 download.
The complete Pages site includes CSV downloads and HTML tables.
A separate deploy job publishes it only when the download/build job succeeds.
Configure Pages to use GitHub Actions; the github-pages environment must allow
deployment from the selected main/v2 branch. It does not trigger on pushes or pull requests.

GitHub masks the bucket secret in workflow logs. Raw Airflow logs are not uploaded
as artifacts because secret masking does not apply to artifact contents.
Uploaded working files contain
catalogue data, not the local AWS config; keep secrets out of DAG source.

AWS role `radiofrance-github-airflow` has been created with GitHub OIDC trust for
`philippegabriel/radiofrancecatalog` on `main` and `v2` only. Set its ARN as the
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
`schema.sql` supplies the rf definition and view, `cutoffdate.sql` computes the
cutoff, and `emithtml.sql` supplies display columns. `build_pages.py` assembles
the site and calls the shared `csv2html.py` renderer (including its existing
HTML escaping behavior). There is no separate HTML table renderer.

Catalogue tests now require access to radiofrance through `RF_CATALOGUE_DSN` or
the local default. They operate solely on temporary tables.
