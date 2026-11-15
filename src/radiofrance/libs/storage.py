"""Shared corpus locations and atomic publication without content hashes."""

import json
import os
from pathlib import Path
from typing import Mapping

CHUNK_DIRECTORY = 'chunks/segment-characters-v1-1000-300'
MODEL_REVISION = '914f7f89142e33e77833254d9c9b90c3cef7303b'
MODEL_DIRECTORY = f'embeddinggemma2-fp32-{MODEL_REVISION}'
EMBEDDING_DIRECTORY = f'embeddings/transcripts/{MODEL_DIRECTORY}/256/segment-characters-v1-1000-300'
DESCRIPTION_DIRECTORY = f'embeddings/descriptions/{MODEL_DIRECTORY}/256'


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


def ensure_directory(directory: Path) -> None:
    """Create public artifact directories readable across the host and Airflow container UIDs."""
    missing: list[Path] = []
    current = directory
    while not current.exists():
        missing.append(current)
        current = current.parent
    for path in reversed(missing):
        path.mkdir(exist_ok=True)
        path.chmod(0o755)


def publish(source: Path, target: Path) -> None:
    """Link a complete file into the corpus, reusing equal bytes and rejecting conflicts."""
    ensure_directory(target.parent)
    source.chmod(0o644)
    try:
        os.link(source, target)
    except FileExistsError:
        if source.read_bytes() != target.read_bytes():
            raise ValueError(f'Existing corpus artifact differs: {target}')


def run_reference(directory: Path) -> str:
    """Identify a shared manifest relative to data, or a self-contained run by basename."""
    root = artifact_root(directory)
    return str(directory.resolve().relative_to(root)) if root != directory.resolve() else directory.name


def replace_file(source: Path, target: Path) -> None:
    """Atomically replace mutable metadata or a fully written cache file on the same filesystem."""
    ensure_directory(target.parent)
    source.chmod(0o644)
    source.replace(target)
