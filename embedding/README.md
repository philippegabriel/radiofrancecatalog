For the current directory layout and upgrade commands, see [pipeline storage](../docs/storage.md).

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
sudo docker build -f embedding/Dockerfile -t radiofrance-embedding:dev .
# Create shared corpus directories first; see docs/storage.md.
sudo docker run --rm --init --network none --memory 4g \
  --user "$(id -u):$(id -g)" \
  --mount type=bind,src="$HOME/.cache/huggingface/hub",dst=/home/embedding/.cache/huggingface/hub,readonly \
  --mount type=bind,src="$PWD/data",dst=/data \
  radiofrance-embedding:dev --data-root /data \
  --chunk-manifest /data/chunks/segment-characters-v1-1000-300/runs/TIMESTAMP/manifest.json \
  --output-dir /data/embeddings/embeddinggemma2-fp32-256/914f7f89142e33e77833254d9c9b90c3cef7303b/segment-characters-v1-1000-300/runs/NEW_TIMESTAMP
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
{"chunk_run": "2026-11-03_14-30-00-000000Z"}
```

The flow is `prepare_embedding → embed → verify_embeddings`. Preparation checks
the selected chunk artifacts and cache-path configuration. The worker verifies
the pinned model cache and performs inference. Airflow then verifies the output,
source provenance and recorded model inventory. The worker has no network, uses
a read-only model cache and a writable shared data mount and 4 GiB limit.
NPY files accumulate in the model/revision/strategy corpus; only completion manifests are per run. See [storage](../docs/storage.md).

## Outputs and retries

Each episode produces `<episode_id>.npy`: a float32 matrix with shape
`(chunk_count, 256)`. Row `i` contains chunk `i`'s finite, unit-normalized vector.
Files are saved and loaded with `allow_pickle=False`; object arrays, wrong dtypes,
wrong shapes and invalid vectors are rejected. Version-3 embedding manifests
reference only NPY files; previous JSONL embedding runs must be converted first.
The completion manifest records the source run, file paths, counts, model revision,
runtime versions and embedding configuration. No model-file inventory is created.

Documents use `title: none | text: `. Future search queries must use the same
model, dimensions and normalization with `task: search result | query: `.
Corpus matrices are published individually and a completion manifest is written only
after success. Failed processing removes staging, retaining reusable corpus files;
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

The standard-cache native integration run processed all 253 sample chunks across
12 episodes offline. Its vectors exactly matched the previous FP32 output.
The generated evidence was removed during data cleanup. These figures describe
that historical native validation; Docker execution was not checked at that time.
