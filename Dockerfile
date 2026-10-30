# GamedevX — AI Visual Novel studio
# Coolify: Build Pack = Dockerfile, Port = 8000, Persistent Storage -> /data
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    PORT=8000

WORKDIR /srv

# ffmpeg = MP4 video export · fribidi/raqm = correct Thai text shaping in video frames
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libfribidi0 libraqm0 \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt \
 && python -c "from pythainlp.tokenize import word_tokenize; print(word_tokenize('ทดสอบระบบตัดคำ'))" \
 && python -c "from PIL import features; print('raqm:', features.check('raqm'))"

COPY app ./app

# runs as root so the /data volume is always writable (Coolify volumes are root-owned)
RUN mkdir -p /data

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8000'), timeout=4)"

# 1 worker on purpose: background AI jobs run inside the process (SQLite + in-process queue)
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*' --workers 1"]
