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
