"""
CSV Manager - Handles encounter data file storage and management
"""

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict
import logging

from .config import LOG_DIR, CSV_HEADER

logger = logging.getLogger(__name__)


class CSVManager:
    """Manages CSV file creation and data appending for encounter logs"""

    def __init__(self, log_dir: Path = LOG_DIR):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def create_timestamped_filename(self, device_name: str) -> str:
        """
        Create a timestamped CSV filename
        Format: encounter_DEVICE_NAME_YYYY-MM-DD_HH-MM-SS.csv
        """
        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        safe_device_name = device_name.replace(" ", "_").replace("/", "_")
        return f"encounter_{safe_device_name}_{now}.csv"

    def initialize_file(self, device_name: str) -> Path:
        """
        Create a new CSV file with header for a device upload session
        Returns the full path to the created file
        """
        filename = self.create_timestamped_filename(device_name)
        filepath = self.log_dir / filename

        try:
            with open(filepath, "w", newline="") as f:
                f.write(CSV_HEADER + "\n")
            logger.info(f"Created CSV file: {filepath}")
            return filepath
        except IOError as e:
            logger.error(f"Failed to create CSV file: {e}")
            raise

    def append_data_line(self, filepath: Path, line: str) -> bool:
        """
        Append a single CSV data line to an existing file
        Returns True if successful, False otherwise
        """
        try:
            with open(filepath, "a", newline="") as f:
                f.write(line + "\n")
            return True
        except IOError as e:
            logger.error(f"Failed to append data to {filepath}: {e}")
            return False

    def append_multiple_lines(self, filepath: Path, lines: List[str]) -> int:
        """
        Append multiple CSV data lines at once
        Returns the number of successfully written lines
        """
        written = 0
        try:
            with open(filepath, "a", newline="") as f:
                for line in lines:
                    # Basic validation: check if line looks like CSV data
                    if line.strip():
                        f.write(line + "\n")
                        written += 1
            logger.debug(f"Appended {written} lines to {filepath}")
            return written
        except IOError as e:
            logger.error(f"Failed to append multiple lines to {filepath}: {e}")
            return written

    def finalize_upload(self, filepath: Path) -> Dict[str, any]:
        """
        Finalize an upload session and return summary information
        """
        try:
            # Count lines in file (excluding header)
            with open(filepath, "r") as f:
                lines = f.readlines()
                data_lines = len(lines) - 1  # Subtract header

            file_size = filepath.stat().st_size
            upload_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            summary = {
                "filepath": str(filepath),
                "filename": filepath.name,
                "rows_saved": data_lines,
                "file_size_bytes": file_size,
                "upload_time": upload_time,
                "success": True,
            }

            logger.info(
                f"Upload finalized: {data_lines} rows saved to {filepath.name}"
            )
            return summary
        except IOError as e:
            logger.error(f"Failed to finalize upload for {filepath}: {e}")
            return {
                "filepath": str(filepath),
                "filename": filepath.name,
                "rows_saved": 0,
                "file_size_bytes": 0,
                "upload_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "success": False,
                "error": str(e),
            }

    def validate_csv_line(self, line: str) -> bool:
        """
        Validate that a line matches the expected CSV format
        Expected: timestamp,device_name,target,type,event,rssi,smooth_rssi,closeness
        """
        parts = line.split(",")
        if len(parts) != 8:
            logger.debug(f"Invalid CSV line format (expected 8 fields, got {len(parts)}): {line}")
            return False
        return True

    def list_encounter_files(self) -> List[Path]:
        """
        List all encounter CSV files in the log directory
        """
        return sorted(self.log_dir.glob("encounter_*.csv"), reverse=True)

    def initialize_evolution_file(self, session_id: str) -> Path:
        """Create an empty JSONL file for one BLE upload session (evolution events)."""
        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        safe = session_id.replace(" ", "_").replace("/", "_")
        filename = f"evolution_{safe}_{now}.jsonl"
        filepath = self.log_dir / filename
        filepath.touch()
        logger.info(f"Created evolution JSONL: {filepath}")
        return filepath

    def append_evolution_json_line(self, filepath: Path, json_line: str) -> bool:
        """Append one JSON object per line (no outer prefix)."""
        try:
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(json_line.strip() + "\n")
            return True
        except IOError as e:
            logger.error(f"Failed to append evolution line: {e}")
            return False

    def finalize_evolution_file(self, filepath: Path) -> Dict[str, any]:
        try:
            n = 0
            if filepath.is_file():
                with open(filepath, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if line.strip():
                            n += 1
            return {"rows": n, "filepath": str(filepath), "filename": filepath.name}
        except OSError as e:
            logger.error("finalize evolution: %s", e)
            return {"rows": 0, "filepath": str(filepath), "error": str(e)}

    def write_device_state_json(self, session_id: str, data: Dict[str, any]) -> Path:
        """Persist one JSON object from ECHO_STATE_JSON for logs and cloud ingest."""
        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        safe = session_id.replace(" ", "_").replace("/", "_")
        filename = f"echo_state_{safe}_{now}.json"
        filepath = self.log_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"), ensure_ascii=False)
            f.write("\n")
        logger.info("Wrote device state snapshot %s", filepath.name)
        return filepath
