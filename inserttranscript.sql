-- RF_TRANSCRIPT_JSON names a client-local source file; PostgreSQL receives JSON.
-- psql quotes the document as a SQL literal, never as executable SQL.
\set document `cat "$RF_TRANSCRIPT_JSON"`
BEGIN;
SELECT import_transcript_json(:'episode_id', :'document'::jsonb) AS inserted_segments;
COMMIT;
