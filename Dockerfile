FROM ghcr.io/astral-sh/uv:0.11.8 AS uv
FROM python:3.12-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev && useradd --uid 10001 --create-home tutor \
    && mkdir -p /app/data /app/courses && chown -R tutor:tutor /app/data /app/courses
ENV HOST=0.0.0.0 COURSE_DIR=/app/courses DATA_DIR=/app/data ENABLE_ENHANCEMENT=false
USER tutor
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"]
CMD ["sctutor", "serve"]
