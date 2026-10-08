BEGIN;
CREATE TEMP TABLE episode_target ON COMMIT DROP AS
SELECT r.episode_id FROM rf r JOIN episode_identity_external e ON e.id = r.episode_id
WHERE e.external_id = :'episode_id';
DO $$ BEGIN
    IF (SELECT count(*) FROM episode_target) <> 1 THEN
        RAISE EXCEPTION 'Episode must be imported into the catalogue first';
    END IF;
END $$;
CREATE TEMP TABLE import (episode_id text NOT NULL, seq integer, start_time double precision, end_time double precision, speaker text, text text NOT NULL) ON COMMIT DROP;
ALTER TABLE import ADD CHECK (episode_id = :'episode_id');
\copy import FROM pstdin WITH (FORMAT CSV)
DELETE FROM transcript_segment WHERE episode_id = (SELECT episode_id FROM episode_target);
INSERT INTO transcript_segment (episode_id, seq, start_time, end_time, speaker, text)
SELECT t.episode_id, i.seq, i.start_time, i.end_time, i.speaker, i.text
FROM import i CROSS JOIN episode_target t;
COMMIT;
