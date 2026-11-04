-- This also checks SELECT privileges on the catalogue and both vector tables.
SELECT 1 FROM episodes LIMIT 0;
SELECT 1 FROM chunk_embedding LIMIT 0;
SELECT 1 FROM episode_embedding LIMIT 0;
