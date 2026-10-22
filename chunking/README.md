# chunking stage

This stage takes one completed download manifest, reuses the existing
`radiofrance.chunk_json_to_csv` logic, and publishes chunk CSVs plus a manifest. It needs
no API key, model, network connection or database. The download workflow remains
unchanged; we trigger chunking separately while learning the pipeline.

`radiofrance.chunk_json_to_csv` owns both the chunking algorithm and CSV conversion through
`transcript_to_csv()`. Its existing CLI and `radiofrance.jobs.chunk` call that same function.
The job wrapper handles manifest validation, provenance and atomic publication.

## 1. Build and update Compose

From the repository root, create the output parent as your normal user:

```bash
mkdir -p data/chunk-pilot
sudo docker build -f chunking/Dockerfile -t radiofrance-chunk:pilot .
sudo docker compose up -d --wait --wait-timeout 300
```

Compose mounts chunk outputs read-only for Airflow's validation tasks. Shared
validation helpers come from the installed `radiofrance` package, so rebuild the
Airflow image after processing-code changes (`sudo docker compose build airflow`).
The chunk container reuses the Docker socket access and host UID/GID configured
for downloads. It mounts downloads read-only and its output parent writable;
networking is disabled.

## 2. Select a completed download run

In the Airflow UI, enable **radiofrance_chunk**, then trigger it. In the
`download_run` field, paste the download directory's basename, for example:

```text
airflow-54780c00bddcd71b9cfda15f79f19dbf10b6510eea64136ac55f927e60db3d0f
```

Do not include `data/download-pilot/` or `/manifest.json`. Select a run whose
download verification succeeded. Airflow validates all listed transcript checksums
before launching the chunk container:

```text
prepare_chunking -> chunk -> verify_chunks
```

The final task log gives the output location:

```text
data/chunk-pilot/airflow-<chunk-DAG-run-hash>/
  <episode-id>.csv
  manifest.json
```

The download and chunk DAGs have different run hashes. The chunk manifest records
the selected download directory and its manifest checksum to connect the two.

## Strategy and format

Strategy `segment-characters`, version `1`, preserves the existing algorithm:
append complete nonblank transcript segments until reaching a target of 1,000
characters; merge a final chunk shorter than 300 characters into its predecessor.
These are character counts, not token limits or strict maximum sizes. The chunker
retains original segment indices, including gaps left by skipped blank segments.

The headerless CSV format remains:

```text
episode_id,chunk_no,start_seq,end_seq,start_time,end_time,text
```

The manifest records strategy parameters, a checksum of the processing source
files, input transcript hashes, output CSV hashes, and episode/chunk counts. It
does not silently overwrite a previous strategy's output. Future strategies will
use separate runs and explicit version identities; no generic strategy framework
is introduced here.

Only transcripts named in the download manifest are processed. A download with
no new episodes yields an empty successful chunk manifest. An episode with no
nonblank segments yields an empty CSV with zero chunks. Unlisted files are ignored.

## Publication and recovery

Outputs are staged privately, verified, then published as one new directory.
Published directories use `0755` and files `0644` so Airflow can read them under
its separate UID. Failed processing removes staging and creates no final output.
The existing transcript data is never modified.

Trigger a new chunk DAG run to repeat processing. Automatic retries are disabled;
clearing a completed `chunk` task will fail rather than overwrite its results.
If verification alone fails, clear only `verify_chunks` after diagnosing the
problem. No embeddings or database loads occur in this stage.

## Native command and checks

The same processing command can run outside Docker:

```bash
venv/airflow/bin/python -m radiofrance.jobs.chunk \
  --download-manifest data/download-pilot/airflow-DOWNLOAD_HASH/manifest.json \
  --output-dir data/chunk-pilot/native-run-001

venv/airflow/bin/python -m unittest tests.test_chunk_job -v
venv/airflow/bin/python -m unittest discover -s airflow/test -p test_chunk.py -v
venv/airflow/bin/pyright
```

Replace the placeholder with your real download directory. The destination must
not already exist. Container execution uses the same command with `/input` and
`/output` mounted paths. The image adds no third-party Python dependencies.
