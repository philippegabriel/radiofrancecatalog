-- Run with psql as the container's airflow administrator, on radiofrance.
-- The password is supplied through the environment, not a command-line argument.
\getenv mcp_password RF_MCP_DATABASE_PASSWORD
BEGIN;
SELECT 'CREATE ROLE radiofrance_mcp LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'radiofrance_mcp') \gexec
ALTER ROLE radiofrance_mcp PASSWORD :'mcp_password';
ALTER ROLE radiofrance_mcp SET default_transaction_read_only = on;
ALTER ROLE radiofrance_mcp SET statement_timeout = '60s';
GRANT CONNECT ON DATABASE radiofrance TO radiofrance_mcp;
GRANT USAGE ON SCHEMA public TO radiofrance_mcp;
GRANT SELECT ON rf, episode_identity_external, transcript, transcript_segment,
    semantic_chunk, embedding_model, chunk_embedding, episode_embedding TO radiofrance_mcp;
COMMIT;
