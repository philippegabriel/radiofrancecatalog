# RadioFrance Airflow pipeline

The current DAGs live in `airflow/dags/` and run processing containers through
DockerOperator. Follow the [Compose deployment guide](DEPLOYMENT.md) for setup,
UI access, fixed `.env` login credentials, persistence and troubleshooting.

| DAG | Purpose |
| --- | --- |
| `radiofrance_smoke` | Check scheduling and persistent output |
| `radiofrance_download` | Download catalogue updates and transcripts |
| `radiofrance_chunk` | Chunk a selected completed download run |
| `radiofrance_embedding` | Generate normalized 256D EmbeddingGemma 2 vectors |
| `radiofrance_database` | Load a selected embedding run and verify committed records |
| `radiofrance_pipeline` | Coordinate all four stages from download through verified database loading |

Trigger `radiofrance_pipeline` for a complete run with automatic handoffs. Stage
DAGs also remain manually runnable by selecting the completed upstream run explicitly.
Download, chunking and embedding jobs publish checksummed manifests and immutable
output directories.

The [database loader DAG](../database/README.md#airflow-loading--increment-3) runs
the processing container and independently verifies the committed import. The
pipeline DAG passes exact upstream run references between these stages.

## Layout

- `dags/`: current DAG definitions and shared `radiofrance_catalogue` helpers.
- `Dockerfile`, `configure`, `DEPLOYMENT.md`: image, host settings and deployment guide.
- `test/`: checks for the current processing DAGs.

Compose mounts `airflow/dags/` read-only at `/opt/airflow/dags/`.

The older S3 download/transform/registration DAGs, their tests and GitHub workflows
have been removed. [Code validation](../ci/README.md) now checks the current
implementation on pushes and pull requests; live pipeline CI is planned separately.
Existing native launcher/snapshot utilities
are retained; they are not used by this Compose deployment.

## Checks

```bash
venv/airflow/bin/pyright
venv/airflow/bin/python -m unittest discover -s airflow/test -p 'test_*.py'
```

See the [deployment architecture](../docs/deployment-architecture.md),
[download](../download/README.md), [chunking](../chunking/README.md),
[embedding](../embedding/README.md) and [database](../database/README.md) guides.
