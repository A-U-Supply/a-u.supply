FROM python:3.12-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies (no dev deps, system python)
RUN uv sync --frozen --no-dev --no-install-project

# Copy application code
COPY . .

# Ensure data directory exists (will be overridden by Dokku storage mount)
RUN mkdir -p /app/data

EXPOSE 5000

CMD [".venv/bin/uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5000"]
