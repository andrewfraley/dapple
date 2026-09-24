# Base images are pinned by digest as well as tag: a tag can be re-pointed, a
# digest can't. Dependabot proposes new digests.

# ---- build the React UI ----------------------------------------------------
# The output is static files, so build it natively even for an arm64 image
# rather than running npm under emulation.
FROM --platform=$BUILDPLATFORM node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS ui

WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- pinned dependencies ---------------------------------------------------
# uv.lock turned into a plain requirements file with hashes, so the runtime
# image installs exactly what CI tested and doesn't need uv itself. The lock
# covers every platform, so this only runs once.
FROM --platform=$BUILDPLATFORM python:3.14-slim@sha256:caaf356f40667c496d405780745b9ac25771c189a51dfcc42430d531ea09f8a2 AS lock

COPY --from=ghcr.io/astral-sh/uv:0.12.9@sha256:8b940d3a9d65bed080436972241af2e21c84b5e8c9193f7014ed71479ee795ff /uv /bin/uv
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-emit-project --no-dev -o /requirements.txt

# ---- runtime ---------------------------------------------------------------
FROM python:3.14-slim@sha256:caaf356f40667c496d405780745b9ac25771c189a51dfcc42430d531ea09f8a2

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DAPPLE_DATA_DIR=/data

WORKDIR /srv

# Dependencies before the app, so a code change doesn't reinstall them. Every
# line in requirements.txt carries hashes, and --require-hashes refuses anything
# that doesn't match. The app itself runs from source rather than being built
# into a package, because building one would fetch setuptools unpinned.
COPY --from=lock /requirements.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY pyproject.toml ./
COPY app/ ./app/

COPY --from=ui /ui/dist/ ./static/

# Starts as root only long enough for the entrypoint to claim /data, then runs
# as PUID:PGID. The strands are on the LAN; the container only needs /data.
COPY scripts/entrypoint.sh /usr/local/bin/dapple-entrypoint
RUN mkdir -p /data

VOLUME ["/data"]
EXPOSE 8080

# /api/ping, not /api/health: this runs every 30s, and health asks every strand.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/ping', timeout=4).status == 200 else 1)"

ENTRYPOINT ["dapple-entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
