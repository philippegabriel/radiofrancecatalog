BEGIN;
CREATE TEMP TABLE episode_target ON COMMIT DROP AS
SELECT r.episode_id FROM rf r JOIN episode_identity_external e ON e.id = r.episode_id
WHERE e.external_id = :'episode_id';
DO $$ BEGIN
    IF (SELECT count(*) FROM episode_target) <> 1 THEN
        RAISE EXCEPTION 'Episode must be imported into the catalogue first';
    END IF;
END $$;
CREATE TEMP TABLE import (episode_id text NOT NULL, chunk_no integer, start_seq integer, end_seq integer, start_time double precision, end_time double precision, text text) ON COMMIT DROP;
ALTER TABLE import ADD CHECK (episode_id = :'episode_id');
\copy import FROM pstdin WITH (FORMAT CSV)
-- Discard text only when it is exactly recoverable from existing segments.
DO $$ BEGIN
    IF EXISTS (
        WITH whitespace AS (
            SELECT string_agg(chr(code), '') AS characters
            FROM unnest(ARRAY[9,10,11,12,13,28,29,30,31,32,133,160,5760,
                8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,
                8232,8233,8239,8287,12288]) AS code
        )
        SELECT 1 FROM import i CROSS JOIN episode_target t CROSS JOIN whitespace w
        LEFT JOIN LATERAL (
            SELECT string_agg(btrim(s.text, w.characters), ' ' ORDER BY s.seq)
                       FILTER (WHERE btrim(s.text, w.characters) <> '') AS text,
                   bool_or(s.seq = i.start_seq) AS start_present,
                   bool_or(s.seq = i.end_seq) AS end_present
            FROM transcript_segment s
            WHERE s.episode_id = t.episode_id AND s.seq BETWEEN i.start_seq AND i.end_seq
        ) reconstructed ON true
        WHERE i.text IS DISTINCT FROM reconstructed.text
           OR reconstructed.start_present IS NOT TRUE
           OR reconstructed.end_present IS NOT TRUE
    ) THEN
        RAISE EXCEPTION 'Chunk text or boundaries do not match the imported transcript';
    END IF;
END $$;
DELETE FROM semantic_chunk WHERE episode_id = (SELECT episode_id FROM episode_target);
INSERT INTO semantic_chunk (episode_id, chunk_no, start_seq, end_seq, start_time, end_time)
SELECT t.episode_id, i.chunk_no, i.start_seq, i.end_seq, i.start_time, i.end_time
FROM import i CROSS JOIN episode_target t;
COMMIT;
