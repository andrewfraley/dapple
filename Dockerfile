# ---- build the React UI ----------------------------------------------------
# The output is static files, so build it natively even for an arm64 image
# rather than running npm under emulation.
FROM --platform=$BUILDPLATFORM node:22-alpine AS ui

WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- pinned dependencies ---------------------------------------------------
# uv.lock turned into a plain requirements file with hashes, so the runtime
# image installs exactly what CI tested and doesn't need uv itself. The lock
# covers every platform, so this only runs once.
FROM --platform=$BUILDPLATFORM python:3.12-slim AS lock

RUN pip install --no-cache-dir uv==0.12.9
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-emit-project --no-dev -o /requirements.txt

# ---- runtime ---------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DAPPLE_DATA_DIR=/data

WORKDIR /srv

# Dependencies before the app, so a code change doesn't reinstall them.
COPY --from=lock /requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY pyproject.toml ./
COPY app/ ./app/
RUN pip install --no-cache-dir --no-deps .

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
