-- Fixture is passed as a quoted psql variable, never interpolated as executable SQL.
BEGIN;
CREATE TEMP TABLE expected_episode ON COMMIT DROP AS
SELECT episode_id, segment_count
FROM jsonb_to_recordset(:'fixture'::jsonb->'episodes')
 AS e(episode_id text, segment_count integer);
DO $$ BEGIN
 IF (SELECT count(*) FROM expected_episode) <> 10
    OR (SELECT count(*) FROM rf) <> 10
    OR (SELECT count(*) FROM transcript) <> 10
    OR (SELECT count(*) FROM import_run) <> 1 THEN
   RAISE EXCEPTION 'Expected one import of exactly ten catalogue episodes and transcripts';
 END IF;
 IF EXISTS (
  SELECT 1 FROM expected_episode e
  LEFT JOIN episode_identity_external i ON i.external_id=e.episode_id
  LEFT JOIN rf r ON r.episode_id=i.id
  LEFT JOIN transcript t ON t.episode_id=i.id
  WHERE r.show IS DISTINCT FROM 'le-cours-de-l-histoire'
     OR t.segment_count IS DISTINCT FROM e.segment_count
     OR (SELECT count(*) FROM transcript_segment s WHERE s.episode_id=i.id) <> e.segment_count
     OR NOT EXISTS (SELECT 1 FROM chunk_embedding c WHERE c.episode_id=i.id)
 ) THEN RAISE EXCEPTION 'Missing/changed selected episode or transcript, or episode without embeddings'; END IF;
 IF (SELECT count(*) FROM chunk_embedding) <> (SELECT count(*) FROM semantic_chunk)
    OR EXISTS (SELECT 1 FROM chunk_embedding WHERE vector_dims(embedding) <> 256 OR abs(vector_norm(embedding)-1)>0.0001)
    OR EXISTS (SELECT 1 FROM chunk_set WHERE chunk_count <= 0)
    OR (SELECT chunk_count FROM import_run) <> (SELECT count(*) FROM chunk_embedding) THEN
   RAISE EXCEPTION 'Invalid chunk/embedding counts, dimensions or normalization';
 END IF;
END $$;
SELECT (SELECT count(*) FROM rf) AS episodes,
 (SELECT count(*) FROM transcript_segment) AS segments,
 (SELECT count(*) FROM chunk_embedding) AS embeddings;
ROLLBACK;
