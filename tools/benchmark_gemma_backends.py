"""Benchmark local Gemma GGUF servers without changing production services."""

import csv
import hashlib
import json
import math
import os
from pathlib import Path
import random
import statistics
import subprocess
import time
from typing import TypedDict, cast
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/benchmarks/gemma2-20261017'
BIN = Path('/home/pgabriel/projects/llama.cpp/build/bin/llama-server')
URL = 'http://127.0.0.1:18093'


class Item(TypedDict):
    index: int
    embedding: list[float]


class Response(TypedDict):
    data: list[Item]
    usage: dict[str, int]


class Sample(TypedDict):
    source: str
    row: int
    text: str


class Measurement(TypedDict):
    model: str
    backend: str
    threads: int
    workload: str
    seconds: list[float]
    median_seconds: float
    vectors_per_second: float
    tokens: int
    min_reference_cosine: float
    rss_kib: int
    temperatures: dict[str, str]


def post(texts: list[str]) -> Response:
    """Request normalized text vectors from the isolated benchmark server."""
    request = Request(URL + '/v1/embeddings', data=json.dumps({
        'input': texts, 'encoding_format': 'float', 'model': 'benchmark',
    }).encode(), headers={'Content-Type': 'application/json'})
    with urlopen(request, timeout=180) as response:
        return cast(Response, json.load(response))


def telemetry(pid: int) -> tuple[int, dict[str, str]]:
    """Read process RSS and available thermal readings, without changing power settings."""
    status = Path(f'/proc/{pid}/status').read_text()
    rss = next(int(line.split()[1]) for line in status.splitlines() if line.startswith('VmRSS:'))
    temperatures: dict[str, str] = {}
    for zone in Path('/sys/class/thermal').glob('thermal_zone*'):
        try:
            temperatures[(zone / 'type').read_text().strip()] = (zone / 'temp').read_text().strip()
        except OSError:
            pass
    return rss, temperatures


def main() -> None:
    """Save deterministic inputs, timings, vector checks, logs and model hashes.

    Each configuration starts a temporary server on port 18093, warms each
    workload once, then performs three timed requests. Servers run sequentially
    and are always terminated. HTTP timings exclude startup and include request
    serialization. Results describe this workload, not retrieval relevance.
    """
    parallel = int(os.environ.get('RF_BENCH_PARALLEL', '1'))
    destination = OUT if parallel == 1 else OUT / f'parallel{parallel}'
    destination.mkdir(parents=True, exist_ok=True)
    samples: list[Sample] = []
    for path in sorted((ROOT / 'data/chunks').glob('*.csv')):
        with path.open() as stream:
            for index, row in enumerate(csv.reader(stream)):
                if row and 500 <= len(row[-1]) <= 1800:
                    samples.append({'source': str(path.relative_to(ROOT)), 'row': index, 'text': row[-1]})
                    break
        if len(samples) == 4:
            break
    assert len(samples) == 4
    (OUT / 'samples.json').write_text(json.dumps(samples, ensure_ascii=False, indent=2))
    documents = ['title: none | text: ' + sample['text'] for sample in samples]
    workloads = {'query': ['task: search result | query: Comment expliquer la montée du nationalisme en Europe ?'],
                 'passage': documents[:1], 'batch4': documents}
    configs = [(model, backend, threads) for model in ('BF16', 'Q8_0')
               for backend, threads in [('cpu', 1), ('cpu', 2), ('cpu', 4), ('cpu', 8), ('vulkan', 4)]]
    configs.append(('BF16', 'vulkan', 1))
    if parallel != 1:
        configs = [(model, backend, 4) for model in ('BF16', 'Q8_0') for backend in ('cpu', 'vulkan')]
    random.Random(20261017).shuffle(configs)
    # Establish reference first, then interleave backend/precision configurations.
    configs.remove(('BF16', 'cpu', 4))
    configs.insert(0, ('BF16', 'cpu', 4))
    references: dict[str, list[list[float]]] = {}
    measurements: list[Measurement] = []
    hashes: dict[str, str] = {}
    for model in ('BF16', 'Q8_0'):
        path = OUT / f'models/embeddinggemma-2-{model}.gguf'
        with path.open('rb') as stream:
            hashes[model] = hashlib.file_digest(stream, 'sha256').hexdigest()
    (OUT / 'model-hashes.json').write_text(json.dumps(hashes, indent=2))
    for model, backend, threads in configs:
        name = f'{model}-{backend}-t{threads}'
        command = [str(BIN), '-m', str(OUT / f'models/embeddinggemma-2-{model}.gguf'),
                   '--device', 'none' if backend == 'cpu' else 'Vulkan0', '-ngl', '0' if backend == 'cpu' else '99',
                   '-t', str(threads), '-tb', str(threads), '-c', '2048', '-b', '2048', '-ub', '2048',
                   '--embedding', '--pooling', 'mean', '--parallel', '1', '--host', '127.0.0.1',
                   '--port', '18093', '--verbosity', '4']
        command[command.index('--parallel') + 1] = str(parallel)
        (destination / f'{name}-command.json').write_text(json.dumps(command, indent=2))
        with (destination / f'{name}.log').open('w') as log:
            process = subprocess.Popen(command, stdout=log, stderr=log)
            try:
                for _ in range(180):
                    if process.poll() is not None:
                        raise RuntimeError(f'{name} exited: see log')
                    try:
                        with urlopen(URL + '/health', timeout=1) as response:
                            if response.status == 200:
                                break
                    except (URLError, TimeoutError):
                        time.sleep(1)
                else:
                    raise TimeoutError('Server startup')
                for workload, texts in workloads.items():
                    post(texts)
                    elapsed: list[float] = []
                    result: Response
                    for _ in range(3):
                        start = time.perf_counter()
                        result = post(texts)
                        elapsed.append(time.perf_counter() - start)
                    vectors = [item['embedding'] for item in sorted(result['data'], key=lambda item: item['index'])]
                    assert len(vectors) == len(texts)
                    for vector in vectors:
                        assert len(vector) == 768 and all(math.isfinite(value) for value in vector)
                        assert abs(sum(value * value for value in vector) - 1) < 1e-4
                    if workload not in references:
                        references[workload] = vectors
                    cosines = [sum(a * b for a, b in zip(vector, reference)) /
                               math.sqrt(sum(a*a for a in vector) * sum(b*b for b in reference))
                               for vector, reference in zip(vectors, references[workload])]
                    rss, temperatures = telemetry(process.pid)
                    median = statistics.median(elapsed)
                    measurements.append({'model': model, 'backend': backend, 'threads': threads,
                        'workload': workload, 'seconds': elapsed, 'median_seconds': median,
                        'vectors_per_second': len(texts) / median, 'tokens': result['usage']['prompt_tokens'],
                        'min_reference_cosine': min(cosines), 'rss_kib': rss, 'temperatures': temperatures})
                    (destination / f'{name}-{workload}-vectors.json').write_text(json.dumps(vectors))
                    (destination / 'results.json').write_text(json.dumps(measurements, indent=2))
                    print(f'{name} {workload}: {median:.3f}s, {len(texts)/median:.2f} vectors/s', flush=True)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == '__main__':
    main()
