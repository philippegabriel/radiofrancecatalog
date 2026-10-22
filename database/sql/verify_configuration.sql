SELECT s.configuration, m.configuration
FROM chunk_strategy s CROSS JOIN embedding_model m
WHERE s.id=%s AND m.id=%s;
