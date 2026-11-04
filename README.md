# Fetch metadata from Radio France podcasts
* API: [Radio France's Open API](https://developers.radiofrance.fr/doc/en)
## Retrieve all metadata from:
* https://www.radiofrance.fr/franceinter/podcasts/affaires-sensibles
* https://www.radiofrance.fr/franceculture/podcasts/lsd-la-serie-documentaire
* https://www.radiofrance.fr/franceculture/podcasts/les-nuits-de-france-culture
* https://www.radiofrance.fr/franceculture/podcasts/les-pieds-sur-terre
* https://www.radiofrance.fr/franceculture/podcasts/le-cours-de-l-histoire
* https://www.radiofrance.fr/franceculture/podcasts/mecaniques-du-journalisme
## Workflow

Start the [Airflow Compose setup](airflow/DEPLOYMENT.md) to run Airflow
and its persistent metadata database, then trigger the smoke-test DAG.
The [chunking stage](chunking/README.md) processes a selected completed download
run through a separate, network-disabled container.

See the [deployment architecture](docs/deployment-architecture.md) and the
[download container](download/README.md) for acquisition details.

The application schema is defined in [20-application-schema.sql](database/init/20-application-schema.sql).
See the [database guide](database/README.md) for initialization and loading.

The [EmbeddingGemma 2 stage](embedding/README.md) consumes a completed chunk
manifest and produces normalized 256D vectors with SentenceTransformers FP32.
It runs in its own CPU container through Airflow.

The [database setup guide](database/README.md) provisions the separate RadioFrance
database on first startup of the PostgreSQL container's persistent volume, with pgvector
and a dedicated application login. The [transactional loader](database/README.md#schema-and-loader--increment-2)
loads a verified artifact chain. The `radiofrance_database` DAG runs the loader
and checks the committed results; see [Airflow loading](database/README.md#airflow-loading--increment-3).

Trigger `radiofrance_pipeline`, select registered shows or enter new programme URLs,
to run download → chunk → embedding → database → catalogue
with automatic, explicit artifact handoffs. See [running the complete pipeline](airflow/DEPLOYMENT.md#run-the-complete-pipeline).

[Code validation](ci/README.md) runs Pyright, DAG/configuration checks and the
maintained test suites on every push and pull request. The manually triggered live pipeline
integration workflow validates ten fixed episodes end to end.

Downloads use each show's committed PostgreSQL cutoff and publish `episodes.json`
metadata deltas plus raw transcript JSON. Catalogue CSV baselines and merges are
no longer part of the pipeline. Chunk CSVs and embedding NPY files remain stage
artifacts. See the [download contract](download/README.md) for retry semantics,
container rebuild commands and fixed-sample integration.

The pipeline finishes by generating a static HTML catalogue from PostgreSQL under
`data/catalogue/`. Open its `index.html` to browse all committed shows and
episodes. See [database/README.md](database/README.md#html-catalogue-export) for
setup and standalone export commands.

See [pipeline storage and upgrade instructions](docs/storage.md) for shared corpus
directories and readable UTC run names.

The initial pipeline task registers new programmes in the `shows` table.
[Multi-show selection and database upgrades](docs/storage.md#select-shows-or-register-new-ones)
require no database reset. Selected shows process sequentially and HTML is generated once.

Episodes now reference the registry through `episodes.show_id → shows.id`. Existing
databases need the [catalogue naming migration](database/CATALOGUE_NAMES_MIGRATION.md).
The `episodes_external` view continues to expose text show slugs.
