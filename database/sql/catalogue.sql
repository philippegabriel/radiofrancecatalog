INSERT INTO rf(episode_id,show,published_iso,published_ts,title,description,web_url,podcast_title,podcast_url,player_url)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT(episode_id) DO UPDATE SET
 show=EXCLUDED.show, published_iso=EXCLUDED.published_iso, published_ts=EXCLUDED.published_ts,
 title=EXCLUDED.title, description=EXCLUDED.description, web_url=EXCLUDED.web_url,
 podcast_title=EXCLUDED.podcast_title, podcast_url=EXCLUDED.podcast_url, player_url=EXCLUDED.player_url;
