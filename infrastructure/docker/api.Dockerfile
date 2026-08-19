# syntax=docker/dockerfile:1.7
#
# API image — Django 5.1 on Python 3.12.
#
# GDAL and GEOS are installed even though `django.contrib.gis` is not yet in
# INSTALLED_APPS (see docs/adr/0004). PostGIS is provisioned from day one, and
# an image that already carries the geospatial libraries means Phase 3 enables
# GeoDjango with a settings change rather than a base-image change.

FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
        binutils \
        curl \
        gdal-bin \
        libgdal-dev \
        libgeos-dev \
        libproj-dev \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

# uv comes from PyPI rather than ghcr.io/astral-sh/uv. ghcr.io does not resolve
# from the development environment, and an image that only builds on CI's
# network is one nobody can debug locally (ADR 0010). PyPI is already the source
# of every other Python dependency here, so this removes a registry from the
# supply chain rather than adding one. Pinned exactly; upgrading is a commit.
ARG UV_VERSION=0.5.11
RUN pip install --no-cache-dir --root-user-action=ignore "uv==${UV_VERSION}"

WORKDIR /app

# ---------------------------------------------------------------------------
# Dependencies are installed before the source is copied so that a code change
# does not invalidate the dependency layer.
# ---------------------------------------------------------------------------
FROM base AS deps
COPY apps/api/pyproject.toml apps/api/uv.lock apps/api/.python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# ---------------------------------------------------------------------------
FROM base AS development
COPY --from=deps /app/.venv /app/.venv
COPY apps/api/pyproject.toml apps/api/uv.lock apps/api/.python-version ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project
ENV PATH="/app/.venv/bin:$PATH"
COPY apps/api/ ./
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "config.asgi:application", \
     "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ---------------------------------------------------------------------------
FROM base AS production
COPY --from=deps /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings.prod

COPY apps/api/ ./

# Never run as root (SRS §30.4).
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/v1/health || exit 1

CMD ["python", "-m", "uvicorn", "config.asgi:application", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
