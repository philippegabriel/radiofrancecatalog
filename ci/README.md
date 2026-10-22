# Code validation

[Code validation](../.github/workflows/code-validation.yml) runs on every push
and pull request, with an optional manual trigger. It has two independent jobs:

| Job | Checks |
| --- | --- |
| Analysis | Pyright, Compose configuration, shell syntax and imports of all six maintained DAGs |
| Tests | Download/chunk/embedding job tests, database tests and Airflow tests |

Both jobs use Python 3.14 on Ubuntu 24.04. GitHub actions are pinned to release
commit SHAs, pip downloads are cached, repository permissions are read-only and
checkout credentials are not persisted. New commits cancel older in-progress
runs for the same Git ref. There are no branch or path filters.

## Dependencies and test data

`requirements.txt` reuses the pinned Airflow and download requirements plus
Pyright. The test job installs no embedding runtime. Tests use deterministic
vectors and temporary transcript/catalogue fixtures; they need no Radio France
API key, Hugging Face token, model download or existing artifact directories.
The optional representative-pilot test is skipped because generated pilot data
is not committed. Model inference and the complete live pipeline belong to the
separate integration workflow described below.

The analysis job installs CPU PyTorch and the existing embedding requirements
in `venv/radiofrance`, separately from `venv/airflow`. This matches the fallback
package path in `pyrightconfig.json`, letting Pyright resolve SentenceTransformers
and numpy imports in the older root embedding scripts without mixing Airflow and
model dependencies. Installing these libraries does not load/download a model.
Hugging Face and Transformers offline modes are enabled for both jobs.

PostgreSQL 17 and pgvector are installed from PGDG in the test runner, following
the [pgvector GitHub Actions guidance](https://github.com/pgvector/setup-pgvector#ubuntu).
The existing database test harness starts a private cluster under `/tmp`, applies
the real initialization scripts, checks restart persistence and roles, runs the
loader/Airflow suites, then stops/removes only that cluster. It does not connect
to an existing host database. `PG_BINDIR` selects the PostgreSQL 17 binaries.
The setup checks and application tests therefore use the same database major
version as Compose. No containers are started by this workflow.

Compose validation uses a CI-only UI password and the checkout path supplied by
the workflow. It does not read your `.env`, access your Docker volumes or deploy
anything. Network access is needed to install dependencies, but tests mock
Radio France calls and do not contact an embedding server.

## Run checks on a configured checkout

With the project's existing environments and PostgreSQL/pgvector installed:

```bash
venv/airflow/bin/pyright
venv/airflow/bin/python ci/check_dags.py
bash ci/run-tests
AIRFLOW_UI_PASSWORD=ci-only-password docker compose --profile jobs config --quiet
```

Set `PG_BINDIR=/usr/lib/postgresql/17/bin` when selecting PostgreSQL 17 explicitly.
`ci/run-tests` invokes the same maintained job and database test entry points as
the GitHub workflow. The older Make-based compact-schema test is not included:
it targets the superseded schema, which was removed during pipeline cleanup.

`ci/check_dags.py` intentionally checks the maintained DAG IDs as well as import
errors. Add a new DAG to that expected set when intentionally extending the
pipeline. A failure in either Analysis or Tests fails code validation. No commit
or push is performed by the workflow, and it requires no repository secrets.

The source and scripts have been validated locally, including the temporary
database suite. The first hosted run still needs to be observed after this
workflow is pushed; runner provisioning and cold dependency installation are
not reproduced by the local test run.

## Full pipeline integration

[Pipeline integration](../.github/workflows/pipeline-integration.yml) runs only
when manually triggered; pushes do not start it. Select a branch and choose
**Run workflow** in GitHub Actions. GitHub requires the workflow to exist on the
default branch before offering the manual button.

Configure two repository Actions secrets:

- `OPENAPIKEY`: the Radio France API key, mounted into the download container.
- `HF_TOKEN`: a Hugging Face read token whose account can download
  `google/embeddinggemma-2` (accept its access terms on Hugging Face first).

The existing AWS secrets and `AIRFLOW_FERNET_KEY` are not used. The workflow runs
entirely on a disposable Ubuntu runner; it does not connect to your installation.
It creates an empty baseline and selects exactly the ten committed episode IDs
in `fixtures/le-cours-de-l-histoire.json`. Metadata and transcripts are fetched
live, with transcript segment counts checked before publication.
A missing episode or changed transcript fails the run without substitution.

It builds all five images from the selected checkout, downloads the pinned model
with the Hugging Face CLI, starts Compose, unpauses the five pipeline DAGs and
triggers `radiofrance_pipeline` through Airflow's API. Airflow owns the sequencing
and runs the existing download → chunk → embedding → database stages. GitHub
Actions provisions the environment and observes the parent DAG; it does not
reimplement the stage orchestration. Inference uses the existing CPU FP32
SentenceTransformers container and normalized 256-dimensional vectors.

After Airflow succeeds, `verify-integration.sql` checks exact episode membership,
transcript segment counts, nonempty embeddings per episode, vector
dimensions/normalization and import counts. A successful DAG alone is insufficient.
The controller refreshes its API token for longer runs. It waits up to 90 minutes
for the pipeline; the job has a 150-minute total limit for image builds and model
downloads as well. Each run downloads the model afresh, so expect a slow cold start.

Diagnostics (parent status, task logs, Compose logs and database assertions) are
uploaded for seven days on success or failure. Credentials, raw artifacts and
model weights are excluded from the upload. Cleanup removes runner containers,
volumes and temporary credential files. Each retry starts with a new database.

Local checks exercise fixed-selection success/failure and the real SQL verifier
against ten synthetic transcripts with deterministic vectors, including rejection
of a missing embedding. They do not establish hosted model throughput or prove
the full Docker run; observe the first GitHub run for that.

## Shared Python package

Processing code lives in `src/radiofrance`. Callable commands remain at package
level or under `jobs/`; shared modules without a `main()` live in `libs/`
(`catalogue`, `types`, `episode_selection`, `transcript_json`).
From the repository root, install it
into a development environment with `venv/airflow/bin/python -m pip install -e .`.
Stage-specific dependencies remain in their existing requirements files; package
installation alone does not install Airflow, PyTorch or model libraries. CI's
requirements install this package in editable mode. Containers install a regular
copy, including the SQL resources maintained in `database/sql`. Catalogue CSV/export
queries are kept in its `catalogue/` subdirectory and packaged as well.

The job entry points are `python -m radiofrance.jobs.download`, `.chunk`,
`.embedding` and `.database` (use the full module name for each). The standalone
fetch commands are `python -m radiofrance.fetch_episodes` and
`python -m radiofrance.fetch_transcripts`. Their arguments are unchanged.

After source changes, rebuild the affected processing images and the Airflow image;
Compose no longer mounts individual processing modules into Airflow.
