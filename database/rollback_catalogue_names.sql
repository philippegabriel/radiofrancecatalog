\set ON_ERROR_STOP on
\connect radiofrance
SET ROLE radiofrance;
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';
LOCK TABLE episodes, shows IN ACCESS EXCLUSIVE MODE;
ALTER TABLE shows RENAME TO show_set;
ALTER TABLE show_set RENAME CONSTRAINT shows_pkey TO show_set_pkey;
ALTER TABLE show_set RENAME CONSTRAINT shows_slug_key TO show_set_slug_key;
ALTER TABLE show_set RENAME CONSTRAINT shows_url_key TO show_set_url_key;
ALTER TABLE show_set RENAME CONSTRAINT shows_slug_check TO show_set_slug_check;
ALTER SEQUENCE shows_id_seq RENAME TO show_set_id_seq;
ALTER TABLE episodes RENAME TO shows;
ALTER TABLE shows RENAME CONSTRAINT episodes_pkey TO shows_pkey;
ALTER TABLE shows RENAME CONSTRAINT episodes_episode_id_fkey TO shows_episode_id_fkey;
ALTER TABLE shows RENAME CONSTRAINT episodes_show_id_fkey TO shows_show_id_fkey;
ALTER INDEX episodes_show_id_idx RENAME TO shows_show_id_idx;
ALTER VIEW episodes_external RENAME TO show_external;
COMMIT;
RESET ROLE;
