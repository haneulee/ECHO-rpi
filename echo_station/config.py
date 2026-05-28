"""
ECHO Station Configuration
"""

import os
from pathlib import Path

# =====================================================
# PATHS
# =====================================================

BASE_DIR = Path(__file__).parent.parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

SOUND_DIR = BASE_DIR / "sounds"
SOUND_DIR.mkdir(exist_ok=True)

# =====================================================
# BLE UUIDs
# =====================================================

SERVICE_UUID = "12345678-1234-1234-1234-1234567890ab"
CHARACTERISTIC_UUID = "abcd1234-5678-1234-5678-abcdef123456"

# LE advertising: primary service UUID(s) for BlueZ LEAdvertisement1.ServiceUUIDs.
# Must match ECHO firmware. BlueZ may place UUID + LocalName across AD and scan response.
ADVERTISING_SERVICE_UUIDS = [
    SERVICE_UUID,
]

# =====================================================
# STATION CONFIGURATION
# =====================================================

# Station names that will be advertised (can support multiple)
STATION_NAMES = [
    "ECHO_station_001",
]

# BLE Advertisement parameters
BLE_DEVICE = "hci0"  # Default Bluetooth device
ADVERTISING_INTERVAL = 100  # milliseconds

# =====================================================
# PROTOCOL CONFIGURATION
# =====================================================

UPLOAD_BEGIN_MARKER = "BEGIN_UPLOAD"
UPLOAD_END_MARKER = "END_UPLOAD"

# CSV header expected from ECHO devices
# Format: timestamp,device_name,target,type,event,rssi,smooth_rssi,closeness
CSV_HEADER = "timestamp,device_name,target,type,event,rssi,smooth_rssi,closeness"

# Timeout for upload session (seconds). Large value = practical unlimited for dock uploads.
UPLOAD_SESSION_TIMEOUT = 2_147_483_647  # ~68 years (max 32-bit signed int)

# =====================================================
# LOGGING
# =====================================================

LOG_LEVEL = "INFO"  # DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_TO_FILE = True
LOG_FILE = LOG_DIR / "station.log"

# =====================================================
# CONNECTION HANDLING
# =====================================================

MAX_CONCURRENT_UPLOADS = 1
CONNECTION_TIMEOUT = 60  # seconds

# =====================================================
# CLOUD INGEST (optional — POST to Next.js after each upload)
# =====================================================

# Base URL, e.g. https://your-app.vercel.app (no trailing slash)
ECHO_APP_URL = os.environ.get("ECHO_APP_URL", "").strip().rstrip("/")
# Same value as server INGEST_SECRET (Bearer token)
ECHO_INGEST_SECRET = os.environ.get("ECHO_INGEST_SECRET", "").strip()
# Seeded SoundProfile id in the web DB
ECHO_SOUND_PROFILE_ID = os.environ.get(
    "ECHO_SOUND_PROFILE_ID",
    "ambient3_meditation_v1",
).strip()
ECHO_INGEST_TIMEOUT_SEC = float(os.environ.get("ECHO_INGEST_TIMEOUT_SEC", "60"))

# =====================================================
# DAILY SOUND (local render + playback + browser endpoint)
# =====================================================


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# After an upload with encounter rows, render today's CSV files into sounds/today.wav.
ECHO_DAILY_SOUND_ENABLED = _env_bool("ECHO_DAILY_SOUND_ENABLED", True)
# Play the generated WAV through the Pi's default audio output. For Bluetooth,
# pair/connect the speaker and make it the default sink.
ECHO_DAILY_SOUND_AUTOPLAY = _env_bool("ECHO_DAILY_SOUND_AUTOPLAY", True)
ECHO_DAILY_SOUND_DURATION_SEC = float(
    os.environ.get("ECHO_DAILY_SOUND_DURATION_SEC", "45")
)
# Optional command template, e.g. 'paplay {path}' or 'aplay -q {path}'.
ECHO_SOUND_PLAY_COMMAND = os.environ.get("ECHO_SOUND_PLAY_COMMAND", "").strip()

# Lightweight local web playback:
#   http://<raspberry-pi-ip>:8765/         browser player
#   http://<raspberry-pi-ip>:8765/today.wav raw WAV
ECHO_DAILY_SOUND_WEB_ENABLED = _env_bool("ECHO_DAILY_SOUND_WEB_ENABLED", True)
ECHO_SOUND_WEB_HOST = os.environ.get("ECHO_SOUND_WEB_HOST", "0.0.0.0").strip()
ECHO_SOUND_WEB_PORT = int(os.environ.get("ECHO_SOUND_WEB_PORT", "8765"))
