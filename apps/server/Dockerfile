# zenstory API - Production Dockerfile for Railway
# =================================================
# Build timestamp to invalidate cache: 2026-02-12-v1

# Stage 1: Base image
FROM python:3.13-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create a non-root user
RUN useradd -m -u 1000 zenstory && \
    chown -R zenstory:zenstory /app
USER zenstory

# Expose port
EXPOSE $PORT

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

# Start application
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port 8080 --workers ${WEB_CONCURRENCY:-1} --timeout-keep-alive 300 --timeout-worker-healthcheck 600"]
