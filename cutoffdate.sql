SELECT COALESCE(
    strftime(
        '%Y-%m-%dT%H:%M:%S+00:00',
        MAX(published_ts),
        'unixepoch'
    ),
    '1900-01-01T00:00:00+00:00'
)
FROM rf;
