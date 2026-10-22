SELECT chunk_manifest_sha256, download_manifest_sha256, show,
 catalogue_episode_count, transcript_count, chunk_count, strategy_id, model_id
FROM import_run WHERE embedding_manifest_sha256=%s;
