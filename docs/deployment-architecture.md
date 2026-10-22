# Radio France pipeline

The current goal is a fully working pipeline that we can understand,
exercise and describe in a blog article. Cloud deployment is deferred to phase two.

```text
Airflow -> download container -> chunking and embedding containers -> PostgreSQL
                   |                         |
                   +---- artifacts ----+
```

## Responsibilities

- **Docker Compose:** start Airflow and one PostgreSQL 17 server with pgvector,
  reusing the existing persistent volume for two databases.
- **Airflow:** schedule work, coordinate dependencies, retries and task logs.
- **Processing containers:** run explicit commands against input/output paths.
- **artifact directories:** preserve raw transcripts and derived files.
- **Radio France PostgreSQL database:** catalogue/search data and produced/missing
  processing state. This is a separate database and application login on the same
  server as Airflow's metadata database.

Transcripts are immutable. Chunking strategies and embedding models/configurations
are versioned so their outputs can coexist and be compared. Completion records
should identify the processing version, not just the episode. Per-episode Airflow
assets are not needed for the target design.

## Small implementation steps

1. Start Airflow and its metadata database with Docker Compose; check the UI.
2. Launch the existing download container from one Airflow task.
3. Add chunking and CPU embedding containers, one stage at a time.
   Both stages now have jobs, containers and manually triggered Airflow DAGs;
   the embedding stage uses SentenceTransformers FP32 with normalized 256D output.
   See [the embedding guide](../embedding/README.md).
4. Add database loading in three reviewable increments:
   database container/setup → schema and loader → Airflow integration.
   [Container/setup and the transactional loader](../database/README.md) are
   available now. First-start initialization creates the application tables; loading is an
   explicit command. The `radiofrance_database` DAG now launches that command
   and verifies the committed results. `radiofrance_pipeline` now coordinates
   download → chunk → embedding → database with explicit run references.
5. Demonstrate safe retries, restart persistence and recovery after a failure.

The [download pilot](../download/README.md) already supports files and
container execution. Its manifest records artifact paths and checksums. Keep that
boundary and publish completion only after successful processing. Reconcile its
baseline-derived cutoff with the existing DAG's cutoff before connecting them.

Current DAGs live in `airflow/dags/` and use the containerized pipeline.
The older S3 DAGs and their GitHub workflows have been retired. Shared catalogue
helpers remain because the current jobs still use them. Cloud deployment is deferred.

## Phase two

Once the pipeline works end to end, revisit cloud execution. Reuse the
processing commands, containers, formats and orchestration structure; implement
provider-specific storage and execution integration then. We are not building a
cross-cloud framework in advance.
