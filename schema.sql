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
id text PRIMARY KEY);

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
FROM rf;

CREATE TABLE IF NOT EXISTS transcript_segment (
    episode_id  TEXT NOT NULL REFERENCES rf(id),
    seq         INTEGER NOT NULL,
    start_time  DOUBLE PRECISION NOT NULL,
    end_time    DOUBLE PRECISION NOT NULL,
    speaker     TEXT,
    text        TEXT NOT NULL,

    PRIMARY KEY (episode_id, seq)
);
