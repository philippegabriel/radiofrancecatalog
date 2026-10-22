-- One-off update for an existing database; preserves migrated application rows.
BEGIN;
SET LOCAL lock_timeout = '10s';
ALTER TABLE transcript DROP COLUMN source_sha256;
ALTER TABLE chunk_set DROP COLUMN source_sha256, DROP COLUMN artifact_sha256;
ALTER TABLE embedding_set DROP COLUMN source_sha256, DROP COLUMN artifact_sha256;
ALTER TABLE chunk_strategy DROP COLUMN configuration_sha256;
ALTER TABLE embedding_model DROP COLUMN configuration_sha256;
UPDATE chunk_strategy SET configuration = configuration - 'code_sha256';
UPDATE embedding_model SET configuration = configuration - 'code_sha256' - 'model_manifest_sha256';
ALTER TABLE chunk_strategy ADD UNIQUE(configuration);
ALTER TABLE embedding_model ADD UNIQUE(configuration);
ALTER TABLE import_run RENAME COLUMN embedding_manifest_sha256 TO embedding_run;
ALTER TABLE import_run RENAME COLUMN chunk_manifest_sha256 TO chunk_run;
ALTER TABLE import_run RENAME COLUMN download_manifest_sha256 TO download_run;
COMMIT;
