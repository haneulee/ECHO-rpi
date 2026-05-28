"""
POST encounter batches to the Next.js ingest API (Bearer INGEST_SECRET).
Uses stdlib only (no requests dependency).
"""

import json
import logging
import ssl
import urllib.error
import urllib.request
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def post_encounters(
    app_url: str,
    ingest_secret: str,
    encounters: List[Dict[str, Any]],
    timeout_sec: float = 60.0,
) -> Dict[str, Any]:
    """
    POST /api/ingest/encounters with a JSON array body.
    app_url: e.g. https://your-app.vercel.app (no trailing slash)
    Returns parsed JSON on success.
    Raises urllib.error.HTTPError on HTTP errors.
    """
    if not encounters:
        return {"ok": True, "count": 0, "skipped": True}

    base = app_url.rstrip("/")
    url = f"{base}/api/ingest/encounters"
    body = json.dumps(encounters).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {ingest_secret}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )

    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec, context=ctx) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if not raw:
                return {"ok": True, "status": resp.status}
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        logger.error(
            "Ingest HTTP %s: %s",
            e.code,
            err_body[:2000],
        )
        raise
    except urllib.error.URLError as e:
        logger.error("Ingest URL error: %s", e)
        raise


def post_evolutions(
    app_url: str,
    ingest_secret: str,
    evolutions: List[Dict[str, Any]],
    timeout_sec: float = 60.0,
) -> Dict[str, Any]:
    """POST /api/ingest/evolutions — same Bearer auth as encounters."""
    if not evolutions:
        return {"ok": True, "count": 0, "skipped": True}

    base = app_url.rstrip("/")
    url = f"{base}/api/ingest/evolutions"
    body = json.dumps(evolutions).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {ingest_secret}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )

    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec, context=ctx) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if not raw:
                return {"ok": True, "status": resp.status}
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        logger.error(
            "Evolution ingest HTTP %s: %s",
            e.code,
            err_body[:2000],
        )
        raise
    except urllib.error.URLError as e:
        logger.error("Evolution ingest URL error: %s", e)
        raise


def post_echo_state(
    app_url: str,
    ingest_secret: str,
    payload: Dict[str, Any],
    timeout_sec: float = 60.0,
) -> Dict[str, Any]:
    """POST /api/ingest/echo-state — single JSON object (not array)."""
    base = app_url.rstrip("/")
    url = f"{base}/api/ingest/echo-state"
    body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {ingest_secret}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )

    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec, context=ctx) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if not raw:
                return {"ok": True, "status": resp.status}
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        logger.error(
            "Echo-state ingest HTTP %s: %s",
            e.code,
            err_body[:2000],
        )
        raise
    except urllib.error.URLError as e:
        logger.error("Echo-state ingest URL error: %s", e)
        raise
