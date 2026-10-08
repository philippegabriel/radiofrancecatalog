-- Rank compact vectors first; reconstruct text only for the returned chunks.
WITH best_per_episode AS (
    SELECT DISTINCT ON (c.episode_id)
        c.episode_id, c.chunk_no, c.start_seq, c.end_seq,
        c.start_time, c.end_time,
        1 - (c.embedding <=> :'queryvector'::halfvec(384)) AS similarity
    FROM semantic_chunk c
    WHERE c.embedding IS NOT NULL
    ORDER BY c.episode_id, c.embedding <=> :'queryvector'::halfvec(384), c.chunk_no
), matches AS (
    SELECT * FROM best_per_episode ORDER BY similarity DESC, episode_id LIMIT 10
), whitespace AS (
    -- Match Python str.strip(), including Unicode whitespace.
    SELECT string_agg(chr(code), '') AS characters
    FROM unnest(ARRAY[9,10,11,12,13,28,29,30,31,32,133,160,5760,
        8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,
        8232,8233,8239,8287,12288]) AS code
)
SELECT e.external_id AS episode_id, r.show, r.title, r.web_url,
       m.chunk_no, m.start_time, m.end_time, m.similarity,
       left(t.text, 300) AS text
FROM matches m
JOIN rf r ON r.episode_id = m.episode_id
JOIN episode_identity_external e ON e.id = m.episode_id
CROSS JOIN whitespace w
LEFT JOIN LATERAL (
    SELECT string_agg(btrim(s.text, w.characters), ' ' ORDER BY s.seq)
        FILTER (WHERE btrim(s.text, w.characters) <> '') AS text
    FROM transcript_segment s
    WHERE s.episode_id = m.episode_id AND s.seq BETWEEN m.start_seq AND m.end_seq
) t ON true
ORDER BY m.similarity DESC, m.episode_id;
