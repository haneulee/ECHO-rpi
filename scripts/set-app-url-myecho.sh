#!/usr/bin/env bash
# Point Pi cloud ingest at https://www.myecho.ch (run on the Raspberry Pi).
# Apex myecho.ch returns HTTP 308 to www; POST ingest must hit www directly.
set -euo pipefail

ENV_FILE="/etc/echo-station.env"
TARGET_URL="https://www.myecho.ch"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}. Copy echo-station.env.example first:"
  echo "  sudo cp echo-station.env.example ${ENV_FILE}"
  echo "  sudo chmod 600 ${ENV_FILE}"
  exit 1
fi

sudo sed -i \
  -e "s|https://echo-nu-orpin\\.vercel\\.app|${TARGET_URL}|g" \
  -e "s|https://your-app\\.vercel\\.app|${TARGET_URL}|g" \
  -e "s|https://myecho\\.ch|${TARGET_URL}|g" \
  "${ENV_FILE}"

if ! grep -q "^ECHO_APP_URL=${TARGET_URL}" "${ENV_FILE}"; then
  if grep -q '^ECHO_APP_URL=' "${ENV_FILE}"; then
    sudo sed -i "s|^ECHO_APP_URL=.*|ECHO_APP_URL=${TARGET_URL}|" "${ENV_FILE}"
  else
    echo "ECHO_APP_URL=${TARGET_URL}" | sudo tee -a "${ENV_FILE}" >/dev/null
  fi
fi

echo "ECHO_APP_URL=$(grep '^ECHO_APP_URL=' "${ENV_FILE}" | cut -d= -f2-)"
echo "Restarting echo-station.service..."
sudo systemctl restart echo-station.service
sudo systemctl status echo-station.service --no-pager || true
