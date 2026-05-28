"""
Normalize evolution JSON lines (from ESP) for POST /api/ingest/evolutions.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .csv_to_encounters import _ensure_aware, _iso, _wall_time_for_row

logger = logging.getLogger(__name__)


def _other_echo_hash(target: str) -> str:
    h = hashlib.sha256(target.strip().encode("utf-8")).hexdigest()[:8]
    return f"echo:{h}"


def _millis_span_from_encounter_csv(enc_csv: Optional[Path]) -> Tuple[int, int]:
    if not enc_csv or not enc_csv.is_file():
        return 0, 1
    min_ts = 1 << 30
    max_ts = 0
    try:
        with open(enc_csv, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("timestamp,"):
                    continue
                parts = line.split(",")
                if len(parts) < 1:
                    continue
                try:
                    ts = int(float(parts[0]))
                except ValueError:
                    continue
                min_ts = min(min_ts, ts)
                max_ts = max(max_ts, ts)
    except OSError:
        return 0, 1
    if max_ts <= min_ts:
        return min_ts, min_ts + 1
    return min_ts, max_ts


def _millis_span_from_evolution_jsonl(evo_path: Path) -> Tuple[int, int]:
    min_ts = 1 << 30
    max_ts = 0
    try:
        with open(evo_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ms = int(obj.get("createdAtMs") or 0)
                if ms <= 0:
                    continue
                min_ts = min(min_ts, ms)
                max_ts = max(max_ts, ms)
    except OSError:
        return 0, 1
    if max_ts <= min_ts:
        return min_ts, min_ts + 1
    return min_ts, max_ts


def evolution_jsonl_to_payloads(
    evo_path: Path,
    *,
    device_id: str,
    session_start: datetime,
    session_end: datetime,
    encounter_csv: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """
    Each line in evolution.jsonl is one JSON object from the ESP.
    Adds deviceId, sourceEchoHash, createdAt (ISO UTC), deterministic id if missing.
    """
    min_ts, max_ts = _millis_span_from_encounter_csv(encounter_csv)
    emin, emax = _millis_span_from_evolution_jsonl(evo_path)
    min_ts = min(min_ts, emin)
    max_ts = max(max_ts, emax)
    if max_ts <= min_ts:
        max_ts = min_ts + 1

    out: List[Dict[str, Any]] = []
    try:
        raw_lines = evo_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        logger.error("Cannot read evolution file %s: %s", evo_path, e)
        return []

    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            logger.warning("Skip bad evolution JSON: %s", e)
            continue
        if not isinstance(obj, dict):
            continue

        src_target = str(obj.get("sourceTarget") or "").strip()
        if not src_target:
            logger.warning("Evolution line missing sourceTarget, skip")
            continue

        created_ms = int(obj.get("createdAtMs") or 0)
        ended_dt = _ensure_aware(
            _wall_time_for_row(created_ms, min_ts, max_ts, session_start, session_end)
        )
        created_iso = _iso(ended_dt)

        evo_id = obj.get("id")
        if not isinstance(evo_id, str) or not evo_id.strip():
            evo_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"{device_id}|{src_target}|{created_ms}|{obj.get('mutationType','')}",
                )
            )

        payload: Dict[str, Any] = {
            "id": evo_id,
            "deviceId": device_id,
            "mutationType": str(obj.get("mutationType") or "melody_fragment_exchange"),
            "sourceEchoHash": _other_echo_hash(src_target),
            "trigger": obj.get("trigger") or {},
            "beforeState": obj.get("beforeState") or {},
            "afterState": obj.get("afterState") or {},
            "createdAt": created_iso,
        }
        if obj.get("borrowedFragment") is not None:
            payload["borrowedFragment"] = obj["borrowedFragment"]
        if obj.get("dailyMemoryId"):
            payload["dailyMemoryId"] = obj["dailyMemoryId"]
        if obj.get("sourceEchoType"):
            payload["sourceEchoType"] = obj["sourceEchoType"]
        out.append(payload)

    return out
