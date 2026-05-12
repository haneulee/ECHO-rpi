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
from .cloud_ingest import post_encounters


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
        file_handler = logging.FileHandler(log_file)
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
            self.logger.info(
                "Cloud ingest enabled → %s/api/ingest/encounters",
                ECHO_APP_URL,
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
            self.main_loop.quit()

        self.ble_server.register_application_async(on_gatt_ok, on_error)
        return False  # idle callback: do not repeat

    def _on_le_advertising_done(self, ok: bool):
        if not ok:
            self.logger.error("Failed to start BLE LE advertising")
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
            f"  Duration: {summary.get('duration_seconds', 0):.1f}s"
        )
        self._maybe_post_cloud_ingest(summary)

    def _maybe_post_cloud_ingest(self, summary: dict) -> None:
        if not ECHO_APP_URL or not ECHO_INGEST_SECRET:
            return
        fp = summary.get("filepath")
        if not fp:
            self.logger.info("Cloud ingest skipped: no CSV file for this session")
            return
        path = Path(fp)
        if not path.is_file():
            self.logger.warning("Cloud ingest skipped: missing file %s", path)
            return

        meta = summary.get("upload_metadata")
        if not isinstance(meta, dict):
            meta = None

        ble_name = summary.get("device_name")
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

        try:
            encounters = csv_file_to_encounters(
                path,
                device_id=device_id,
                session_start=session_start,
                session_end=session_end,
                sound_profile_id=ECHO_SOUND_PROFILE_ID,
            )
        except Exception as e:
            self.logger.exception("Cloud ingest: failed to build encounters: %s", e)
            return

        if not encounters:
            self.logger.info("Cloud ingest: no encounter sessions in CSV — nothing to POST")
            return

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
            self.logger.error("Cloud ingest failed: %s", e)

    def _on_error(self, device_name: str, error: str, **kwargs):
        """Error occurred during upload"""
        self.logger.error(f"Upload error from {device_name}: {error}")

    def _signal_handler(self, signum, frame):
        """Handle termination signals"""
        self.logger.info(f"Received signal {signum}, shutting down...")
        self.stop()

    def start(self):
        """Start the station"""
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
    station.start()


if __name__ == "__main__":
    main()
