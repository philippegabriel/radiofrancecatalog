SELECT source_title,source_description,embedding::text
FROM episode_embedding WHERE episode_id=%s AND model_id=%s;
