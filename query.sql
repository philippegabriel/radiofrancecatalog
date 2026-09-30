SELECT *
FROM (
    SELECT DISTINCT ON (semantic_chunk.episode_id)
        semantic_chunk.episode_id,
        rf.show,
        rf.title,
        rf.web_url,
        semantic_chunk.chunk_no,
        semantic_chunk.start_time,
        semantic_chunk.end_time,
        1 - (semantic_chunk.embedding <=> :'queryvector'::vector) AS similarity,
        left(semantic_chunk.text, 300) AS text
    FROM semantic_chunk
    JOIN rf
        ON rf.id = semantic_chunk.episode_id
    WHERE semantic_chunk.embedding IS NOT NULL
    ORDER BY
        semantic_chunk.episode_id,
        semantic_chunk.embedding <=> :'queryvector'::vector
) AS best_per_episode
ORDER BY similarity DESC
LIMIT 10;
