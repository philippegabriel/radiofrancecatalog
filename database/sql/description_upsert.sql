INSERT INTO episode_embedding(episode_id,model_id,source_title,source_description,embedding)
VALUES (%s,%s,%s,%s,%s::vector)
ON CONFLICT(episode_id,model_id) DO UPDATE
SET source_title=EXCLUDED.source_title,source_description=EXCLUDED.source_description,
    embedding=EXCLUDED.embedding,updated_at=clock_timestamp();
