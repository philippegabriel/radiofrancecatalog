SELECT e.external_id AS episode_id, r.show, r.title, r.description,
       r.web_url, r.podcast_url, r.player_url, r.published_iso::text AS published,
       EXISTS (SELECT FROM transcript t WHERE t.episode_id = r.episode_id) AS has_transcript
FROM episodes_external r JOIN episode_identity_external e ON e.id = r.episode_id
WHERE e.external_id = %(episode_id)s;
