#!/usr/bin/env python3

import csv
import json
import sys
from pathlib import Path
from typing import cast
from radiofrance_types import Chunk, Transcript, TranscriptSegment


TARGET_SIZE = 1000
MIN_SIZE = 300


def chunk_text(chunk: Chunk) -> str:
    return " ".join(
        segment["text"].strip()
        for _, segment in chunk
    )


def make_chunks(segments: list[TranscriptSegment]) -> list[Chunk]:
    chunks: list[Chunk] = []
    chunk: Chunk = []
    chunk_size = 0

    for seq, segment in enumerate(segments):
        text = segment["text"].strip()

        if not text:
            continue

        chunk.append((seq, segment))
        chunk_size += len(text) + 1

        if chunk_size >= TARGET_SIZE:
            chunks.append(chunk)
            chunk = []
            chunk_size = 0

    # Keep the final incomplete chunk for now.
    if chunk:
        chunks.append(chunk)

    # Avoid producing a very small final chunk.
    # Merge it into the preceding chunk instead.
    if len(chunks) >= 2:
        final_text = chunk_text(chunks[-1])

        if len(final_text) < MIN_SIZE:
            chunks[-2].extend(chunks[-1])
            chunks.pop()

    return chunks


def main() -> None:
    if len(sys.argv) != 2:
        print(
            f"Usage: {Path(sys.argv[0]).name} TRANSCRIPT.json",
            file=sys.stderr,
        )
        sys.exit(1)

    path = Path(sys.argv[1])
    episode_id = path.stem

    with path.open(encoding="utf-8") as f:
        data = cast(Transcript, json.load(f))

    chunks = make_chunks(data["transcript"])

    writer = csv.writer(sys.stdout)

    for chunk_no, chunk in enumerate(chunks):
        start_seq = chunk[0][0]
        end_seq = chunk[-1][0]

        start_time = chunk[0][1]["start"]
        end_time = chunk[-1][1]["end"]

        text = chunk_text(chunk)

        writer.writerow([
            episode_id,
            chunk_no,
            start_seq,
            end_seq,
            start_time,
            end_time,
            text,
        ])


if __name__ == "__main__":
    main()
