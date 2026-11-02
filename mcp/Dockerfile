FROM python:3.14-slim-trixie
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 HF_HUB_CACHE=/models RF_MCP_HOST=0.0.0.0 \
    TOKENIZERS_PARALLELISM=false HF_HUB_DISABLE_TELEMETRY=1 \
    OMP_NUM_THREADS=4 MKL_NUM_THREADS=4
WORKDIR /app
COPY embedding/requirements.txt /tmp/embedding-requirements.txt
COPY mcp/requirements.txt /tmp/mcp-requirements.txt
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.14.0+cpu torchvision==0.29.1+cpu \
    && pip install --no-cache-dir -r /tmp/embedding-requirements.txt -r /tmp/mcp-requirements.txt && pip check
COPY pyproject.toml ./
COPY src/ ./src/
COPY database/sql/ ./database/sql/
RUN pip install --no-cache-dir --no-deps . && useradd --uid 10001 --create-home mcp
COPY mcp/server.py mcp/retrieval.py ./mcp/
COPY mcp/sql/ ./mcp/sql/
USER mcp
EXPOSE 8000
CMD ["python", "mcp/server.py"]
