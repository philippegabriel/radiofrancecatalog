WITH documents AS (
    SELECT e.external_id AS episode_id, r.show, v.source_title AS title, r.web_url,
           r.published_iso::text AS published, v.source_description AS text,
           1 - (v.embedding <=> %(vector)s::vector(256)) AS similarity,
           NULL::integer AS strategy_id, NULL::integer AS chunk_no,
           NULL::double precision AS start_time, NULL::double precision AS end_time
    FROM episode_embedding v
    JOIN episodes_external r USING (episode_id)
    JOIN episode_identity_external e ON e.id = v.episode_id
    JOIN embedding_model model ON model.id = v.model_id
    WHERE model.configuration @> %(configuration)s::jsonb
      AND (%(show)s::text IS NULL OR r.show = %(show)s)
      AND (%(date_from)s::date IS NULL OR r.published_iso >= %(date_from)s::date)
      AND (%(date_until)s::date IS NULL OR r.published_iso < %(date_until)s::date)
), unique_documents AS (
    SELECT DISTINCT ON (title, text) *,
           count(*) OVER (PARTITION BY title, text) AS duplicate_count
    FROM documents
    ORDER BY title, text, (NULLIF(btrim(web_url), '') IS NOT NULL) DESC,
             similarity DESC, episode_id
)
SELECT episode_id, show, title, web_url, published, similarity, duplicate_count,
       left(text, 1500) AS text, strategy_id, chunk_no, start_time, end_time
FROM unique_documents ORDER BY similarity DESC, episode_id LIMIT %(limit)s;
