# ─── Gengar DJ — Dockerfile ─────────────────────────────────────
# Multi-stage build for minimal final image size

# ── Build stage ─────────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Install build deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY bot/requirements.txt .
RUN pip install --user --no-warn-script-location -r requirements.txt

# ── Runtime stage ───────────────────────────────────────────────
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Install FFmpeg (required for voice playback) and curl (health checks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy Python deps from builder
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

WORKDIR /app

# Copy bot code
COPY bot/ /app/bot/

# Create data directories
RUN mkdir -p /data/songs

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -sf http://localhost:8080/api/health || exit 1

# Entry point — run the bot
CMD ["python", "-m", "bot.main"]
