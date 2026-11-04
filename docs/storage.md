# Pipeline storage

Trigger **radiofrance_pipeline** in Airflow and select registered shows, all enabled
shows, or new programme URLs for the complete pipeline. The `embedding_run` prompt belongs to the standalone database recovery
DAG, not the parent pipeline.

```text
data/
  runs/<UTC timestamp>/download/<show>/
    episodes.json
    manifest.json
  transcripts/<episode-id>.json
  chunks/segment-characters-v1-1000-300/
    <episode-id>.csv
    runs/<UTC timestamp>--<show>/manifest.json
  embeddings/embeddinggemma2-fp32-256/<model revision>/segment-characters-v1-1000-300/
    <episode-id>.npy
    runs/<UTC timestamp>--<show>/manifest.json
  catalogue/
    index.html
    <show>.html
    manifest.json
```

Names such as `2026-11-03_14-30-00-123456Z` come from the parent's UTC trigger
instant. Microseconds distinguish nearby runs. Child stages use that same instant;
Airflow's internal identifiers remain separate. Chunk and embedding `runs`
subdirectories hold small completion manifests only, never duplicate corpus files.

Download metadata is incremental. Its manifest references shared transcripts.
Each subsequent manifest lists exactly the selected episode files, counts and
configuration and points to its source manifest. Shared artifact paths are relative
to `data`; the manifest records its relative ancestor anchor. Paths are checked for
traversal and escaping symlinks. The loader follows these explicit references,
not a directory scan or the newest run.

Transcripts accumulate by episode ID and are reused without another HTTP request.
Chunks accumulate by strategy; equal existing files are reused and differing
content is rejected. Vectors accumulate by pinned model revision and strategy;
existing NPY shape, dtype, count and normalization are checked before reuse.
Reusing a valid matrix does not load or run the model. There are no SHA256 checks.
Already published corpus files can survive a later failure. A new run can reuse
them; only the final database transaction advances the download cutoff.

The HTML catalogue covers all committed episodes, including those without a
transcript. It refreshes the same pages, newest episodes first. Each page is
replaced atomically; the manifest is written last. A failure during publication
may leave mixed pages until export is rerun. Nothing is automatically published
to the web. Open `data/catalogue/index.html` directly.

## Upgrade an existing setup

Run these commands from the project root as the project owner (sudo only for Docker):

```bash
mkdir -p data/runs data/transcripts \
  data/chunks/segment-characters-v1-1000-300/runs \
  data/embeddings/embeddinggemma2-fp32-256/914f7f89142e33e77833254d9c9b90c3cef7303b/segment-characters-v1-1000-300/runs \
  data/catalogue
sudo docker build -f download/Dockerfile -t radiofrance-download:dev .
sudo docker build -f chunking/Dockerfile -t radiofrance-chunk:dev .
sudo docker build -f embedding/Dockerfile -t radiofrance-embedding:dev .
sudo docker compose build airflow database-loader
sudo docker compose up -d --wait --wait-timeout 300 airflow
```

No database migration or reset is required. Existing artifacts are not moved or
deleted automatically. Unnamespaced older chunk/vector exports remain where they
are; new pipeline writes use the configuration directories above. Existing
`data/transcripts` files can be reused directly. Completed old self-contained
manifests remain readable by the job functions, but the current Airflow forms and
mounts select the new layout. Old stage directories are no longer pipeline outputs.

Enable the parent, its single-show coordinator and four stage DAGs. Use the
parent selection form described below. For recovery only, trigger `radiofrance_chunk` with
`download_run: <timestamp>/download/<show>`, `radiofrance_embedding` with
`chunk_run: <timestamp>--<show>`, or `radiofrance_database` with `embedding_run: <timestamp>--<show>`.
Standalone download/chunk/embedding runs get their own trigger timestamps; manifests
preserve the explicit source reference when source and output timestamps differ.

To refresh only HTML, with no downloading or inference:

```bash
sudo docker compose run --rm --user "$(id -u):$(id -g)" \
  --volume "$PWD/data:/data" --entrypoint python database-loader \
  -m radiofrance.jobs.catalogue --output-dir /data/catalogue --replace
```

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

Rebuild the images using the upgrade commands above. Enable these six DAGs:
`radiofrance_pipeline`, `process_episodes`, `radiofrance_download`,
`radiofrance_chunk`, `radiofrance_embedding`, and `radiofrance_database`.
Trigger the parent and fill its selection form. The internal show coordinator
has `max_active_runs=1`, which serializes complete show chains, including across
manual coordinator runs. Parent mapped tasks can all be deferred, but this does
not allow multiple shows to process at once. Queued shows may run in scheduler
order; do not rely on the form's order.

Per-show download records live at `data/runs/<timestamp>/download/<show>/`.
Chunk and embedding completion folders use `runs/<timestamp>--<show>/` to avoid
collisions between shows. Shared per-episode corpus files retain their paths.
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

The embedding worker atomically replaces `<run>.progress.json` beside its run
completion directory. This small sidecar persists alongside the manifest and is
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
