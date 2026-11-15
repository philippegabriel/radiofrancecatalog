# Pipeline storage

```text
data/
  episodes/<episode-id>.json
  transcripts/<episode-id>.json
  chunks/<chunking-strategy>/<episode-id>.csv
  embeddings/
    transcripts/<model-and-revision>/256/<chunking-strategy>/<episode-id>.npy
    descriptions/<model-and-revision>/256/<episode-id>.npy
    descriptions/<model-and-revision>/256/<episode-id>.json
  runs/<UTC timestamp>/<show>/
    download/manifest.json
    chunk/manifest.json
    embedding/{manifest.json,progress.json}
    description-embedding/{manifest.json,progress.json}
    database/result.json
  catalogue/                 # HTML, CSS and logo
  database-backups/
```

Model directories include precision and the pinned revision, for example
`embeddinggemma2-fp32-914f7f89142e33e77833254d9c9b90c3cef7303b`.
The current chunk strategy is `segment-characters-v1-1000-300`.
Episode JSON contains a single-element metadata list, including title and description.
It can be updated; raw transcripts are treated as immutable.

Manifests select exact episode IDs and shared paths; directory scans never select
pipeline work. Paths are relative to data, with a relative ancestor anchor stored
in the manifest. The loader checks containment, membership and source lineage.
Run timestamps use UTC and include microseconds. Runs contain records, not corpus copies.
A manifest is the completion marker; progress.json alone never indicates success.
Database import completion remains transactional and is recorded in PostgreSQL.
The loader writes database/result.json after a successful commit; this file is
feedback, while PostgreSQL remains the authority for committed work.

Each completed transcript, chunk CSV and transcript NPY is published to the shared
cache immediately. Incomplete files stay temporary; publication uses an atomic
filesystem operation on the same filesystem. If later work fails, completed cache
entries remain reusable. No SHA256 calculations are needed. NPY shape, FP32 dtype,
counts and normalization are checked before reuse.

Description vectors are also shared. Their adjacent JSON records the exact title,
description and encoder configuration. Equal snapshots reuse the vector without
loading the model; changed text regenerates it. The JSON is published last as the
pair's completion marker. The description stage is serialized by Airflow: do not
run independent writers to the same description cache concurrently. Old manifests
whose source text has changed fail verification rather than loading a mismatched vector.

## Applying the layout change

Existing files on this workstation were moved with a one-off migration; original
manifests and the relocation journal are retained in
`data/archive/storage-layout-20261114.tar.gz` for recovery. Older
CSV exports and completed one-off job records are retained under data/archive.
The migration does not change the database or rerun inference.

Rebuild all job images and Airflow before triggering another run:

```bash
bash airflow/configure
sudo docker build -f download/Dockerfile -t radiofrance-download:dev .
sudo docker build -f chunking/Dockerfile -t radiofrance-chunk:dev .
sudo docker build -f embedding/Dockerfile -t radiofrance-embedding:dev .
sudo docker compose build airflow database-loader
sudo docker compose up -d --wait --wait-timeout 300 airflow
```

For standalone recovery supply `download_run: <timestamp>/<show>/download`,
`chunk_run: <timestamp>/<show>/chunk`, or
`embedding_run: <timestamp>/<show>/embedding`. Description recovery uses
`description_run: <timestamp>/<show>/description-embedding`.
All are relative to data/runs; the parent supplies them automatically.
No database migration or reset is needed. Historical database import records and
Airflow XCom retain their original path strings; they are audit history. For new
recovery runs, select the relocated manifest path explicitly.

## Select shows or register new ones

The `radiofrance_pipeline` trigger form has three fields; the effective selection
is their deduplicated union:

- `shows`: a multi-select populated from the show registry, storing slugs such as
  `["le-cours-de-l-histoire", "affaires-sensibles"]`.
- `all_enabled`: select every enabled row in the `shows` registry.
- `new_show_url`: an array of canonical programme URLs, for example
  `["https://www.radiofrance.fr/franceculture/podcasts/un-nouveau-programme"]`.
  Replace the example with a real programme URL.

At least one option is required. You do not need to change `.env` or restart
Airflow to switch shows. Slugs and URLs are validated before acquisition, missing
shows are registered transactionally, and the exact selection is recorded in the
initial task's XCom. Unknown selected slugs fail with a message asking for their
programme URLs. URL validation checks syntax and origin; the live API confirms
actual availability. Registrations remain even if a later download fails.

### Existing database upgrades

Fresh volumes initialize the registry in `database/init/15-shows.sql`, before the
application tables. Existing databases with text `rf.show` need the transactional
[integer show-ID upgrade](../database/SHOW_ID_MIGRATION.md), not a replay of the
first-start scripts. Then apply the [catalogue naming migration](../database/CATALOGUE_NAMES_MIGRATION.md). The guides include image rebuild, migration, verification
and rollback commands. It preserves catalogue data and Airflow history.

Rebuild the images using the upgrade commands above. Enable these seven DAGs:
`radiofrance_pipeline`, `process_episodes`, `radiofrance_download`,
`radiofrance_chunk`, `radiofrance_embedding`, `radiofrance_description_embedding`, and `radiofrance_database`.
Trigger the parent and fill its selection form. The internal show coordinator
has `max_active_runs=1`, which serializes complete show chains, including across
manual coordinator runs. Parent mapped tasks can all be deferred, but this does
not allow multiple shows to process at once. Queued shows may run in scheduler
order; do not rely on the form's order.

Per-show download records live at `data/runs/<timestamp>/<show>/download/`.
Every stage writes its records under the same timestamp/show directory. Shared episode files are kept separately from those records.
The HTML catalogue is refreshed once after all selected shows succeed. If one
show fails, already committed shows remain committed and other queued shows can
still finish; the parent reports failure and does not refresh HTML. A new run
reuses existing files and committed database cutoffs. Standalone stage DAGs
remain available for recovery with these explicit per-show references.

### Registry-backed selection fields

Both `radiofrance_pipeline` (multiple shows) and `process_episodes`
(single show) read registry suggestions with a two-second connection timeout and
one-second statement timeout when their DAGs are parsed. New registrations appear
after the next successful parse. If the registry is unavailable, the DAG still
loads with no suggestions; runtime selection continues to validate the database.
Suggestions are not restrictive enums, so the parent can immediately pass a newly
registered show to a child whose cached form has not refreshed yet.

`show_url` is the single-show URL field; leaving it blank resolves the chosen
show's stored URL at task execution. The parent form's `new_show_url` field accepts
one or more URLs (one per line). Old parameter names are no longer accepted.
Rebuild the processing images and Airflow, then recreate Airflow using the upgrade
commands above. Enable `process_episodes`; the retired coordinator's
historical runs may remain visible in Airflow, but new runs use the renamed DAG.

These form controls use Airflow's documented `examples` suggestions:
https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/params.html

## DAG hierarchy

```text
radiofrance_pipeline
  select_shows                 task: validate selection and register new URLs
  process_shows                mapped trigger task: one child run per selected show
    process_episodes           child DAG: one active show run at a time
      radiofrance_download
      radiofrance_chunk
      radiofrance_embedding
      radiofrance_description_embedding
      radiofrance_database
  catalogue                    task: refresh HTML after all shows succeed
  monitor_run                  task: unified live log, parallel with processing
```

`process_shows` is the trigger inside the parent, not another DAG. `process_episodes`
is the child DAG it starts. Enable the renamed child before triggering a new parent
run. Previous DAG names may remain visible as historical records; new runs target
`process_episodes`. The processing order and shared corpus layout are unchanged.

## Unified pipeline progress

Open **radiofrance_pipeline → your run → monitor_run → Logs** to follow all
selected shows in one place. The monitor starts after selection and checks every
ten seconds, releasing its worker slot between polls. It logs changes in episode,
transcript and chunk counts as completion manifests appear. Counts are run-scoped;
missing stages are reported as unknown, not zero. Transcript counts include reused
files; they mean transcripts available for this run, not newly downloaded files.

Embedding updates distinguish new vectors from cached vectors. Model loading is
reported before encoding. A provisional rate and remaining encoding-time estimate
appear after ten new vectors; subsequent updates are published at each hundred.
The rate measures encoding calls only, excluding model loading, artifact I/O and
cached vectors. Short jobs report their rate at completion; all-cached/empty jobs
have no measured rate. Final metrics include encoding and total job elapsed time.
ETA is approximate and may vary with chunk length or other CPU activity.

The embedding worker atomically replaces `progress.json` inside its stage run directory. This small sidecar persists alongside the manifest and is
not itself proof of successful processing. The parent monitor's final XCom contains
per-show metrics, combined totals and terminal processing states. Failed/skipped stages are logged
explicitly and still fail/block the normal pipeline as appropriate. Cancellation
of the parent also stops further monitor polling; use a fresh run for recovery.

Build updated code before the next run:

```bash
sudo docker build -f embedding/Dockerfile -t radiofrance-embedding:dev .
sudo docker compose build airflow
sudo docker compose up -d --wait --wait-timeout 300 airflow
```

## Limit a run to broadcast dates

The **radiofrance_pipeline** trigger form accepts optional **From date**
(`from_date`) and **To date** (`to_date`) fields, using `YYYY-MM-DD`. Both dates
are inclusive and interpreted in Europe/Paris, including daylight-saving changes.
The same range applies to all selected shows and is shown in `monitor_run` logs.
Invalid dates or a reversed range fail before show registration or downloads.

- Leave both dates blank for the normal per-show database watermark/delta.
- Set both dates to process a bounded period, such as `2026-10-01` through
  `2026-10-31` for October only.
- Set only From date to process that date onward, or only To date for everything
  up to and including that day. A To-only selection can cover a large archive.

With either date supplied, the downloader ignores the latest-date watermark and
skips all IDs already committed for that show. This allows importing an older
month after a newer month. Omitting dates afterward resumes from the latest
committed date; it does not automatically fill older gaps. Use another explicit
range to fill those gaps. Existing immutable transcripts/chunks/vectors remain
reusable. No database migration is needed.

The API is paginated newest-first. Newer out-of-range records are skipped while
pagination continues; it stops when the lower bound is reached. The job checks
bounds and committed IDs before acquiring transcripts. Downstream stages consume
only the resulting download manifest, which records the selected dates and UTC
bounds. Existing manifests remain readable. Date filtering does not cap chunks
or guarantee a particular runtime; long episodes can still produce many chunks.
The fixed-ID integration fixture cannot be combined with date ranges.

To deploy this change, rebuild the download image and Airflow, then recreate
Airflow after current runs finish:

```bash
sudo docker build -f download/Dockerfile -t radiofrance-download:dev .
sudo docker compose build airflow
sudo docker compose up -d --wait --wait-timeout 300 airflow
```

The download CLI supports the same optional `--from-date` and `--to-date` flags.

## Description embeddings in the normal pipeline

Each show runs download → chunk → transcript embedding → description embedding →
database. The two embedding stages run sequentially to keep memory bounded. Both
read only the selected artifacts, with no API or database credentials. Description
prompts are `title: <title> | text: <description>`; blank descriptions are skipped.
The database loader validates both vector kinds and the exact metadata snapshots
before committing metadata, transcripts, chunks, vectors and import completion in
one transaction. Empty runs publish valid empty manifests without model inference.

The normal pipeline handles only the selected download. Independent backfill tools
remain available for unrelated historical gaps. Standalone loader recovery can
omit description_run for a transcript-only import; the parent always supplies both.

## Pipeline duration

Processing tasks, child-DAG waits, the show coordinator and the unified monitor
have no elapsed-time deadline. Large backfills can run until completion or manual
cancellation. The monitor continues to reschedule every ten seconds, releasing
its worker slot between checks. The integration driver also waits for a terminal
pipeline state without an application deadline. GitHub runner platform limits
still apply even though custom workflow job limits have been removed.

Network/database connection limits, health-check/startup limits, bounded registry
queries and test timeouts remain in place. These detect unresponsive dependencies
and do not cap embedding inference or overall pipeline duration.
