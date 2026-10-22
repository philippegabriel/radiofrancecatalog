INSERT INTO import_run(embedding_manifest_sha256,chunk_manifest_sha256,download_manifest_sha256,show,
 strategy_id,model_id,catalogue_episode_count,transcript_count,chunk_count)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s);
