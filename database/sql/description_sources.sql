SELECT r.episode_id,e.external_id,r.title,r.description
FROM rf r JOIN episode_identity_external e ON e.id=r.episode_id
WHERE r.episode_id=ANY(%s) FOR SHARE OF r;
