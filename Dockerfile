# ---- build the React UI ----------------------------------------------------
# The output is static files, so build it natively even for an arm64 image
# rather than running npm under emulation.
FROM --platform=$BUILDPLATFORM node:22-alpine AS ui

WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- runtime ---------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DAPPLE_DATA_DIR=/data

WORKDIR /srv

COPY pyproject.toml ./
COPY app/ ./app/
RUN pip install --no-cache-dir .

COPY --from=ui /ui/dist/ ./static/

# Starts as root only long enough for the entrypoint to claim /data, then runs
# as PUID:PGID. The strands are on the LAN; the container only needs /data.
COPY scripts/entrypoint.sh /usr/local/bin/dapple-entrypoint
RUN mkdir -p /data

VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=8).status == 200 else 1)"

ENTRYPOINT ["dapple-entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
