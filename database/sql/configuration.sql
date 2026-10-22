-- Both registries use the same columns; identifiers are substituted with psycopg.sql.
INSERT INTO {table}(configuration_sha256,configuration) VALUES (%s,%s::jsonb)
ON CONFLICT(configuration_sha256) DO UPDATE SET configuration_sha256=EXCLUDED.configuration_sha256
RETURNING id,configuration;
