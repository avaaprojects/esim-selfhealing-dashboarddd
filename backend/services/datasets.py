"""Predefined datasets: what lives in ./data and how the dashboard reads it.

DATA = predefined, read-only, SYNTHETIC / SIMULATED (see data/README.md).
UPLOADS = files an operator submitted (see uploads.py). The two never mix.

Nothing here is embedded in frontend code: the Input screen asks the API, the
API reads these CSVs. `data/manifest.json` carries provenance (what each file
is, how it was generated, its owner client, a SHA-256); the file itself is the
source of truth for rows, columns and time range, computed on read.

No FastAPI imports: importable and testable on its own.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from esim_selfhealing.schemas import FEATURE_NAMES, Observation

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"

SYNTHETIC_LABEL = "SYNTHETIC / SIMULATED"


class DatasetError(Exception):
    pass


class UnknownDataset(DatasetError):
    pass


def data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR") or DEFAULT_DATA_DIR)


_lock = threading.Lock()
_cache: Dict[Any, Any] = {}


def _cached(path: Path, loader):
    """Memoise a parse by (path, mtime, size): edits on disk are picked up."""
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    with _lock:
        if key not in _cache:
            _cache[key] = loader(path)
        return _cache[key]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest() -> Dict[str, Any]:
    path = data_dir() / "manifest.json"
    m = _cached(path, lambda p: json.loads(p.read_text(encoding="utf-8")))
    if m is None:
        raise DatasetError(f"{path} is missing. Run: python tools/generate_datasets.py")
    return m


def _read_csv(path: Path) -> List[Dict[str, str]]:
    def load(p: Path):
        with p.open(newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    rows = _cached(path, load)
    if rows is None:
        raise DatasetError(f"{path.name} is missing")
    return rows


def _integrity(entry_file: str, expected: Optional[str]) -> str:
    path = data_dir() / entry_file
    if not path.is_file():
        return "missing"
    if not expected:
        return "unchecked"
    return "verified" if _sha256(path) == expected else "modified"


# ---------------------------------------------------------------------------
# registry: clients, groups, devices, network / auth / RSP contexts
# ---------------------------------------------------------------------------
def registry() -> Dict[str, Any]:
    """All registry tables, each row tagged with the file it came from."""
    m = manifest()
    files = m["registry"]

    def table(key: str) -> List[Dict[str, Any]]:
        info = files[key]
        rows = _read_csv(data_dir() / info["file"])
        return [{**r, "source_file": f"data/{info['file']}"} for r in rows]

    auth = {a["auth_id"]: a for a in table("authentication")}
    networks = []
    for n in table("network"):
        n["cells"] = [c for c in n["cells"].split(";") if c]
        n["auth"] = auth.get(n["auth_id"])
        networks.append(n)
    rsp = []
    for r in table("rsp"):
        r["interfaces"] = [i for i in r["interfaces"].split(";") if i]
        rsp.append(r)
    devices = table("devices")
    for d in devices:
        d["fleet_size"] = int(d["fleet_size"])
    return {
        "provenance": m.get("provenance", SYNTHETIC_LABEL),
        "clients": table("clients"),
        "groups": table("groups"),
        "devices": devices,
        "networks": networks,
        "authentication": list(auth.values()),
        "rsp_environments": rsp,
        "files": {k: {"file": f"data/{v['file']}", "description": v.get("description"),
                      "integrity": _integrity(v["file"], v.get("sha256"))}
                  for k, v in files.items()},
    }


# ---------------------------------------------------------------------------
# telemetry datasets
# ---------------------------------------------------------------------------
def _entry(dataset_id: str) -> Dict[str, Any]:
    for e in manifest()["datasets"]:
        if e["id"] == dataset_id:
            return e
    raise UnknownDataset(dataset_id)


def _num(x: str) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _summarise(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Provenance from the manifest plus facts read from the file itself.

    A missing or hand-edited file is reported (`integrity`), never allowed to
    break the listing: malformed rows are skipped when computing the summary.
    """
    try:
        rows = _read_csv(data_dir() / entry["file"])
    except DatasetError:
        rows = []
    ts = [v for v in (_num(r.get("ts_epoch")) for r in rows) if v is not None]
    devices = sorted({r["euicc_id"] for r in rows if r.get("euicc_id")})
    faults = sorted({r["sim_fault_label"] for r in rows if r.get("sim_fault_label")})
    return {
        **entry,
        "provenance": entry.get("provenance", SYNTHETIC_LABEL),
        "path": f"data/{entry['file']}",
        "rows": len(rows),
        "time_range": {"from": min(ts), "to": max(ts)} if ts else None,
        "devices": devices,
        "columns": list(rows[0].keys()) if rows else [],
        "fields": list(FEATURE_NAMES),
        "fault_labels_present": faults,
        "integrity": _integrity(entry["file"], entry.get("sha256")),
    }


def list_datasets(client_id: Optional[str] = None) -> List[Dict[str, Any]]:
    out = [_summarise(e) for e in manifest()["datasets"]]
    if client_id:
        out = [d for d in out if d["client_id"] == client_id]
    return out


def get_dataset(dataset_id: str, preview_rows: int = 12) -> Dict[str, Any]:
    entry = _entry(dataset_id)
    summary = _summarise(entry)
    if summary["integrity"] == "missing":
        raise DatasetError(f"{summary['path']} is missing. Run: python tools/generate_datasets.py")
    rows = _read_csv(data_dir() / entry["file"])
    stats = {}
    for f in FEATURE_NAMES:
        vals = [v for v in (_num(r.get(f)) for r in rows) if v is not None]
        if vals:
            stats[f] = {"min": min(vals), "mean": sum(vals) / len(vals), "max": max(vals)}
    n = max(0, min(int(preview_rows), 50))
    # First rows, plus the rows around the first labelled fault if there is one.
    first_fault = next((i for i, r in enumerate(rows) if r.get("sim_fault_label")), None)
    preview = rows[:n]
    around = rows[first_fault:first_fault + 3] if first_fault is not None and first_fault >= n else []
    return {**summary, "stats": stats, "preview": preview, "preview_fault_start": around,
            "first_fault_row": first_fault}


def dataset_observations(dataset_id: str) -> List[Observation]:
    """The dataset as agent input. `sim_fault_label` becomes `ground_truth_fault`,
    the simulator-only field the orchestrator uses to keep the bundled RSP model
    consistent; the agent itself never reads it."""
    entry = _entry(dataset_id)
    if _integrity(entry["file"], entry.get("sha256")) != "verified":
        raise DatasetError(f"{entry['file']} does not match its recorded checksum; "
                           "regenerate it with tools/generate_datasets.py before processing")
    out = []
    for r in _read_csv(data_dir() / entry["file"]):
        out.append(Observation(
            ts=float(r["ts_epoch"]), euicc_id=r["euicc_id"], cell_id=r["cell_id"],
            features=[float(r[f]) for f in FEATURE_NAMES],
            ground_truth_fault=r.get("sim_fault_label") or None))
    return out
