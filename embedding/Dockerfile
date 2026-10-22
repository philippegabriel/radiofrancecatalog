FROM python:3.14-slim-trixie
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 \
    HF_HUB_DISABLE_TELEMETRY=1 HOME=/home/embedding \
    HF_HOME=/home/embedding/.cache/huggingface \
    HF_HUB_CACHE=/home/embedding/.cache/huggingface/hub \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
WORKDIR /app
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
    torch==2.14.0+cpu torchvision==0.29.1+cpu
COPY embedding/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt && pip check
COPY pyproject.toml /app/
COPY src /app/src
COPY database/sql /app/database/sql
RUN pip install --no-cache-dir --no-deps .
USER 10001:10001
ENTRYPOINT ["python", "-m", "radiofrance.jobs.embedding"]
