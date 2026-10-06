"""Run the agent on the operator's OWN telemetry CSV.

An uploaded CSV that carries the five telemetry channels can be chosen as the
data source on the Input screen (mode "upload"). This module turns the file
into the same `Observation` stream a stored dataset produces, so the agent,
safety gate, remediation and recovery run exactly as they do for a stored one.

It is deliberately strict, because the file is operator-supplied and untrusted:

* the five channels must be present, and every value a finite number;
* the file is for ONE device - the one selected on the Input screen. If it has
  an `euicc_id` column, every row must name that device. That is also what stops
  a client feeding readings into another company's device;
* it needs at least MIN_ROWS rows (the detector spends its first 60 readings
  learning what "normal" looks like) and at most MAX_ROWS;
* an optional `sim_fault_label` column is accepted only with fault names the
  simulated RSP server knows.

Time may come from `ts_epoch` (seconds or milliseconds), `ts_iso`, `timestamp`
or `ts`. Without any, readings are assumed to be one second apart, and the
summary says so.

No FastAPI imports: importable and testable on its own.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from esim_selfhealing import fields
from esim_selfhealing.rsp_api import FAULT_EFFECTS
from esim_selfhealing.schemas import FEATURE_NAMES, Observation

from . import uploads

MIN_ROWS = 100
MAX_ROWS = 5000
TIME_COLUMNS = ("ts_epoch", "ts_iso", "timestamp", "ts")
#: when a file has no time column: readings are assumed one second apart from here
ASSUMED_START = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
MAX_MAGNITUDE = 1e9


class CustomInputError(ValueError):
    """The file cannot be used as agent input. The message is shown to the operator."""


def _delimiter(text: str) -> str:
    first = text.splitlines()[0] if text.splitlines() else ""
    return max((",", ";", "\t"), key=first.count)


def _parse_time(raw: str, column: str, line: int) -> float:
    raw = (raw or "").strip()
    try:
        if column in ("ts_epoch", "timestamp", "ts"):
            try:
                value = float(raw)
            except ValueError:
                value = datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
            return value / 1000.0 if value > 1e11 else value           # milliseconds
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except (ValueError, OverflowError):
        raise CustomInputError(f"Row {line}: '{raw}' in {column} is not a time I can read "
                               "(use epoch seconds or ISO 8601, e.g. 2026-03-02T09:30:00Z).")


def parse(data: bytes, *, device: Dict[str, Any]) -> Dict[str, Any]:
    """Validate `data` for `device` and return {"observations": [...], "summary": {...}}."""
    if not device:
        raise CustomInputError("Choose a device first, so the file can be checked against it.")
    text, _enc = uploads._decode(data)
    delim = _delimiter(text)
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    header = [(h or "").strip() for h in (reader.fieldnames or [])]
    if not header:
        raise CustomInputError("The CSV has no header row.")
    reader.fieldnames = header

    missing = [f for f in FEATURE_NAMES if f not in header]
    if missing:
        raise CustomInputError("The CSV is missing telemetry column(s): " + ", ".join(missing)
                               + ". It needs all of: " + ", ".join(FEATURE_NAMES) + ".")
    time_col = next((c for c in TIME_COLUMNS if c in header), None)
    has_device_col = "euicc_id" in header
    has_label_col = "sim_fault_label" in header
    device_id = device["euicc_id"]

    rows: List[Dict[str, Any]] = []
    for line, r in enumerate(reader, start=2):                         # line 1 is the header
        if not any((v or "").strip() for v in r.values() if v is not None):
            continue
        if len(rows) >= MAX_ROWS:
            raise CustomInputError(f"The CSV has more than {MAX_ROWS} rows. Trim it to the window of interest.")
        if has_device_col:
            named = (r.get("euicc_id") or "").strip()
            if named and named != device_id:
                raise CustomInputError(
                    f"Row {line} is for device {named}, but the selected device is {device_id}. "
                    "A file can carry one device's readings; choose that device or fix the file.")
        feats = []
        for f in FEATURE_NAMES:
            raw = (r.get(f) or "").strip()
            try:
                v = float(raw)
            except ValueError:
                raise CustomInputError(f"Row {line}: '{raw}' in {f} is not a number.")
            if not math.isfinite(v) or abs(v) > MAX_MAGNITUDE:
                raise CustomInputError(f"Row {line}: {f} is not a usable number ({raw}).")
            # Physically impossible values are refused rather than clipped: a
            # positive RSRP or a 400% failure rate means the column is wrong
            # (wrong units, wrong order), and silently clipping would hide that
            # while corrupting the covariance MONITOR scores against.
            impossible = fields.TELEMETRY[f].violates(v)
            if impossible:
                raise CustomInputError(
                    f"Row {line}: {impossible}. Check the column's units: "
                    f"{f} is {fields.TELEMETRY[f].unit}.")
            feats.append(v)
        label = None
        if has_label_col:
            label = (r.get("sim_fault_label") or "").strip() or None
            if label is not None and label not in FAULT_EFFECTS:
                raise CustomInputError(f"Row {line}: sim_fault_label '{label}' is not one the simulator knows. "
                                       "Use one of: " + ", ".join(sorted(FAULT_EFFECTS)) + ", or leave it blank.")
        ts = _parse_time(r.get(time_col), time_col, line) if time_col else None
        cell = (r.get("cell_id") or "").strip() or device.get("cell_id") or "CELL-UNKNOWN"
        rows.append({"ts": ts, "features": feats, "label": label, "cell": cell})

    if len(rows) < MIN_ROWS:
        raise CustomInputError(
            f"The CSV has {len(rows)} data rows; it needs at least {MIN_ROWS}. The detector spends its "
            "first 60 readings learning what normal looks like, so it needs a baseline plus some room.")

    assumed = time_col is None
    if assumed:
        for i, r in enumerate(rows):
            r["ts"] = ASSUMED_START + i
    reordered = any(rows[i]["ts"] > rows[i + 1]["ts"] for i in range(len(rows) - 1))
    if reordered:
        rows.sort(key=lambda r: r["ts"])

    observations = [Observation(ts=r["ts"], euicc_id=device_id, cell_id=r["cell"],
                                features=r["features"], ground_truth_fault=r["label"]) for r in rows]
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    # Possible but unusual values are reported, not refused: a file may be
    # entirely fault data. The operator sees the note and decides.
    notes: List[str] = []
    for name in FEATURE_NAMES:
        col = [r["features"][FEATURE_NAMES.index(name)] for r in rows]
        share = sum(1 for v in col if fields.TELEMETRY[name].implausible(v)) / len(col)
        if share > 0.5:
            lo, hi = fields.TELEMETRY[name].typical
            notes.append(f"{name}: {share:.0%} of rows sit outside the typical healthy range "
                         f"{lo:g}..{hi:g} {fields.TELEMETRY[name].unit}")

    summary = {
        "rows": len(rows),
        "notes": notes,
        "columns": header,
        "time_from": iso(rows[0]["ts"]), "time_to": iso(rows[-1]["ts"]),
        "assumed_time": assumed,
        "reordered": reordered,
        "has_fault_labels": any(r["label"] for r in rows),
        "device_column": has_device_col,
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    return {"observations": observations, "summary": summary}
