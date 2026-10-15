# Persistent E5 embedding server

This server keeps `intfloat/multilingual-e5-small` loaded for the Transform DAG.
It uses the existing cached Hugging Face weights at revision
`614241f622f53c4eeff9890bdc4f31cfecc418b3`, converted to full-precision F32 GGUF.
No quantization or model substitution is involved. Python embedding dependencies
stay in `venv/radiofrance`; Airflow needs neither PyTorch nor NumPy.

## Build and convert

Requires Git, CMake, a C/C++ compiler and the existing embedding environment/model cache:

```bash
embeddings/setup
```

The script pins llama.cpp to `aa94f2086147cd4bfbcff818ce998dffce5fcb39`.
Its small conversion patch selects upstream XLM-R tokenizer conversion for E5's
BERT architecture, without applying XLM-R position offsets. Original model
weights/configuration are not modified. Generated source, binaries and GGUF live
under ignored `data/llama.cpp/`. Override `RF_E5_SNAPSHOT` to select a local snapshot.

## Run

```bash
embeddings/run
```

The API listens on `127.0.0.1:8081`, exposes `/v1/embeddings`, and uses mean
pooling with L2 normalization. The model alias is its original Hugging Face name.
`RF_EMBEDDING_THREADS`, `RF_EMBEDDING_HOST`, and `RF_EMBEDDING_PORT` override defaults.
The launcher is CPU-based; GPU builds for EC2 can be added later.

For a systemd user service (paths assume this checkout location):

```bash
mkdir -p ~/.config/systemd/user
cp embeddings/radiofrance-embeddings.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now radiofrance-embeddings
systemctl --user status radiofrance-embeddings
systemctl --user stop radiofrance-embeddings
systemctl --user start radiofrance-embeddings
journalctl --user -u radiofrance-embeddings -f
```

Airflow defaults to `http://127.0.0.1:8081/v1/embeddings`. Set
`RF_EMBEDDING_URL` in the worker process environment to override it. An optional `RF_EMBEDDING_API_KEY` sends a bearer token for
an authenticated remote server. A GitHub runner cannot reach this loopback URL;
that runner needs its own server or an accessible endpoint.

## Compatibility validation

```bash
HF_HUB_OFFLINE=1 venv/radiofrance/bin/python embeddings/check_compatibility.py
```

The local CPU server passed seven French passage comparisons against the original
SentenceTransformer implementation, including an over-length passage. Cosine
similarity ranged from 0.99999787 to 0.99999961; maximum coordinate error was
0.000366. These are close, not bit-identical vectors; this is a small numerical
smoke test rather than a full retrieval benchmark.

The client uses llama.cpp's `/tokenize` endpoint and preserves the existing
512-token truncation, including the final separator, before requesting embeddings.
It sends the `passage:` prefix and verifies finite, nonzero 384-dimensional vectors.
See the [upstream server API](https://github.com/ggml-org/llama.cpp/blob/aa94f2086147cd4bfbcff818ce998dffce5fcb39/tools/server/README.md)
for the tokenizer and embedding endpoints.
