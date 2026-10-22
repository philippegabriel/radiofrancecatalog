FROM apache/airflow:3.3.2-python3.14
RUN pip install --no-cache-dir \
    "apache-airflow==3.3.2" "apache-airflow-providers-docker==4.5.9" \
    --constraint https://raw.githubusercontent.com/apache/airflow/constraints-3.3.2/constraints-3.14.txt
COPY ui_auth.py /opt/airflow-bootstrap/ui_auth.py
