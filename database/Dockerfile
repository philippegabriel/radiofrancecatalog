FROM python:3.14-slim-trixie
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY database/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt && pip check
COPY pyproject.toml /app/
COPY src /app/src
COPY database/sql /app/database/sql
RUN pip install --no-cache-dir --no-deps .
USER 10001:10001
ENTRYPOINT ["python", "-m", "radiofrance.jobs.database"]
