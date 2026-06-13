#!/usr/bin/env bash
# One-command local run with a real Telegram webhook:
#   1. starts a Cloudflare quick tunnel to localhost
#   2. writes the public https URL into .env as WEBHOOK_URL
#   3. starts the bot with docker compose
#
# Requires: docker, cloudflared (https://developers.cloudflare.com/cloudflared/)
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-8080}"
[ -f .env ] || { echo "No .env found — run: cp .env.example .env  (and fill BOT_TOKEN)"; exit 1; }

LOG="$(mktemp)"
echo "▶ Starting Cloudflare quick tunnel → http://localhost:${PORT} ..."
cloudflared tunnel --url "http://localhost:${PORT}" >"$LOG" 2>&1 &
CF_PID=$!
cleanup() { kill "$CF_PID" 2>/dev/null || true; }
trap cleanup EXIT

URL=""
for _ in $(seq 1 30); do
  URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -n1 || true)"
  [ -n "$URL" ] && break
  sleep 1
done
[ -z "$URL" ] && { echo "✗ Tunnel did not come up. Log:"; cat "$LOG"; exit 1; }
echo "✔ Tunnel ready: $URL"

# write WEBHOOK_URL into .env (replace or append)
if grep -q '^WEBHOOK_URL=' .env; then
  sed -i.bak "s|^WEBHOOK_URL=.*|WEBHOOK_URL=${URL}|" .env && rm -f .env.bak
else
  echo "WEBHOOK_URL=${URL}" >> .env
fi
echo "✔ .env updated (WEBHOOK_URL=${URL})"
echo "▶ Starting the bot (Ctrl+C stops bot + tunnel)..."
docker compose up --build
