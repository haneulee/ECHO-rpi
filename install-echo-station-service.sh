#!/usr/bin/env bash
# Install systemd unit so ECHO station starts on boot (Raspberry Pi OS / Debian).
set -euo pipefail

ECHO_RPI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="${ECHO_RPI}/systemd/echo-station.service.in"
UNIT="/etc/systemd/system/echo-station.service"

if [[ ! -f "${ECHO_RPI}/.venv/bin/python3" ]]; then
  echo "Missing ${ECHO_RPI}/.venv — create venv first, e.g.:"
  echo "  cd ${ECHO_RPI} && python3 -m venv .venv --system-site-packages && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

if [[ ! -f "${TEMPLATE}" ]]; then
  echo "Missing template: ${TEMPLATE}"
  exit 1
fi

tmp="$(mktemp)"
sed "s|@ECHO_RPI@|${ECHO_RPI}|g" "${TEMPLATE}" >"${tmp}"

echo "Installing unit to ${UNIT}"
sudo cp "${tmp}" "${UNIT}"
rm -f "${tmp}"

sudo systemctl daemon-reload
sudo systemctl enable echo-station.service
sudo systemctl restart echo-station.service

echo "Done. Status:"
sudo systemctl status echo-station.service --no-pager || true
echo ""
echo "Logs: sudo journalctl -u echo-station.service -f"
