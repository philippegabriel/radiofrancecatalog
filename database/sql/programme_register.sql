INSERT INTO "show" (slug, url) VALUES (%s, %s)
ON CONFLICT (slug) DO UPDATE SET url = "show".url
RETURNING slug, url, enabled;
