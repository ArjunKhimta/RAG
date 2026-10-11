# The retrieval service: gunicorn serving the Flask app on one worker (see gunicorn.conf.py).
#
# Build from the retrieval/ folder: docker build retrieval
# The base image is pinned by digest, so every build starts from the same bytes; the tag says
# which release that digest is. Every Python package comes from requirements.lock, as wheels
# whose SHA-256 must match (regenerate it with scripts/lock_requirements.py). The service runs
# as an unprivileged user and reads its settings from the environment; no .env file is copied.
FROM python:3.12.14-slim-trixie@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PYTHONPATH=/app/retrieval/src \
    HOST=0.0.0.0 \
    PORT=5001

RUN apt-get update \
    && apt-get install --yes --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin retrieval

WORKDIR /app

COPY requirements.lock retrieval/requirements.lock
RUN pip install --require-hashes --only-binary :all: --no-deps --requirement retrieval/requirements.lock

COPY gunicorn.conf.py retrieval/gunicorn.conf.py
COPY src retrieval/src

RUN mkdir -p data/repos && chown retrieval:retrieval data data/repos

USER retrieval

EXPOSE 5001

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ['PORT'] + '/health', timeout=4)"]

CMD ["gunicorn", "--config", "retrieval/gunicorn.conf.py"]
