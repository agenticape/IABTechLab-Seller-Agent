# =============================================================================
# Hosted mock-only seller sandbox (Fly.io) — see docs/deploy-fly.md
# =============================================================================
# Build from the repo root:  docker build -f infra/fly/Dockerfile -t iab-seller .
#
# - Locked install with uv (same uv.lock as CI), no dev/postgres/redis/gam extras.
# - Only the fictional aws_workshop CSV catalog is copied into the image.
# - Starts as root only so infra/fly/entrypoint.sh can chown the Fly volume at
#   /data, then the server runs as the unprivileged "seller" user.
# - Listens on 0.0.0.0:$PORT (default 8080), one uvicorn worker.
# =============================================================================

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

# -----------------------------------------------------------------------------
# Builder: resolve nothing, install exactly what uv.lock pins
# -----------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS builder

COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /bin/uv

# git: pyproject pins iab-agentic-primitives as a git dependency.
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Dependencies first so source edits don't re-download CrewAI and friends.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

# The project itself is installed editable on purpose: audience_capabilities
# locates data/taxonomies relative to src/, so the package must stay at
# /app/src/ad_seller rather than move into site-packages.
COPY src ./src
RUN uv sync --locked --no-dev

# -----------------------------------------------------------------------------
# Runtime
# -----------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

RUN groupadd --gid 1000 seller \
    && useradd --uid 1000 --gid seller --create-home --shell /usr/sbin/nologin seller

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY pyproject.toml README.md ./
COPY src ./src
COPY data/csv/samples/aws_workshop ./data/csv/samples/aws_workshop
COPY data/taxonomies ./data/taxonomies
COPY sandbox/sandbox_env.py sandbox/mint_keys.py sandbox/fly_server.py sandbox/.env.fly ./sandbox/
COPY infra/fly/entrypoint.sh ./infra/fly/entrypoint.sh
RUN chmod 755 /app/infra/fly/entrypoint.sh

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080 \
    DATA_DIR=/data

EXPOSE 8080

ENTRYPOINT ["/app/infra/fly/entrypoint.sh"]
CMD ["serve"]
