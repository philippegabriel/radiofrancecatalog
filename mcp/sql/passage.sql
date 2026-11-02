SELECT e.external_id AS episode_id, c.strategy_id, c.chunk_no,
       c.start_seq, c.end_seq, c.start_time, c.end_time,
       (SELECT string_agg(btrim(s.text, w.characters), ' ' ORDER BY s.seq)
        FILTER (WHERE btrim(s.text, w.characters) <> '')
        FROM transcript_segment s WHERE s.episode_id = c.episode_id
        AND s.seq BETWEEN c.start_seq AND c.end_seq) AS text,
       (SELECT jsonb_agg(jsonb_build_object('seq', s.seq, 'start_time', s.start_time,
           'end_time', s.end_time, 'speaker', s.speaker, 'text', s.text) ORDER BY s.seq)
        FROM transcript_segment s WHERE s.episode_id = c.episode_id
        AND s.seq BETWEEN greatest(0, c.start_seq - %(context)s) AND c.end_seq + %(context)s) AS segments
FROM semantic_chunk c JOIN episode_identity_external e ON e.id = c.episode_id
CROSS JOIN (
    SELECT string_agg(chr(code), '') AS characters
    FROM unnest(ARRAY[9,10,11,12,13,28,29,30,31,32,133,160,5760,
        8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,
        8232,8233,8239,8287,12288]) AS code
) w
WHERE e.external_id = %(episode_id)s AND c.strategy_id = %(strategy_id)s AND c.chunk_no = %(chunk_no)s;
