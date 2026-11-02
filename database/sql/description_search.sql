-- Description-only search; caller selects an exact model configuration.
SELECT e.external_id,r.title,r.show,1-(v.embedding <=> %s::vector(256)) AS similarity
FROM episode_embedding v
JOIN rf r USING(episode_id)
JOIN episode_identity_external e ON e.id=v.episode_id
WHERE v.model_id=%s
ORDER BY v.embedding <=> %s::vector(256),v.episode_id
LIMIT %s;
