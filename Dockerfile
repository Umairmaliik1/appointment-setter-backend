# =========================
# Builder stage
# =========================
FROM python:3.11 AS builder

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# =========================
# Runtime stage
# =========================
FROM python:3.11-slim

# Runtime OS deps
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Set env early (rarely changes, so cached)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Copy system-wide Python packages (cached unless requirements.txt changes)
COPY --from=builder /usr/local /usr/local

# Copy application code. Gemini Live's native-audio model ships VAD, STT,
# TTS and turn detection server-side, so there are no local model weights
# to prefetch — the runtime image just needs the source.
COPY . .

EXPOSE 8000

# Default CMD (can be overridden in docker-compose)
CMD ["python", "run_voice_worker.py", "start"]
