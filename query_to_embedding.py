#!/usr/bin/env python3
"""Encode a search query with the cached corpus-compatible EmbeddingGemma 2 model."""

import argparse
import json
from pathlib import Path
from typing import cast

from radiofrance.jobs.embedding import (
    DIMENSIONS, MAX_TOKENS, QUERY_PREFIX, SentenceEncoder,
    cached_snapshot, default_model_cache, validate_vector, verify_model,
)


def parse_args() -> argparse.Namespace:
    """Accept a query and offline model-cache settings; no token is needed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-cache', type=Path, default=default_model_cache(),
                        help='Hugging Face Hub cache containing the pinned model revision')
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('query', help='Natural-language search query')
    return parser.parse_args()


def encode_query(query: str, cache: Path, threads: int = 4) -> list[float]:
    """Return one FP32 CPU query vector, truncating to 256D before normalization."""
    if not query.strip() or threads < 1:
        raise ValueError('Provide a nonempty query and positive thread count')
    verify_model(cache)
    encoder = SentenceEncoder(cached_snapshot(cache), batch_size=1, threads=threads)
    formatted = QUERY_PREFIX + query
    if len(encoder.model.tokenizer(formatted, truncation=False)['input_ids']) > MAX_TOKENS:
        raise ValueError('Query exceeds model token limit')
    vector = cast(list[float], encoder.model.encode(formatted, truncate_dim=DIMENSIONS,
        normalize_embeddings=True, show_progress_bar=False).tolist())
    validate_vector(vector)
    return vector


def main() -> None:
    """Print only the PostgreSQL-compatible vector on standard output."""
    args = parse_args()
    print(json.dumps(encode_query(args.query, args.model_cache, args.threads), allow_nan=False))


if __name__ == '__main__':
    main()
