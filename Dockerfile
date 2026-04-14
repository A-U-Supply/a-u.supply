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
RUN apt-get update && apt-get install -y --no-install-recommends git ffmpeg media-types libgomp1 unzip curl && rm -rf /var/lib/apt/lists/*
# Install deno (required by yt-dlp for YouTube extraction)
RUN curl -fsSL -L https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip -o /tmp/deno.zip \
    && unzip /tmp/deno.zip -d /usr/local/bin/ \
    && rm /tmp/deno.zip \
    && chmod +x /usr/local/bin/deno
WORKDIR /app
RUN curl -fsSL https://astral.sh/uv/install.sh | sh && mv /root/.local/bin/uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
COPY --from=frontend /app/dist ./dist/
RUN mkdir -p /app/data
EXPOSE 5000
CMD [".venv/bin/uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5000"]
