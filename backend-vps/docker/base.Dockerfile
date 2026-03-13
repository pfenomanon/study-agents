FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml supabase_schema.sql README.md ./
COPY prompts ./prompts
COPY src ./src
COPY scripts/use_env.sh /app/use_env.sh
RUN pip install --upgrade pip setuptools wheel
RUN pip install -e .[full]
RUN chmod +x /app/use_env.sh

ENV PYTHONPATH="/app/src:${PYTHONPATH}"

# Drop privileges
RUN groupadd -r app && useradd -r -g app app && chown -R app:app /app
USER app
