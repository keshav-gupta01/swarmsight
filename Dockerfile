FROM python:3.11-slim

# ── System deps for OpenCV headless ────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxrender1 \
        libxext6 \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# ── App ────────────────────────────────────────────────────────────────────
WORKDIR /opt/swarmsight

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# ── Runtime ────────────────────────────────────────────────────────────────
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# Use same invocation as the live systemd unit (--app-dir /opt/swarmsight)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "/opt/swarmsight"]
