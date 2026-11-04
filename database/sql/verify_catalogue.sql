SELECT count(*) FROM episodes_external r JOIN episode_identity_external e ON r.episode_id=e.id
WHERE e.external_id=ANY(%s) AND r.show=%s;
