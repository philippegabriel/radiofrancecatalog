CREATE TABLE rf (
published_iso	text,
published_ts	integer,
title	text,
description	text,
web_url	text,
podcast_title	text,
podcast_url	text,
player_url	text,
id text PRIMARY KEY);

DROP VIEW IF EXISTS rf_html;
CREATE VIEW rf_html AS
SELECT
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
