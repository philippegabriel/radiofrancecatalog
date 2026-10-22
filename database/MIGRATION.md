# Completed one-off database migration

On 22 October 2026 the existing host `radiofrance` database was copied into the
Compose application database, replacing its pilot data. The original host database
and Airflow metadata were left unchanged. The destination contains:

- 38,083 catalogue episodes;
- 37,574 transcripts with 13,903,820 segments;
- 1,310,547 chunks;
- no embeddings or legacy embedding schema.

E5 vectors remain in the original database and its backup. The destination is
reserved for EmbeddingGemma 2. Empty transcripts could not be distinguished from
unprocessed episodes in the old schema, so completion records were reconstructed
only for episodes with stored segments. Source integer IDs and timestamps were
preserved, and the identity sequence was advanced to 92,069.

Recovery files are in the Git-ignored directory
`data/database-backups/legacy-migration-20261022/`:

- `source.dump`: full custom-format backup of the original database;
- `destination-before.dump`: backup of the old container application database;
- `*.copy`: exports used during the transfer;
- `verified.json`: the historical migration verification report.

The one-off migration compared every segment and chunk against the original local
artifacts and verified the transfer before committing. Its historical report is
retained as evidence; it is not used by the application. The temporary migration
runner has been removed. PostgreSQL dumps can be inspected with `pg_restore --list`
and restored into a separate empty database with a compatible PostgreSQL version.

The current proof-of-concept uses run names, configuration JSON and row counts.
Checksums are not calculated or stored in the application database. Apply
`database/remove_checksums.sql` once to an older application schema; fresh
installations already use the simpler schema. This update preserves catalogue,
transcript and chunk rows. It does not rerun the data migration.

The download workflow and catalogue CSVs have not changed. Database-driven
cutoff lookup remains a separate future change.
