SELECT chunk_run, download_run, show,
 catalogue_episode_count, transcript_count, chunk_count, strategy_id, model_id
FROM import_run WHERE embedding_run=%s;
