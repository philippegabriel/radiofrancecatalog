-- Restore the original rf, rf_external and show names and text relationship.
\set ON_ERROR_STOP on
\connect radiofrance
SET ROLE radiofrance;
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';
LOCK TABLE show_set, shows IN ACCESS EXCLUSIVE MODE;
ALTER TABLE shows ADD COLUMN show text;
UPDATE shows r SET show=s.slug FROM show_set s WHERE s.id=r.show_id;
ALTER TABLE shows ALTER COLUMN show SET NOT NULL;
CREATE OR REPLACE VIEW show_external AS
 SELECT r.episode_id, r.show, r.published_iso, r.published_ts,
        r.title, r.description, r.web_url, r.podcast_title, r.podcast_url, r.player_url,
        e.external_id
 FROM shows r JOIN episode_identity_external e ON e.id=r.episode_id;
ALTER TABLE shows DROP COLUMN show_id;
ALTER TABLE show_set DROP CONSTRAINT show_set_pkey;
ALTER TABLE show_set ADD CONSTRAINT show_pkey PRIMARY KEY(slug);
ALTER TABLE show_set DROP CONSTRAINT show_set_slug_key;
ALTER TABLE show_set DROP COLUMN id;
ALTER TABLE show_set RENAME CONSTRAINT show_set_url_key TO show_url_key;
ALTER TABLE show_set RENAME CONSTRAINT show_set_slug_check TO show_slug_check;
ALTER TABLE shows RENAME CONSTRAINT shows_pkey TO rf_pkey;
ALTER TABLE shows RENAME CONSTRAINT shows_episode_id_fkey TO rf_episode_id_fkey;
ALTER TABLE shows RENAME TO rf;
ALTER TABLE show_set RENAME TO "show";
ALTER VIEW show_external RENAME TO rf_external;
COMMIT;
RESET ROLE;
