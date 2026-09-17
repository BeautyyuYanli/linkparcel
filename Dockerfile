FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.13 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --locked --no-dev --no-editable
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app

ENV PATH="/app/.venv/bin:$PATH"

USER app

EXPOSE 59000

CMD ["fastapi", "run", "src/linkparcel/app.py", "--host", "0.0.0.0", "--port", "59000"]
