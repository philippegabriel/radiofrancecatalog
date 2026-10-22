FROM python:3.14-slim-trixie
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY download/requirements.txt /app/download/requirements.txt
RUN pip install --no-cache-dir -r download/requirements.txt
COPY download_job.py episode_selection.py rf_dump.py fetchtranscript.py radiofrance_types.py /app/
COPY airflow/dags/radiofrance_catalogue/__init__.py airflow/dags/radiofrance_catalogue/catalogue.py /app/airflow/dags/radiofrance_catalogue/
USER 10001:10001
ENTRYPOINT ["python", "/app/download_job.py"]
