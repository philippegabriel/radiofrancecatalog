#!/usr/bin/env python3
"""Compare the local llama.cpp E5 server with the existing SentenceTransformer model."""
import json
import math
import sys
from pathlib import Path

from sentence_transformers import SentenceTransformer

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'airflow/dags'))
from radiofrance_catalogue.transform import MODEL, embedding

TEXTS = [
    'La politique française et les élections européennes.',
    'Une enquête sur la mémoire, les archives et les témoins de la guerre.',
    'Bonjour ! À quelle heure commence cette émission ? Le 12 octobre, à 18 h 30.',
    'Économie : inflation, pouvoir d’achat, énergie et financement des retraites.',
    'Les chercheurs expliquent comment les océans influencent le climat. ' * 15,
    'Un récit historique.\nUne seconde phrase avec des caractères : œ, æ, é, ç, €.',
    'La politique française, les archives et la mémoire collective. ' * 150,
]


def main() -> None:
    """Load cached original weights, compare passage vectors, and fail on incompatibility.

    Print cosine similarity and maximum coordinate error for each sample. No
    corpus files, S3 objects or database records are written. This is a small
    compatibility smoke test, not a retrieval-quality benchmark.
    """
    model = SentenceTransformer(MODEL, local_files_only=True)
    expected = model.encode([f'passage: {text}' for text in TEXTS], normalize_embeddings=True)
    passed = True
    for text, original in zip(TEXTS, expected):
        actual = embedding(text)
        reference = [float(value) for value in original]
        cosine = sum(a * b for a, b in zip(actual, reference)) / math.sqrt(sum(value * value for value in reference))
        error = max(abs(a - b) for a, b in zip(actual, reference))
        print(json.dumps({'text': text[:65], 'cosine': cosine, 'max_absolute_error': error}, ensure_ascii=False), flush=True)
        passed = passed and cosine >= 0.9999 and error <= 0.001
    if not passed:
        raise SystemExit('E5 compatibility check failed; do not mix these vectors into the existing corpus')
    print('E5 compatibility smoke test passed')


if __name__ == '__main__':
    main()
