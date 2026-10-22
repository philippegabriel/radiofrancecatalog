# Airflow with Docker Compose

This setup starts Airflow and its PostgreSQL **metadata** database. The manual
download DAG can now launch the existing download container (section 5).
The same PostgreSQL container can now host the Radio France database: follow
[database setup](../database/README.md) for first-start initialization and
the deliberate state reset and first-start schema and loader commands.
The existing virtual environment, Airflow service and S3-based DAGs are unchanged.

## What runs

```text
Browser: http://localhost:8082
             |
Airflow container: API + scheduler + DAG processor + triggerer
             |
PostgreSQL 17 + pgvector container: airflow database + radiofrance database
```

The Airflow version matches `airflow/requirements.txt`: 3.3.2 with Python 3.14.
We use its documented `standalone` command for development. It initializes
the metadata schema and generates a login automatically, so there is no separate
migration service or manual database setup. `LocalExecutor` runs tasks as processes
inside the Airflow container. A small custom image adds the pinned Docker provider;
`DockerOperator` launches download work as a separate sibling container.

## 1. Check Docker Compose

From the repository root:

```bash
docker compose version
```

This requires the Compose plugin (`docker compose`, with a space). If Docker says
`unknown command: compose`, install the plugin for your Docker installation using
the [Docker Compose installation instructions](https://docs.docker.com/compose/install/).
It is a separate component from Docker Engine. No pip installation is needed.
For this machine's Ubuntu-packaged Docker, the matching package is available:

```bash
sudo apt install docker-compose-v2
docker compose version
```

Use `sudo docker compose` instead of `docker compose` below if your account needs
sudo to access Docker. No Radio France API key needs to be exported or preserved
through sudo for this step.

## 2. Start the two services

```bash
docker compose config --quiet
docker compose up -d --build --wait --wait-timeout 300
docker compose ps
```

The first start downloads the images and initializes PostgreSQL and Airflow, so
allow a few minutes. Both services should become healthy. If not, inspect:

```bash
docker compose logs --tail=100 postgres airflow
```

The UI is at [localhost:8082](http://localhost:8082). Port 8082 is intentional so
the older host Airflow service on 8080 can remain running. The port binds only to
loopback. PostgreSQL has no published host port and cannot conflict with host
PostgreSQL on port 5432. Choose another UI port if necessary:

```bash
AIRFLOW_PORT=8083 docker compose up -d
# With sudo: sudo env AIRFLOW_PORT=8083 docker compose up -d
```

## 3. Sign in and run the smoke test

The UI login comes from the Git-ignored `.env`:

```dotenv
AIRFLOW_UI_USERNAME=admin
AIRFLOW_UI_PASSWORD=airflow-ui-dev
```

Edit these values to change the login, then recreate the service:

```bash
sudo docker compose up -d --build --wait --wait-timeout 300 airflow
```

The startup wrapper writes `/opt/airflow/ui-passwords.json` with permissions 0600
before starting Airflow. SimpleAuthManager reads this explicitly configured file;
passwords are no longer generated on volume recreation. Intentional `.env` edits
are applied on the next container recreation. Do not use `restart` to apply new
Compose environment settings. No database or volume reset is required.
These UI settings are separate from PostgreSQL credentials. The development
login is not committed through `.env`; fresh configuration supplies the defaults
above. Set a different password in `.env` as needed.

In the UI, find **radiofrance_smoke**, enable it and trigger one run.
Open `write_greeting` and inspect its logs. When it succeeds, inspect its output:

```bash
docker compose exec airflow cat /opt/airflow/smoke-output/hello.json
```

Current DAGs are loaded from `airflow/dags/`. The retired S3 DAGs and their
GitHub workflows have been removed. There are no API
requests or asset registrations in the smoke test. The separate download DAG
performs real Radio France API requests only when manually triggered.

## 4. Check persistence and stop

`restart` restarts an existing container; it does not recreate containers removed
by `down`, or start the PostgreSQL dependency when only Airflow is selected. Use
`up` first to ensure the complete stack exists and is healthy:

```bash
docker compose up -d --wait --wait-timeout 300
docker compose restart airflow
docker compose up -d --wait --wait-timeout 300
# Check the greeting and the previous run in the UI after services are healthy.
docker compose exec airflow cat /opt/airflow/smoke-output/hello.json
docker compose down
```

If `docker compose ps` shows only headings, inspect stopped containers using
`docker compose ps -a`. Bring the stack back with `docker compose up -d --wait
--wait-timeout 300`; if startup fails, inspect `docker compose logs --tail=100
postgres airflow`. Continue to use `sudo` consistently if required by your Docker
installation. None of these commands restart the Docker Engine itself.

`down` removes containers and the Compose network but keeps both named volumes:

| Volume | Contents |
|---|---|
| `radiofrance-db-volume` | Airflow and provisioned RadioFrance databases |
| `radiofrance-airflow-volume` | Airflow configuration, UI login file, logs and smoke output |

The next `docker compose up -d --wait --wait-timeout 300` reuses them. Do not add
`--volumes` to `down` unless deliberately resetting this installation:
that would delete the history, login and generated files. No host catalogue or
transcript directories are modified by the smoke test.

## 5. Connect the pilot download container

Run these commands from the project root. Keep `.OpenAPIKey` as a file
containing the API key, and retain `data/mecaniques-du-journalisme.csv` as the
pilot baseline. The secret is mounted read-only into the download container;
it is not exported into Airflow or stored in XCom.

First, capture the host paths and permissions, **without sudo**:

```bash
bash airflow/configure
```

This creates an ignored `.env` containing the absolute project directory, your
UID/GID and the Docker socket's group ID. It contains no API key. The Docker
daemon interprets sibling-container bind mounts relative to the **host**, not
Airflow's filesystem, so the absolute path is necessary.

If `.env` already exists, the script refuses to overwrite it. Preserve it and
add/update these four settings using the values on your host:

```text
RF_PROJECT_ROOT=/absolute/path/to/radiofrance
RF_UID=<output of id -u>
RF_GID=<output of id -g>
RF_DOCKER_GID=<output of stat -c '%g' /var/run/docker.sock>
```

Build the updated download image and recreate Airflow:

```bash
sudo docker build -f download/Dockerfile -t radiofrance-download:pilot .
sudo docker compose up -d --build --wait --wait-timeout 300
```

Rebuild the download image even if you built it earlier: this version accepts
`--api-key-file`. Compose reads `.env` itself, including when invoked through sudo.
It preserves your existing metadata/history and configured UI login.

In the UI, enable **radiofrance_download** and trigger one run. It executes:

```text
prepare_download -> download (DockerOperator) -> verify_download
```

The download container reads the baseline and key read-only, writes outputs as
your host UID/GID, and is removed after the task finishes. Its stdout appears in
the Airflow task log; the final task verifies the show and artifact files and counts.
Artifacts remain under `data/download-pilot/airflow-<run-id>/`, including
`manifest.json`, the cumulative CSV, updates and any new transcripts. No API key
appears in the command line. There are no S3 calls, asset events or DB imports.

The baseline is **not promoted automatically**. Repeated runs against an unchanged
baseline may download the same new episodes into separate directories. An empty
update is valid. We will address baseline promotion and processing state when
connecting the application database. Trigger a new DAG run after a failure;
automatic retries are disabled and completed outputs are not overwritten.

The host Docker socket is now mounted in Airflow, with its group supplied through
`.env`. This is powerful access to the host: use only trusted DAG code.
The PostgreSQL container does not receive the socket. This is sibling-container
execution, not a second Docker daemon running inside Airflow.

If the task cannot connect to Docker, rerun the host `stat` command and update
`RF_DOCKER_GID`, then recreate Airflow. If it reports an unknown `--api-key-file`
option, rebuild the download image. A missing baseline/key or unavailable transcript
fails the task rather than silently skipping work.

If verification fails with `PermissionError` on an output produced by an older
image, its run directory may still have the staging mode `0700`. Give that
specific completed run directory mode `0755`, then clear only `verify_download`
in the UI to retry verification without repeating the download. Rebuild the
download image for future runs; completed artifact permissions are now set
explicitly. Do not change API key permissions or apply recursive permissions
to the project directory.

## 6. Chunk the downloaded transcripts

The next manual DAG is **radiofrance_chunk**. Follow the
[chunking guide](../chunking/README.md) to build its image and choose a
completed download run in the trigger form. No API key or model is needed.

## Limits and next step

This deliberately uses Airflow's development mode, not a production deployment.
The health check inspects component statuses, not just the HTTP response code.
The setup uses Docker-managed volume ownership, so no host UID changes or
recursive chmod commands are needed for Airflow state. Download outputs use your
configured host UID/GID.

The next stage after chunking is CPU embedding work. PostgreSQL now supports
both databases in one container; see [database setup](../database/README.md).
The application schema is created on first startup; the loader is an explicit container command;
Airflow integration is the next increment.

References: [Airflow quick start](https://airflow.apache.org/docs/apache-airflow/3.3.2/start.html),
[Simple auth manager](https://airflow.apache.org/docs/apache-airflow/3.3.2/core-concepts/auth-manager/simple/index.html),
[component health](https://airflow.apache.org/docs/apache-airflow/3.3.2/administration-and-deployment/logging-monitoring/check-health.html).

## Embedding stage

The next manual stage is `radiofrance_embedding`. Build its image, download
the pinned Google model with `hf download`, and set `RF_HF_HUB_CACHE` in `.env`
before recreating Compose. See [embedding/README.md](../embedding/README.md)
for the cache setup, direct container command, chunk-run parameter and output
verification. It uses SentenceTransformers FP32 and normalized 256D output; database
loading is now available through the [manual loader](../database/README.md#schema-and-loader--increment-2);
its Airflow task is the next increment.

## Database loading

The `radiofrance_database` DAG is now available in the same DAG directory.
Build `database-loader` and recreate Airflow as described in
[Airflow loading](../database/README.md#airflow-loading--increment-3), then select
an existing embedding run in the trigger form. It loads through a sibling
processing container and verifies the committed data in a read-only transaction.
The existing database schema and volumes need no reset or migration.

## Run the complete pipeline

After all individual stages work, use **radiofrance_pipeline** to coordinate them
without manually entering upstream directory names. No database reset, model
change or image rebuild is needed just to add this DAG. Airflow discovers it in
the mounted `airflow/dags/` directory; refresh the UI after the next parse.

Enable these five DAGs:

- `radiofrance_pipeline`
- `radiofrance_download`
- `radiofrance_chunk`
- `radiofrance_embedding`
- `radiofrance_database`

Trigger **radiofrance_pipeline** with no parameters. It runs:

```text
prepare_pipeline → download → chunk → embedding → database
```

Each stage task triggers its existing child DAG and waits for the entire child
run, including verification, to succeed. The parent uses deferrable waits, freeing
worker slots for child processing; the scheduler and triggerer must be healthy.
A paused child fails the parent task with a useful error rather than waiting
indefinitely. Open the parent task's triggered-DAG link or the child DAG's run
list to inspect detailed processing logs. Child run IDs have a `pipeline__` prefix
and identify the parent through its readable run ID.

The pipeline passes explicit run-directory references; it never scans for the
latest output. Files remain in `data/download-pilot`, `data/chunk-pilot` and
`data/embedding-pilot`. The final database stage follows that exact artifact chain.
Each parent run uses distinct child run IDs and producing-stage directories.
The pilot still uses `mecaniques-du-journalisme` and the configured baseline CSV;
the pipeline does not advance or overwrite that baseline. A second pipeline run
may therefore download the same delta again. Database imports reuse matching
immutable data. Successful empty deltas also flow through the full chain.

If a stage fails, later stages do not start. Inspect its child run and inputs.
For a whole-pipeline retry, trigger a new pipeline run. Automatic retries and
child resets are disabled: clearing an already-triggered parent task will reject
its existing child run, rather than rerunning a job that publishes immutable files.
You can recover using the individual stage DAGs and their explicit upstream run
parameters. If only database verification failed after commit, clear that child
verification task to recheck the existing import. A failed parent remains a record
of that pipeline attempt; it is not automatically resumed by a manual child repair.
If a parent times out, inspect any active child run before starting another attempt;
parent cancellation does not cancel the child.

Offline checks:

```bash
venv/airflow/bin/python -m unittest airflow/test/test_pipeline.py
venv/airflow/bin/pyright
```

These verify ordering, failure/wait settings and rendered artifact handoffs. A
live whole-pipeline run requires the host Docker daemon and Radio France API.
