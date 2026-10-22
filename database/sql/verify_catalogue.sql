SELECT count(*) FROM rf r JOIN episode_identity_external e ON r.episode_id=e.id
WHERE e.external_id=ANY(%s) AND r.show=%s;
