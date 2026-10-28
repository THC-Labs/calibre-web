# ==============================================================================
# Production Dockerfile for Calibre-Web (THC Labs)
# Complies with THC Labs Multi-Service Container Contract
# ==============================================================================
FROM python:3.11-slim-bookworm

# 1. Default to Production Runtime and Internal Port Binding
ENV ENVIRONMENT=production \
    NODE_ENV=production \
    FLASK_ENV=production \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8083 \
    CALIBRE_PORT=8083 \
    CALIBRE_DBPATH=/app

# 2. Install runtime system dependencies (including wget for HEALTHCHECK)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 \
    libxml2 \
    libxslt1.1 \
    imagemagick \
    curl \
    wget \
    && rm -rf /var/lib/apt/lists/*

# 3. Create unprivileged runtime user (UID/GID 1000)
RUN groupadd -g 1000 calibre && \
    useradd -u 1000 -g calibre -d /app -s /bin/bash calibre

WORKDIR /app

# 4. Copy dependency specifications first to leverage Docker layer caching
COPY requirements.txt ./
COPY optional-requirements.txt* ./

# Install python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 5. Copy application source code and set ownership
COPY . /app
RUN chown -R calibre:calibre /app

# 6. Unprivileged Execution
USER calibre

# 7. Explicit Internal Port Binding
EXPOSE 8083

# 8. Built-in Healthcheck targeting /healthz
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -qO- http://localhost:8083/healthz || exit 1

# Run Calibre-Web
CMD ["python", "cps.py"]
