"""Shared corpus locations and atomic publication without content hashes."""

import json
import os
from pathlib import Path
from typing import Mapping

CHUNK_DIRECTORY = 'chunks/segment-characters-v1-1000-300'
MODEL_REVISION = '914f7f89142e33e77833254d9c9b90c3cef7303b'
EMBEDDING_DIRECTORY = f'embeddings/embeddinggemma2-fp32-256/{MODEL_REVISION}/segment-characters-v1-1000-300'


def artifact_root(directory: Path) -> Path:
    """Resolve an optional shared-data anchor; old self-contained artifacts stay local.

    Shared manifests may reference only an ancestor named data. Artifact paths
    themselves are still checked for traversal and escaping symlinks by callers.
    """
    manifest = json.loads((directory / 'manifest.json').read_text())
    anchor = manifest.get('artifact_root')
    if anchor is None:
        return directory
    if not isinstance(anchor, str) or not anchor or any(part != '..' for part in anchor.split('/')):
        raise ValueError('Invalid shared artifact root')
    root = (directory / anchor).resolve()
    if root.name != 'data' or not directory.resolve().is_relative_to(root):
        raise ValueError('Shared artifacts must be inside data')
    return root


def shared_metadata(manifest: Mapping[str, object], directory: Path, data_root: Path) -> dict[str, object]:
    """Add the relative data anchor to a completed stage manifest."""
    if not directory.resolve().is_relative_to(data_root.resolve()) or data_root.name != 'data':
        raise ValueError('Shared output must be under a directory named data')
    return {**manifest, 'artifact_root': os.path.relpath(data_root.resolve(), directory.resolve())}


def publish(source: Path, target: Path) -> None:
    """Link a complete file into the corpus, reusing equal bytes and rejecting conflicts."""
    target.parent.mkdir(parents=True, exist_ok=True)
    source.chmod(0o644)
    try:
        os.link(source, target)
    except FileExistsError:
        if source.read_bytes() != target.read_bytes():
            raise ValueError(f'Existing corpus artifact differs: {target}')
