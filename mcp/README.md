# Radio France MCP

A read-only semantic search server for the desktop client. It runs in its own
container, shares the catalogue PostgreSQL container, and keeps the pinned
EmbeddingGemma 2 CPU FP32 query encoder loaded. Queries use normalized 256D
vectors and the same formatting and duplicate collapse as the existing Make
queries. No Airflow process is required. The existing Make targets still work.

## Tools

- `search_podcasts(query, source="both", show=None, date_from=None,
  date_to=None, limit=10)`: search transcripts, episode descriptions, or both. `show`
  is an exact programme slug; dates are inclusive UTC publication dates in
  `YYYY-MM-DD` format. Returns at most 20 distinct matches, source links,
  snippets, similarity scores and stable transcript passage identities.
- `get_passage(episode_id, strategy_id, chunk_no, context_segments=0)`: retrieve
  the full chunk and optionally up to five adjacent transcript segments on each
  side, including timestamps in seconds. `text` remains the matching chunk;
  `segments` includes the requested context. Speaker labels are anonymous and
  local to the episode.
- `get_episode(episode_id)`: read catalogue metadata and source links.

Search returns one best chunk per episode, then collapses identical full
passages, preferring records with a usable URL. Description search collapses
identical title/description pairs. Filters apply before ranking and deduplication.
`duplicate_count` counts matching records within the selected filters.

Searches use `source="both"` by default, so ordinary requests automatically search
transcripts and descriptions. Specify a single source only to restrict retrieval. The query is encoded once; each corpus
returns up to `max(50, limit * 5)` candidates after its existing duplicate collapse.
Results are merged by external episode ID and ranked by reciprocal rank fusion:
`sum(1 / (60 + source_rank))`, with ranks starting at one and equal source weights.
A missing source contributes zero; ties use episode ID. Candidate depth is bounded,
so this is fusion of two retrieved lists rather than a global score over all episodes.
Aliases with different episode IDs may still survive across the two lists.

Each result has `source` (`transcripts`, `descriptions`, or `both`) and `evidence`
containing each matching source's snippet, cosine similarity, rank and passage
identity. The top-level snippet and passage identity prefer transcript evidence.
Mixed searches return a `fusion_score` and null top-level `similarity`; raw cosine
scores are never averaged. Single-source searches retain their cosine similarity
and have a null `fusion_score`. A source label means the episode appeared in that
source's candidate list, not an assertion that it lacks evidence in the other corpus.

Example: ask the assistant to call
`search_podcasts(query="général de Gaulle pendant la guerre")`.
Rebuild the image and restart `radiofrance-mcp` after changing the server code;
reconnect the desktop client to refresh its tool schema. Similarity
is a ranking score, not confidence. Publication dates are not event dates or
original broadcast dates. Missing URLs are returned as stored, never invented.

`retrieval.py` contains the independent retrieval layer; `server.py` is the thin
MCP adapter. SQL stays in `sql/`, uses bound parameters, and resolves compatible
embedding configurations rather than hardcoding database model IDs. Exact
pgvector search is retained. Each database request has a 60-second timeout.

## Progress during tool calls

Search streams MCP progress notifications as work happens: preparing the query
embedding, searching transcripts, searching descriptions, merging/ranking, and
completion. Messages include elapsed time, not an estimated completion time.
The stage counts describe completed operations, not a percentage of elapsed time.
Single-source searches report only the selected corpus. Episode and passage
retrieval also report start and completion. Search stage messages appear in
container logs without query text, credentials or transcript content.

The HTTP transport uses streamed responses (`json_response=False`) so progress
can arrive before the result. Blocking model and database operations run in worker
threads to keep the transport responsive. The server asks the assistant to give a
brief explanation before tool calls, covering the initial pause before retrieval.

Clients must request MCP progress and choose how to render it. The desktop app
may display it in tool activity rather than as chat messages, or may not display
it at all; delivery was tested with the SDK HTTP client, not verified in the
desktop UI. Server notifications cannot force chat messages into a client's UI.
Progress begins when a tool call starts, not while the assistant is deciding to
call a tool or while the service is initially loading the model. For service
startup, use `docker compose --profile mcp logs -f mcp`.

The transport behavior and client opt-in are described in the official
[MCP progress documentation](https://py.sdk.modelcontextprotocol.io/handlers/progress/).
Rebuild the container and restart the service to activate these changes, then
reconnect the desktop client to refresh server instructions.

## First setup

Run from the repository root, with the existing `.env` configured by the Airflow
setup. `RF_HF_HUB_CACHE` must point to the existing Hugging Face **hub** cache
containing revision `914f7f89142e33e77833254d9c9b90c3cef7303b` of
`google/embeddinggemma-2`. No model download occurs inside this container.

```bash
sudo docker compose up -d --wait postgres
sudo docker compose --profile mcp build mcp
sudo bash mcp/configure
sudo bash mcp/install-service
sudo systemctl start radiofrance-mcp
```

`configure` generates `RF_MCP_DATABASE_PASSWORD` in `.env` if absent, restricts
that file to its owner, and creates/updates the dedicated `radiofrance_mcp` login.
It works with the existing volume; it does not recreate databases or import data.
The existing PostgreSQL administrator performs this one-time setup. The server
receives only the dedicated login, with SELECT access to the required tables,
read-only transactions by default, and no access to Airflow. There is no arbitrary
SQL tool. Rerun `configure` after recreating the database volume.

The installer creates `/etc/systemd/system/radiofrance-mcp.service` and reloads
systemd. It does **not** start the service or enable it at boot. Docker must be
installed at `/usr/bin/docker`. The service records the current project path;
rerun the installer after moving the checkout.

```bash
sudo systemctl status radiofrance-mcp
sudo systemctl stop radiofrance-mcp
sudo systemctl start radiofrance-mcp
sudo docker compose --profile mcp logs --tail 100 mcp
```

Starting the service brings up MCP and PostgreSQL if needed, without Airflow.
Stopping it stops only MCP and frees the model's memory; PostgreSQL keeps running.
After rebuilding the image, use `sudo systemctl restart radiofrance-mcp`.
The oneshot systemd unit records the requested start/stop state; use Compose
`ps` and container logs to diagnose a later container failure.

The MCP Compose profile keeps normal pipeline startup unchanged. The server has
a 4 GiB memory limit and one worker to avoid duplicating the model. Default query
threads: 4 (`RF_MCP_THREADS`). Allow roughly 2–3 GiB for the loaded model plus the
existing database. `/health` becomes available after model loading and the initial
database access check; it is an HTTP readiness probe, not a periodic database
availability check.

## Desktop connection

Keep the server running while adding a Streamable HTTP connection:

- Name: `radiofrance-mcp`
- URL: `http://127.0.0.1:8083/mcp`

For a desktop client using the shared Codex configuration, the equivalent entry
in `~/.codex/config.toml` is:

```toml
[mcp_servers.radiofrance-mcp]
url = "http://127.0.0.1:8083/mcp"
startup_timeout_sec = 30
tool_timeout_sec = 120
required = false
```

Start the service before restarting/reconnecting the desktop client. Configuration
can remain saved when the service is stopped; calls require it to be running.
This implementation does not modify desktop settings automatically.

The port is published on **127.0.0.1 only**, with the SDK's localhost Host/Origin
checks retained. No public endpoint, tunnel, or HTTP authentication is configured.
Other local processes can access it. Do not expose the port on external interfaces.
`RF_MCP_PORT` can change the published port; update the desktop URL accordingly.

## Tests

Model-free tests use real SQL on a disposable PostgreSQL cluster and real
Streamable HTTP calls with a fake retrieval backend. They check filters,
deduplication, passage reconstruction, read-only permissions, denial of Airflow
access, tool schemas and a single shared server lifespan. No production data is
changed and no model weights are required.

```bash
python3 -m venv venv/mcp
venv/mcp/bin/pip install -r mcp/requirements-test.txt
bash mcp/test/run-tests
venv/airflow/bin/pyright
```

PostgreSQL and pgvector development/test prerequisites are the same as
`database/test/test-setup`. CI runs this suite alongside the pipeline unit tests.
Container build and actual-model checks are separate from these fast tests.
