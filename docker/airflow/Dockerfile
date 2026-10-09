# syntax=docker/dockerfile:1
FROM apache/airflow:3.3.2-python3.14

USER root
COPY docker/airflow/packages.txt /opt/radiofrance/packages.txt
# The official Airflow image already configures the PostgreSQL apt repository.
# Install AWS CLI through apt to keep it outside Airflow's Python dependencies.
RUN apt-get update \
    && xargs -r apt-get install --yes --no-install-recommends < /opt/radiofrance/packages.txt \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Prefer the exact client major used to create our database snapshots, even if
# the base image also includes newer PostgreSQL client binaries.
ENV PATH="/usr/lib/postgresql/17/bin:${PATH}"

USER airflow
COPY --chown=airflow:root airflow/requirements.txt /opt/radiofrance/requirements.txt
RUN pip install --no-cache-dir "apache-airflow==${AIRFLOW_VERSION}" \
        -r /opt/radiofrance/requirements.txt \
    && pip check

COPY --chown=airflow:root docker/airflow/smoke-test.sh /opt/radiofrance/smoke-test.sh
RUN bash /opt/radiofrance/smoke-test.sh

# Keep the upstream entrypoint and non-root user. DAGs and data are supplied at
# runtime by the repository checkout, rather than embedded in this image.
