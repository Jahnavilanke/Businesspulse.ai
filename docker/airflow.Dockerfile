# Airflow image with the BusinessPulse package + dbt. Airflow runs on Python 3.12 here;
# the pinned libraries in requirements.txt publish 3.12 wheels.
FROM apache/airflow:2.10.5-python3.12
USER root
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
USER airflow
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt boto3
ENV PYTHONPATH=/opt/businesspulse
