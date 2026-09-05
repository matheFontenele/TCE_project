# Mesma família de Python do app (3.10) para evitar conflito de dependências
FROM apache/airflow:2.10.4-python3.10

USER root

# Instala dependências de sistema necessárias (precisa ser root)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

#  VOLTA para o usuário airflow ANTES de usar o pip
USER airflow

# Requirements ENXUTO do Airflow (sem streamlit)
COPY src/orchestration/airflow/requirements.txt /tmp/requirements-airflow.txt
RUN pip install --no-cache-dir -r /tmp/requirements-airflow.txt