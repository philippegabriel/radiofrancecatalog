#!/usr/bin/env bash
# Exercise real JSON function imports and queries without touching an existing database.
set -euo pipefail
cd "$(dirname "$0")/.."
export PGDATABASE="radiofrance_schema_test_${$}"
work=$(mktemp -d)
createdb "$PGDATABASE"
cleanup() { dropdb --if-exists "$PGDATABASE"; rm -rf "$work"; }
trap cleanup EXIT
psql_cmd=(psql -X -v ON_ERROR_STOP=1)
python_cmd="$(pwd)/venv/airflow/bin/python"
"${psql_cmd[@]}" -f schema.sql > "$work/schema.log"
episode=12345678-1234-4234-8234-123456789abc_5
cat > "$work/catalogue.csv" <<CSV
show,published_iso,published_ts,title,description,web_url,podcast_title,podcast_url,player_url,id
affaires-sensibles,,100,test,description,https://example.test,Podcast,,,${episode}
CSV
"${psql_cmd[@]}" -f importcatalogue.sql < "$work/catalogue.csv"
"${psql_cmd[@]}" -f importcatalogue.sql < "$work/catalogue.csv"
cat > "$work/$episode.json" <<'JSON'
{"transcript":[{"start":0,"end":1,"speaker":"speaker","text":" hello "},{"start":1,"end":2,"speaker":"speaker","text":"   "},{"start":2,"end":3,"speaker":"speaker","text":" world "}]}
JSON
printf '%s,0,0,2,0,3,hello world\r\n' "$episode" > "$work/chunks.csv"
RF_TRANSCRIPT_JSON="$work/$episode.json" "${psql_cmd[@]}" -v episode_id="$episode" -f inserttranscript.sql
"${psql_cmd[@]}" -v episode_id="$episode" -f insertchunks.sql < "$work/chunks.csv"
vector="[1$(printf ',0%.0s' {1..383})]"
printf '%s,0,"%s"\n' "$episode" "$vector" > "$work/embeddings.csv"
"${psql_cmd[@]}" -v episode_id="$episode" -f insertembeddings.sql < "$work/embeddings.csv"
# Deferred boundary references allow a complete delete/reload of the transcript.
RF_TRANSCRIPT_JSON="$work/$episode.json" "${psql_cmd[@]}" -v episode_id="$episode" -f inserttranscript.sql
# Removing a referenced end segment must fail at commit and restore all rows.
mkdir "$work/short"
printf '{"transcript":[{"start":0,"end":1,"text":"replacement"}]}' > "$work/short/$episode.json"
if RF_TRANSCRIPT_JSON="$work/short/$episode.json" "${psql_cmd[@]}" -v episode_id="$episode" -f inserttranscript.sql > "$work/bad.log" 2>&1; then
    echo 'Missing referenced boundary unexpectedly accepted' >&2; exit 1
fi
[[ $("${psql_cmd[@]}" -Atc 'SELECT count(*) FROM transcript_segment') == 3 ]]
# Direct SQL writes must obey the schema even when bypassing the importer.
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
# Call the database function without Python: shape/type validation is server-side.
for document in 'null' '{}' '{"transcript":null}' '{"transcript":[{"start":true,"end":1,"text":"bad"}]}' '{"transcript":[{"start":0,"end":1,"text":"bad","speaker":42}]}'; do
    if "${psql_cmd[@]}" -v episode_id="$episode" -v document="$document" <<'SQL' > "$work/bad.log" 2>&1
SELECT import_transcript_json(:'episode_id', :'document'::jsonb);
SQL
    then
        echo 'Invalid JSON function call unexpectedly succeeded' >&2; exit 1
    fi
    [[ $("${psql_cmd[@]}" -Atc 'SELECT count(*) FROM transcript_segment') == 3 ]]
done
if "${psql_cmd[@]}" -c "SELECT import_transcript_json('unknown', '{\"transcript\":[]}'::jsonb)" > "$work/bad.log" 2>&1; then
    echo 'Unknown episode unexpectedly accepted' >&2; exit 1
fi
# A bad row must not delete previously imported data.
mkdir "$work/bad"
printf '{"transcript":[{"start":0,"end":1,"text":null}]}' > "$work/bad/$episode.json"
if RF_TRANSCRIPT_JSON="$work/bad/$episode.json" "${psql_cmd[@]}" -v episode_id="$episode" -f inserttranscript.sql > "$work/bad.log" 2>&1; then
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
# Pretty-printed JSON and escaped text survive transport as one JSON value.
cat > "$work/${episode%_5}_1.json" <<'JSON'
{
  "transcript": [
    {"start": 0, "end": 1, "speaker": null, "text": "été, \"citation\"\nligne\\fin"},
    {"start": 1, "end": 2, "text": ""}
  ]
}
JSON
RF_TRANSCRIPT_JSON="$work/${episode%_5}_1.json" "${psql_cmd[@]}" -v episode_id="${episode%_5}_1" -f inserttranscript.sql
"${psql_cmd[@]}" <<'SQL'
DO $$ BEGIN
    IF (SELECT text FROM transcript_segment t JOIN episode_identity_external e ON e.id=t.episode_id WHERE e.external_id LIKE '%_1' AND t.seq=0) IS DISTINCT FROM E'été, "citation"\nligne\\fin' THEN
        RAISE EXCEPTION 'JSON text transport changed the source';
    END IF;
END $$;
SQL
# Exercise Make's actual recipes and dependency ordering in an isolated workspace.
mkdir -p "$work/make/data/transcripts" "$work/make/data/chunks" "$work/make/data/embeddings" "$work/make/transcripts"
cp psql.mk schema.sql importcatalogue.sql inserttranscript.sql insertchunks.sql insertembeddings.sql chunk_json_to_csv.py transcript_json.py radiofrance_types.py "$work/make/"
cp "$work/$episode.json" "$work/make/transcripts/$episode.json"
cp "$work/catalogue.csv" "$work/make/data/affaires-sensibles.csv"
# Chunk generation requires no database; even an invalid PSQL command is unused.
make -C "$work/make" -f psql.mk PYTHON="$python_cmd" PSQL=false "data/chunks/$episode.csv"
cmp "$work/chunks.csv" "$work/make/data/chunks/$episode.csv"
cp "$work/chunks.csv" "$work/make/data/chunks/$episode.csv"
cp "$work/embeddings.csv" "$work/make/data/embeddings/$episode.csv"
make -C "$work/make" -f psql.mk PYTHON="$python_cmd" PSQL="psql -X -v ON_ERROR_STOP=1" data/affaires-sensibles.db
make -j 4 -C "$work/make" -f psql.mk PYTHON="$python_cmd" PSQL="psql -X -v ON_ERROR_STOP=1" "data/embeddings/$episode.db"
[[ -f "$work/make/data/transcripts/$episode.db" ]]
[[ ! -f "$work/make/data/transcripts/$episode.csv" ]]
[[ -f "$work/make/data/chunks/$episode.db" ]]
# An empty transcript without an embedding artifact must not trigger a download.
printf '{"transcript":[]}' > "$work/make/transcripts/${episode%_5}_1.json"
make -C "$work/make" -f psql.mk PYTHON="$python_cmd" PSQL="psql -X -v ON_ERROR_STOP=1" uploadembeds
[[ ! -f "$work/make/data/embeddings/${episode%_5}_1.csv" ]]
[[ $("${psql_cmd[@]}" -Atc 'SELECT count(*) FROM semantic_chunk WHERE embedding IS NOT NULL') == 1 ]]
"${psql_cmd[@]}" -f droptables.sql
"${psql_cmd[@]}" -f schema.sql > "$work/schema.log"
echo 'Compact schema integration checks passed.'
