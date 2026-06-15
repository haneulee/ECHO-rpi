#!/usr/bin/env bash
# Remove embedded NUL bytes from station.log (breaks plain grep without -a).
set -euo pipefail

LOG_FILE="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/logs/station.log}"

if [[ ! -f "${LOG_FILE}" ]]; then
  echo "Missing log file: ${LOG_FILE}"
  exit 1
fi

python3 - "${LOG_FILE}" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = path.read_bytes()
if b"\x00" not in data:
    print(f"No NUL bytes in {path}")
    sys.exit(0)

bak = path.with_suffix(path.suffix + ".bak")
bak.write_bytes(data)
cleaned = data.replace(b"\x00", b"")
path.write_bytes(cleaned)
print(f"Removed {len(data) - len(cleaned)} NUL byte(s) from {path}")
print(f"Backup: {bak}")
PY
