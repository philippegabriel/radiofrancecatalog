-- Interleave shows deterministically, independent of database physical row order.
SELECT episode_id,external_id,title,description
FROM (
 SELECT r.episode_id,e.external_id,r.title,r.description,r.show,
        row_number() OVER (PARTITION BY r.show ORDER BY r.episode_id) AS position
 FROM rf r JOIN episode_identity_external e ON e.id=r.episode_id
 WHERE r.description ~ '[^[:space:]]'
) eligible
ORDER BY position,show,episode_id LIMIT %s;
