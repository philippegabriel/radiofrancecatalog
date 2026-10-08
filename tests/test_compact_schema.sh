#!/usr/bin/env bash
# Exercise real CSV imports and queries without touching an existing database.
set -euo pipefail
cd "$(dirname "$0")/.."
export PGDATABASE="radiofrance_schema_test_${$}"
work=$(mktemp -d)
createdb "$PGDATABASE"
cleanup() { dropdb --if-exists "$PGDATABASE"; rm -rf "$work"; }
trap cleanup EXIT
psql_cmd=(psql -X -v ON_ERROR_STOP=1)
"${psql_cmd[@]}" -f schema.sql > "$work/schema.log"
episode=12345678-1234-4234-8234-123456789abc_5
cat > "$work/catalogue.csv" <<CSV
show,published_iso,published_ts,title,description,web_url,podcast_title,podcast_url,player_url,id
affaires-sensibles,,100,test,description,https://example.test,Podcast,,,${episode}
CSV
"${psql_cmd[@]}" -f importcatalogue.sql < "$work/catalogue.csv"
"${psql_cmd[@]}" -f importcatalogue.sql < "$work/catalogue.csv"
printf '%s,0,0,1,speaker, hello \n%s,1,1,2,speaker,   \n%s,2,2,3,speaker, world \n' "$episode" "$episode" "$episode" > "$work/transcript.csv"
printf '%s,0,0,2,0,3,hello world\n' "$episode" > "$work/chunks.csv"
"${psql_cmd[@]}" -v episode_id="$episode" -f inserttranscript.sql < "$work/transcript.csv"
"${psql_cmd[@]}" -v episode_id="$episode" -f insertchunks.sql < "$work/chunks.csv"
vector="[1$(printf ',0%.0s' {1..383})]"
printf '%s,0,"%s"\n' "$episode" "$vector" > "$work/embeddings.csv"
"${psql_cmd[@]}" -v episode_id="$episode" -f insertembeddings.sql < "$work/embeddings.csv"
# Deferred boundary references allow a complete delete/reload of the transcript.
"${psql_cmd[@]}" -v episode_id="$episode" -f inserttranscript.sql < "$work/transcript.csv"
# Removing a referenced end segment must fail at commit and restore all rows.
head -n 2 "$work/transcript.csv" > "$work/missing-boundary.csv"
if "${psql_cmd[@]}" -v episode_id="$episode" -f inserttranscript.sql < "$work/missing-boundary.csv" > "$work/bad.log" 2>&1; then
    echo 'Missing referenced boundary unexpectedly accepted' >&2; exit 1
fi
[[ $("${psql_cmd[@]}" -Atc 'SELECT count(*) FROM transcript_segment') == 3 ]]
# Direct SQL writes must obey the schema even when bypassing CSV validation.
for boundaries in '99,100' '0,100' '2,0'; do
    if "${psql_cmd[@]}" -c "INSERT INTO semantic_chunk (episode_id, chunk_no, start_seq, end_seq, start_time, end_time) SELECT episode_id, 99, $boundaries, 0, 3 FROM rf" > "$work/bad.log" 2>&1; then
        echo "Invalid boundaries unexpectedly accepted: $boundaries" >&2; exit 1
    fi
done
"${psql_cmd[@]}" <<'SQL'
DO $$ BEGIN
    IF (SELECT count(*) FROM episode_identity) <> 1 OR (SELECT count(*) FROM rf) <> 1 THEN
        RAISE EXCEPTION 'Repeated catalogue import did not preserve identities';
    END IF;
    IF (SELECT external_id FROM episode_identity_external) <> '12345678-1234-4234-8234-123456789abc_5' THEN
        RAISE EXCEPTION 'External ID round trip failed';
    END IF;
    IF (SELECT pg_typeof(episode_id)::text FROM semantic_chunk LIMIT 1) <> 'integer' THEN
        RAISE EXCEPTION 'Chunk foreign key is not integer';
    END IF;
    IF (SELECT pg_typeof(embedding)::text FROM semantic_chunk LIMIT 1) <> 'halfvec' THEN
        RAISE EXCEPTION 'Embedding is not half precision';
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='semantic_chunk' AND column_name='text') THEN
        RAISE EXCEPTION 'Redundant text column exists';
    END IF;
END $$;
SQL
"${psql_cmd[@]}" -At -F '|' -v queryvector="$vector" -f query.sql > "$work/search.txt"
[[ $(cut -d '|' -f 9 "$work/search.txt") == 'hello world' ]]
[[ $(cut -d '|' -f 1 "$work/search.txt") == "$episode" ]]
"${psql_cmd[@]}" --csv -v show=affaires-sensibles -f emitcsv.sql > "$work/export.csv"
"${psql_cmd[@]}" -f importcatalogue.sql < "$work/export.csv"
# A bad row must not delete previously imported data.
printf 'bad_id,0,0,1,speaker,replacement\n' > "$work/bad.csv"
if "${psql_cmd[@]}" -v episode_id="$episode" -f inserttranscript.sql < "$work/bad.csv" > "$work/bad.log" 2>&1; then
    echo 'Invalid transcript import unexpectedly succeeded' >&2; exit 1
fi
[[ $("${psql_cmd[@]}" -Atc 'SELECT count(*) FROM transcript_segment') == 3 ]]
printf '%s,0,0,2,0,3,incorrect text\n' "$episode" > "$work/bad-chunk.csv"
if "${psql_cmd[@]}" -v episode_id="$episode" -f insertchunks.sql < "$work/bad-chunk.csv" > "$work/bad.log" 2>&1; then
    echo 'Irrecoverable chunk text unexpectedly accepted' >&2; exit 1
fi
[[ $("${psql_cmd[@]}" -Atc 'SELECT count(*) FROM semantic_chunk WHERE embedding IS NOT NULL') == 1 ]]
printf '%s,99,"%s"\n' "$episode" "$vector" > "$work/bad-embedding.csv"
if "${psql_cmd[@]}" -v episode_id="$episode" -f insertembeddings.sql < "$work/bad-embedding.csv" > "$work/bad.log" 2>&1; then
    echo 'Missing chunk embedding import unexpectedly succeeded' >&2; exit 1
fi
# Existing Airflow CSV behaviour works against the compact database as scratch space.
RF_CATALOGUE_DSN="dbname=$PGDATABASE" venv/airflow/bin/python -m unittest discover -s airflow -p test_catalogue.py
sed 's/_5$/_1/' "$work/catalogue.csv" > "$work/second-suffix.csv"
"${psql_cmd[@]}" -f importcatalogue.sql < "$work/second-suffix.csv"
[[ $("${psql_cmd[@]}" -Atc 'SELECT count(*) FROM episode_identity') == 2 ]]
[[ $("${psql_cmd[@]}" -Atc 'SELECT count(DISTINCT source_uuid) FROM episode_identity') == 1 ]]
# Exercise Make's actual recipes and dependency ordering in an isolated workspace.
mkdir -p "$work/make/data/transcripts" "$work/make/data/chunks" "$work/make/data/embeddings" "$work/make/transcripts"
cp psql.mk schema.sql importcatalogue.sql inserttranscript.sql insertchunks.sql insertembeddings.sql chunk_json_to_csv.py transcript_json_to_csv.py "$work/make/"
# Older JSON fixture prevents CSV regeneration; this test uses existing CSVs.
printf '{}' > "$work/make/transcripts/$episode.json"
touch -t 200001010000 "$work/make/transcripts/$episode.json"
cp "$work/catalogue.csv" "$work/make/data/affaires-sensibles.csv"
cp "$work/transcript.csv" "$work/make/data/transcripts/$episode.csv"
cp "$work/chunks.csv" "$work/make/data/chunks/$episode.csv"
cp "$work/embeddings.csv" "$work/make/data/embeddings/$episode.csv"
make -C "$work/make" -f psql.mk PSQL="psql -X -v ON_ERROR_STOP=1" data/affaires-sensibles.db
make -j 4 -C "$work/make" -f psql.mk PSQL="psql -X -v ON_ERROR_STOP=1" "data/embeddings/$episode.db"
[[ -f "$work/make/data/transcripts/$episode.db" ]]
[[ -f "$work/make/data/chunks/$episode.db" ]]
[[ $("${psql_cmd[@]}" -Atc 'SELECT count(*) FROM semantic_chunk WHERE embedding IS NOT NULL') == 1 ]]
"${psql_cmd[@]}" -f droptables.sql
"${psql_cmd[@]}" -f schema.sql > "$work/schema.log"
echo 'Compact schema integration checks passed.'
