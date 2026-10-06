#!/usr/bin/env python3

import argparse
import csv
from pathlib import Path
from collections.abc import Iterable

import numpy as np
from numpy.typing import NDArray
from sentence_transformers import SentenceTransformer


MODEL_NAME = "intfloat/multilingual-e5-small"

EPISODE_ID_COLUMN = 0
CHUNK_NO_COLUMN = 1
TEXT_COLUMN = 6


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate embeddings for semantic chunks."
    )
    parser.add_argument(
        "--hftoken",
        required=True,
        type=Path,
        help="File containing the Hugging Face access token",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Directory where embedding CSV files are written",
    )
    parser.add_argument(
        "chunk_files",
        nargs="+",
        type=Path,
        help="Chunk CSV files to process",
    )
    return parser.parse_args()


def read_token(path: Path) -> str:
    token = path.read_text(encoding="utf-8").strip()

    if not token:
        raise ValueError(f"Empty token file: {path}")

    return token


def read_chunks(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.reader(file))


def vector_to_string(vector: Iterable[float]) -> str:
    return "[" + ",".join(str(value) for value in vector) + "]"


def generate_embeddings(
    model: SentenceTransformer,
    rows: list[list[str]],
) -> NDArray[np.float32]:
    texts = [
        f"passage: {row[TEXT_COLUMN]}"
        for row in rows
    ]

    return model.encode(
        texts,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )


def write_embeddings(
    path: Path,
    rows: list[list[str]],
    embeddings: NDArray[np.float32],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)

        for row, embedding in zip(rows, embeddings):
            writer.writerow([
                row[EPISODE_ID_COLUMN],
                row[CHUNK_NO_COLUMN],
                vector_to_string(embedding),
            ])
def main() -> None:
    args = parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    pending_files: list[tuple[Path, Path]] = []

    for chunk_file in args.chunk_files:
        output_file = args.output_dir / chunk_file.name

        if output_file.exists():
            print(f"Skipping {output_file}")
            continue

        pending_files.append((chunk_file, output_file))

    if not pending_files:
        return

    token = read_token(args.hftoken)

    model = SentenceTransformer(
        MODEL_NAME,
        token=token,
    )

    for chunk_file, output_file in pending_files:
        rows = read_chunks(chunk_file)

        if not rows:
            continue

        embeddings = generate_embeddings(model, rows)

        write_embeddings(
            output_file,
            rows,
            embeddings,
        )

        print(f"Wrote {output_file}")

if __name__ == "__main__":
    main()
