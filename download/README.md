# Download deployment pilot

This pilot is an independent command around the existing Radio France API
clients and CSV merger. It does not rewrite or replace the working Airflow Download
DAG. The next step is to run it from a Airflow deployment using Docker Compose.

## Contract

Acquisition is shared with the original scripts: `rf_dump.fetch_all()` handles
catalogue requests and pagination, and `fetchtranscript.fetch_transcript()` handles
transcript requests. Catalogue sorting/CSV output uses `write_catalogue_csv()`;
transcript JSON serialization uses `write_transcript_json()`. The original CLIs
call these same functions. The job adds baseline handling, merging, manifests,
validation and atomic publication; it does not reimplement either HTTP client.

Inputs: one supported show, its baseline catalogue CSV, an API key supplied via
`RADIOFRANCE_API_KEY`, and a new output directory. Even the initial run requires
a baseline (a header-only CSV explicitly requests a full catalogue download).
Missing files are errors, not implicit instructions for a full backfill.

The maximum publication timestamp in the baseline is the exclusive cutoff.
This differs from the existing DAG, which derives its cutoff from Airflow episode
events. Wiring the pilot into Airflow will require an explicit decision about
which component owns that state; this change does not switch the DAG's behavior.

Outputs:

```text
OUTPUT/
  baseline.csv                 # exact input snapshot
  updates.csv                  # only newly fetched episodes
  SHOW.csv                     # merged cumulative catalogue
  transcripts/EPISODE_ID.json   # raw JSON for each new episode
  manifest.json                # show, cutoff, counts, paths
```

No database, S3 or Airflow services are contacted by the command. The catalogue
module currently imports psycopg2, so the container includes that dependency;
the functions used here never open a database connection.

The output directory appears only after all downloads succeed. A transcript 404,
rate limit or HTTP failure fails the command. Failed staging data is removed;
retrying repeats acquisition from the unchanged baseline. This first pilot does
not resume individual transcripts or recover older missing downloads. A successful
empty update still publishes the catalogue and an empty transcript list.
The job never overwrites an existing output directory.
Completed podcast artifact directories use mode `0755` and files `0644` so
Airflow's different container user can verify them. Staging remains private
during acquisition, and the API key is never copied into the output directory.

## execution

Use `mecaniques-du-journalisme` for the pilot. Export the API key first;
if it is stored in the project's existing `.OpenAPIKey` file:

```bash
export RADIOFRANCE_API_KEY="$(cat .OpenAPIKey)"
venv/airflow/bin/python -m radiofrance.jobs.download \
  --show mecaniques-du-journalisme \
  --baseline data/mecaniques-du-journalisme.csv \
  --output-dir data/download-pilot/run-001
```

The default delay between transcript requests is one second. Keep credentials
out of command arguments, images, source control and logs. The command deliberately
does not read the `.OpenAPIKey` file implicitly.

For a runtime-mounted secret, use `--api-key-file /run/secrets/radiofrance-api-key`.
An explicit key file takes precedence over the environment. The Airflow DAG
uses this interface so no key value is passed through rendered fields or XCom.

## Container execution

The Docker daemon must be accessible to your account. From the repository root:

```bash
docker build -f download/Dockerfile -t radiofrance-download:pilot .
mkdir -p data/download-pilot
docker run --rm --init \
  --user "$(id -u):$(id -g)" \
  --env RADIOFRANCE_API_KEY \
  --mount type=bind,src="$PWD/data/mecaniques-du-journalisme.csv",dst=/input/baseline.csv,readonly \
  --mount type=bind,src="$PWD/data/download-pilot",dst=/output \
  radiofrance-download:pilot \
  --show mecaniques-du-journalisme --baseline /input/baseline.csv --output-dir /output/run-002
```

The allowlisted Docker build context excludes credentials, models and datasets.
The runtime uses an unprivileged user; the example uses your UID/GID so
bind-mounted outputs remain writable. No host ports or model files are needed.
The Python base image still uses a tag: pin the tested digest before deployment.

## Validation and next steps

```bash
venv/airflow/bin/python -m unittest tests.test_download_job -v
venv/airflow/bin/python -m unittest discover -s airflow/test -p test_download.py -v
venv/airflow/bin/pyright
```

The offline test covers cutoff selection, merging, transcript UUID conversion,
artifact paths, empty updates, destination protection and failure cleanup.
A live acquisition requires an API key and makes real requests; it is not part
of offline validation.

The container run has been exercised. Next, start Airflow and its metadata
database through Docker Compose, then connect this download container. Keep artifacts in explicit directories and serialize catalogue updates per show.
Later steps add chunking, embedding and transactional loading into PostgreSQL.
The Radio France database will record processing state; per-episode Airflow assets
are not part of the target design. Cloud deployment is deferred to a second phase.
