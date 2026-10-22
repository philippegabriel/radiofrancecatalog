# EmbeddingGemma 2 stage

Generate embeddings from a completed chunk run using SentenceTransformers,
Google's original `google/embeddinggemma-2` checkpoint, CPU FP32 and normalized
256-dimensional vectors. The model revision is pinned to
`914f7f89142e33e77833254d9c9b90c3cef7303b`. Audio and vision encoders are disabled.
No database changes are involved.

## Download the model on the host

Use the Hugging Face CLI, without sudo or `--local-dir`:

```bash
hf download google/embeddinggemma-2 --revision 914f7f89142e33e77833254d9c9b90c3cef7303b
```

If the CLI is installed in the project environment, use
`venv/radiofrance/bin/hf` instead of `hf`. Authenticate on the host if required.
The standard cache is `~/.cache/huggingface/hub`. The job also honors
`HF_HUB_CACHE`, `HF_HOME` and `XDG_CACHE_HOME` for native execution.

There is no separate container preparation mode. The image contains the runtime;
it expects the pinned model to be cached before starting. Missing or corrupt
model files cause a clear failure. Processing does not download anything.

## Build and run the container

From the project root:

```bash
sudo docker build -f embedding/Dockerfile -t radiofrance-embedding:pilot .
mkdir -p data/embedding-pilot
sudo docker run --rm --init --network none --memory 4g \
  --user "$(id -u):$(id -g)" \
  --mount type=bind,src="$HOME/.cache/huggingface/hub",dst=/home/embedding/.cache/huggingface/hub,readonly \
  --mount type=bind,src="$PWD/data/chunk-pilot",dst=/input,readonly \
  --mount type=bind,src="$PWD/data/embedding-pilot",dst=/output \
  radiofrance-embedding:pilot \
  --chunk-manifest /input/airflow-c81b47eb5d4e7866014e7813bd2cc93959a6e5b2d2849f63539a60733e56c5ef/manifest.json \
  --output-dir /output/run-001
```

If your host cache is elsewhere, change the mount source. Mount the entire `hub`
directory: snapshot links can reference both repository blobs and the shared Hub
blob store. Mounting only the snapshot or model repository can break those links.
The mount is read-only and excludes the Hugging Face token file, which lives
outside `hub`. The image sets its own cache path and offline environment defaults.

Defaults are batch size 1 and four CPU threads; override with `--batch-size` and
`--threads`. The text model loads once per job and is reused across episodes.
Chunks exceeding 8,192 tokens fail rather than being silently truncated.
Native invocation uses the same arguments, with optional `--model-cache` to
specify a Hub cache directory explicitly.

## Run through Airflow

For a new setup, `airflow/configure` records the absolute host Hub cache
path as `RF_HF_HUB_CACHE` in `.env`. For an existing setup, add that setting to
`.env`, preserving its other settings. For the default cache, this appends it:

```bash
printf "\nRF_HF_HUB_CACHE='%s'\n" "$HOME/.cache/huggingface/hub" >> .env
sudo docker compose up -d --wait --wait-timeout 300
```

Use your actual cache path if customized. An explicit host path is needed because
Airflow launches a sibling container through the host Docker daemon.
The cache is mounted in the processing container only.

Enable and trigger `radiofrance_embedding` with a completed chunk run:

```json
{"chunk_run": "airflow-c81b47eb5d4e7866014e7813bd2cc93959a6e5b2d2849f63539a60733e56c5ef"}
```

The flow is `prepare_embedding → embed → verify_embeddings`. Preparation checks
the selected chunk artifacts and cache-path configuration. The worker verifies
the pinned model cache and performs inference. Airflow then verifies the output,
source provenance and recorded model inventory. The worker has no network, uses
read-only source/cache mounts, and has a writable output mount and 4 GiB limit.
Outputs appear in `data/embedding-pilot/airflow-<DAG-run-hash>/`.

## Outputs and retries

Each episode produces JSONL records containing `episode_id`, `chunk_index` and
an `embedding` array of 256 finite, unit-normalized coordinates.
The version-2 completion manifest records source/output checksums, counts,
model revision, runtime versions and embedding configuration. A separate
`model-manifest.json` records model-file checksums inside the output run; nothing
is written into the Hugging Face cache. Older version-1 outputs remain verifiable.

Documents use `title: none | text: `. Future search queries must use the same
model, dimensions and normalization with `task: search result | query: `.
Outputs are verified and published atomically. Failed processing removes staging;
completed output is never overwritten. Retry processing with a fresh output or
DAG run. A verification task alone may be cleared and retried. Empty chunk runs
publish without loading the model runtime, but still require a valid cached model.
Existing custom model directories are left untouched.

## Checks

```bash
venv/airflow/bin/python -m unittest tests.test_embedding_job airflow/test/test_embedding.py
venv/airflow/bin/pyright
```

Offline tests cover publication, provenance, invalid vectors and both repository
and shared Hub blob links. Real-model inference is checked separately. Docker
image execution must also be checked on a host with Docker daemon access.

The standard-cache native integration run processed all 253 pilot chunks across
12 episodes offline. Its vectors exactly matched the previous FP32 output.
Evidence is in the Git-ignored `data/embedding-pilot/hf-cache-validation-001/`
and sibling validation JSON/log. The combined job/DAG suite passed 19 tests;
Pyright, Compose configuration and configure-script syntax checks passed.
Docker execution was not checked because daemon access was unavailable.
