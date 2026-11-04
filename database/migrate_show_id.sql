-- Existing rf/show schema only. Stop application readers/writers first.
-- All identifier changes, ID backfill and validation commit together.
\set ON_ERROR_STOP on
\connect radiofrance
SET ROLE radiofrance;
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';
LOCK TABLE "show", rf IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS (SELECT FROM rf r LEFT JOIN "show" s ON s.slug=r.show WHERE s.slug IS NULL) THEN
  RAISE EXCEPTION 'Unregistered rf.show slug: register all existing shows before migrating';
 END IF;
END $$;
-- Renames preserve relation OIDs, grants and incoming foreign keys.
ALTER TABLE "show" RENAME TO show_set;
ALTER TABLE rf RENAME TO shows;
ALTER VIEW rf_external RENAME TO show_external;
ALTER TABLE show_set ADD COLUMN id integer GENERATED ALWAYS AS IDENTITY;
ALTER TABLE show_set ADD CONSTRAINT show_set_slug_key UNIQUE(slug);
ALTER TABLE show_set DROP CONSTRAINT show_pkey;
ALTER TABLE show_set ADD CONSTRAINT show_set_pkey PRIMARY KEY(id);
ALTER TABLE show_set RENAME CONSTRAINT show_url_key TO show_set_url_key;
ALTER TABLE show_set RENAME CONSTRAINT show_slug_check TO show_set_slug_check;
ALTER TABLE shows RENAME CONSTRAINT rf_pkey TO shows_pkey;
ALTER TABLE shows RENAME CONSTRAINT rf_episode_id_fkey TO shows_episode_id_fkey;
ALTER TABLE shows ADD COLUMN show_id integer;
UPDATE shows r SET show_id=s.id FROM show_set s WHERE s.slug=r.show;
ALTER TABLE shows ALTER COLUMN show_id SET NOT NULL;
ALTER TABLE shows ADD CONSTRAINT shows_show_id_fkey FOREIGN KEY(show_id) REFERENCES show_set(id);
CREATE INDEX shows_show_id_idx ON shows(show_id);
CREATE OR REPLACE VIEW show_external AS
 SELECT r.episode_id, s.slug AS show, r.published_iso, r.published_ts,
        r.title, r.description, r.web_url, r.podcast_title, r.podcast_url, r.player_url,
        e.external_id
 FROM shows r JOIN show_set s ON s.id=r.show_id
 JOIN episode_identity_external e ON e.id=r.episode_id;
ALTER TABLE shows DROP COLUMN show;
DO $$ BEGIN
 IF EXISTS (SELECT FROM pg_roles WHERE rolname='radiofrance_mcp') THEN
  GRANT SELECT ON show_external TO radiofrance_mcp;
 END IF;
 IF EXISTS (SELECT FROM shows r LEFT JOIN show_set s ON s.id=r.show_id WHERE s.id IS NULL) THEN
  RAISE EXCEPTION 'Episode has no matching show after migration';
 END IF;
END $$;
COMMIT;
RESET ROLE;
