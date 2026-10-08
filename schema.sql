CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS episode_identity (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_uuid uuid NOT NULL,
    source_suffix integer NOT NULL CHECK (source_suffix >= 0),
    UNIQUE (source_uuid, source_suffix)
);

CREATE OR REPLACE VIEW episode_identity_external AS
SELECT id, source_uuid::text || '_' || source_suffix::text AS external_id
FROM episode_identity;

CREATE TABLE IF NOT EXISTS rf (
show text NOT NULL,
published_iso	timestamptz,
published_ts	bigint,
title	text,
description	text,
web_url	text,
podcast_title	text,
podcast_url	text,
player_url	text,
episode_id integer PRIMARY KEY REFERENCES episode_identity(id));

CREATE OR REPLACE VIEW rf_external AS
SELECT r.show, r.published_iso, r.published_ts, r.title, r.description,
       r.web_url, r.podcast_title, r.podcast_url, r.player_url,
       e.external_id AS id
FROM rf r JOIN episode_identity_external e ON e.id = r.episode_id;

CREATE OR REPLACE VIEW rf_html AS
SELECT
    show,
    published_iso,
    published_ts,
    title,
    description,
    '<a href="' || web_url || '">web url</a>' AS web_url,
    podcast_title,
    '<a href="' || podcast_url || '">podcast url</a>' AS podcast_url,
    '<a href="' || player_url || '">player url</a>' AS player_url,
    id
FROM rf_external;

CREATE TABLE IF NOT EXISTS transcript_segment (
    episode_id  integer NOT NULL REFERENCES rf(episode_id),
    seq         INTEGER NOT NULL,
    start_time  DOUBLE PRECISION NOT NULL,
    end_time    DOUBLE PRECISION NOT NULL,
    speaker     TEXT,
    text        TEXT NOT NULL,

    PRIMARY KEY (episode_id, seq)
);
CREATE TABLE IF NOT EXISTS semantic_chunk (
    episode_id      integer NOT NULL REFERENCES rf(episode_id),
    chunk_no        INTEGER NOT NULL,

    -- Inclusive boundaries in this episode's transcript_segment sequence.
    start_seq       INTEGER NOT NULL,
    end_seq         INTEGER NOT NULL,

    start_time      DOUBLE PRECISION NOT NULL,
    end_time        DOUBLE PRECISION NOT NULL,

    embedding       halfvec(384),

    PRIMARY KEY (episode_id, chunk_no),
    CHECK (start_seq <= end_seq),
    CONSTRAINT semantic_chunk_start_segment_fk
        FOREIGN KEY (episode_id, start_seq)
        REFERENCES transcript_segment (episode_id, seq)
        DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT semantic_chunk_end_segment_fk
        FOREIGN KEY (episode_id, end_seq)
        REFERENCES transcript_segment (episode_id, seq)
        DEFERRABLE INITIALLY DEFERRED
);
