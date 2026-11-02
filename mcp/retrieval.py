"""Read-only retrieval shared by the MCP tools, independent of the protocol."""
from collections.abc import Callable, Mapping
from contextlib import closing
from datetime import date, timedelta
import json
from pathlib import Path
from threading import Lock
from typing import Literal, NotRequired, Protocol, TypedDict, cast

import psycopg2
from psycopg2.extras import RealDictCursor

from radiofrance.jobs.embedding import (
    DIMENSIONS, DOCUMENT_PREFIX, MODEL_ID, MODEL_REVISION, QUERY_PREFIX,
    validate_vector,
)

type Progress = Callable[[int, int, str], None]

type Corpus = Literal['transcripts', 'descriptions']
type Source = Literal['transcripts', 'descriptions', 'both']


class ModelFilter(TypedDict):
    model_id: str
    revision: str
    precision: str
    dimensions: int
    normalized: bool
    query_prefix: str
    document_prefix: NotRequired[str]
    document_template: NotRequired[str]


class SearchParameters(TypedDict):
    vector: str
    configuration: str
    show: str | None
    date_from: date | None
    date_until: date | None
    limit: int


class Encoder(Protocol):
    def encode_formatted(self, formatted: list[str]) -> list[list[float]]:
        """Encode already formatted strings using the persistent model."""
        ...


class CorpusHit(TypedDict):
    episode_id: str
    show: str
    title: str | None
    web_url: str | None
    published: str | None
    similarity: float
    duplicate_count: int
    text: str
    strategy_id: int | None
    chunk_no: int | None
    start_time: float | None
    end_time: float | None


class Evidence(CorpusHit):
    source: Corpus
    rank: int


class SearchHit(TypedDict):
    episode_id: str
    show: str
    title: str | None
    web_url: str | None
    published: str | None
    similarity: float | None
    duplicate_count: int
    text: str
    strategy_id: int | None
    chunk_no: int | None
    start_time: float | None
    end_time: float | None
    source: Source
    fusion_score: float | None
    evidence: list[Evidence]


def combine_hits(ranked: list[tuple[Corpus, list[CorpusHit]]], limit: int,
                 mixed: bool) -> list[SearchHit]:
    """Fuse source ranks by episode using sum(1/(60+rank)); retain each source's evidence.

    Each corpus has one representative per episode after its existing duplicate
    collapse. Missing source hits contribute zero. Ties use the external episode
    ID. Transcript evidence supplies the primary snippet and passage reference.
    Raw cosine scores stay attached to their source; mixed similarity is null.
    """
    merged: dict[str, SearchHit] = {}
    for source, hits in ranked:
        for rank, hit in enumerate(hits, 1):
            evidence: Evidence = {**hit, 'source': source, 'rank': rank}
            identity = hit['episode_id']
            if identity not in merged:
                merged[identity] = {**hit, 'source': source,
                    'fusion_score': 0.0 if mixed else None, 'evidence': []}
            item = merged[identity]
            if any(entry['source'] == source for entry in item['evidence']):
                continue
            item['evidence'].append(evidence)
            if source == 'transcripts':
                item['text'] = hit['text']
                item['strategy_id'] = hit['strategy_id']
                item['chunk_no'] = hit['chunk_no']
                item['start_time'] = hit['start_time']
                item['end_time'] = hit['end_time']
            if len(item['evidence']) > 1:
                item['source'] = 'both'
            if mixed:
                item['similarity'] = None
                item['fusion_score'] = (item['fusion_score'] or 0.0) + 1.0 / (60 + rank)
    values = list(merged.values())
    if mixed:
        values.sort(key=lambda hit: (-(hit['fusion_score'] or 0.0), hit['episode_id']))
    return values[:limit]


class SearchResult(TypedDict):
    source: Source
    model: str
    revision: str
    dimensions: int
    results: list[SearchHit]


class Segment(TypedDict):
    seq: int
    start_time: float
    end_time: float
    speaker: str | None
    text: str


class Passage(TypedDict):
    episode_id: str
    strategy_id: int
    chunk_no: int
    start_seq: int
    end_seq: int
    start_time: float
    end_time: float
    text: str
    segments: list[Segment]


class Episode(TypedDict):
    episode_id: str
    show: str
    title: str | None
    description: str | None
    web_url: str | None
    podcast_url: str | None
    player_url: str | None
    published: str | None
    has_transcript: bool


class Retrieval:
    """Keep one encoder in memory; open short, read-only transactions per request."""

    def __init__(self, encoder: Encoder, dsn: str = '') -> None:
        self.encoder = encoder
        self.dsn = dsn
        self.lock = Lock()

    def rows(self, filename: str, parameters: Mapping[str, object]) -> list[dict[str, object]]:
        """Execute only bundled SQL, with bound parameters and a query timeout."""
        sql = (Path(__file__).parent / 'sql' / filename).read_text()
        with closing(psycopg2.connect(self.dsn, connect_timeout=5,
                             options='-c statement_timeout=60000 -c timezone=UTC')) as connection:
            connection.set_session(readonly=True)
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(sql, parameters)
                return [dict(row) for row in cursor.fetchall()]

    def check_database(self) -> None:
        """Check access to the catalogue and vector tables before accepting requests."""
        self.rows('ready.sql', {})

    def search(self, query: str, source: Source = 'both', show: str | None = None,
               date_from: str | None = None, date_to: str | None = None,
               limit: int = 10, *, progress: Progress | None = None) -> SearchResult:
        """Encode once, search selected corpora and report completed stages if requested.

        The optional synchronous callback receives increasing stage counts, the
        total stage count and a message. It runs before expensive operations so
        protocol adapters can publish updates while inference or SQL is running.
        """
        if source not in ('transcripts', 'descriptions', 'both'):
            raise ValueError('source must be transcripts, descriptions or both')
        if not query.strip() or len(query) > 4000:
            raise ValueError('query must contain 1–4000 characters')
        if not 1 <= limit <= 20:
            raise ValueError('limit must be between 1 and 20')
        start = date.fromisoformat(date_from) if date_from else None
        end = date.fromisoformat(date_to) if date_to else None
        if start and end and start > end:
            raise ValueError('date_from must not follow date_to')
        corpora: list[Corpus] = ['transcripts', 'descriptions'] if source == 'both' else [source]
        total = len(corpora) + 2

        def report(step: int, message: str) -> None:
            """Publish a stage boundary without coupling retrieval to MCP."""
            if progress is not None:
                progress(step, total, message)

        report(0, 'Preparing the query embedding; waiting for the encoder if busy.')
        with self.lock:
            vector = self.encoder.encode_formatted([QUERY_PREFIX + query.strip()])[0]
        validate_vector(vector)
        ranked: list[tuple[Corpus, list[CorpusHit]]] = []
        for step, corpus in enumerate(corpora, 1):
            report(step, 'Searching podcast transcripts.' if corpus == 'transcripts'
                   else 'Searching episode descriptions.')
            configuration: ModelFilter = {
                'model_id': MODEL_ID, 'revision': MODEL_REVISION, 'precision': 'float32',
                'dimensions': DIMENSIONS, 'normalized': True, 'query_prefix': QUERY_PREFIX,
            }
            if corpus == 'transcripts':
                configuration['document_prefix'] = DOCUMENT_PREFIX
            else:
                configuration['document_template'] = 'title: {title} | text: {description}'
            parameters: SearchParameters = {
                'vector': json.dumps(vector), 'configuration': json.dumps(configuration),
                'show': show, 'date_from': start,
                'date_until': end + timedelta(days=1) if end else None,
                'limit': max(50, limit * 5) if source == 'both' else limit,
            }
            ranked.append((corpus, cast(list[CorpusHit], self.rows(corpus + '.sql', parameters))))
        report(len(corpora) + 1, 'Merging and ranking the matching episodes.'
               if source == 'both' else 'Preparing the matching episodes.')
        hits = combine_hits(ranked, limit, mixed=source == 'both')
        report(total, f'Search complete: {len(hits)} matching episodes returned.')
        return {'source': source, 'model': MODEL_ID, 'revision': MODEL_REVISION,
                'dimensions': DIMENSIONS, 'results': hits}

    def episode(self, episode_id: str) -> Episode:
        """Fetch catalogue metadata by the external episode ID returned by search."""
        rows = self.rows('episode.sql', {'episode_id': episode_id})
        if not rows:
            raise ValueError('Episode not found')
        return cast(Episode, rows[0])

    def passage(self, episode_id: str, strategy_id: int, chunk_no: int,
                context_segments: int = 0) -> Passage:
        """Return a complete matching chunk and up to five adjacent segments each side."""
        if strategy_id < 1 or chunk_no < 0 or not 0 <= context_segments <= 5:
            raise ValueError('Invalid chunk identity or context_segments (expected 0–5)')
        rows = self.rows('passage.sql', {'episode_id': episode_id, 'strategy_id': strategy_id,
                        'chunk_no': chunk_no, 'context': context_segments})
        if not rows:
            raise ValueError('Passage not found')
        return cast(Passage, rows[0])
