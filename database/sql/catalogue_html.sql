SELECT external_id, show,
       COALESCE(to_char(COALESCE(published_iso, to_timestamp(published_ts)) AT TIME ZONE 'Europe/Paris', 'YYYY-MM-DD'), ''),
       COALESCE(title, ''), COALESCE(description, ''), COALESCE(web_url, '')
FROM episodes_external
ORDER BY show, published_ts DESC, external_id;
