-- Episode metadata vectors are independent of transcript chunks.
-- Also apply explicitly with psql -f on an existing application database.
\connect radiofrance
SET ROLE radiofrance;
BEGIN;
CREATE TABLE episode_embedding (
 episode_id integer NOT NULL REFERENCES rf,
 model_id integer NOT NULL REFERENCES embedding_model,
 source_title text,
 source_description text NOT NULL,
 embedding vector(256) NOT NULL,
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY (episode_id,model_id),
 CHECK (abs(vector_norm(embedding)-1) < 0.0001)
);
COMMIT;
RESET ROLE;
