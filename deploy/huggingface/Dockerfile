# Hugging Face Spaces image (Docker SDK).
# The data pipeline runs at BUILD time: the image downloads UCI Online Retail II, applies the
# quality rules, builds the dbt warehouse, trains the models and bakes the result in, so the
# Space starts in seconds instead of rebuilding data on every cold start.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    HOME=/home/user PATH=/home/user/.local/bin:$PATH \
    BP_ENV=huggingface BP_AUTH_MODE=demo

RUN apt-get update && apt-get install -y --no-install-recommends build-essential libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Spaces run the container as uid 1000; own everything so SQLite app state stays writable.
RUN useradd -m -u 1000 user
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=user . .
USER user

# Full pipeline: extract -> ingest -> quality -> synthetic -> dbt -> ML -> alerts -> exports
RUN python -m businesspulse.pipeline.runner

EXPOSE 8501
HEALTHCHECK CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8501/_stcore/health')"
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0", \
     "--server.enableCORS=false", "--server.enableXsrfProtection=false"]
