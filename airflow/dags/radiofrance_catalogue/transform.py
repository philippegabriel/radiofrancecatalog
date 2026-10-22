"""Transform explicit transcript inputs using the existing chunker and an embedding API."""
import csv
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import TypedDict, cast
from urllib.request import Request, urlopen

from radiofrance_catalogue.storage import PROJECT, s3_uri

MODEL = 'intfloat/multilingual-e5-small'
DIMENSIONS = 384


class EmbeddingItem(TypedDict):
    index: int
    embedding: list[float]


class TokenResponse(TypedDict):
    tokens: list[int]


class EmbeddingResponse(TypedDict):
    data: list[EmbeddingItem]


class ArtifactEvent(TypedDict):
    episode_id: str
    source_uri: str
    sha256: str


class EmbeddingEvent(ArtifactEvent):
    model: str
    dimensions: int


def transcript_id(source: str) -> str:
    """Validate an input belongs to the configured transcript prefix; return its ID."""
    prefix = s3_uri('data/transcripts/')
    if not source.startswith(prefix):
        raise ValueError('Transcript must be in the configured S3 transcript prefix')
    filename = source.removeprefix(prefix)
    if not filename.endswith('.json') or '/' in filename or filename in ('.json', '..json'):
        raise ValueError('Invalid transcript filename')
    return filename[:-5]


def make_chunk_file(source: Path, target: Path) -> None:
    """Run the existing chunker and atomically write its unchanged CSV format."""
    temporary = target.with_suffix('.csv.tmp')
    with temporary.open('w', encoding='utf-8', newline='') as output:
        subprocess.run([sys.executable, '-m', 'radiofrance.chunk_json_to_csv', str(source)],
                       stdout=output, check=True, cwd=PROJECT)
    temporary.replace(target)


def embedding(text: str) -> list[float]:
    """Request one E5 passage vector; validate dimensions and normalize to unit length.

    RF_EMBEDDING_URL is the full /v1/embeddings endpoint. The server must serve
    the existing E5 model under its canonical name and expose llama.cpp /tokenize.
    Preserve the existing 512-token truncation; this client does not load models.
    """
    endpoint = os.environ.get('RF_EMBEDDING_URL', 'http://127.0.0.1:8081/v1/embeddings')
    headers = {'Content-Type': 'application/json'}
    if key := os.environ.get('RF_EMBEDDING_API_KEY'):
        headers['Authorization'] = f'Bearer {key}'
    if not endpoint.endswith('/v1/embeddings'):
        raise ValueError('RF_EMBEDDING_URL must end with /v1/embeddings')
    tokenize = Request(endpoint.removesuffix('/v1/embeddings') + '/tokenize',
                       data=json.dumps({'content': f'passage: {text}', 'add_special': True}).encode(),
                       headers=headers, method='POST')
    with urlopen(tokenize, timeout=120) as response:
        tokens = cast(TokenResponse, json.load(response))['tokens']
    # Match SentenceTransformer's 512-token truncation, retaining the final EOS.
    if len(tokens) > 512:
        tokens = tokens[:511] + tokens[-1:]
    request = Request(endpoint, data=json.dumps({
        'model': MODEL, 'input': tokens, 'encoding_format': 'float',
    }).encode(), headers=headers, method='POST')
    with urlopen(request, timeout=120) as response:
        result = cast(EmbeddingResponse, json.load(response))
    items = result['data']
    if len(items) != 1 or items[0]['index'] != 0:
        raise ValueError('Expected exactly one embedding at index zero')
    vector = items[0]['embedding']
    if len(vector) != DIMENSIONS or not all(math.isfinite(value) for value in vector):
        raise ValueError('Expected a finite 384-dimensional E5 embedding')
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        raise ValueError('Embedding has zero norm')
    return [value / norm for value in vector]


def make_embedding_file(source: Path, target: Path) -> None:
    """Embed each chunk through the persistent server and atomically publish a CSV.

    Preserve the existing episode ID, chunk number, PostgreSQL vector CSV layout.
    Empty transcripts produce empty chunk and embedding CSVs without API requests.
    """
    temporary = target.with_suffix('.csv.tmp')
    with source.open(encoding='utf-8', newline='') as stream, temporary.open('w', encoding='utf-8', newline='') as output:
        writer = csv.writer(output)
        for row in csv.reader(stream):
            vector = embedding(row[6])
            writer.writerow([row[0], row[1], '[' + ','.join(str(value) for value in vector) + ']'])
    temporary.replace(target)
