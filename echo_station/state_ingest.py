"""
Build POST body for /api/ingest/echo-state from a saved state JSON file.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from .csv_to_encounters import _ensure_aware, _iso, _wall_time_for_row


def echo_state_file_to_api_payload(
    path: Path,
    *,
    device_id: str,
    session_start: datetime,
    session_end: datetime,
    sound_profile_id: str,
) -> Dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("echo state file must contain a JSON object")

    ms = int(raw.get("updatedAtMs") or 0)
    min_ts = ms
    max_ts = ms + 1
    wall = _ensure_aware(
        _wall_time_for_row(ms, min_ts, max_ts, session_start, session_end)
    )
    last_synced = _iso(wall)

    out: Dict[str, Any] = {
        "deviceId": device_id,
        "soundProfileId": str(raw.get("soundProfileId") or sound_profile_id),
        "profileSnapshot": raw.get("profileSnapshot") or {},
        "lastSyncedAt": last_synced,
    }
    if raw.get("echoModelType"):
        out["echoModelType"] = raw["echoModelType"]
    if raw.get("uniqueDeviceName"):
        out["uniqueDeviceName"] = raw["uniqueDeviceName"]
    return out
