SELECT
 (SELECT count(*) FROM transcript_segment WHERE episode_id=%s),
 (SELECT count(*) FROM semantic_chunk WHERE episode_id=%s AND strategy_id=%s),
 (SELECT count(*) FROM chunk_embedding WHERE episode_id=%s AND strategy_id=%s AND model_id=%s);
