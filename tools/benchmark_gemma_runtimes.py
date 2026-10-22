"""Compare persistent CPU Gemma runtimes on deterministic local pilot chunks.

Run with venv/airflow/bin/python tools/benchmark_gemma_runtimes.py prepare;
see the output directory's METHOD.md for individual backend commands.
The SentenceTransformers worker uses the existing radiofrance interpreter with
an isolated Transformers installation in the output directory. No production
services, model files, database tables or pipeline dependencies are modified.
"""

import argparse
import csv
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import random
import statistics
import subprocess
import threading
import time
from typing import NotRequired, TypedDict, cast
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'data/benchmarks/gemma2-runtime-20261021'
OUT = BASE
DIMENSIONS = 768
BIN = Path('/home/pgabriel/projects/llama.cpp/build/bin/llama-server')
URL = 'http://127.0.0.1:18094'
THREADS = 4
REPEATS = 3
BATCH_SIZES = (1, 4, 8)


class Sample(TypedDict):
    source: str
    row: int
    text: str


class Item(TypedDict):
    index: int
    embedding: list[float]


class Response(TypedDict):
    data: list[Item]
    usage: dict[str, int]


class Measurement(TypedDict):
    workload: str
    batch_size: int
    seconds: list[float]
    median_seconds: float
    vectors_per_second: float
    rss_mib: float


class PassResult(TypedDict):
    seconds: float
    vectors: list[list[float]]
    tokens: NotRequired[int]


class SentenceDetails(TypedDict):
    torch: str
    sentence_transformers: str
    transformers: str
    threads: int
    interop_threads: int
    max_seq_length: int
    token_counts: list[int]
    model: str
    revision: str


class LlamaDetails(TypedDict):
    command: list[str]
    total_document_tokens: int
    model_sha256: str


type Details = SentenceDetails | LlamaDetails


class Result(TypedDict):
    runtime: str
    dimensions: int
    process_startup_seconds: float
    model_load_seconds: float
    peak_sampled_rss_mib: float
    idle_loaded_rss_mib: float
    measurements: list[Measurement]
    vectors: list[list[float]]
    query_vectors: list[list[float]]
    details: Details


class RankingAgreement(TypedDict):
    query_index: int
    reference_top3: list[int]
    runtime_top3: list[int]
    top1_agrees: bool
    top3_overlap: int


class Agreement(TypedDict):
    min_cosine: float
    mean_cosine: float
    max_coordinate_difference: float
    rankings: list[RankingAgreement]


def save(name: str, value: object) -> None:
    """Write a benchmark artifact, with no changes outside the output directory."""
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def rss(pid: int) -> float:
    """Read resident process memory in MiB; this excludes other processes."""
    try:
        lines = Path(f'/proc/{pid}/status').read_text().splitlines()
        return next(int(line.split()[1]) / 1024 for line in lines if line.startswith('VmRSS:'))
    except (OSError, StopIteration):
        return 0.0


class Monitor:
    """Sample one process's RSS every 50 ms until stopped."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.peak = 0.0
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self) -> None:
        while not self.stop.is_set():
            self.peak = max(self.peak, rss(self.pid))
            self.stop.wait(0.05)

    def __enter__(self) -> 'Monitor':
        self.thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.stop.set()
        self.thread.join()


def validate(vectors: list[list[float]], count: int) -> None:
    """Reject incomplete, nonfinite, incorrectly sized or unnormalized vectors."""
    assert len(vectors) == count
    for vector in vectors:
        assert len(vector) == DIMENSIONS and all(math.isfinite(v) for v in vector)
        assert abs(sum(v*v for v in vector) - 1) < 1e-4


def measure(workload: str, batch: int, count: int, times: list[float], pid: int) -> Measurement:
    """Summarize warm wall-clock durations, excluding the separate warmup."""
    median = statistics.median(times)
    print(f'{workload} batch={batch}: {median:.3f}s, {count/median:.2f} vectors/s', flush=True)
    return {'workload': workload, 'batch_size': batch, 'seconds': times,
            'median_seconds': median, 'vectors_per_second': count/median, 'rss_mib': rss(pid)}


def inputs() -> tuple[list[str], list[str]]:
    """Read previously saved, identically formatted documents and queries."""
    samples = cast(list[Sample], json.loads((OUT / 'samples.json').read_text()))
    queries = cast(list[str], json.loads((OUT / 'queries.json').read_text()))
    return ['title: none | text: ' + sample['text'] for sample in samples], queries


def sentence_worker() -> None:
    """Load Google's text-only FP32 model once and measure direct Python calls."""
    process_start = time.perf_counter()
    torch = importlib.import_module('torch')
    st = importlib.import_module('sentence_transformers')
    transformers = importlib.import_module('transformers')
    torch.set_num_threads(THREADS)
    torch.set_num_interop_threads(1)
    docs, queries = inputs()
    with Monitor(os.getpid()) as monitor:
        start = time.perf_counter()
        model = st.SentenceTransformer(str(BASE / 'google-model'), device='cpu',
            config_kwargs={'vision_config': None, 'audio_config': None},
            model_kwargs={'dtype': torch.float32}, local_files_only=True)
        load = time.perf_counter() - start
        startup = time.perf_counter() - process_start
        idle = rss(os.getpid())
        measurements: list[Measurement] = []
        vectors: list[list[float]] = []
        for batch in BATCH_SIZES:
            model.encode(docs[:batch], batch_size=batch, normalize_embeddings=True, truncate_dim=DIMENSIONS, show_progress_bar=False)
            times: list[float] = []
            for repetition in range(REPEATS):
                start = time.perf_counter()
                vectors = model.encode(docs, batch_size=batch, normalize_embeddings=True, truncate_dim=DIMENSIONS,
                                       show_progress_bar=len(docs) > 16).tolist()
                times.append(time.perf_counter() - start)
                validate(vectors, len(docs))
                pass_result: PassResult = {'seconds': times[-1], 'vectors': vectors}
                save(f'SentenceTransformers-FP32-batch{batch}-pass{repetition+1}.json', pass_result)
                print(f'SentenceTransformers batch={batch} pass={repetition+1}/{REPEATS}: {times[-1]:.3f}s', flush=True)
            validate(vectors, len(docs))
            measurements.append(measure('documents', batch, len(docs), times, os.getpid()))
        query_vectors: list[list[float]] = []
        times = []
        for query in queries:
            model.encode([query], normalize_embeddings=True, truncate_dim=DIMENSIONS, show_progress_bar=False)
            for _ in range(REPEATS):
                start = time.perf_counter()
                encoded = model.encode([query], normalize_embeddings=True, truncate_dim=DIMENSIONS, show_progress_bar=False).tolist()
                times.append(time.perf_counter() - start)
            query_vectors.extend(encoded)
        validate(query_vectors, len(queries))
        measurements.append(measure('single-query', 1, 1, times, os.getpid()))
        result: Result = {'runtime': 'SentenceTransformers-FP32', 'dimensions': DIMENSIONS, 'process_startup_seconds': startup,
            'model_load_seconds': load, 'peak_sampled_rss_mib': monitor.peak,
            'idle_loaded_rss_mib': idle, 'measurements': measurements, 'vectors': vectors,
            'query_vectors': query_vectors, 'details': {'torch': torch.__version__,
            'sentence_transformers': st.__version__, 'transformers': transformers.__version__,
            'threads': THREADS, 'interop_threads': 1, 'max_seq_length': model.max_seq_length,
            'token_counts': [len(x) for x in model.tokenizer(docs)['input_ids']],
            'model': str(model), 'revision': '914f7f89142e33e77833254d9c9b90c3cef7303b'}}
    save('SentenceTransformers-FP32.json', result)


def post(texts: list[str]) -> Response:
    """Call only the temporary local llama-server embeddings endpoint."""
    request = Request(URL + '/v1/embeddings', data=json.dumps({'input': texts,
        'encoding_format': 'float', 'model': 'benchmark'}).encode(),
        headers={'Content-Type': 'application/json'})
    with urlopen(request, timeout=300) as response:
        result = cast(Response, json.load(response))
    if DIMENSIONS != 768:
        for item in result['data']:
            vector = item['embedding'][:DIMENSIONS]
            norm = math.sqrt(sum(value*value for value in vector))
            if not norm or not math.isfinite(norm):
                raise ValueError('Invalid truncated embedding norm')
            item['embedding'] = [value/norm for value in vector]
    return result


def llama(model_name: str) -> None:
    """Run one isolated CPU server; always terminate it after measurement."""
    path = ROOT / f'data/benchmarks/gemma2-20261017/models/embeddinggemma-2-{model_name}.gguf'
    command = [str(BIN), '-m', str(path), '--device', 'none', '-ngl', '0',
        '-t', str(THREADS), '-tb', str(THREADS), '-c', '8192', '-b', '2048', '-ub', '2048',
        '--embedding', '--pooling', 'mean', '--parallel', '4', '--host', '127.0.0.1', '--port', '18094']
    save(f'llama-{model_name}-command.json', command)
    docs, queries = inputs()
    with (OUT / f'llama-{model_name}.log').open('w') as log:
        start = time.perf_counter()
        process = subprocess.Popen(command, stdout=log, stderr=log)
        try:
            with Monitor(process.pid) as monitor:
                for _ in range(600):
                    if process.poll() is not None:
                        raise RuntimeError('llama-server exited; inspect its benchmark log')
                    try:
                        with urlopen(URL + '/health', timeout=1) as response:
                            if response.status == 200:
                                break
                    except (URLError, TimeoutError):
                        time.sleep(0.1)
                else:
                    raise TimeoutError('llama-server startup')
                startup = time.perf_counter() - start
                idle = rss(process.pid)
                measurements: list[Measurement] = []
                vectors: list[list[float]] = []
                token_count = 0
                for batch in BATCH_SIZES:
                    post(docs[:batch])
                    times: list[float] = []
                    for repetition in range(REPEATS):
                        vectors = []
                        token_count = 0
                        start = time.perf_counter()
                        for offset in range(0, len(docs), batch):
                            response = post(docs[offset:offset+batch])
                            vectors.extend(item['embedding'] for item in sorted(response['data'], key=lambda x: x['index']))
                            token_count += response['usage']['prompt_tokens']
                        times.append(time.perf_counter() - start)
                        validate(vectors, len(docs))
                        pass_result: PassResult = {'seconds': times[-1], 'tokens': token_count, 'vectors': vectors}
                        save(f'llama-{model_name}-batch{batch}-pass{repetition+1}.json', pass_result)
                        print(f'llama-{model_name} batch={batch} pass={repetition+1}/{REPEATS}: {times[-1]:.3f}s', flush=True)
                    validate(vectors, len(docs))
                    measurements.append(measure('documents', batch, len(docs), times, process.pid))
                query_vectors: list[list[float]] = []
                times = []
                for query in queries:
                    post([query])
                    for _ in range(REPEATS):
                        start = time.perf_counter()
                        response = post([query])
                        times.append(time.perf_counter()-start)
                    query_vectors.append(response['data'][0]['embedding'])
                validate(query_vectors, len(queries))
                measurements.append(measure('single-query', 1, 1, times, process.pid))
                result: Result = {'runtime': f'llama-{model_name}', 'dimensions': DIMENSIONS, 'process_startup_seconds': startup,
                    'model_load_seconds': startup, 'peak_sampled_rss_mib': monitor.peak,
                    'idle_loaded_rss_mib': idle, 'measurements': measurements, 'vectors': vectors,
                    'query_vectors': query_vectors, 'details': {'command': command, 'total_document_tokens': token_count,
                    'model_sha256': hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()}}
                save(f'llama-{model_name}.json', result)
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def prepare(chunk_dir: Path | None, sample_count: int) -> None:
    """Validate a pilot manifest and save a deterministic sample or every chunk.

    A zero sample count retains all chunks in manifest/CSV order. Source files
    are read only; benchmark inputs and provenance are written under OUT.
    """
    if chunk_dir is None:
        manifests = sorted((ROOT / 'data/chunk-pilot').glob('*/manifest.json'))
        manifest_path = manifests[-1]
    else:
        manifest_path = chunk_dir.resolve() / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    samples: list[Sample] = []
    for item in manifest['chunks']:
        path = manifest_path.parent / item['path']
        if not path.resolve().is_relative_to(manifest_path.parent.resolve()):
            raise ValueError('Chunk path escapes source directory')
        assert hashlib.file_digest(path.open('rb'), 'sha256').hexdigest() == item['sha256']
        before = len(samples)
        with path.open() as stream:
            for index, row in enumerate(csv.reader(stream)):
                if row and row[-1].strip():
                    samples.append({'source': str(path.relative_to(ROOT)), 'row': index, 'text': row[-1]})
        assert len(samples) - before == item['chunk_count']
    assert len(samples) == manifest['chunk_count']
    selected = samples if sample_count == 0 else random.Random(20261021).sample(samples, sample_count)
    save('samples.json', selected)
    save('queries.json', ['task: search result | query: ' + query for query in [
        'Comment les journalistes vérifient-ils leurs sources ?',
        'Quelle place occupent les réseaux sociaux dans une enquête journalistique ?',
        'Comment protéger les sources et les lanceurs d’alerte ?']])
    save('environment.json', {'source_manifest': str(manifest_path.relative_to(ROOT)),
        'source_manifest_sha256': hashlib.file_digest(manifest_path.open('rb'), 'sha256').hexdigest(),
        'threads': THREADS, 'repeats': REPEATS, 'batch_sizes': BATCH_SIZES,
        'sample_count': len(selected), 'episode_count': len(manifest['chunks']),
        'llama_version': subprocess.check_output([str(BIN), '--version'], stderr=subprocess.STDOUT, text=True),
        'cpu': subprocess.check_output(['lscpu'], text=True), 'note': 'Normal desktop services remain running. Downloads excluded from loading time. No OS cache eviction.'})


def summarize() -> None:
    """Compare saved vectors and rankings without rerunning inference or judging relevance."""
    results = [cast(Result, json.loads((OUT / name).read_text())) for name in
               ('SentenceTransformers-FP32.json', 'llama-BF16.json', 'llama-Q8_0.json')]
    reference = results[0]
    count = len(reference['vectors'])
    columns = ' | '.join(f'Batch {batch} chunks/s' for batch in BATCH_SIZES)
    comparisons: dict[str, Agreement] = {}
    for result in results[1:]:
        cosines: list[float] = []
        for a, b in zip(reference['vectors'] + reference['query_vectors'],
                        result['vectors'] + result['query_vectors'], strict=True):
            cosines.append(sum(x*y for x, y in zip(a, b, strict=True)) /
                math.sqrt(sum(x*x for x in a)*sum(y*y for y in b)))
        ranks: list[RankingAgreement] = []
        for i, (a, b) in enumerate(zip(reference['query_vectors'], result['query_vectors'], strict=True)):
            expected = sorted(range(len(reference['vectors'])), reverse=True,
                key=lambda j: sum(x*y for x, y in zip(a, reference['vectors'][j], strict=True)))
            actual = sorted(range(len(result['vectors'])), reverse=True,
                key=lambda j: sum(x*y for x, y in zip(b, result['vectors'][j], strict=True)))
            ranks.append({'query_index': i, 'reference_top3': expected[:3], 'runtime_top3': actual[:3],
                'top1_agrees': expected[0] == actual[0], 'top3_overlap': len(set(expected[:3]) & set(actual[:3]))})
        comparisons[result['runtime']] = {'min_cosine': min(cosines), 'mean_cosine': statistics.mean(cosines),
            'max_coordinate_difference': max(abs(x-y) for a, b in zip(reference['vectors'], result['vectors'], strict=True)
                for x, y in zip(a, b, strict=True)), 'rankings': ranks}
    save('agreement.json', comparisons)
    lines = [f'# EmbeddingGemma 2 CPU runtime comparison: {DIMENSIONS} dimensions', '',
        f'Measured on 2026-10-21 using {count} local pilot chunks, four CPU threads and three repetitions.', '',
        f'| Runtime | {columns} | Query median ms | Startup s | Peak RSS MiB |',
        '|---|' + '---:|' * (len(BATCH_SIZES) + 3)]
    for result in results:
        docs = [m for m in result['measurements'] if m['workload'] == 'documents']
        query = next(m for m in result['measurements'] if m['workload'] == 'single-query')
        lines.append(f"| {result['runtime']} | " + ' | '.join(f"{m['vectors_per_second']:.2f}" for m in docs)
            + f" | {query['median_seconds']*1000:.1f} | {result['process_startup_seconds']:.2f} | {result['peak_sampled_rss_mib']:.0f} |")
    lines.extend(['', 'Vector agreement against the Google/SentenceTransformers reference:', ''])
    for name, comparison in comparisons.items():
        top1 = sum(r['top1_agrees'] for r in comparison['rankings'])
        lines.append(f"- {name}: minimum cosine {comparison['min_cosine']:.8f}, mean cosine {comparison['mean_cosine']:.8f}; top-1 agreement {top1}/3 queries.")
    lines.extend(['', '## Scope and limitations', '',
        '- FP32 PyTorch and BF16/Q8 GGUF differ in both runtime and weight precision.',
        '- Google revision is pinned; the original Google revision behind the existing GGUF is not established.',
        '- Python uses direct encode calls; llama includes HTTP overhead.',
        f'- Output is {DIMENSIONS} dimensions, normalized after truncation. SentenceTransformers uses truncate_dim; this llama build truncates in the timed client.',
        '- RSS includes framework overhead; the installed PyTorch wheel contains CUDA support despite CPU execution.',
        '- Desktop and Airflow services remained running. Close timings can be affected by temperature and background load.',
        '- Startup uses existing local files and normal OS caching, excluding downloads.',
        f'- Ranking agreement on three queries and {count} chunks is a consistency check, not a retrieval-quality evaluation.',
        '', 'See METHOD.md, samples.json, environment.json, per-runtime JSON and logs for full details.', ''])
    (OUT / 'REPORT.md').write_text('\n'.join(lines))


def main() -> None:
    """Prepare inputs or run one backend, keeping runs sequential and reviewable."""
    global OUT, DIMENSIONS, BATCH_SIZES
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prepare', 'sentence', 'BF16', 'Q8_0', 'summarize'])
    parser.add_argument('--dimensions', type=int, choices=[256, 768], default=768)
    parser.add_argument('--output-dir', type=Path, help='Separate directory for a repeat run; model and packages are reused.')
    parser.add_argument('--chunk-dir', type=Path, help='Pilot directory with manifest.json, used by prepare.')
    parser.add_argument('--sample-count', type=int, default=16, help='Number of chunks to sample; 0 uses all chunks.')
    parser.add_argument('--batch-sizes', type=int, nargs='+', choices=[1, 4, 8], default=[1, 4, 8])
    args = parser.parse_args()
    mode = args.mode
    DIMENSIONS = args.dimensions
    BATCH_SIZES = tuple(args.batch_sizes)
    if args.sample_count < 0 or len(set(BATCH_SIZES)) != len(BATCH_SIZES):
        parser.error('sample-count must be nonnegative and batch sizes must be unique')
    OUT = BASE if DIMENSIONS == 768 else BASE.with_name(BASE.name + '-256d')
    if args.output_dir is not None:
        OUT = args.output_dir.resolve()
    OUT.mkdir(parents=True, exist_ok=True)
    if mode == 'prepare':
        prepare(args.chunk_dir, args.sample_count)
    elif mode == 'sentence':
        sentence_worker()
    elif mode == 'summarize':
        summarize()
    else:
        llama(mode)


if __name__ == '__main__':
    main()
