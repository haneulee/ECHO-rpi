"""
Daily sound rendering and lightweight web playback for ECHO Station.

The renderer intentionally uses only the Python standard library so it can run
on a Raspberry Pi without extra DSP dependencies. Its canonical input is the
same Encounter-shaped summary that the web app stores in the database, so the
website can port the same deterministic synth and match Raspberry Pi playback.
"""

from __future__ import annotations

import csv
import hashlib
import html
import json
import logging
import math
import shlex
import shutil
import struct
import subprocess
import threading
import wave
from dataclasses import dataclass
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .config import CSV_HEADER, LOG_DIR, SOUND_DIR

logger = logging.getLogger(__name__)

SAMPLE_RATE = 44100
CHANNELS = 2
SAMPLE_WIDTH_BYTES = 2

_TYPE_PALETTES = {
    "BOUNCE": {
        "notes": (523.25, 659.25, 783.99, 880.00, 1046.50),
        "amp": 0.30,
        "decay": 0.34,
        "pan": 0.62,
    },
    "SHY": {
        "notes": (261.63, 329.63, 392.00, 493.88, 587.33),
        "amp": 0.22,
        "decay": 0.82,
        "pan": 0.38,
    },
    "MESSY": {
        "notes": (196.00, 233.08, 293.66, 369.99, 466.16, 554.37),
        "amp": 0.26,
        "decay": 0.46,
        "pan": 0.50,
    },
}


@dataclass
class EncounterRow:
    source: str
    target: str
    other_type: str
    event: str
    rssi: float
    smooth_rssi: float
    closeness: float
    source_file: Path


@dataclass
class SoundEncounter:
    """Renderer input shared with the web DB Encounter shape."""

    id: str
    other_echo_type: str
    duration_sec: float
    closeness_avg: float
    rssi_avg: float
    order_key: str


@dataclass
class DailySoundResult:
    dated_path: Path
    today_path: Path
    rows_used: int
    duration_sec: float
    type_counts: Dict[str, int]
    avg_closeness: float


def _safe_float(raw: str, default: float = 0.0) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _is_today_encounter_file(path: Path, today: date) -> bool:
    stamp = today.strftime("%Y-%m-%d")
    if stamp in path.name:
        return True
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).date() == today
    except OSError:
        return False


def _read_encounter_rows(path: Path) -> List[EncounterRow]:
    rows: List[EncounterRow] = []
    try:
        with open(path, newline="", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            for i, parts in enumerate(reader):
                if not parts:
                    continue
                joined = ",".join(parts).strip()
                if i == 0 and joined == CSV_HEADER:
                    continue
                if len(parts) != 8:
                    continue
                rows.append(
                    EncounterRow(
                        source=parts[1].strip(),
                        target=parts[2].strip(),
                        other_type=parts[3].strip().upper(),
                        event=parts[4].strip().lower(),
                        rssi=_safe_float(parts[5]),
                        smooth_rssi=_safe_float(parts[6]),
                        closeness=max(0.0, min(1.0, _safe_float(parts[7]))),
                        source_file=path,
                    )
                )
    except OSError as e:
        logger.warning("Daily sound: failed to read %s: %s", path, e)
    return rows


def load_today_encounters(log_dir: Path = LOG_DIR, today: Optional[date] = None) -> List[EncounterRow]:
    """Load all encounter rows from today's station CSV files."""
    day = today or datetime.now().date()
    files = sorted(
        p
        for p in Path(log_dir).glob("encounter_*.csv")
        if p.is_file() and _is_today_encounter_file(p, day)
    )
    out: List[EncounterRow] = []
    for path in files:
        out.extend(_read_encounter_rows(path))
    return out


def _sound_encounter_id(rows: List[EncounterRow], fallback_index: int) -> str:
    if not rows:
        return f"local:{fallback_index}"
    first = rows[0]
    last = rows[-1]
    h = hashlib.sha256(
        f"{first.source}|{first.target}|{first.source_file.name}|{fallback_index}|"
        f"{first.closeness:.3f}|{last.closeness:.3f}".encode("utf-8")
    ).hexdigest()[:16]
    return f"local:{h}"


def encounter_rows_to_sound_encounters(rows: List[EncounterRow]) -> List[SoundEncounter]:
    """
    Convert station CSV rows to the same coarse Encounter summary used by the DB.

    The web app should call the equivalent renderer with DB Encounter rows
    directly, not with raw BLE CSV rows.
    """
    open_rows: Dict[Tuple[str, str], List[EncounterRow]] = {}
    completed: List[List[EncounterRow]] = []

    for row in rows:
        key = (row.source, row.target)
        if row.event == "seen":
            open_rows[key] = [row]
        elif row.event == "lost":
            if key in open_rows:
                open_rows[key].append(row)
                completed.append(open_rows.pop(key))
        elif key in open_rows:
            open_rows[key].append(row)

    completed.extend(group for group in open_rows.values() if group)

    out: List[SoundEncounter] = []
    for i, group in enumerate(completed):
        seen = [r for r in group if r.event == "seen"] or group
        other_type = (group[0].other_type or "SHY").upper()
        closeness_avg = sum(r.closeness for r in seen) / max(1, len(seen))
        rssi_avg = sum(r.rssi for r in seen) / max(1, len(seen))
        # Local CSV rows have ESP millis, but not always enough persisted wall-clock
        # context after restart. Duration affects timbre only; DB playback should use
        # the persisted Encounter.durationSec value for exact website parity.
        duration_sec = max(1.0, float(len(seen)) * 0.8)
        out.append(
            SoundEncounter(
                id=_sound_encounter_id(group, i),
                other_echo_type=other_type,
                duration_sec=duration_sec,
                closeness_avg=max(0.0, min(1.0, closeness_avg)),
                rssi_avg=rssi_avg,
                order_key=f"{group[0].source_file.name}:{i:04d}",
            )
        )
    return out


def sound_encounters_from_db_payload(items: Iterable[Dict[str, Any]]) -> List[SoundEncounter]:
    """Normalize web DB/API Encounter rows into renderer input."""
    out: List[SoundEncounter] = []
    for i, item in enumerate(items):
        other_type = str(item.get("otherEchoType") or item.get("other_echo_type") or "shy")
        order_key = str(
            item.get("startedAt")
            or item.get("started_at")
            or item.get("endedAt")
            or item.get("ended_at")
            or f"{i:04d}"
        )
        out.append(
            SoundEncounter(
                id=str(item.get("id") or f"db:{i}"),
                other_echo_type=other_type.upper(),
                duration_sec=max(0.0, _safe_float(str(item.get("durationSec", item.get("duration_sec", 0))))),
                closeness_avg=max(
                    0.0,
                    min(
                        1.0,
                        _safe_float(str(item.get("closenessAvg", item.get("closeness_avg", 0)))),
                    ),
                ),
                rssi_avg=_safe_float(str(item.get("rssiAvg", item.get("rssi_avg", 0)))),
                order_key=order_key,
            )
        )
    return sorted(out, key=lambda enc: (enc.order_key, enc.id))


def _summarize(encounters: Iterable[SoundEncounter]) -> Tuple[Dict[str, int], float]:
    counts: Dict[str, int] = {"BOUNCE": 0, "SHY": 0, "MESSY": 0}
    closeness_values: List[float] = []
    for enc in encounters:
        if enc.other_echo_type in counts:
            counts[enc.other_echo_type] += 1
        else:
            counts.setdefault(enc.other_echo_type or "UNKNOWN", 0)
            counts[enc.other_echo_type or "UNKNOWN"] += 1
        closeness_values.append(enc.closeness_avg)
    avg = sum(closeness_values) / len(closeness_values) if closeness_values else 0.0
    return counts, avg


def _seed_for_day(encounters: List[SoundEncounter], today: date) -> int:
    h = hashlib.sha256(today.isoformat().encode("utf-8"))
    for enc in encounters[:500]:
        h.update(enc.id.encode("utf-8", errors="ignore"))
        h.update(enc.other_echo_type.encode("utf-8", errors="ignore"))
        h.update(f"{enc.duration_sec:.1f}:{enc.closeness_avg:.3f}".encode("ascii"))
    return int(h.hexdigest()[:12], 16)


def _note_plan(
    encounters: List[SoundEncounter],
    duration_sec: float,
    type_counts: Dict[str, int],
    avg_closeness: float,
) -> List[Tuple[float, float, float, float, float]]:
    """Return notes as (start_sec, freq_hz, note_sec, amp, pan)."""
    if not encounters:
        return []

    total_encounters = max(1, len(encounters))
    density = min(1.0, total_encounters / 32.0)
    max_notes = min(220, max(24, int(38 + density * 150)))
    notes: List[Tuple[float, float, float, float, float]] = []

    if total_encounters <= max_notes:
        selected = encounters
    else:
        step = total_encounters / float(max_notes)
        selected = [encounters[int(i * step)] for i in range(max_notes)]

    for idx, enc in enumerate(selected):
        palette = _TYPE_PALETTES.get(enc.other_echo_type, _TYPE_PALETTES["SHY"])
        notes_for_type = palette["notes"]
        frac = idx / max(1, len(selected) - 1)
        start = frac * max(0.1, duration_sec - 1.2)
        close = max(0.0, min(1.0, enc.closeness_avg))
        dur_weight = min(1.0, enc.duration_sec / 120.0)
        note_i = (idx + int(close * 10)) % len(notes_for_type)
        freq = notes_for_type[note_i] * (1.0 + (close - 0.5) * 0.018)
        note_len = float(palette["decay"]) * (0.75 + close * 0.65 + dur_weight * 0.25)
        amp = float(palette["amp"]) * (0.45 + close * 0.65 + dur_weight * 0.18)
        pan = float(palette["pan"])
        notes.append((start, freq, note_len, amp, pan))

    # Add a few slow anchor tones based on the day's dominant encounter types.
    dominant = sorted(type_counts.items(), key=lambda kv: kv[1], reverse=True)
    for i, (type_name, count) in enumerate(dominant):
        if count <= 0 or type_name not in _TYPE_PALETTES:
            continue
        palette = _TYPE_PALETTES[type_name]
        base = palette["notes"][0] / 2.0
        amp = 0.07 + min(0.09, count / max(1.0, total_encounters) * 0.20)
        start = i * 0.9
        notes.append((start, base, min(duration_sec - start, 8.0), amp, palette["pan"]))

    # A close day gets a small closing shimmer.
    if avg_closeness > 0.45:
        for i, freq in enumerate((659.25, 783.99, 987.77, 1174.66)):
            notes.append((duration_sec - 2.8 + i * 0.28, freq, 1.4, 0.10, 0.45 + i * 0.04))

    return notes


def _add_note(
    left: List[float],
    right: List[float],
    start_sec: float,
    freq_hz: float,
    note_sec: float,
    amp: float,
    pan: float,
) -> None:
    start = max(0, int(start_sec * SAMPLE_RATE))
    length = max(1, int(note_sec * SAMPLE_RATE))
    end = min(len(left), start + length)
    if end <= start:
        return

    pan = max(0.0, min(1.0, pan))
    gain_l = math.cos(pan * math.pi * 0.5)
    gain_r = math.sin(pan * math.pi * 0.5)
    phase = 0.0
    phase_inc = 2.0 * math.pi * freq_hz / SAMPLE_RATE
    attack = max(1, int(0.025 * SAMPLE_RATE))

    for i in range(start, end):
        rel = i - start
        if rel < attack:
            env = rel / float(attack)
        else:
            tail = (rel - attack) / max(1.0, length - attack)
            env = (1.0 - tail) ** 2
        value = math.sin(phase) + 0.28 * math.sin(phase * 2.0)
        value *= amp * env
        left[i] += value * gain_l
        right[i] += value * gain_r
        phase += phase_inc


def _write_wav(path: Path, left: List[float], right: List[float]) -> None:
    peak = max(0.01, max(max(abs(v) for v in left), max(abs(v) for v in right)))
    scale = min(0.92 / peak, 1.0) * 32767.0
    frames = bytearray()
    for l_val, r_val in zip(left, right):
        l_i = int(max(-32768, min(32767, l_val * scale)))
        r_i = int(max(-32768, min(32767, r_val * scale)))
        frames.extend(struct.pack("<hh", l_i, r_i))

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.wav")
    with wave.open(str(tmp), "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH_BYTES)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(frames)
    tmp.replace(path)


def render_sound_from_encounters(
    encounters: List[SoundEncounter],
    out_path: Path,
    *,
    duration_sec: float = 45.0,
    today: Optional[date] = None,
) -> Optional[DailySoundResult]:
    """Render canonical Encounter-shaped inputs to WAV."""
    day = today or datetime.now().date()
    encounters = sorted(encounters, key=lambda enc: (enc.order_key, enc.id))
    if not encounters:
        return None
    duration = max(8.0, min(float(duration_sec), 180.0))
    type_counts, avg_closeness = _summarize(encounters)
    notes = _note_plan(encounters, duration, type_counts, avg_closeness)
    if not notes:
        return None

    total_frames = int(duration * SAMPLE_RATE)
    left = [0.0] * total_frames
    right = [0.0] * total_frames

    seed = _seed_for_day(encounters, day)
    drift = ((seed % 2001) - 1000) / 1000.0
    drone_freq = 110.0 + avg_closeness * 55.0 + drift * 1.5
    for i in range(total_frames):
        t = i / float(SAMPLE_RATE)
        env = min(1.0, t / 2.5, (duration - t) / 3.0)
        env = max(0.0, env)
        drone = math.sin(2.0 * math.pi * drone_freq * t) * 0.035 * env
        left[i] += drone * 0.85
        right[i] += drone

    for note in notes:
        _add_note(left, right, *note)

    _write_wav(out_path, left, right)

    return DailySoundResult(
        dated_path=out_path,
        today_path=out_path,
        rows_used=len(encounters),
        duration_sec=duration,
        type_counts=type_counts,
        avg_closeness=avg_closeness,
    )


def render_daily_sound(
    log_dir: Path = LOG_DIR,
    sound_dir: Path = SOUND_DIR,
    duration_sec: float = 45.0,
    today: Optional[date] = None,
) -> Optional[DailySoundResult]:
    """
    Render today's local station data to a deterministic WAV.

    This function adapts local CSV rows into the same Encounter-shaped renderer
    input that the website should build from the database.
    """
    day = today or datetime.now().date()
    rows = load_today_encounters(log_dir, day)
    encounters = encounter_rows_to_sound_encounters(rows)
    if not encounters:
        return None

    dated_path = Path(sound_dir) / f"daily_echo_{day.isoformat()}.wav"
    result = render_sound_from_encounters(
        encounters,
        dated_path,
        duration_sec=duration_sec,
        today=day,
    )
    if not result:
        return None

    today_path = Path(sound_dir) / "today.wav"
    shutil.copyfile(dated_path, today_path)
    result.today_path = today_path
    return result


def _default_player_command(path: Path) -> Optional[List[str]]:
    for candidate in ("paplay", "pw-play", "aplay"):
        exe = shutil.which(candidate)
        if exe:
            if candidate == "aplay":
                return [exe, "-q", str(path)]
            return [exe, str(path)]
    return None


def play_sound_file(
    path: Path,
    command_template: str = "",
    log: Optional[logging.Logger] = None,
) -> bool:
    """Play a WAV in the background using the configured/default Pi audio command."""
    log = log or logger
    if not path.is_file():
        log.warning("Daily sound playback skipped: missing %s", path)
        return False

    if command_template.strip():
        command = shlex.split(command_template.format(path=str(path)))
    else:
        command = _default_player_command(path)

    if not command:
        log.warning(
            "Daily sound playback skipped: install/configure paplay, pw-play, or aplay"
        )
        return False

    try:
        subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        log.info("Daily sound playback started: %s", " ".join(command))
        return True
    except OSError as e:
        log.warning("Daily sound playback failed: %s", e)
        return False


def _sound_status(sound_dir: Path) -> Dict[str, object]:
    path = Path(sound_dir) / "today.wav"
    if not path.is_file():
        return {"available": False, "url": None}
    stat = path.stat()
    return {
        "available": True,
        "url": "/today.wav",
        "filename": path.name,
        "bytes": stat.st_size,
        "updatedAt": datetime.fromtimestamp(stat.st_mtime).isoformat(),
    }


def _html_page(status: Dict[str, object]) -> bytes:
    updated = html.escape(str(status.get("updatedAt") or "not generated yet"))
    src = "/today.wav"
    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ECHO Today's Sound</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 720px; margin: 48px auto; padding: 0 20px; background: #101312; color: #f4f1e8; }}
    audio {{ width: 100%; margin: 24px 0; }}
    .card {{ border: 1px solid #37423f; border-radius: 18px; padding: 24px; background: #171d1b; }}
    a {{ color: #9ed9bc; }}
  </style>
</head>
<body>
  <main class="card">
    <h1>ECHO Today's Sound</h1>
    <p>Updated: <span id="updated">{updated}</span></p>
    <audio id="player" controls autoplay src="{src}"></audio>
    <p><a href="{src}">Open WAV directly</a></p>
  </main>
  <script>
    async function refresh() {{
      const res = await fetch('/api/today-sound', {{ cache: 'no-store' }});
      const data = await res.json();
      if (data.available) {{
        document.getElementById('updated').textContent = data.updatedAt;
        const player = document.getElementById('player');
        const next = data.url + '?t=' + encodeURIComponent(data.updatedAt);
        if (!player.src.endsWith(next)) player.src = next;
      }}
    }}
    setInterval(refresh, 30000);
  </script>
</body>
</html>
"""
    return body.encode("utf-8")


def start_sound_web_server(
    host: str,
    port: int,
    sound_dir: Path = SOUND_DIR,
    log: Optional[logging.Logger] = None,
) -> ThreadingHTTPServer:
    """Start a tiny HTTP server for browser playback."""
    log = log or logger
    sound_root = Path(sound_dir)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            log.debug("Sound web: " + fmt, *args)

        def _send_bytes(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send_bytes(200, "text/html; charset=utf-8", _html_page(_sound_status(sound_root)))
                return
            if path == "/api/today-sound":
                body = json.dumps(_sound_status(sound_root), separators=(",", ":")).encode("utf-8")
                self._send_bytes(200, "application/json", body)
                return
            if path == "/today.wav":
                wav_path = sound_root / "today.wav"
                if not wav_path.is_file():
                    self._send_bytes(404, "text/plain; charset=utf-8", b"today.wav not generated yet\n")
                    return
                data = wav_path.read_bytes()
                self._send_bytes(200, "audio/wav", data)
                return
            self._send_bytes(404, "text/plain; charset=utf-8", b"not found\n")

    httpd = ThreadingHTTPServer((host, int(port)), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    log.info("Daily sound web server listening on http://%s:%s", host, port)
    return httpd
