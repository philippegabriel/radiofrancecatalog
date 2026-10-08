BEGIN;
CREATE TEMP TABLE import (
    episode_id text NOT NULL, chunk_no integer NOT NULL, embedding halfvec(384),
    PRIMARY KEY (episode_id, chunk_no)
) ON COMMIT DROP;
ALTER TABLE import ADD CHECK (episode_id = :'episode_id');
\copy import FROM pstdin WITH (FORMAT CSV)
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM import i
        LEFT JOIN episode_identity e ON e.source_uuid = split_part(i.episode_id, '_', 1)::uuid
            AND e.source_suffix = split_part(i.episode_id, '_', 2)::integer
            AND e.source_uuid::text || '_' || e.source_suffix::text = i.episode_id
        LEFT JOIN semantic_chunk c ON c.episode_id = e.id AND c.chunk_no = i.chunk_no
        WHERE c.episode_id IS NULL
    ) THEN
        RAISE EXCEPTION 'Embedding references a missing catalogue episode or chunk';
    END IF;
END $$;
UPDATE semantic_chunk c SET embedding = i.embedding
FROM import i JOIN episode_identity e ON e.source_uuid = split_part(i.episode_id, '_', 1)::uuid
            AND e.source_suffix = split_part(i.episode_id, '_', 2)::integer
            AND e.source_uuid::text || '_' || e.source_suffix::text = i.episode_id
WHERE c.episode_id = e.id AND c.chunk_no = i.chunk_no;
COMMIT;
