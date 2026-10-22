INSERT INTO {table}(configuration) VALUES (%s::jsonb)
ON CONFLICT(configuration) DO UPDATE SET configuration=EXCLUDED.configuration
RETURNING id,configuration;
