"""Operator uploads: screenshots, CSVs and text/log files.

UPLOADS = files submitted through the Input screen, under ./uploads:

    uploads/screenshots/   PNG, JPEG, GIF, WebP
    uploads/csv/           .csv
    uploads/logs/          .txt, .log, .out

Predefined datasets live in ./data and are never written here (see datasets.py).

Files are validated by what they contain, not by what they are called: an image
must start with the right magic bytes, a CSV must parse, a log must be text.
SVG is refused on purpose (it can carry script). The stored name is generated
(`UPL-0007_<sanitised name>`); the client-supplied name is only ever shown, never
used as a path. No FastAPI imports here.
"""

from __future__ import annotations

import csv
import io
import os
import re
import struct
from pathlib import Path
from typing import Any, Dict, List, Optional

from esim_selfhealing.schemas import FEATURE_NAMES

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_UPLOAD_DIR = PROJECT_ROOT / "uploads"

MAX_BYTES = 10 * 1024 * 1024
KINDS = ("screenshot", "csv", "log")
FOLDER = {"screenshot": "screenshots", "csv": "csv", "log": "logs"}
EXTENSIONS = {
    "screenshot": {".png", ".jpg", ".jpeg", ".gif", ".webp"},
    "csv": {".csv"},
    "log": {".txt", ".log", ".out"},
}
PREVIEW_ROWS = 5
PREVIEW_LINES = 20


class UploadError(ValueError):
    """The file is not acceptable. The message says why and what to do."""


def upload_dir() -> Path:
    return Path(os.environ.get("UPLOADS_DIR") or DEFAULT_UPLOAD_DIR)


def ensure_dirs() -> None:
    for folder in FOLDER.values():
        (upload_dir() / folder).mkdir(parents=True, exist_ok=True)


def safe_name(name: str) -> str:
    """Reduce a client-supplied file name to a harmless one."""
    base = (name or "").replace("\\", "/").split("/")[-1]
    base = re.sub(r"[\x00-\x1f]", "", base)
    stem, dot, ext = base.rpartition(".")
    if not dot:
        stem, ext = base, ""
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_")[:60] or "file"
    ext = re.sub(r"[^A-Za-z0-9]", "", ext)[:8].lower()
    return f"{stem}.{ext}" if ext else stem


def _ext(name: str) -> str:
    return os.path.splitext((name or "").lower())[1]


# -- images -----------------------------------------------------------------
def _image_type(data: bytes) -> Optional[str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _image_size(mime: str, data: bytes) -> Optional[Dict[str, int]]:
    try:
        if mime == "image/png" and len(data) >= 24:
            w, h = struct.unpack(">II", data[16:24])
            return {"width": w, "height": h}
        if mime == "image/gif" and len(data) >= 10:
            w, h = struct.unpack("<HH", data[6:10])
            return {"width": w, "height": h}
        if mime == "image/jpeg":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return {"width": w, "height": h}
                i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    except (struct.error, IndexError):
        return None
    return None


def _inspect_image(name: str, data: bytes) -> Dict[str, Any]:
    if _ext(name) not in EXTENSIONS["screenshot"]:
        raise UploadError("Screenshots must be PNG, JPEG, GIF or WebP images.")
    mime = _image_type(data)
    if mime is None:
        raise UploadError("This file is not a PNG, JPEG, GIF or WebP image, whatever its name says.")
    return {"mime": mime, "meta": {"size_px": _image_size(mime, data)}}


# -- text -------------------------------------------------------------------
def _decode(data: bytes) -> tuple[str, str]:
    if b"\x00" in data[:8192]:
        raise UploadError("This looks like a binary file, not text.")
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise UploadError("The file is not readable text (tried UTF-8 and Windows-1252).")


def _inspect_csv(name: str, data: bytes) -> Dict[str, Any]:
    if _ext(name) != ".csv":
        raise UploadError("CSV uploads must have a .csv extension.")
    text, enc = _decode(data)
    if not text.strip():
        raise UploadError("The CSV is empty.")
    sample = text[:4096]
    try:
        delim = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        delim = ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if len(rows) < 1:
        raise UploadError("The CSV has no rows.")
    header, body = [c.strip() for c in rows[0]], rows[1:]
    if len(header) > 200 or len(set(header)) != len(header) or not all(header):
        raise UploadError("The first row must be a header of unique, non-empty column names.")
    present = [f for f in FEATURE_NAMES if f in header]
    return {"mime": "text/csv", "meta": {
        "encoding": enc, "delimiter": delim, "columns": header, "rows": len(body),
        "preview": [dict(zip(header, r)) for r in body[:PREVIEW_ROWS]],
        "telemetry_columns": present,
        "telemetry_compatible": len(present) == len(FEATURE_NAMES),
    }}


def _inspect_log(name: str, data: bytes) -> Dict[str, Any]:
    if _ext(name) not in EXTENSIONS["log"]:
        raise UploadError("Text and log uploads must be .txt, .log or .out files.")
    text, enc = _decode(data)
    if not text.strip():
        raise UploadError("The file is empty.")
    lines: List[str] = text.splitlines()
    return {"mime": "text/plain", "meta": {
        "encoding": enc, "lines": len(lines),
        "preview": [ln[:200] for ln in lines[:PREVIEW_LINES]],
    }}


def inspect(kind: str, filename: str, data: bytes) -> Dict[str, Any]:
    """Validate and describe an upload. Returns {mime, meta}; raises UploadError."""
    if kind not in KINDS:
        raise UploadError(f"kind must be one of: {', '.join(KINDS)}")
    if not data:
        raise UploadError("The file is empty.")
    if len(data) > MAX_BYTES:
        raise UploadError(f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.")
    return {"screenshot": _inspect_image, "csv": _inspect_csv, "log": _inspect_log}[kind](filename, data)


# -- disk -------------------------------------------------------------------
def stored_name(upload_id: str, filename: str) -> str:
    return f"{upload_id}_{safe_name(filename)}"


def path_for(kind: str, stored: str) -> Path:
    """Where a stored file lives. Refuses anything that would leave the folder."""
    base = (upload_dir() / FOLDER[kind]).resolve()
    p = (base / stored).resolve()
    if base not in p.parents:
        raise UploadError("invalid stored path")
    return p


def write(kind: str, stored: str, data: bytes) -> Path:
    ensure_dirs()
    p = path_for(kind, stored)
    p.write_bytes(data)
    return p


def remove(kind: str, stored: str) -> None:
    try:
        path_for(kind, stored).unlink()
    except (FileNotFoundError, UploadError):
        pass
