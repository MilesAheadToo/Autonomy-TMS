# syntax=docker/dockerfile:1.4
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (git needed for azirella-data-model pip install)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

# Build context: repository root (see backend/Dockerfile.cpu rationale).
# Copy requirements + heuristics package locally before pip install.
COPY backend/requirements.txt /app/requirements.txt
COPY packages/autonomy-tms-heuristics /app/packages/autonomy-tms-heuristics

RUN --mount=type=secret,id=autonomy_core_token,required=true \
    printf "machine github.com\nlogin x-access-token\npassword $(cat /run/secrets/autonomy_core_token)\n" > /root/.netrc && \
    chmod 600 /root/.netrc && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir -e /app/packages/autonomy-tms-heuristics && \
    rm -f /root/.netrc

# Copy the backend application code
COPY backend/ /app/

# Set environment variables
ENV PYTHONPATH=/app

# The command to run the script
CMD ["python", "scripts/create_users.py"]
