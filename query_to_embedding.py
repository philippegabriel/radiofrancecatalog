#!/usr/bin/env python3

import argparse
from pathlib import Path
from typing import Sequence

from sentence_transformers import SentenceTransformer


MODEL_NAME = "intfloat/multilingual-e5-small"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate an embedding for a semantic search query."
    )
    parser.add_argument(
        "--hftoken",
        required=True,
        type=Path,
        help="File containing the Hugging Face access token",
    )
    parser.add_argument(
        "query",
        help="Natural-language search query",
    )
    return parser.parse_args()


def read_token(path: Path) -> str:
    token = path.read_text(encoding="utf-8").strip()

    if not token:
        raise ValueError(f"Empty token file: {path}")

    return token


def vector_to_string(vector: Sequence[float]) -> str:
    return "[" + ",".join(str(value) for value in vector) + "]"


def main() -> None:
    args = parse_args()
    token = read_token(args.hftoken)

    model = SentenceTransformer(
        MODEL_NAME,
        token=token,
    )

    embedding = model.encode(
        f"query: {args.query}",
        normalize_embeddings=True,
    )

    print(vector_to_string(embedding))


if __name__ == "__main__":
    main()

