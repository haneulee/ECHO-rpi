"""
ECHO Station - Main entry point for BLE GATT server
Receives and logs encounter data from ECHO peripheral devices
"""

import signal
import sys
import logging
from pathlib import Path
from datetime import datetime

from dbus.mainloop.glib import DBusGMainLoop, threads_init
import gi
gi.require_version("GLib", "2.0")
from gi import version_info as gi_version

try:
    from gi.repository import GLib
except ImportError:
    print("ERROR: GLib not found. Install with: sudo apt-get install libglib2.0-dev")
    sys.exit(1)

from .config import (
    LOG_DIR,
    LOG_LEVEL,
    LOG_FILE,
    STATION_NAMES,
    ECHO_APP_URL,
    ECHO_INGEST_SECRET,
    ECHO_SOUND_PROFILE_ID,
    ECHO_INGEST_TIMEOUT_SEC,
)
from .ble_server import BLEServer
from .upload_handler import UploadHandler
from .csv_manager import CSVManager
from .csv_to_encounters import csv_file_to_encounters, resolve_device_id
from .cloud_ingest import (
    normalize_app_url,
    post_encounters,
    post_evolutions,
    post_echo_state,
)
from .evolution_ingest import evolution_jsonl_to_payloads
from .state_ingest import echo_state_file_to_api_payload


def setup_logging(log_level=LOG_LEVEL, log_file=LOG_FILE, log_to_file=True):
    """Configure logging"""
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    # File handler (optional)
    file_handler = None
    if log_to_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(
            log_path,
            mode="a",
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.addHandler(console_handler)

    if file_handler:
        root_logger.addHandler(file_handler)

    return root_logger


class EchoStation:
    """Main ECHO station application"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.csv_manager = CSVManager()
        self.upload_handler = UploadHandler(self.csv_manager)
        self.ble_server = None
        self.main_loop = None
        self.running = False
        self.startup_failed = False

    def setup(self) -> bool:
        """Initialize all components"""
        self.logger.info("=" * 60)
        self.logger.info("ECHO Station Starting")
        self.logger.info("=" * 60)

        # Setup DBus main loop
        DBusGMainLoop(set_as_default=True)
        threads_init()
        self.main_loop = GLib.MainLoop()

        # Create BLE server
        self.ble_server = BLEServer(
            station_names=STATION_NAMES,
            on_data_received=self._on_ble_data_received,
        )

        # Initialize BLE server
        if not self.ble_server.initialize():
            self.logger.error("Failed to initialize BLE server")
            return False

        # Setup upload handler callbacks
        self.upload_handler.set_callback(
            "on_upload_start", self._on_upload_start
        )
        self.upload_handler.set_callback(
            "on_data_received", self._on_data_received
        )
        self.upload_handler.set_callback(
            "on_upload_complete", self._on_upload_complete
        )
        self.upload_handler.set_callback(
            "on_error", self._on_error
        )

        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        self.logger.info("Station setup complete")
        if ECHO_APP_URL and ECHO_INGEST_SECRET:
            ingest_base = normalize_app_url(ECHO_APP_URL)
            self.logger.info(
                "Cloud ingest enabled -> %s/api/ingest/{encounters,evolutions,echo-state}",
                ingest_base,
            )
        elif ECHO_APP_URL or ECHO_INGEST_SECRET:
            self.logger.warning(
                "Cloud ingest partially configured: set both ECHO_APP_URL and "
                "ECHO_INGEST_SECRET to enable POST after each upload."
            )
        return True

    def _complete_ble_setup(self) -> bool:
        """Runs inside GLib main loop; registers GATT asynchronously then advertises."""

        def on_gatt_ok():
            self.ble_server.start_advertising_async(self._on_le_advertising_done)

        def on_error(err: Exception):
            self.logger.error("Failed to register GATT application with BlueZ: %s", err)
            self.startup_failed = True
            self.main_loop.quit()

        self.ble_server.register_application_async(on_gatt_ok, on_error)
        return False  # idle callback: do not repeat

    def _on_le_advertising_done(self, ok: bool):
        if not ok:
            self.logger.error("Failed to start BLE LE advertising")
            self.startup_failed = True
            self.main_loop.quit()
            return
        self.running = True
        self.logger.info("=" * 60)
        self.logger.info("ECHO Station is running")
        self.logger.info(f"Station names: {STATION_NAMES}")
        self.logger.info("Waiting for ECHO devices...")
        self.logger.info("=" * 60)
        GLib.timeout_add_seconds(5, self._cleanup_timer)

    def _on_ble_data_received(self, data: str):
        """
        Callback when BLE characteristic receives data
        Called by BLE server when device writes data
        """
        try:
            # BLE link id (BlueZ does not pass central name here yet); CSV rows carry MY_NAME.
            device_name = "ECHO_DEVICE"

            self.upload_handler.handle_message(device_name, data)

        except Exception as e:
            self.logger.error(f"Error processing BLE data: {e}")

    def _on_upload_start(self, device_name: str, session_id: str, **kwargs):
        """Upload session started"""
        self.logger.info(f"[{session_id}] Upload started from {device_name}")

    def _on_data_received(self, device_name: str, session_id: str, row_count: int, **kwargs):
        """Data line received"""
        self.logger.debug(f"[{session_id}] Received row {row_count} from {device_name}")

    def _on_upload_complete(self, device_name: str, session_id: str, summary: dict, **kwargs):
        """Upload session completed"""
        self.logger.info(
            f"[{session_id}] Upload complete from {device_name}\n"
            f"  Rows saved: {summary.get('rows_saved', 0)}\n"
            f"  File: {summary.get('filename', 'unknown')}\n"
            f"  Evolution: {summary.get('evolution_rows', 0)} row(s)"
            f" → {summary.get('evolution_filename') or 'none'}\n"
            f"  Encounter sonic: {summary.get('encounter_sonic_rows', 0)} row(s)"
            f" → {summary.get('encounter_sonic_filename') or 'none'}\n"
            f"  State: {summary.get('state_filepath') or 'none'}\n"
            f"  Duration: {summary.get('duration_seconds', 0):.1f}s"
        )
        self._maybe_post_cloud_ingest(summary)

    def _maybe_post_cloud_ingest(self, summary: dict) -> None:
        if not ECHO_APP_URL or not ECHO_INGEST_SECRET:
            return

        fp = (summary.get("filepath") or "").strip()
        evo_fp = summary.get("evolution_filepath")
        sonic_fp = summary.get("encounter_sonic_filepath")
        state_fp = (summary.get("state_filepath") or "").strip()
        if not fp and not evo_fp and not sonic_fp and not state_fp:
            self.logger.info(
                "Cloud ingest skipped: no CSV, evolution, encounter sonic, or state file"
            )
            return

        enc_path = Path(fp) if fp else None
        if enc_path and not enc_path.is_file():
            self.logger.warning("Cloud ingest: encounter file missing %s", enc_path)
            enc_path = None

        evo_path = Path(evo_fp) if evo_fp else None
        if evo_path and not evo_path.is_file():
            self.logger.warning("Cloud ingest: evolution file missing %s", evo_path)
            evo_path = None

        sonic_path = Path(sonic_fp) if sonic_fp else None
        if sonic_path and not sonic_path.is_file():
            self.logger.warning(
                "Cloud ingest: encounter sonic file missing %s", sonic_path
            )
            sonic_path = None

        state_path = Path(state_fp) if state_fp else None
        if state_path and not state_path.is_file():
            self.logger.warning("Cloud ingest: state file missing %s", state_path)
            state_path = None

        if not enc_path and not evo_path and not sonic_path and not state_path:
            return

        meta = summary.get("upload_metadata")
        if not isinstance(meta, dict):
            meta = None

        ble_name = summary.get("device_name")
        if meta and isinstance(meta.get("bleDeviceName"), str) and meta["bleDeviceName"].strip():
            ble_name = meta["bleDeviceName"].strip()
        try:
            device_id = resolve_device_id(meta, ble_name)
        except ValueError as e:
            self.logger.error("Cloud ingest: bad device id — %s", e)
            return

        session_start = summary.get("session_start")
        session_end = summary.get("session_end")
        if not isinstance(session_start, datetime) or not isinstance(session_end, datetime):
            self.logger.error("Cloud ingest: missing session wall times")
            return

        encounters: list = []
        if enc_path:
            try:
                encounters = csv_file_to_encounters(
                    enc_path,
                    device_id=device_id,
                    session_start=session_start,
                    session_end=session_end,
                    sound_profile_id=ECHO_SOUND_PROFILE_ID,
                    encounter_sonic_jsonl=sonic_path,
                )
            except Exception as e:
                self.logger.exception("Cloud ingest: failed to build encounters: %s", e)

        if encounters:
            try:
                resp = post_encounters(
                    ECHO_APP_URL,
                    ECHO_INGEST_SECRET,
                    encounters,
                    timeout_sec=ECHO_INGEST_TIMEOUT_SEC,
                )
                self.logger.info(
                    "Cloud ingest OK: posted %d encounter(s) — %s",
                    len(encounters),
                    resp,
                )
            except Exception as e:
                self.logger.error("Cloud ingest (encounters) failed: %s", e)
        else:
            self.logger.info("Cloud ingest: no encounter sessions to POST")

        if evo_path and summary.get("evolution_rows", 0) > 0:
            try:
                evolutions = evolution_jsonl_to_payloads(
                    evo_path,
                    device_id=device_id,
                    session_start=session_start,
                    session_end=session_end,
                    encounter_csv=enc_path,
                )
            except Exception as e:
                self.logger.exception("Cloud ingest: failed to build evolutions: %s", e)
                evolutions = []
            if evolutions:
                try:
                    eresp = post_evolutions(
                        ECHO_APP_URL,
                        ECHO_INGEST_SECRET,
                        evolutions,
                        timeout_sec=ECHO_INGEST_TIMEOUT_SEC,
                    )
                    self.logger.info(
                        "Cloud evolution ingest OK: posted %d — %s",
                        len(evolutions),
                        eresp,
                    )
                except Exception as e:
                    self.logger.error("Cloud ingest (evolutions) failed: %s", e)
            else:
                self.logger.info("Cloud ingest: evolution file produced no payloads")

        if state_path:
            try:
                st_payload = echo_state_file_to_api_payload(
                    state_path,
                    device_id=device_id,
                    session_start=session_start,
                    session_end=session_end,
                    sound_profile_id=ECHO_SOUND_PROFILE_ID,
                )
                sresp = post_echo_state(
                    ECHO_APP_URL,
                    ECHO_INGEST_SECRET,
                    st_payload,
                    timeout_sec=ECHO_INGEST_TIMEOUT_SEC,
                )
                self.logger.info("Cloud echo-state ingest OK — %s", sresp)
            except Exception as e:
                self.logger.error("Cloud ingest (echo-state) failed: %s", e)

    def _on_error(self, device_name: str, error: str, **kwargs):
        """Error occurred during upload"""
        self.logger.error(f"Upload error from {device_name}: {error}")

    def _signal_handler(self, signum, frame):
        """Handle termination signals"""
        self.logger.info(f"Received signal {signum}, shutting down...")
        self.stop()

    def start(self):
        """Start the station"""
        self.startup_failed = False

        if not self.setup():
            self.logger.error("Failed to setup station")
            return False

        # Defer GATT RegisterApplication until the loop runs (avoids BlueZ NoReply deadlock)
        GLib.idle_add(self._complete_ble_setup)

        # Run main loop
        try:
            self.main_loop.run()
        except KeyboardInterrupt:
            self.logger.info("Interrupted by user")
            self.stop()

        return not self.startup_failed

    def _cleanup_timer(self):
        """Periodic cleanup of expired sessions"""
        self.upload_handler.cleanup_expired_sessions()

        # Print stats every 30 seconds
        stats = self.upload_handler.get_session_stats()
        self.logger.debug(
            f"Session stats - Completed: {stats['total_completed']}, "
            f"Active: {stats['currently_active']}, "
            f"Total rows: {stats['total_rows_received']}"
        )

        # Return True to keep the timer running
        return self.running

    def stop(self):
        """Stop the station"""
        self.logger.info("Stopping ECHO Station...")

        self.running = False

        if self.ble_server:
            self.ble_server.stop()

        if self.main_loop:
            self.main_loop.quit()

        # Print final stats
        stats = self.upload_handler.get_session_stats()
        self.logger.info(
            f"\nFinal stats:\n"
            f"  Total completed uploads: {stats['total_completed']}\n"
            f"  Total rows received: {stats['total_rows_received']}"
        )

        self.logger.info("ECHO Station stopped")


def main():
    """Main entry point"""
    # Setup logging
    setup_logging()

    # Create and start station
    station = EchoStation()
    if not station.start():
        sys.exit(1)


if __name__ == "__main__":
    main()
