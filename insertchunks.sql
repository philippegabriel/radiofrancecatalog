BEGIN;

DELETE FROM semantic_chunk
WHERE episode_id = :'episode_id';

\copy semantic_chunk (episode_id, chunk_no, start_seq, end_seq, start_time, end_time, text) FROM pstdin WITH (FORMAT csv)

COMMIT;

