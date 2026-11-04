WITH best_per_episode AS (
    SELECT DISTINCT ON (c.episode_id)
        c.episode_id, c.strategy_id, c.chunk_no, c.start_seq, c.end_seq,
        c.start_time, c.end_time,
        1 - (v.embedding <=> %(vector)s::vector(256)) AS similarity
    FROM chunk_embedding v
    JOIN semantic_chunk c USING (episode_id, strategy_id, chunk_no)
    JOIN embedding_model model ON model.id = v.model_id
    JOIN episodes_external r ON r.episode_id = c.episode_id
    WHERE model.configuration @> %(configuration)s::jsonb
      AND (%(show)s::text IS NULL OR r.show = %(show)s)
      AND (%(date_from)s::date IS NULL OR r.published_iso >= %(date_from)s::date)
      AND (%(date_until)s::date IS NULL OR r.published_iso < %(date_until)s::date)
    ORDER BY c.episode_id, v.embedding <=> %(vector)s::vector(256), c.strategy_id, c.chunk_no, v.model_id
), whitespace AS (
    -- Match Python str.strip(), as used by chunk reconstruction.
    SELECT string_agg(chr(code), '') AS characters
    FROM unnest(ARRAY[9,10,11,12,13,28,29,30,31,32,133,160,5760,
        8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,
        8232,8233,8239,8287,12288]) AS code
), passages AS MATERIALIZED (
    SELECT e.external_id AS episode_id, r.show, r.title, r.web_url,
           r.published_iso::text AS published,
           m.strategy_id, m.chunk_no, m.start_time, m.end_time, m.similarity, t.text
    FROM best_per_episode m
    JOIN episodes_external r ON r.episode_id = m.episode_id
    JOIN episode_identity_external e ON e.id = m.episode_id
    CROSS JOIN whitespace w
    CROSS JOIN LATERAL (
        SELECT string_agg(btrim(s.text, w.characters), ' ' ORDER BY s.seq)
            FILTER (WHERE btrim(s.text, w.characters) <> '') AS text
        FROM transcript_segment s
        WHERE s.episode_id = m.episode_id AND s.seq BETWEEN m.start_seq AND m.end_seq
    ) t
    WHERE t.text IS NOT NULL AND t.text <> ''
), unique_passages AS (
    SELECT DISTINCT ON (text) *, count(*) OVER (PARTITION BY text) AS duplicate_count
    FROM passages
    ORDER BY text, (NULLIF(btrim(web_url), '') IS NOT NULL) DESC,
             similarity DESC, episode_id
)
SELECT episode_id, show, title, web_url, published, strategy_id, chunk_no,
       start_time, end_time, similarity, duplicate_count, left(text, 1500) AS text
FROM unique_passages ORDER BY similarity DESC, episode_id LIMIT %(limit)s;
