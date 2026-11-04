INSERT INTO shows (slug, url) VALUES (%s, %s)
ON CONFLICT (slug) DO UPDATE SET url = shows.url
RETURNING slug, url, enabled;
