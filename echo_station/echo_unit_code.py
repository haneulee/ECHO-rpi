"""
Normalize Echo unit codes to match the web app / ingest API (signup sticker id).
"""

import re
from typing import Optional

_UNIT_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{2,63}$")


def normalize_echo_unit_code(raw: Optional[str]) -> Optional[str]:
    """
    Strip whitespace, uppercase, then validate ^[A-Z0-9][A-Z0-9_-]{2,63}$.
    Returns None if raw is empty after strip; raises ValueError if invalid.
    """
    if raw is None:
        return None
    s = "".join(raw.strip().split()).upper()
    if not s:
        return None
    if not _UNIT_RE.match(s):
        raise ValueError(
            f"Invalid echo unit code (expected 3–64 chars, "
            f"^[A-Z0-9][A-Z0-9_-]{{2,63}}$): {raw!r}"
        )
    return s
