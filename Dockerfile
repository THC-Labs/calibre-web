# Production Dockerfile for Calibre-Web (THC Labs)
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CALIBRE_PORT=8083 \
    CALIBRE_DBPATH=/app

# Install runtime system dependencies for Calibre-Web (libmagic, lxml, ImageMagick)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 \
    libxml2 \
    libxslt1.1 \
    imagemagick \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency specifications first to take advantage of layer caching
COPY requirements.txt ./
COPY optional-requirements.txt* ./

# Install python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . /app

# Expose default Calibre-Web port
EXPOSE 8083

# Run Calibre-Web
CMD ["python", "cps.py"]
