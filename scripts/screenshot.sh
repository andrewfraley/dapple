#!/usr/bin/env bash
# Screenshot the UI from a throwaway instance, never the live one.
#
#   scripts/screenshot.sh [page] [out.png] [width] [height]
#   scripts/screenshot.sh pattern docs/ui.png 1100 1360
#   scripts/screenshot.sh strands /tmp/strands.png 390 1100
#
# The instance gets a fresh data directory with the example config below, so
# the image shows the example addresses and built-in presets, whatever is in
# data/. LED counts are pinned so the preview renders with no strands present,
# and the short timeout lets the unanswered strand lookups give up quickly.
#
# Needs a built UI (npm --prefix frontend run build), chromium and ImageMagick.
set -euo pipefail

page=${1:-pattern}
out=${2:-docs/ui.png}
width=${3:-1100}
height=${4:-1360}
port=${DAPPLE_SCREENSHOT_PORT:-8799}

cd "$(dirname "$0")/.."
browser=$(command -v chromium-browser || command -v chromium || command -v google-chrome)

data=$(mktemp -d)
server=
cleanup() {
  [[ -n $server ]] && kill "$server" 2>/dev/null
  rm -rf "$data"
}
trap cleanup EXIT

cat >"$data/config.yaml" <<'EOF'
timeout: 0.5
groups:
  - id: tree
    name: "Christmas tree"
    strands:
      - name: TreeTop
        host: 192.168.40.21
        number_of_led: 250
        led_profile: RGBW
      - name: TreeBottom
        host: 192.168.40.22
        number_of_led: 250
        led_profile: RGBW
  - id: porch
    name: "Front porch"
    strands:
      - name: Porch
        host: 192.168.40.30
        number_of_led: 100
        led_profile: RGBW
EOF

DAPPLE_DATA_DIR=$data .venv/bin/python -m uvicorn app.main:app --port "$port" \
  >"$data/server.log" 2>&1 &
server=$!
for _ in $(seq 50); do
  curl -sf "http://127.0.0.1:$port/api/health" >/dev/null && break
  sleep 0.2
done

"$browser" --headless=new --disable-gpu --hide-scrollbars \
  --window-size="$width,$height" --virtual-time-budget=5000 \
  --screenshot="$out" "http://127.0.0.1:$port/#$page" 2>/dev/null
magick mogrify -strip "$out"
echo "Saved $out — look at it before committing."
