FROM python:3.11-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY agent/ ./agent/
COPY api.py ./

EXPOSE 8080
CMD ["uv", "run", "--no-dev", "uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8080"]
