FROM python:3.14-slim-trixie
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY database/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt && pip check
COPY database_job.py chunk_job.py embedding_job.py transcript_json.py radiofrance_types.py /app/
COPY airflow/dags/radiofrance_catalogue /app/airflow/dags/radiofrance_catalogue
COPY database/sql /app/database/sql
USER 10001:10001
ENTRYPOINT ["python", "/app/database_job.py"]
