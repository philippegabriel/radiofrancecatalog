# Chunking

The full `radiofrance_pipeline` DAG runs this stage automatically. For recovery,
trigger `radiofrance_chunk` with `download_run` set to `<UTC timestamp>/download`.
See [storage and setup](../docs/storage.md) for directories and rebuild commands.

Only transcripts listed by the selected download manifest are processed. The
segment-character strategy targets 1,000 characters with a 300-character minimum.
Per-episode CSVs retain the seven-column format: episode ID, chunk number, start
and end segment indices, start and end timestamps, and text.

Files accumulate under `data/chunks/segment-characters-v1-1000-300`. Completion
manifests live under its `runs/<timestamp>` subdirectory. Existing equal CSVs are
reused; conflicting content fails. No network or model inference is needed.

Native example (choose an existing download and a new output timestamp):

```bash
venv/airflow/bin/python -m radiofrance.jobs.chunk \
  --data-root data \
  --download-manifest data/runs/2026-11-03_14-30-00-000000Z/download/manifest.json \
  --output-dir data/chunks/segment-characters-v1-1000-300/runs/2026-11-03_14-30-00-000000Z
```

The data mount is writable for corpus publication. Files are published atomically;
already completed corpus files survive a failed stage and can be reused. A manifest
is published only after successful processing. Empty source sets are valid.
