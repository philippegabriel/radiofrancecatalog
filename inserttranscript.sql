BEGIN;

DELETE FROM transcript_segment
WHERE episode_id = :'episode_id';

\copy transcript_segment (episode_id, seq, start_time, end_time, speaker, text) FROM pstdin WITH (FORMAT csv)

COMMIT;