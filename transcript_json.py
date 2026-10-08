"""Read transcript source artifacts with stable, zero-based segment numbering."""
import json
from pathlib import Path
from typing import cast

from radiofrance_types import TranscriptSegment


def read_segments(path: Path) -> list[TranscriptSegment]:
    """Validate JSON segments, retaining blank text and original order for seq IDs."""
    data: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("transcript"), list):
        raise ValueError(f"{path}: expected a transcript array")
    segments: list[TranscriptSegment] = []
    for seq, raw in enumerate(data["transcript"]):
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: segment {seq} must be an object")
        if not isinstance(raw.get("text"), str):
            raise ValueError(f"{path}: segment {seq} requires text")
        for field in ("start", "end"):
            if isinstance(raw.get(field), bool) or not isinstance(raw.get(field), (int, float)):
                raise ValueError(f"{path}: segment {seq} requires numeric {field}")
        if raw.get("speaker") is not None and not isinstance(raw["speaker"], str):
            raise ValueError(f"{path}: segment {seq} has invalid speaker")
        segments.append(cast(TranscriptSegment, raw))
    return segments
