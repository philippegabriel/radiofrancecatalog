FROM apache/airflow:3.3.2-python3.14
RUN pip install --no-cache-dir \
    "apache-airflow==3.3.2" "apache-airflow-providers-docker==4.5.9" \
    --constraint https://raw.githubusercontent.com/apache/airflow/constraints-3.3.2/constraints-3.14.txt
COPY airflow/ui_auth.py /opt/airflow-bootstrap/ui_auth.py
USER root
RUN mkdir -p /opt/radiofrance-package && chown airflow:root /opt/radiofrance-package
USER airflow
COPY --chown=airflow:root pyproject.toml /opt/radiofrance-package/
COPY --chown=airflow:root src /opt/radiofrance-package/src
COPY --chown=airflow:root database/sql /opt/radiofrance-package/database/sql
RUN pip install --no-cache-dir --no-deps /opt/radiofrance-package
