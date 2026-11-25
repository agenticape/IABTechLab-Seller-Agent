# Buyer demo web UI (hosted mode) for Fly.io.
# Build context is examples/buyer-demo (see DEPLOY.md):
#   fly deploy . --config deploy/fly/fly.toml --dockerfile deploy/fly/Dockerfile --ha=false
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --python /usr/local/bin/python3

COPY buyer_demo.py demo_ui.py narration.py ui.html ./
COPY static ./static

RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin demo \
    && mkdir -p /app/runs \
    && chown demo /app/runs

USER demo
ENV PATH=/app/.venv/bin:$PATH \
    PORT=8080
EXPOSE 8080

# Keys and SELLER_URL come from Fly secrets; nothing secret is baked in.
CMD ["python", "buyer_demo.py", "--serve", "--host", "0.0.0.0", "--port", "8080"]
