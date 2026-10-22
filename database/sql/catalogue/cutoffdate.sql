SELECT COALESCE(
    to_char(
        to_timestamp(MAX(published_ts)),
        'YYYY-MM-DD"T"HH24:MI:SSOF'
    ),
    '1900-01-01T00:00:00+00:00'
)
FROM rf
WHERE show = :'show';