# Fixed integration sample

`le-cours-de-l-histoire.json` selects exactly ten episodes for the
full Airflow integration workflow. IDs are committed explicitly; the sample must
not be replaced with the latest ten episodes during a run.

The selected episodes were published from 15 to 30 September 2026. They were
chosen from the existing show catalogue, selecting distinct AOD UUIDs. All ten
transcript endpoints were checked on 22 October 2026 and returned nonempty,
valid transcripts. Titles and publication timestamps provide human-readable
context; the episode IDs define membership.

Each entry records the observed segment count and transcript SHA256. Checksums
use the download job's existing `write_transcript_json` serialization followed
by a newline. They identify the exact artifact bytes, including formatting.
A deliberately changed serialization requires updating these fingerprints after
review; missing/changed source data must not silently select replacement episodes.
The fixture contains no transcript text, model weights or credentials. Raw
transcripts downloaded while checking the selection remain in the Git-ignored
`data/download-pilot/integration-selection/` directory.

The integration workflow consumes this selection and verifies that
all ten IDs complete the pipeline. The existing code-validation workflow remains
model-free and does not call these live endpoints.
