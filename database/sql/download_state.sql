-- Read a show's watermark and boundary IDs, or all IDs for explicit date ranges.
WITH state AS (
    SELECT COALESCE(MAX(published_ts), 0) AS cutoff_timestamp,
           count(*) AS episode_count
    FROM episodes_external WHERE show = %s
)
SELECT state.cutoff_timestamp, state.episode_count,
       COALESCE(array_agg(e.external_id ORDER BY e.external_id)
           FILTER (WHERE e.id IS NOT NULL), ARRAY[]::text[]) AS existing_ids
FROM state
LEFT JOIN episodes_external r ON r.show = %s AND (r.published_ts = state.cutoff_timestamp OR %s)
LEFT JOIN episode_identity_external e ON e.id = r.episode_id
GROUP BY state.cutoff_timestamp, state.episode_count;
