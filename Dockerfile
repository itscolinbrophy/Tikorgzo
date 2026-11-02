FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0 \
    TIKORGZO_DATA_DIR=/data/downloads \
    TIKORGZO_THUMB_DIR=/data/.thumbnails \
    TIKORGZO_JOB_LOG_DIR=/data/.jobs \
    TIKORGZO_USE_XVFB=1 \
    TIKORGZO_MAX_CONCURRENT=1 \
    TIKORGZO_CHROME_ARGS="--no-sandbox --disable-dev-shm-usage --disable-gpu" \
    TIKORGZO_BIN=/usr/local/bin/tikorgzo \
    HOME=/home/app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl ffmpeg fonts-liberation gnupg wget xauth xvfb \
    && wget -q -O /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get install -y /tmp/chrome.deb \
    && rm -rf /tmp/chrome.deb /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

RUN pip install --upgrade pip wheel \
    && pip install -e /app \
    && pip install -r /app/web/requirements.txt \
    && useradd --create-home --uid 1000 app \
    && mkdir -p /data/downloads /data/.thumbnails /data/.jobs \
    && chown -R app:app /app /data /home/app

USER app
EXPOSE 8080
VOLUME ["/data"]

CMD ["uvicorn", "web.app:app", "--app-dir", "/app", "--host", "0.0.0.0", "--port", "8080"]
