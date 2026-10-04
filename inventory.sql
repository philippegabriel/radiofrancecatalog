SELECT 'rf' AS table_name, count(*) AS record_count FROM rf
UNION ALL
SELECT 'semantic_chunk', count(*) FROM semantic_chunk
UNION ALL
SELECT 'transcript_segment', count(*) FROM transcript_segment
ORDER BY table_name;

SELECT show, count(*) AS record_count
FROM rf
GROUP BY show
ORDER BY show;
