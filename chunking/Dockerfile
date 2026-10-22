FROM python:3.14-slim-trixie
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY chunk_job.py chunk_json_to_csv.py transcript_json.py radiofrance_types.py /app/
USER 10001:10001
ENTRYPOINT ["python", "/app/chunk_job.py"]
