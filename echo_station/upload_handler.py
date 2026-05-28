"""
Upload Handler - Manages the ECHO device upload protocol state machine
"""

from enum import Enum
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional, List
import json
import logging
import re
import uuid

from .config import UPLOAD_BEGIN_MARKER, UPLOAD_END_MARKER, UPLOAD_SESSION_TIMEOUT
from .csv_manager import CSVManager

logger = logging.getLogger(__name__)

META_PREFIX = "ECHO_JSON_META:"
EVO_PREFIX = "ECHO_EVOLUTION_JSON:"
STATE_PREFIX = "ECHO_STATE_JSON:"

# Some BLE→text paths prepend BOM/NUL/zero-width chars so the line *looks* like
# "ECHO_EVOLUTION_JSON:..." in logs but startswith(EVO_PREFIX) fails → "Skipped malformed line".
_BLE_LINE_LEADING_JUNK = "\ufeff\x00\u200b\u200c\u200d\u2060"


def normalize_ble_upload_text(s: str) -> str:
    return s.strip().lstrip(_BLE_LINE_LEADING_JUNK)


# Second column of encounter.csv lines: logging device (MY_NAME on ESP)
_ECHO_NAME_RE = re.compile(r"^ECHO_[A-Za-z0-9_\-]{1,48}$")


def extract_encounter_source_device(line: str) -> Optional[str]:
    """Parse device_name from encounter CSV row (column index 1)."""
    parts = line.strip().split(",")
    if len(parts) != 8:
        return None
    name = parts[1].strip()
    if _ECHO_NAME_RE.match(name):
        return name
    return None


class UploadState(Enum):
    """States in the upload protocol"""
    IDLE = "idle"
    RECEIVING = "receiving"
    COMPLETE = "complete"


class UploadSession:
    """Represents a single device upload session"""

    def __init__(self, device_name: str, csv_manager: CSVManager):
        self.session_id = str(uuid.uuid4())[:8]
        self.device_name = device_name
        self.csv_manager = csv_manager
        self.state = UploadState.IDLE
        self.filepath: Optional[Path] = None
        self.row_count = 0
        self.start_time = None
        self.end_time = None
        self.error_count = 0
        self.skipped_lines = []
        self.source_device_name: Optional[str] = None
        self.metadata: Optional[Dict[str, Any]] = None
        self.evolution_filepath: Optional[Path] = None
        self.evolution_row_count = 0
        self.device_state_payload: Optional[Dict[str, Any]] = None

    def start(self) -> bool:
        """Begin upload session; CSV file is created on first valid data row (ESP device name)."""
        try:
            self.state = UploadState.RECEIVING
            self.start_time = datetime.now()
            self.filepath = None
            self.row_count = 0
            self.error_count = 0
            self.skipped_lines = []
            self.source_device_name = None
            self.metadata = None
            self.evolution_filepath = None
            self.evolution_row_count = 0
            self.device_state_payload = None
            logger.info(
                f"[Session {self.session_id}] Upload session open "
                f"(link={self.device_name!r}, file after first row)"
            )
            return True
        except Exception as e:
            logger.error(f"[Session {self.session_id}] Failed to start upload: {e}")
            self.state = UploadState.IDLE
            return False

    def receive_data_line(self, line: str) -> bool:
        """
        Process a single data line from the device
        Returns True if line was saved, False if skipped
        """
        if self.state != UploadState.RECEIVING:
            logger.warning(
                f"[Session {self.session_id}] Received data outside RECEIVING state"
            )
            return False

        stripped = normalize_ble_upload_text(line)
        if stripped.startswith(META_PREFIX):
            payload = stripped[len(META_PREFIX) :].strip()
            try:
                parsed = json.loads(payload)
                if isinstance(parsed, dict):
                    self.metadata = parsed
                    logger.info(
                        f"[Session {self.session_id}] Parsed upload metadata keys: "
                        f"{list(self.metadata.keys())}"
                    )
                else:
                    logger.warning(
                        f"[Session {self.session_id}] Metadata JSON must be an object"
                    )
                    self.error_count += 1
            except json.JSONDecodeError as e:
                logger.warning(
                    f"[Session {self.session_id}] Invalid JSON metadata: {e}"
                )
                self.error_count += 1
            return True

        if stripped.startswith(EVO_PREFIX):
            payload = stripped[len(EVO_PREFIX) :].strip()
            try:
                parsed = json.loads(payload)
                if not isinstance(parsed, dict):
                    raise ValueError("evolution JSON must be an object")
            except (json.JSONDecodeError, ValueError) as e:
                head = payload[:160].replace("\n", "\\n")
                logger.warning(
                    f"[Session {self.session_id}] Invalid evolution JSON (len={len(payload)}): "
                    f"{e}; head={head!r}"
                )
                self.error_count += 1
                return False
            if self.evolution_filepath is None:
                self.evolution_filepath = self.csv_manager.initialize_evolution_file(
                    self.session_id
                )
                logger.info(
                    f"[Session {self.session_id}] Evolution log: "
                    f"{self.evolution_filepath.name}"
                )
            if self.csv_manager.append_evolution_json_line(
                self.evolution_filepath, json.dumps(parsed, separators=(",", ":"))
            ):
                self.evolution_row_count += 1
                return True
            self.error_count += 1
            return False

        if stripped.startswith(STATE_PREFIX):
            payload = stripped[len(STATE_PREFIX) :].strip()
            try:
                parsed = json.loads(payload)
                if not isinstance(parsed, dict):
                    raise ValueError("state JSON must be an object")
                self.device_state_payload = parsed
                logger.info(
                    f"[Session {self.session_id}] Parsed device state keys: {list(parsed.keys())}"
                )
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(
                    f"[Session {self.session_id}] Invalid state JSON: {e}"
                )
                self.error_count += 1
                return False
            return True

        # Validate CSV format
        if not self.csv_manager.validate_csv_line(stripped):
            logger.warning(
                f"[Session {self.session_id}] Skipped malformed line: {stripped[:80]}..."
            )
            self.skipped_lines.append(stripped)
            self.error_count += 1
            return False

        if self.filepath is None:
            src = extract_encounter_source_device(line)
            if not src:
                src = "ECHO_UNKNOWN"
                logger.warning(
                    f"[Session {self.session_id}] "
                    f"Could not parse ECHO device name; using {src!r}"
                )
            self.source_device_name = src
            self.filepath = self.csv_manager.initialize_file(src)
            logger.info(
                f"[Session {self.session_id}] "
                f"Saving as {self.filepath.name} (source device {src!r})"
            )

        # Append to file
        if self.csv_manager.append_data_line(self.filepath, stripped):
            self.row_count += 1
            return True
        else:
            self.error_count += 1
            return False

    def finish(self) -> dict:
        """
        Finalize the upload session and return summary
        """
        if self.state != UploadState.RECEIVING:
            logger.warning(
                f"[Session {self.session_id}] Attempted to finish non-receiving session"
            )
            return {}

        self.state = UploadState.COMPLETE
        self.end_time = datetime.now()

        if not self.filepath and not self.evolution_filepath and not self.device_state_payload:
            logger.warning(
                f"[Session {self.session_id}] Upload finished with no CSV, evolution, or state"
            )
            return {}

        if self.filepath:
            summary = self.csv_manager.finalize_upload(self.filepath)
        else:
            summary = {
                "filepath": "",
                "filename": "none",
                "rows_saved": 0,
                "file_size_bytes": 0,
                "upload_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "success": True,
            }

        if self.evolution_filepath:
            evo_info = self.csv_manager.finalize_evolution_file(self.evolution_filepath)
            summary["evolution_filepath"] = str(self.evolution_filepath)
            summary["evolution_filename"] = evo_info.get("filename", "")
            summary["evolution_rows"] = evo_info.get("rows", 0)
        else:
            summary["evolution_filepath"] = None
            summary["evolution_rows"] = 0

        if self.device_state_payload:
            state_path = self.csv_manager.write_device_state_json(
                self.session_id, self.device_state_payload
            )
            summary["state_filepath"] = str(state_path)
        else:
            summary["state_filepath"] = None

        # Augment with session info
        duration = (self.end_time - self.start_time).total_seconds()
        logical = self.source_device_name or self.device_name
        if (
            logical == "ECHO_DEVICE"
            and isinstance(self.metadata, dict)
            and self.metadata.get("bleDeviceName")
        ):
            logical = str(self.metadata.get("bleDeviceName"))
        summary.update({
            "session_id": self.session_id,
            "device_name": logical,
            "duration_seconds": duration,
            "errors": self.error_count,
            "skipped_lines": len(self.skipped_lines),
            "upload_metadata": self.metadata,
            "session_start": self.start_time,
            "session_end": self.end_time,
        })

        logger.info(
            f"[Session {self.session_id}] Upload complete: "
            f"{summary.get('rows_saved', 0)} CSV rows, "
            f"{summary.get('evolution_rows', 0)} evolution event(s), "
            f"state={'yes' if summary.get('state_filepath') else 'no'} in {duration:.1f}s"
        )

        return summary

    def is_expired(self) -> bool:
        """Check if session has timed out"""
        if self.state == UploadState.IDLE or self.state == UploadState.COMPLETE:
            return False

        if self.start_time is None:
            return False

        elapsed = (datetime.now() - self.start_time).total_seconds()
        return elapsed > UPLOAD_SESSION_TIMEOUT

    def __repr__(self) -> str:
        return (
            f"UploadSession(id={self.session_id}, device={self.device_name}, "
            f"state={self.state.value}, rows={self.row_count})"
        )


class UploadHandler:
    """
    Manages ECHO device upload protocol:
    1. Device sends: "BEGIN_UPLOAD"
    2. Device sends: optional "ECHO_JSON_META:" line, optional "ECHO_STATE_JSON:" + JSON,
       encounter CSV lines, optional "ECHO_EVOLUTION_JSON:" + JSON per line
    3. Device sends: "END_UPLOAD"
    """

    def __init__(self, csv_manager: CSVManager):
        self.csv_manager = csv_manager
        self.active_sessions: dict[str, UploadSession] = {}  # {session_id: UploadSession}
        self.completed_sessions: List[dict] = []
        self.callbacks = {
            "on_upload_start": None,
            "on_data_received": None,
            "on_upload_complete": None,
            "on_error": None,
        }

    def set_callback(self, event: str, callback: Callable):
        """Register a callback for upload events"""
        if event in self.callbacks:
            self.callbacks[event] = callback
        else:
            logger.warning(f"Unknown callback event: {event}")

    def _trigger_callback(self, event: str, **kwargs):
        """Trigger a registered callback"""
        cb = self.callbacks.get(event)
        if cb:
            try:
                cb(**kwargs)
            except Exception as e:
                logger.error(f"Error in callback {event}: {e}")

    def handle_message(self, device_name: str, message: str) -> bool:
        """
        Process a message from a device
        Returns True if message was handled successfully
        """
        message = normalize_ble_upload_text(message)

        # Suppress empty messages
        if not message:
            return True

        logger.debug(f"Device '{device_name}' message: {message[:100]}")

        if message == UPLOAD_BEGIN_MARKER:
            return self._handle_begin_upload(device_name)
        elif message == UPLOAD_END_MARKER:
            return self._handle_end_upload(device_name)
        else:
            return self._handle_data_line(device_name, message)

    def _handle_begin_upload(self, device_name: str) -> bool:
        """Handle BEGIN_UPLOAD marker"""
        # Check for existing active session
        active_session = self._find_active_session_for_device(device_name)
        if active_session:
            logger.warning(
                f"Device '{device_name}' already has active session "
                f"{active_session.session_id}, ending it"
            )
            self._cleanup_session(active_session.session_id)

        # Create new session
        session = UploadSession(device_name, self.csv_manager)
        if session.start():
            self.active_sessions[session.session_id] = session
            self._trigger_callback(
                "on_upload_start",
                device_name=device_name,
                session_id=session.session_id,
            )
            return True
        else:
            self._trigger_callback(
                "on_error",
                device_name=device_name,
                error="Failed to initialize upload session",
            )
            return False

    def _handle_data_line(self, device_name: str, line: str) -> bool:
        """Handle a data line"""
        session = self._find_active_session_for_device(device_name)
        if not session:
            logger.warning(
                f"Device '{device_name}' sent data without active session"
            )
            self._trigger_callback(
                "on_error",
                device_name=device_name,
                error="Data received outside of upload session",
            )
            return False

        success = session.receive_data_line(line)
        if success:
            self._trigger_callback(
                "on_data_received",
                device_name=device_name,
                session_id=session.session_id,
                row_count=session.row_count,
            )
        return success

    def _handle_end_upload(self, device_name: str) -> bool:
        """Handle END_UPLOAD marker"""
        session = self._find_active_session_for_device(device_name)
        if not session:
            logger.warning(f"Device '{device_name}' ended upload without active session")
            return False

        summary = session.finish()
        self._cleanup_session(session.session_id)
        if not summary:
            logger.warning(
                f"Device '{device_name}' ended upload with no stored CSV or evolution data"
            )
            return True
        self.completed_sessions.append(summary)

        self._trigger_callback(
            "on_upload_complete",
            device_name=device_name,
            session_id=session.session_id,
            summary=summary,
        )
        return True

    def _find_active_session_for_device(self, device_name: str) -> Optional[UploadSession]:
        """Find an active upload session for a device"""
        for session in self.active_sessions.values():
            if session.device_name == device_name and session.state == UploadState.RECEIVING:
                return session
        return None

    def _cleanup_session(self, session_id: str):
        """Remove a session from active sessions"""
        if session_id in self.active_sessions:
            del self.active_sessions[session_id]

    def cleanup_expired_sessions(self):
        """Remove sessions that have exceeded timeout"""
        expired = [
            sid for sid, session in self.active_sessions.items()
            if session.is_expired()
        ]
        for sid in expired:
            session = self.active_sessions[sid]
            logger.warning(f"Session {sid} expired, cleaning up")
            session.state = UploadState.COMPLETE
            self._cleanup_session(sid)

    def get_active_sessions(self) -> List[UploadSession]:
        """Get list of active upload sessions"""
        return list(self.active_sessions.values())

    def get_session_stats(self) -> dict:
        """Get statistics about completed and active sessions"""
        return {
            "total_completed": len(self.completed_sessions),
            "currently_active": len(self.active_sessions),
            "total_rows_received": sum(s.get("rows_saved", 0) for s in self.completed_sessions),
        }
