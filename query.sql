select 
    date(published_iso) as date,
    title,
    description,
    web_url as "Web URL",
    podcast_title as "Podcast title",
    podcast_url as "Podcast URL",
    player_url as "Player URL",
    id
from rf_html;
