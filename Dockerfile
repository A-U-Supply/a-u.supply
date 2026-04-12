# Stage 1: Build Astro frontend
FROM node:22-slim AS frontend
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY astro.config.mjs tsconfig.json ./
COPY src/ ./src/
COPY public/ ./public/
RUN npm run build

# Stage 2: Python app
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ffmpeg media-types libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
COPY --from=frontend /app/dist ./dist/
RUN mkdir -p /app/data
EXPOSE 5000
CMD [".venv/bin/uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5000"]
