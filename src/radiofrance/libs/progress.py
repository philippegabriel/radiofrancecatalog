"""Small atomic embedding progress records shared with the Airflow run monitor."""

import json
from pathlib import Path
import tempfile
import time
from typing import TypedDict


class EmbeddingProgress(TypedDict):
    phase: str
    total: int
    cached: int
    generated: int
    encoding_seconds: float
    elapsed_seconds: float
    rate: float | None
    remaining_seconds: float | None
    provisional: bool


class EmbeddingReporter:
    """Publish model-loading, first-ten, hundred-vector and completion updates."""

    def __init__(self, output: Path, total: int, cached: int) -> None:
        self.path = output.parent / f'{output.name}.progress.json'
        self.total = total
        self.cached = cached
        self.generated = 0
        self.encoding_seconds = 0.0
        self.started = time.monotonic()
        self.next_update = 10
        self.publish('starting')

    def publish(self, phase: str) -> EmbeddingProgress:
        """Replace a sidecar atomically, readable by the host and Airflow UID."""
        rate = self.generated / self.encoding_seconds if self.generated and self.encoding_seconds > 0 else None
        remaining = max(0, self.total - self.cached - self.generated)
        record: EmbeddingProgress = {'phase': phase, 'total': self.total, 'cached': self.cached,
            'generated': self.generated, 'encoding_seconds': self.encoding_seconds,
            'elapsed_seconds': time.monotonic() - self.started, 'rate': rate,
            'remaining_seconds': remaining / rate if rate else None, 'provisional': self.generated < 100}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', dir=self.path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                json.dump(record, stream)
                stream.flush()
                temporary.chmod(0o644)
                temporary.replace(self.path)
            finally:
                temporary.unlink(missing_ok=True)
        print('Embedding progress: ' + json.dumps(record), flush=True)
        return record

    def encoded(self, count: int, seconds: float) -> None:
        """Account for inference only; first estimate after ten, then every hundred."""
        self.generated += count
        self.encoding_seconds += seconds
        if self.generated >= self.next_update:
            self.publish('encoding')
            self.next_update = (self.generated // 100 + 1) * 100
