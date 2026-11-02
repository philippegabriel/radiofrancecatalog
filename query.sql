-- Default to transcript search when invoked directly with psql.
\if :{?descriptions}
\else
\set descriptions false
\endif

\if :descriptions
-- Search episode metadata using the same query vector as transcript retrieval.
WITH documents AS (
    SELECT e.external_id AS episode_id, r.show, v.source_title AS title, r.web_url,
           v.source_description AS description,
           1 - (v.embedding <=> :'queryvector'::vector(256)) AS similarity
    FROM episode_embedding v
    JOIN rf r USING (episode_id)
    JOIN episode_identity_external e ON e.id = v.episode_id
    JOIN embedding_model model ON model.id = v.model_id
    WHERE model.configuration @> '{
        "model_id": "google/embeddinggemma-2",
        "revision": "914f7f89142e33e77833254d9c9b90c3cef7303b",
        "precision": "float32", "dimensions": 256, "normalized": true,
        "document_template": "title: {title} | text: {description}",
        "query_prefix": "task: search result | query: "
    }'::jsonb
), unique_documents AS (
    SELECT DISTINCT ON (title, description) *,
           count(*) OVER (PARTITION BY title, description) AS duplicate_count
    FROM documents
    ORDER BY title, description, (NULLIF(btrim(web_url), '') IS NOT NULL) DESC,
             similarity DESC, episode_id
)
SELECT episode_id, show, title, web_url, similarity, duplicate_count,
       left(description, 300) AS description
FROM unique_documents
ORDER BY similarity DESC, episode_id
LIMIT 10;
\else
-- Search FP32 256D Gemma vectors, then collapse identical full matching passages.
-- Keep catalogue records intact; prefer a representative with a usable URL.
WITH best_per_episode AS (
    SELECT DISTINCT ON (c.episode_id)
        c.episode_id, c.chunk_no, c.start_seq, c.end_seq,
        c.start_time, c.end_time,
        1 - (v.embedding <=> :'queryvector'::vector(256)) AS similarity
    FROM chunk_embedding v
    JOIN semantic_chunk c USING (episode_id, strategy_id, chunk_no)
    JOIN embedding_model model ON model.id = v.model_id
    WHERE model.configuration @> '{
        "model_id": "google/embeddinggemma-2",
        "revision": "914f7f89142e33e77833254d9c9b90c3cef7303b",
        "precision": "float32", "dimensions": 256, "normalized": true,
        "document_prefix": "title: none | text: ",
        "query_prefix": "task: search result | query: "
    }'::jsonb
    ORDER BY c.episode_id, v.embedding <=> :'queryvector'::vector(256), c.strategy_id, c.chunk_no, v.model_id
), whitespace AS (
    -- Match Python str.strip(), including Unicode whitespace.
    SELECT string_agg(chr(code), '') AS characters
    FROM unnest(ARRAY[9,10,11,12,13,28,29,30,31,32,133,160,5760,
        8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,
        8232,8233,8239,8287,12288]) AS code
), passages AS MATERIALIZED (
    -- Reconstruct before deduplication and LIMIT so repeats do not consume slots.
    SELECT e.external_id AS episode_id, r.show, r.title, r.web_url,
           m.chunk_no, m.start_time, m.end_time, m.similarity, t.text
    FROM best_per_episode m
    JOIN rf r ON r.episode_id = m.episode_id
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
SELECT episode_id, show, title, web_url, chunk_no, start_time, end_time,
       similarity, duplicate_count, left(text, 300) AS text
FROM unique_passages
ORDER BY similarity DESC, episode_id
LIMIT 10;
\endif
