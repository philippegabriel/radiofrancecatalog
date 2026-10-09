# syntax=docker/dockerfile:1
FROM python:3.14-slim-bookworm

ENV AIRFLOW_VERSION=3.3.2 \
    AIRFLOW_HOME=/opt/airflow \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

USER root
COPY docker/airflow/packages.txt /opt/radiofrance/packages.txt
# Configure the official PostgreSQL apt repository for the exact client major.
# Install AWS CLI through apt to keep it outside Airflow's Python dependencies.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl \
    && mkdir -p /usr/share/postgresql-common/pgdg \
    && curl --fail --silent --show-error https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && . /etc/os-release \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && xargs -r apt-get install --yes --no-install-recommends < /opt/radiofrance/packages.txt \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Prefer the exact client major used to create our database snapshots, even if
# the base image also includes newer PostgreSQL client binaries.
ENV PATH="/opt/venv/bin:/usr/lib/postgresql/17/bin:${PATH}"
RUN python -m venv /opt/venv \
    && useradd --uid 50000 --gid 0 --create-home --shell /bin/bash airflow \
    && mkdir -p /opt/airflow \
    && chown airflow:root /opt/airflow /opt/venv

COPY --chown=airflow:root airflow/requirements-runtime.txt /opt/radiofrance/requirements.txt
RUN pip install --no-cache-dir --no-compile "apache-airflow==${AIRFLOW_VERSION}" \
        -r /opt/radiofrance/requirements.txt \
    && pip check

COPY --chown=airflow:root docker/airflow/smoke-test.sh /opt/radiofrance/smoke-test.sh
RUN bash /opt/radiofrance/smoke-test.sh

# Standalone non-root runtime; CI overrides the user for runner-mounted files.
# DAGs, configuration and data are supplied at runtime by repository checkout.
USER airflow
WORKDIR /opt/airflow
