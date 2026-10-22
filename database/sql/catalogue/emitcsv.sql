SELECT *
FROM rf_external
WHERE show = :'show'
ORDER BY published_ts;