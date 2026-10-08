-- CSV header/order is unchanged; stage before resolving compact identities.
BEGIN;
CREATE TEMP TABLE rf_import (LIKE rf_external) ON COMMIT DROP;
ALTER TABLE rf_import ADD CHECK (
    id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}_(0|[1-9][0-9]*)$'
);
\copy rf_import FROM pstdin WITH (FORMAT CSV, HEADER TRUE)
INSERT INTO episode_identity (source_uuid, source_suffix)
SELECT DISTINCT split_part(id, '_', 1)::uuid, split_part(id, '_', 2)::integer
FROM rf_import
ON CONFLICT (source_uuid, source_suffix) DO NOTHING;
INSERT INTO rf (show, published_iso, published_ts, title, description, web_url,
                podcast_title, podcast_url, player_url, episode_id)
SELECT r.show, r.published_iso, r.published_ts, r.title, r.description, r.web_url,
       r.podcast_title, r.podcast_url, r.player_url, e.id
FROM rf_import r JOIN episode_identity_external e ON e.external_id = r.id
ON CONFLICT (episode_id) DO UPDATE SET
    show = EXCLUDED.show, published_iso = EXCLUDED.published_iso,
    published_ts = EXCLUDED.published_ts, title = EXCLUDED.title,
    description = EXCLUDED.description, web_url = EXCLUDED.web_url,
    podcast_title = EXCLUDED.podcast_title, podcast_url = EXCLUDED.podcast_url,
    player_url = EXCLUDED.player_url;
COMMIT;
