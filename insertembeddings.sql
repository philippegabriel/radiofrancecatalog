BEGIN;

CREATE TEMP TABLE import (
    episode_id  TEXT,
    chunk_no    INTEGER,
    embedding   vector(384)
) ON COMMIT DROP;

\copy import (episode_id, chunk_no, embedding) FROM pstdin WITH (FORMAT csv)

UPDATE semantic_chunk AS chunk
SET embedding = import.embedding
FROM import
WHERE chunk.episode_id = import.episode_id
  AND chunk.chunk_no = import.chunk_no;

COMMIT;

