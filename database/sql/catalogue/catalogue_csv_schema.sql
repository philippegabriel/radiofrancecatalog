-- Temporary CSV-shaped catalogue used by Airflow; no pgvector dependency.
CREATE TEMP TABLE rf (
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
