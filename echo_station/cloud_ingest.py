"""
POST encounter batches to the Next.js ingest API (Bearer INGEST_SECRET).
Uses stdlib only (no requests dependency).
"""

from __future__ import annotations

import json
import logging
import ssl
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse, urlunparse

logger = logging.getLogger(__name__)

_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 5


def normalize_app_url(app_url: str) -> str:
    """
    Canonical ingest base URL (no trailing slash).

    myecho.ch apex redirects 308 → www.myecho.ch; use www up front so POST
    bodies are not dropped by redirect handlers.
    """
    base = app_url.strip().rstrip("/")
    if not base:
        return base

    parsed = urlparse(base)
    host = (parsed.hostname or "").lower()
    if host == "myecho.ch":
        netloc = "www.myecho.ch"
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        parsed = parsed._replace(netloc=netloc)
        base = urlunparse(parsed).rstrip("/")
    return base


def _redirect_target(
    error: urllib.error.HTTPError,
    current_url: str,
) -> Optional[str]:
    location = error.headers.get("Location")
    if location:
        return urljoin(current_url, location.strip())

    try:
        raw = error.read().decode("utf-8", errors="replace")
    except Exception:
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None

    redirect = data.get("redirect")
    if isinstance(redirect, str) and redirect.strip():
        return redirect.strip()
    return None


def _post_json(
    url: str,
    ingest_secret: str,
    body_obj: Any,
    *,
    timeout_sec: float,
    log_label: str,
) -> Dict[str, Any]:
    body = json.dumps(body_obj).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {ingest_secret}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    ctx = ssl.create_default_context()
    current_url = url

    for attempt in range(_MAX_REDIRECTS + 1):
        req = urllib.request.Request(
            current_url,
            data=body,
            method="POST",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec, context=ctx) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                if not raw:
                    return {"ok": True, "status": resp.status}
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            if e.code in _REDIRECT_CODES and attempt < _MAX_REDIRECTS:
                next_url = _redirect_target(e, current_url)
                if next_url and next_url != current_url:
                    logger.info(
                        "%s HTTP %s → follow redirect to %s",
                        log_label,
                        e.code,
                        next_url,
                    )
                    current_url = next_url
                    continue

            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            logger.error(
                "%s HTTP %s: %s",
                log_label,
                e.code,
                err_body[:2000],
            )
            raise
        except urllib.error.URLError as e:
            logger.error("%s URL error: %s", log_label, e)
            raise

    raise RuntimeError(f"{log_label}: too many redirects")


def post_encounters(
    app_url: str,
    ingest_secret: str,
    encounters: List[Dict[str, Any]],
    timeout_sec: float = 60.0,
) -> Dict[str, Any]:
    """
    POST /api/ingest/encounters with a JSON array body.
    app_url: e.g. https://www.myecho.ch (no trailing slash)
    Returns parsed JSON on success.
    Raises urllib.error.HTTPError on HTTP errors.
    """
    if not encounters:
        return {"ok": True, "count": 0, "skipped": True}

    base = normalize_app_url(app_url)
    url = f"{base}/api/ingest/encounters"
    return _post_json(
        url,
        ingest_secret,
        encounters,
        timeout_sec=timeout_sec,
        log_label="Ingest",
    )


def post_evolutions(
    app_url: str,
    ingest_secret: str,
    evolutions: List[Dict[str, Any]],
    timeout_sec: float = 60.0,
) -> Dict[str, Any]:
    """POST /api/ingest/evolutions — same Bearer auth as encounters."""
    if not evolutions:
        return {"ok": True, "count": 0, "skipped": True}

    base = normalize_app_url(app_url)
    url = f"{base}/api/ingest/evolutions"
    return _post_json(
        url,
        ingest_secret,
        evolutions,
        timeout_sec=timeout_sec,
        log_label="Evolution ingest",
    )


def post_echo_state(
    app_url: str,
    ingest_secret: str,
    payload: Dict[str, Any],
    timeout_sec: float = 60.0,
) -> Dict[str, Any]:
    """POST /api/ingest/echo-state — single JSON object (not array)."""
    base = normalize_app_url(app_url)
    url = f"{base}/api/ingest/echo-state"
    return _post_json(
        url,
        ingest_secret,
        payload,
        timeout_sec=timeout_sec,
        log_label="Echo-state ingest",
    )
