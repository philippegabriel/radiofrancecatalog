-- First-start application tables, owned by the application role.
\connect radiofrance
SET ROLE radiofrance;
BEGIN;
-- Dedicated pipeline schema; leave the legacy E5 schema.sql unchanged.
CREATE TABLE episode_identity (
 id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 source_uuid uuid NOT NULL, source_suffix integer NOT NULL CHECK(source_suffix >= 0),
 UNIQUE(source_uuid, source_suffix)
);
CREATE VIEW episode_identity_external AS
 SELECT id, source_uuid::text || '_' || source_suffix::text AS external_id FROM episode_identity;
CREATE TABLE rf (
 episode_id integer PRIMARY KEY REFERENCES episode_identity,
 show text NOT NULL, published_iso timestamptz, published_ts bigint NOT NULL,
 title text, description text, web_url text, podcast_title text, podcast_url text, player_url text
);
CREATE VIEW rf_external AS
 SELECT r.*, e.external_id FROM rf r JOIN episode_identity_external e ON e.id=r.episode_id;
CREATE TABLE transcript (
 episode_id integer PRIMARY KEY REFERENCES rf,
 source_sha256 text NOT NULL, segment_count integer NOT NULL CHECK(segment_count >= 0)
);
CREATE TABLE transcript_segment (
 episode_id integer REFERENCES transcript, seq integer CHECK(seq >= 0),
 start_time double precision NOT NULL, end_time double precision NOT NULL,
 speaker text, text text NOT NULL, PRIMARY KEY(episode_id, seq)
);
CREATE TABLE chunk_strategy (
 id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 configuration_sha256 text NOT NULL UNIQUE, configuration jsonb NOT NULL
);
CREATE TABLE chunk_set (
 episode_id integer REFERENCES transcript, strategy_id integer REFERENCES chunk_strategy,
 source_sha256 text NOT NULL, artifact_sha256 text NOT NULL,
 chunk_count integer NOT NULL CHECK(chunk_count >= 0), PRIMARY KEY(episode_id, strategy_id)
);
CREATE TABLE semantic_chunk (
 episode_id integer, strategy_id integer, chunk_no integer CHECK(chunk_no >= 0),
 start_seq integer NOT NULL, end_seq integer NOT NULL,
 start_time double precision NOT NULL, end_time double precision NOT NULL,
 PRIMARY KEY(episode_id, strategy_id, chunk_no), CHECK(start_seq <= end_seq),
 FOREIGN KEY(episode_id, strategy_id) REFERENCES chunk_set,
 FOREIGN KEY(episode_id, start_seq) REFERENCES transcript_segment(episode_id, seq),
 FOREIGN KEY(episode_id, end_seq) REFERENCES transcript_segment(episode_id, seq)
);
CREATE TABLE embedding_model (
 id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 configuration_sha256 text NOT NULL UNIQUE, configuration jsonb NOT NULL,
 CHECK((configuration->>'dimensions')::integer = 256)
);
CREATE TABLE embedding_set (
 episode_id integer, strategy_id integer, model_id integer REFERENCES embedding_model,
 source_sha256 text NOT NULL, artifact_sha256 text NOT NULL,
 chunk_count integer NOT NULL CHECK(chunk_count >= 0),
 PRIMARY KEY(episode_id, strategy_id, model_id),
 FOREIGN KEY(episode_id, strategy_id) REFERENCES chunk_set
);
CREATE TABLE chunk_embedding (
 episode_id integer, strategy_id integer, model_id integer, chunk_no integer,
 embedding vector(256) NOT NULL,
 PRIMARY KEY(episode_id, strategy_id, model_id, chunk_no),
 FOREIGN KEY(episode_id, strategy_id, model_id) REFERENCES embedding_set,
 FOREIGN KEY(episode_id, strategy_id, chunk_no) REFERENCES semantic_chunk,
 CHECK(abs(vector_norm(embedding)-1) < 0.0001)
);
CREATE TABLE import_run (
 embedding_manifest_sha256 text PRIMARY KEY,
 chunk_manifest_sha256 text NOT NULL, download_manifest_sha256 text NOT NULL,
 show text NOT NULL, strategy_id integer NOT NULL REFERENCES chunk_strategy,
 model_id integer NOT NULL REFERENCES embedding_model,
 catalogue_episode_count integer NOT NULL, transcript_count integer NOT NULL,
 chunk_count integer NOT NULL, completed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
COMMIT;
RESET ROLE;
