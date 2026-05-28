# ECHO Station - BLE GATT Server

A Python BLE GATT server that listens for ECHO ESP32 personality devices to upload proximity encounter logs via Bluetooth Low Energy.

## Overview

**ECHO Station** acts as a BLE peripheral (server) that ECHO ESP32 devices connect to and upload their encounter data. The server:

- Advertises a GATT service over BLE
- Receives CSV-formatted encounter logs from ECHO devices
- Saves data to timestamped CSV files in the `logs/` directory
- Renders a "today's sound" WAV from the day's encounter data after each upload
- Optionally auto-plays the sound through the Pi's default audio output
- Serves the latest daily sound at a local browser URL
- Handles multi-device connections and uploads
- Provides console logging and progress tracking

## System Requirements

### Hardware
- Raspberry Pi (3, 4, or later) or Linux desktop with Bluetooth support
- Bluetooth 4.0+ adapter (built-in on most modern Pis)

### Software
- Python 3.7+
- `dbus-python` library
- BlueZ Bluetooth stack (usually pre-installed on Raspberry Pi OS)
- GLib development files

### Installation

#### On Raspberry Pi OS (Debian-based)

```bash
# Install system dependencies
sudo apt-get update
sudo apt-get install -y \
    python3-pip \
    python3-dbus \
    libglib2.0-dev \
    libdbus-1-dev

# Clone or navigate to the project directory
cd ~/Documents/echo/ECHO-rpi

# Install Python dependencies
pip3 install -r requirements.txt
```

#### On other Linux distributions

```bash
# Ubuntu/Debian
sudo apt-get install python3-dbus libglib2.0-dev python3-pip

# Fedora
sudo dnf install dbus-python glib2-devel python3-pip

# Arch
sudo pacman -S dbus python-dbus glib python-pip
```

## Configuration

Edit [echo_station/config.py](echo_station/config.py) to customize:

- **STATION_NAMES**: List of BLE device names to advertise (e.g., `["ECHO_station_001"]`)
- **SERVICE_UUID** and **CHARACTERISTIC_UUID**: GATT identifiers (hardcoded to match ESP32)
- **LOG_DIR**: Output directory for CSV files (default: `./logs/`)
- **LOG_LEVEL**: Verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`)
- **UPLOAD_SESSION_TIMEOUT**: Maximum duration (seconds) for a single upload session; default is set very high so uploads rarely time out

## Today's Sound Playback

After an ESP32 finishes a dock upload with encounter rows, the station rebuilds a deterministic WAV from all of today's `encounter_*.csv` files:

- File output: `sounds/today.wav` and `sounds/daily_echo_YYYY-MM-DD.wav`
- Browser player: `http://<raspberry-pi-ip>:8765/`
- Raw audio URL: `http://<raspberry-pi-ip>:8765/today.wav`
- API status: `http://<raspberry-pi-ip>:8765/api/today-sound`

For automatic playback, pair/connect the Bluetooth speaker on Raspberry Pi OS and make it the default audio output. The station tries `paplay`, then `pw-play`, then `aplay`. If your speaker needs a specific command, set:

```bash
ECHO_SOUND_PLAY_COMMAND=paplay {path}
```

Useful optional settings in `/etc/echo-station.env`:

```bash
ECHO_DAILY_SOUND_ENABLED=true
ECHO_DAILY_SOUND_AUTOPLAY=true
ECHO_DAILY_SOUND_DURATION_SEC=45
ECHO_DAILY_SOUND_WEB_ENABLED=true
ECHO_SOUND_WEB_HOST=0.0.0.0
ECHO_SOUND_WEB_PORT=8765
```

Restart the service after changing these values:

```bash
sudo systemctl restart echo-station.service
```

## Usage

### Running the Station

```bash
# From the project directory
python3 run_station.py

# Or as a module
python3 -m echo_station.station
```

### Example Output

```
============================================================
ECHO Station Starting
============================================================
2026-05-12 14:23:45,123 - echo_station.ble_server - INFO - Using Bluetooth adapter: hci0
2026-05-12 14:23:46,456 - echo_station.ble_server - INFO - GATT application registered
2026-05-12 14:23:46,789 - echo_station.ble_server - INFO - Adapter powered and discoverable
============================================================
ECHO Station is running
Station names: ['ECHO_station_001']
Waiting for ECHO devices...
============================================================

2026-05-12 14:24:10,234 - echo_station.station - INFO - [a1b2c3d4] Upload started from ECHO_MESSY_001
2026-05-12 14:24:15,567 - echo_station.station - INFO - [a1b2c3d4] Upload complete from ECHO_MESSY_001
  Rows saved: 42
  File: encounter_ECHO_MESSY_001_2026-05-12_14-24-10.csv
  Duration: 5.3s
```

### Stopping the Station

Press `Ctrl+C` to gracefully shut down:

```
^CReceived signal 2, shutting down...
Stopping ECHO Station...

Final stats:
  Total completed uploads: 1
  Total rows received: 42

ECHO Station stopped
```

## Run on boot (Raspberry Pi)

You can register a **systemd** service so the station starts automatically after power-on—no need to start it manually each time.

### Prerequisites

- A **Python virtual environment** at `.venv` under `ECHO-rpi`. If you do not have one yet:
  ```bash
  cd ~/Documents/echo/ECHO-rpi
  python3 -m venv .venv --system-site-packages
  sudo apt-get install -y python3-gi gir1.2-glib-2.0
  .venv/bin/pip install -r requirements.txt
  ```
- The service runs as **root**, same as running the station with `sudo` for BlueZ / Bluetooth access.

### One-time install

On the Raspberry Pi:

```bash
cd ~/Documents/echo/ECHO-rpi
./install-echo-station-service.sh
```

Enter your `sudo` password when prompted. After that, the station will start **on every boot**.

### Check that it is running

```bash
sudo systemctl status echo-station.service
```

Follow logs:

```bash
sudo journalctl -u echo-station.service -f
```

### Disable auto-start

```bash
sudo systemctl disable --now echo-station.service
```

If you move the project to a different path, run `./install-echo-station-service.sh` again **from the new directory** so the unit file is regenerated with the correct paths.

## CSV Output Format

Data is saved in timestamped CSV files: `encounter_DEVICE_NAME_YYYY-MM-DD_HH-MM-SS.csv`

### CSV Header
```
timestamp,device_name,target,type,event,rssi,smooth_rssi,closeness
```

### Example Data
```
timestamp,device_name,target,type,event,rssi,smooth_rssi,closeness
1234567890,ECHO_MESSY_001,ECHO_BOUNCE_003,BOUNCE,seen,-65,-64.50,0.812
1234567901,ECHO_MESSY_001,ECHO_BOUNCE_003,BOUNCE,seen,-64,-64.25,0.821
1234567912,ECHO_MESSY_001,ECHO_SHY_002,SHY,seen,-72,-70.50,0.654
1234567923,ECHO_MESSY_001,ECHO_BOUNCE_003,BOUNCE,lost,-64,0.00,0.000
```

## Protocol Details

The ECHO upload protocol follows this sequence:

1. **ECHO device connects** to the BLE GATT server
2. **Device sends**: `BEGIN_UPLOAD` marker
3. **Device sends** (recommended): one line `ECHO_JSON_META:` + JSON object with `echoUnitCode` (web signup / `EchoDevice.id`), `bleDeviceName` (`MY_NAME`), and `echoModelType` (`shy` \| `messy` \| `bounce`). This line is **not** written to the CSV file.
4. **Device sends** (recommended): `ECHO_STATE_JSON:` + JSON snapshot (`profileSnapshot`, `soundProfileId`, …) — not written to the encounter CSV
5. **Device sends** (optional): CSV data lines (one per write operation) when encounter data exists
6. **Device sends** (optional): `ECHO_EVOLUTION_JSON:` + one JSON object per evolution event (not written to the encounter CSV)
7. **Device sends**: `END_UPLOAD` marker
8. **Server finalizes** encounter CSV, evolution JSONL, and state JSON (if any), logs summary, and (if configured) **POST**s to the Next.js ingest API
9. **Connection closes**

The server handles:
- **Multi-device uploads**: Each device gets its own session and CSV file
- **Concurrent uploads**: Sequential processing (one at a time)
- **Malformed data**: Skipped lines logged as warnings; upload continues
- **Timeouts**: Sessions use a very long timeout (see `config.py`); practical unlimited for dock uploads
- **Mid-upload disconnects**: Graceful cleanup; partial data preserved

## Cloud database ingest (optional)

After each successful upload, the station can **POST** JSON encounters (`POST /api/ingest/encounters`), evolution rows (`POST /api/ingest/evolutions`), and device echo state (`POST /api/ingest/echo-state`) to your deployed Next.js app, using the same Bearer `INGEST_SECRET` as your web backend.

### Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ECHO_APP_URL` | with ingest | Base URL, e.g. `https://your-app.vercel.app` (no trailing slash) |
| `ECHO_INGEST_SECRET` | with ingest | Same value as server `INGEST_SECRET` |
| `ECHO_SOUND_PROFILE_ID` | no | Defaults to `ambient3_meditation_v1` |
| `ECHO_INGEST_TIMEOUT_SEC` | no | HTTP timeout (default `60`) |

If either `ECHO_APP_URL` or `ECHO_INGEST_SECRET` is missing, cloud ingest is skipped (local CSV only).

### systemd (recommended)

The unit file includes `EnvironmentFile=-/etc/echo-station.env`. **`./install-echo-station-service.sh`** copies `echo-station.env.example` to `/etc/echo-station.env` **only if that file does not exist** (then edit secrets). You can also create it manually (root-readable only):

```
ECHO_APP_URL=https://your-app.vercel.app
ECHO_INGEST_SECRET=your-ingest-secret
```

Then `sudo systemctl restart echo-station.service`.

Re-run `./install-echo-station-service.sh` after pulling changes so the updated unit file is installed.

### Behaviour

- CSV rows are grouped into **seen → lost** sessions per `(device_name, target)` and mapped to the ingest **Encounter** shape (`proximityZone`, `otherEchoModelName` from `target`, ISO timestamps from the upload session window on the Pi, deterministic `id` for upsert-friendly retries).
- Evolution JSONL lines are forwarded as **`POST /api/ingest/evolutions`** (implement this route on the Next app if missing; same `INGEST_SECRET`).

## Project Structure

```
ECHO-rpi/
├── echo_station/
│   ├── __init__.py              # Package definition
│   ├── config.py                # Configuration constants
│   ├── csv_manager.py           # File I/O and CSV handling
│   ├── csv_to_encounters.py     # CSV → ingest Encounter JSON
│   ├── cloud_ingest.py          # HTTPS POST ingest (encounters + evolutions)
│   ├── daily_sound.py           # Today's WAV render, autoplay, and web playback
│   ├── evolution_ingest.py      # evolution.jsonl → API payloads
│   ├── echo_unit_code.py        # Normalize unit codes (match web app)
│   ├── upload_handler.py        # Protocol state machine
│   ├── ble_server.py            # BlueZ GATT server
│   └── station.py               # Main application
├── logs/                        # Output directory for CSV files
├── requirements.txt             # Python dependencies
├── run_station.py              # CLI entry point
└── README.md                   # This file
```

## Module Overview

### `config.py`
Central configuration: UUIDs, paths, timeouts, logging levels.

### `csv_manager.py`
Handles file operations:
- Creates timestamped CSV files
- Validates and appends data lines
- Finalizes uploads with summaries

### `upload_handler.py`
Manages the ECHO protocol:
- Tracks upload sessions per device
- Parses BEGIN/END markers
- Buffers CSV lines until completion
- Triggers callbacks for events

### `ble_server.py`
BlueZ integration via DBus:
- Registers GATT service and characteristic
- Handles BLE write operations
- Controls adapter power and discoverability

### `station.py`
Main application:
- Initializes all components
- Sets up event callbacks; after each upload may POST encounters to the cloud when `ECHO_APP_URL` and `ECHO_INGEST_SECRET` are set
- Runs async GLib event loop
- Handles graceful shutdown

## Troubleshooting

### "Permission denied" errors

BLE operations require root or `bluetooth` group membership:

```bash
# Run with sudo
sudo python3 run_station.py

# Or add user to bluetooth group (requires re-login)
sudo usermod -a -G bluetooth $USER
sudo usermod -a -G sudo $USER
```

### "No such device" (hci0 not found)

Bluetooth adapter not detected. Check:

```bash
# List Bluetooth devices
hcitool dev

# If empty, adapter may be off or not installed
# Enable if available
sudo hciconfig hci0 up
```

### "DBus connection refused"

DBus daemon not running or permission issue:

```bash
# Check DBus status
systemctl status dbus

# If not running (unlikely on Pi)
sudo systemctl start dbus
```

### Devices not connecting

1. Verify station is advertising:
   ```bash
   # On another device/Pi
   bluetoothctl
   > scan on
   # Look for "ECHO_station_001"
   ```

2. Check logs for errors:
   ```bash
   tail -f logs/station.log
   ```

3. Verify ESP32 is configured with matching UUIDs in firmware

## Testing

### Manual Testing with `bluetoothctl`

```bash
# On another machine/Pi
bluetoothctl
> scan on
# Should see "ECHO_station_001"

> connect <MAC_ADDRESS>
# Device should show "Connected: yes"

> attributes <MAC_ADDRESS>
# Should show the GATT service and characteristic

# To send test data (on connected device):
# This depends on your BLE tools; typically would use a BLE write operation
```

### Simulating a Device Upload

For testing without an ESP32, create a test script:

```python
from echo_station.upload_handler import UploadHandler
from echo_station.csv_manager import CSVManager

csv_mgr = CSVManager()
handler = UploadHandler(csv_mgr)

# Simulate device upload
handler.handle_message("TEST_DEVICE", "BEGIN_UPLOAD")
handler.handle_message("TEST_DEVICE", "1234567890,TEST_DEVICE,TARGET_001,BOUNCE,seen,-65,-64.50,0.812")
handler.handle_message("TEST_DEVICE", "1234567901,TEST_DEVICE,TARGET_002,SHY,seen,-72,-70.50,0.654")
handler.handle_message("TEST_DEVICE", "END_UPLOAD")

# Check output in logs/
ls -la logs/encounter_*.csv
```

## Performance Considerations

- **CSV write speed**: ~25ms per line (matching ESP32 firmware delay)
- **Max devices**: Tested with 5 simultaneous connections
- **File I/O**: Synchronous per line (can be optimized to batch writes)
- **Memory usage**: ~10MB base + ~1MB per active upload session

For high-volume scenarios, consider:
- Batching CSV writes (currently one per line)
- Async file I/O
- Compression of completed CSV files
- Cloud sync after upload

## ESP32 Integration

The ECHO ESP32 firmware ([prototype_3_save_events.ino](../ECHO-esp/prototype_3_save_events/)) connects to this station via the `uploadMemoryToStation()` function. Key points:

- **BLE scan duration**: 5 seconds to find station
- **Service/Characteristic UUIDs**: Hardcoded; must match `config.py`
- **Write interval**: 25ms between lines
- **Auto-restart after upload**: ESP32 restarts after successful upload

## Future Enhancements

- [ ] Support for multiple concurrent uploads
- [ ] Configurable station names without code change
- [ ] Web dashboard for monitoring
- [ ] Cloud sync (AWS S3, Google Drive, etc.)
- [ ] Data compression and archival
- [ ] Metrics export (Prometheus, InfluxDB)
- [ ] Encryption for BLE communication
- [ ] Support for other ESP32 variants/boards

## License

Part of the ECHO Personality System project.

## Support

For issues or questions:
1. Check logs in `logs/station.log`
2. Run with `LOG_LEVEL = "DEBUG"` in `config.py` for verbose output
3. Verify BLE adapter is powered: `sudo hciconfig hci0 up`
4. Check ESP32 firmware is using matching UUIDs


---

- run 'sudo systemctl restart echo-station.service' after changing code
- run 'cd /home/echo/Documents/echo/ECHO-rpi
./install-echo-station-service.sh' after changing service related code ( systemd/echo-station.service.in이나 install-echo-station-service.sh)