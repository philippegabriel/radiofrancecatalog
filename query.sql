select 
    date(published_iso) as date,
    title,
    description,
    web_url as "Web URL"
from rf_html
ORDER BY published_ts;
