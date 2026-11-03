# Episode-description embeddings

This stage searches episode metadata independently of transcript chunks. It uses
`rf.title` plus `rf.description`; it does not embed programme descriptions.
Episodes with blank descriptions are excluded. The default export selects up to
50 episodes deterministically, interleaving shows so one show cannot fill the
sample. The exact selected IDs and source strings are saved in `selection.json`.

The input is `title: <title> | text: <description>` (`none` for a missing title),
using the same pinned EmbeddingGemma 2 revision as transcript search, CPU FP32,
256 dimensions and L2 normalization. The input template is recorded in the model
configuration. The existing query prefix remains `task: search result | query: `.
There is no extra document prefix around the formatted title and description.

## Schema

New database volumes create `episode_embedding` from
`database/init/30-episode-description-schema.sql`. For an existing volume, apply
that file once using the application database connection:

```bash
psql -h 127.0.0.1 -p 55432 -U radiofrance -d radiofrance \
  -X -v ON_ERROR_STOP=1 -f database/init/30-episode-description-schema.sql
```

The script adds only the new table and does not reset existing data. It is not a
repeatable migration: do not apply it again if the table already exists.

The primary key is `(episode_id, model_id)`. Each row stores the source title,
source description, vector and update timestamp. Snapshot fields let the loader
reject exports that have gone stale and update an older embedding when a newly
exported title/description has changed. Equal input snapshots must reproduce the
same vector. Transcript tables and search results are unaffected.

## Run locally

Use the existing model environment, with the shared package and DB client installed:

```bash
venv/radiofrance/bin/python -m pip install --no-deps -e .
venv/radiofrance/bin/python -m pip install -r database/requirements.txt
```

It also needs the project's embedding dependencies and pinned model in the standard
Hugging Face cache. Configure `RF_DATABASE_DSN` privately, or use standard libpq
variables (`PGHOST=127.0.0.1`, `PGPORT=55432`, `PGUSER=radiofrance`,
`PGDATABASE=radiofrance` and a private password source). Do not put passwords in Git.

```bash
venv/radiofrance/bin/python -m radiofrance.jobs.episode_descriptions export \
  --limit 50 --output data/episode-descriptions/sample-050/selection.json
venv/radiofrance/bin/python -m radiofrance.jobs.episode_descriptions embed \
  --selection data/episode-descriptions/sample-050/selection.json \
  --output-dir data/episode-descriptions/sample-050/embeddings \
  --batch-size 4 --threads 4
venv/radiofrance/bin/python -m radiofrance.jobs.episode_descriptions load \
  --input-dir data/episode-descriptions/sample-050/embeddings
```

The exporter refuses to overwrite a snapshot. Each episode produces one
`<external-id>.npy` file with shape `(1,256)` and float32 dtype. Pickles are disabled.
Embedding retries validate and skip completed files. Changed metadata/model
settings require a fresh output directory. Use one writer per directory.
`manifest.json` is published only when every episode has a validated vector.
`benchmark.json` records startup, inference, total elapsed time and Linux peak
process RSS; it is a sample measurement, not a whole-corpus performance guarantee.
Save it before a resume, which writes a new report for that invocation.

Loading validates the files and locks the selected source rows while checking
metadata. All database writes commit together. Repeating a load skips matching
rows. The new model registration describes the titled-description template;
transcript embeddings retain their own model configuration.

Tests cover prompts, FP32 matrix shape, interrupted-run resume, corrupt files,
blank/duplicate metadata, stale exports, conflicting vectors and changed-source
updates. Run `venv/airflow/bin/pyright` and `bash ci/run-tests`.

This is an explicit CLI stage, separate from the transcript Airflow pipeline.

## First measured sample

On 2026-11-02 the local sample processed 50 episodes across all six shows (9, 9,
8, 8, 8, 8), using batch size 4 and four CPU threads. Description text totalled
10,394 characters. Encoding took 8.42 seconds; model loading took 13.29 seconds;
total job time was 21.79 seconds. Linux peak process RSS was 2,398 MiB. The 50
NPY files occupy 57,600 bytes, excluding JSON metadata.

A retry encoded zero new episodes and reused all 50 checkpoints. The first database
load wrote 50 rows; a second load changed zero rows. The generated local evidence
was removed during data cleanup; the figures above describe that historical run.

`database/sql/description_search.sql` provides description-only retrieval using
an explicit model ID and a compatible query vector. Three query smoke tests checked the
retrieval path on the small sample; it is not a judged retrieval-quality benchmark
or a comparison against the whole transcript corpus.

## Completed g4dn description backfill

The 2026-11-02 backfill selected all 22,291 nonblank episode descriptions. One
London g4dn.xlarge Spot worker reused the 50 sample vectors and encoded 22,241 new
vectors using CUDA FP32, batch size 32 and four CPU threads. Inference took
336.34 seconds; the embedding job took 396.20 seconds including model loading
and local file work (excluding instance provisioning, dependency installation
and model download). This is not a g4dn versus g6 benchmark; no g6 was launched.

The worker uploaded immutable per-episode NPY checkpoints concurrently. Inputs,
model configuration and completion metadata are retained with the results:

```text
s3://9c47ab5c-5039-42c1-a790-7e732bde98f4/data/embedding-output/embeddinggemma2-fp32-256/episode-descriptions-20261102/
```

Their NPY payload files occupied 25,679,232 bytes. The PostgreSQL loader inserted
22,241 new vectors and left the 50 existing sample vectors unchanged. Verification
found zero missing eligible descriptions and zero stale snapshots; transcript
corpus counts were unchanged. The temporary local backfill output and reports were
removed during data cleanup. The S3 location above records the historical upload.

## Query descriptions from Make

Both targets use `query_to_embedding.py` and `query.sql` with the same query
prefix, cached model and normalized 256D encoding:

```bash
make query-descriptions QUERY="général de Gaulle pendant la guerre"
make query QUERY="général de Gaulle pendant la guerre"
```

`query-descriptions` searches title/description embeddings and prints the stored
source title/description, URL and similarity. Identical title/description pairs
are collapsed before selecting ten results, preferring an entry with a URL.
`query` retains transcript passage search. Database authentication uses the
existing psql configuration/password prompt. No inference credentials are needed.
