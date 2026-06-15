"""
Load per-encounter peer sonic snapshots from station JSONL and attach to ingest payloads.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def load_encounter_sonic_jsonl(path: Optional[Path]) -> List[Dict[str, Any]]:
    if not path or not path.is_file():
        return []

    rows: List[Dict[str, Any]] = []

    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as e:
                    logger.warning(
                        "encounter sonic JSONL %s:%d invalid JSON: %s",
                        path.name,
                        line_no,
                        e,
                    )
                    continue
                if isinstance(obj, dict):
                    rows.append(obj)
    except OSError as e:
        logger.warning("encounter sonic JSONL read failed %s: %s", path, e)

    return rows


def _overlap_ms(
    a0: int,
    a1: int,
    b0: int,
    b1: int,
) -> int:
    start = max(a0, b0)
    end = min(a1, b1)
    return max(0, end - start)


def match_sonic_for_session(
    sonic_rows: List[Dict[str, Any]],
    *,
    target: str,
    t0_ms: int,
    t1_ms: int,
) -> Optional[Dict[str, Any]]:
    """
    Pick the sonic snapshot whose target and seen/lost window best overlaps the CSV session.
    """
    target_norm = target.strip()
    best: Optional[Dict[str, Any]] = None
    best_overlap = -1

    for row in sonic_rows:
        row_target = str(row.get("target") or "").strip()
        if row_target != target_norm:
            continue

        seen_ms = int(row.get("seenAtMs") or 0)
        lost_ms = int(row.get("lostAtMs") or seen_ms)
        if lost_ms < seen_ms:
            lost_ms = seen_ms

        overlap = _overlap_ms(t0_ms, t1_ms, seen_ms, lost_ms)
        if overlap > best_overlap:
            best_overlap = overlap
            best = row

    if best is None:
        for row in sonic_rows:
            if str(row.get("target") or "").strip() == target_norm:
                return row

    return best


def profile_snapshot_from_sonic_row(
    row: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not row:
        return None

    snap = row.get("profileSnapshot")
    if isinstance(snap, dict) and snap:
        return snap

    return None
