"""
Build ingest API encounter objects from station encounter CSV + upload session wall times.
"""

from __future__ import annotations

import csv
import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import CSV_HEADER
from .echo_unit_code import normalize_echo_unit_code

logger = logging.getLogger(__name__)

_TYPE_MAP = {
    "BOUNCE": "bounce",
    "SHY": "shy",
    "MESSY": "messy",
}


def _other_echo_hash(target: str) -> str:
    h = hashlib.sha256(target.strip().encode("utf-8")).hexdigest()[:8]
    return f"echo:{h}"


def _proximity_zone(closeness_avg: float) -> str:
    if closeness_avg < 0.33:
        return "far"
    if closeness_avg < 0.66:
        return "near"
    if closeness_avg < 0.85:
        return "close"
    return "very_close"


def _parse_row_ts_ms(raw: str) -> int:
    try:
        return int(float(raw.strip()))
    except ValueError:
        return 0


def _wall_time_for_row(
    ts_ms: int,
    min_ts: int,
    max_ts: int,
    session_start: datetime,
    session_end: datetime,
) -> datetime:
    """Map ESP millis monotonically into [session_start, session_end] on the Pi."""
    if session_end <= session_start:
        return session_start
    span_ms = max(1, max_ts - min_ts)
    frac = (ts_ms - min_ts) / span_ms
    frac = max(0.0, min(1.0, frac))
    delta = (session_end - session_start) * frac
    return session_start + delta


def _local_tz():
    """Interpret naive Pi datetimes in the station's local timezone."""
    return datetime.now().astimezone().tzinfo


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_local_tz())
    return dt


def _iso(dt: datetime) -> str:
    """UTC Z suffix (DB / API friendly). Naive times = Pi local clock."""
    dt = _ensure_aware(dt)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class CsvRow:
    ts_ms: int
    device_name: str
    target: str
    type_raw: str
    event: str
    rssi: float
    smooth_rssi: float
    closeness: float


def _read_csv_rows(filepath: Path) -> List[CsvRow]:
    rows: List[CsvRow] = []
    with open(filepath, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        for i, parts in enumerate(reader):
            if not parts or len(parts) < 8:
                continue
            joined = ",".join(parts).strip()
            if i == 0 and joined == CSV_HEADER.strip():
                continue
            if len(parts) != 8:
                continue
            rows.append(
                CsvRow(
                    ts_ms=_parse_row_ts_ms(parts[0]),
                    device_name=parts[1].strip(),
                    target=parts[2].strip(),
                    type_raw=parts[3].strip().upper(),
                    event=parts[4].strip().lower(),
                    rssi=float(parts[5]) if parts[5] else 0.0,
                    smooth_rssi=float(parts[6]) if parts[6] else 0.0,
                    closeness=float(parts[7]) if parts[7] else 0.0,
                )
            )
    return rows


def csv_file_to_encounters(
    filepath: Path,
    *,
    device_id: str,
    session_start: datetime,
    session_end: datetime,
    sound_profile_id: str,
) -> List[Dict[str, Any]]:
    """
    device_id: normalized Echo unit code for ingest (EchoDevice.id / serialNumber).
    """
    data_rows = _read_csv_rows(filepath)
    if not data_rows:
        return []

    min_ts = min(r.ts_ms for r in data_rows)
    max_ts = max(r.ts_ms for r in data_rows)

    # (device_name, target) -> list of rows in current open session
    open_rows: Dict[Tuple[str, str], List[CsvRow]] = {}
    completed: List[List[CsvRow]] = []

    for row in data_rows:
        key = (row.device_name, row.target)
        if row.event == "seen":
            open_rows[key] = [row]
        elif row.event == "lost":
            if key in open_rows:
                open_rows[key].append(row)
                completed.append(open_rows.pop(key))
            else:
                logger.debug("lost without seen for %s — skip", key)
        else:
            if key in open_rows:
                open_rows[key].append(row)

    for key, acc in open_rows.items():
        if len(acc) >= 1:
            logger.info(
                "Closing incomplete session at EOF: %s (%d rows)",
                key,
                len(acc),
            )
            completed.append(acc)

    out: List[Dict[str, Any]] = []
    for acc in completed:
        if not acc:
            continue
        start_row = acc[0]
        end_row = acc[-1]
        other_model_name = start_row.target
        t0 = start_row.ts_ms
        t1 = end_row.ts_ms
        duration_ms = max(0, t1 - t0)
        duration_sec = max(0, int(round(duration_ms / 1000.0)))

        seen_like = [r for r in acc if r.event == "seen"] or acc
        rssis = [r.rssi for r in seen_like]
        closes = [r.closeness for r in seen_like]
        rssi_min = min(rssis) if rssis else 0.0
        rssi_max = max(rssis) if rssis else 0.0
        rssi_avg = sum(rssis) / len(rssis) if rssis else 0.0
        closeness_avg = sum(closes) / len(closes) if closes else 0.0

        other_type = _TYPE_MAP.get(start_row.type_raw.upper(), "shy")
        # Map only the session *end* into the Pi upload window; set start from durationSec
        # so ISO times stay consistent with duration (avoids compressing 56s into 1s wall).
        ended_dt = _ensure_aware(
            _wall_time_for_row(
                t1, min_ts, max_ts, session_start, session_end
            )
        )
        started_dt = ended_dt - timedelta(seconds=float(duration_sec))
        started_at = _iso(started_dt)
        ended_at = _iso(ended_dt)

        enc_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{device_id}|{start_row.target}|{t0}|{t1}",
            )
        )

        out.append(
            {
                "id": enc_id,
                "deviceId": device_id,
                "otherEchoHash": _other_echo_hash(other_model_name),
                "otherEchoModelName": other_model_name,
                "otherEchoType": other_type,
                "startedAt": started_at,
                "endedAt": ended_at,
                "durationSec": duration_sec,
                "rssiAvg": round(rssi_avg, 2),
                "rssiMin": int(round(rssi_min)),
                "rssiMax": int(round(rssi_max)),
                "closenessAvg": round(closeness_avg, 4),
                "proximityZone": _proximity_zone(closeness_avg),
                "soundProfileId": sound_profile_id,
            }
        )

    return out


def resolve_device_id(
    metadata: Optional[Dict[str, Any]],
    fallback_ble_name: Optional[str],
) -> str:
    """
    Prefer metadata['echoUnitCode']; else normalize CSV device_name (fallback_ble_name).
    """
    if metadata and isinstance(metadata.get("echoUnitCode"), str):
        raw = metadata["echoUnitCode"].strip()
        if raw:
            try:
                return normalize_echo_unit_code(raw)
            except ValueError:
                logger.warning(
                    "Invalid echoUnitCode in metadata %r — trying BLE name fallback",
                    raw,
                )
    if fallback_ble_name:
        return normalize_echo_unit_code(fallback_ble_name)
    raise ValueError(
        "No echo unit code: set ECHO_JSON_META echoUnitCode or valid CSV device_name"
    )
