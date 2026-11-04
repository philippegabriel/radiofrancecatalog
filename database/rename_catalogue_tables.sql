\set ON_ERROR_STOP on
\connect radiofrance
SET ROLE radiofrance;
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';
LOCK TABLE shows, show_set IN ACCESS EXCLUSIVE MODE;
ALTER TABLE shows RENAME TO episodes;
ALTER TABLE episodes RENAME CONSTRAINT shows_pkey TO episodes_pkey;
ALTER TABLE episodes RENAME CONSTRAINT shows_episode_id_fkey TO episodes_episode_id_fkey;
ALTER TABLE episodes RENAME CONSTRAINT shows_show_id_fkey TO episodes_show_id_fkey;
ALTER INDEX shows_show_id_idx RENAME TO episodes_show_id_idx;
ALTER TABLE show_set RENAME TO shows;
ALTER TABLE shows RENAME CONSTRAINT show_set_pkey TO shows_pkey;
ALTER TABLE shows RENAME CONSTRAINT show_set_slug_key TO shows_slug_key;
ALTER TABLE shows RENAME CONSTRAINT show_set_url_key TO shows_url_key;
ALTER TABLE shows RENAME CONSTRAINT show_set_slug_check TO shows_slug_check;
ALTER SEQUENCE show_set_id_seq RENAME TO shows_id_seq;
ALTER VIEW show_external RENAME TO episodes_external;
COMMIT;
RESET ROLE;
