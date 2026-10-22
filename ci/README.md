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
separate integration workflow planned next.

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
