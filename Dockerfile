# ── Stage 1: base image ───────────────────────────────────────────────────────
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies (SQLite is included in python:slim)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Create a directory for the SQLite database (persisted via volume)
RUN mkdir -p /app/data

# Non-root user for security
RUN adduser --disabled-password --gecos "" appuser \
    && chown -R appuser:appuser /app
USER appuser

# Expose Flask port
EXPOSE 5000

# Environment defaults (override via docker-compose or -e flags)
ENV FLASK_APP=app.py \
    FLASK_ENV=production \
    DATABASE_PATH=/app/data/leave.db

# Run with Gunicorn (production WSGI server)
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "60", "app:app"]
