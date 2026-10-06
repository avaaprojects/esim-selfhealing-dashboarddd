"""The INPUT side of the system: what data is entering the self-healing loop.

Three things live here.

1. `InputStore` - SQLite (backend/data/inputs.db) for
     * uploads         metadata for operator files (the bytes are in ./uploads)
     * input_state     each operator's working selection (survives refresh/restart)
     * input_commits   immutable snapshots taken by PROCESS DATA
2. `apply_patch` - turns a partial selection into a consistent one
   (client -> group -> device -> eSIM / RSP / network; dataset and incident
   must belong to the device).
3. `readiness`, `provenance`, `build_commit` - pure functions that describe the
   selection: is it complete, where each piece of input came from, and the
   frozen record the next screen will read.

The server computes all of this so the UI only renders it. No FastAPI here.
"""

from __future__ import annotations

import copy
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from esim_selfhealing.schemas import FEATURE_NAMES

from . import uploads as up

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "inputs.db"

MODES = ("realtime", "synthetic", "upload")
REALTIME_LABEL = "REAL-TIME"
IMPORT_LABEL = "OPERATOR IMPORT"
SYNTHETIC_LABEL = "SYNTHETIC / SIMULATED"
UPLOAD_LABEL = "OPERATOR UPLOAD"


class InputError(ValueError):
    """A selection that cannot be made. The message says what to change."""


class NotFound(KeyError):
    pass


def empty_state() -> Dict[str, Any]:
    return {"client_id": None, "group_id": None, "device_id": None, "incident_id": None,
            "rsp_env_id": None, "network_id": None,
            "data_source": {"mode": None, "dataset_id": None, "upload_id": None, "upload": None}}


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------
class InputStore:
    def __init__(self, path: Any = DEFAULT_DB_PATH) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS uploads (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL, original_name TEXT NOT NULL, stored_name TEXT,
                    mime TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
                    uploaded_by TEXT NOT NULL, uploaded_at REAL NOT NULL,
                    client_id TEXT, group_id TEXT, device_id TEXT, incident_id TEXT,
                    meta TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_uploads_client ON uploads(client_id);
                CREATE TABLE IF NOT EXISTS input_state (
                    username TEXT PRIMARY KEY, state TEXT NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS input_commits (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL, created_at REAL NOT NULL, payload TEXT NOT NULL
                );
            """)
            self._db.commit()

    # -- uploads ------------------------------------------------------------
    @staticmethod
    def _upload(row: sqlite3.Row) -> Dict[str, Any]:
        r = dict(row)
        return {
            "id": f"UPL-{r['seq']:04d}", "kind": r["kind"], "filename": r["original_name"],
            "stored_name": r["stored_name"], "folder": f"uploads/{up.FOLDER[r['kind']]}",
            "mime": r["mime"], "size": r["size"], "sha256": r["sha256"],
            "uploaded_by": r["uploaded_by"], "uploaded_at": r["uploaded_at"],
            "client_id": r["client_id"], "group_id": r["group_id"],
            "device_id": r["device_id"], "incident_id": r["incident_id"],
            "meta": json.loads(r["meta"]) if r["meta"] else {},
        }

    def add_upload(self, *, kind: str, filename: str, mime: str, size: int, sha256: str,
                   uploaded_by: str, client_id: Optional[str], group_id: Optional[str],
                   device_id: Optional[str], incident_id: Optional[str],
                   meta: Dict[str, Any], data: bytes) -> Dict[str, Any]:
        """Record the upload, then write the bytes. If the write fails the row goes."""
        with self._lock:
            cur = self._db.execute(
                """INSERT INTO uploads (kind, original_name, mime, size, sha256, uploaded_by,
                   uploaded_at, client_id, group_id, device_id, incident_id, meta)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (kind, filename[:200], mime, size, sha256, uploaded_by, time.time(),
                 client_id, group_id, device_id, incident_id, json.dumps(meta)))
            seq = cur.lastrowid
            stored = up.stored_name(f"UPL-{seq:04d}", filename)
            try:
                up.write(kind, stored, data)
            except OSError:
                self._db.execute("DELETE FROM uploads WHERE seq=?", (seq,))
                self._db.commit()
                raise
            self._db.execute("UPDATE uploads SET stored_name=? WHERE seq=?", (stored, seq))
            self._db.commit()
            return self.get_upload(f"UPL-{seq:04d}")

    def get_upload(self, upload_id: str) -> Dict[str, Any]:
        try:
            seq = int(upload_id.split("-")[1])
        except (IndexError, ValueError, AttributeError):
            raise NotFound(upload_id)
        with self._lock:
            row = self._db.execute("SELECT * FROM uploads WHERE seq=?", (seq,)).fetchone()
        if row is None or not upload_id.startswith("UPL-"):
            raise NotFound(upload_id)
        return self._upload(row)

    def list_uploads(self, client_id: Optional[str] = None) -> List[Dict[str, Any]]:
        sql, args = "SELECT * FROM uploads", []
        if client_id:
            sql += " WHERE client_id = ?"
            args.append(client_id)
        sql += " ORDER BY uploaded_at DESC, seq DESC"
        with self._lock:
            return [self._upload(r) for r in self._db.execute(sql, args).fetchall()]

    def delete_upload(self, upload_id: str) -> Dict[str, Any]:
        with self._lock:
            row = self.get_upload(upload_id)
            self._db.execute("DELETE FROM uploads WHERE seq=?", (int(upload_id.split("-")[1]),))
            self._db.commit()
        up.remove(row["kind"], row["stored_name"])
        return row

    # -- working state --------------------------------------------------------
    def get_state(self, username: str) -> Dict[str, Any]:
        with self._lock:
            row = self._db.execute("SELECT state FROM input_state WHERE username=?", (username,)).fetchone()
        if row is None:
            return empty_state()
        try:
            return {**empty_state(), **json.loads(row["state"])}
        except ValueError:
            return empty_state()

    def put_state(self, username: str, state: Dict[str, Any]) -> None:
        with self._lock:
            self._db.execute(
                """INSERT INTO input_state (username, state, updated_at) VALUES (?,?,?)
                   ON CONFLICT(username) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at""",
                (username, json.dumps(state), time.time()))
            self._db.commit()

    # -- commits -------------------------------------------------------------
    def add_commit(self, username: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            now = time.time()
            cur = self._db.execute(
                "INSERT INTO input_commits (username, created_at, payload) VALUES (?,?,?)",
                (username, now, "{}"))
            cid = f"INP-{cur.lastrowid:04d}"
            full = {**payload, "id": cid, "created_at": now, "by": username}
            self._db.execute("UPDATE input_commits SET payload=? WHERE seq=?",
                             (json.dumps(full), cur.lastrowid))
            self._db.commit()
            return full

    def get_commit(self, commit_id: str) -> Dict[str, Any]:
        try:
            seq = int(commit_id.split("-")[1])
        except (IndexError, ValueError, AttributeError):
            raise NotFound(commit_id)
        with self._lock:
            row = self._db.execute("SELECT payload FROM input_commits WHERE seq=?", (seq,)).fetchone()
        if row is None:
            raise NotFound(commit_id)
        return json.loads(row["payload"])

    def latest_commit(self, username: Optional[str] = None) -> Optional[Dict[str, Any]]:
        sql, args = "SELECT payload FROM input_commits", []
        if username:
            sql += " WHERE username=?"
            args.append(username)
        sql += " ORDER BY seq DESC LIMIT 1"
        with self._lock:
            row = self._db.execute(sql, args).fetchone()
        return json.loads(row["payload"]) if row else None

    def close(self) -> None:
        with self._lock:
            self._db.close()


_store: Optional[InputStore] = None
_store_lock = threading.Lock()


def get_store() -> InputStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = InputStore(os.environ.get("INPUTS_DB_PATH") or DEFAULT_DB_PATH)
        return _store


def use_store(store: Optional[InputStore]) -> None:
    global _store
    with _store_lock:
        _store = store


# ---------------------------------------------------------------------------
# selection: client -> group -> device -> eSIM / RSP / network
# ---------------------------------------------------------------------------
def _by(rows: List[Dict[str, Any]], key: str) -> Dict[str, Dict[str, Any]]:
    return {r[key]: r for r in rows}


def apply_patch(current: Dict[str, Any], patch: Dict[str, Any], *, reg: Dict[str, Any],
                dsets: List[Dict[str, Any]],
                incident_device: Callable[[str], Optional[str]]) -> Dict[str, Any]:
    """Merge a partial selection into the current one and keep it consistent.

    Choosing a device fills its client, group, RSP environment and network
    context. Choosing a dataset selects the device it describes. Anything that
    no longer fits (a dataset for another device, an incident on another device)
    is cleared rather than left dangling. Unknown ids raise `InputError`.
    """
    s = copy.deepcopy({**empty_state(), **(current or {})})
    s["data_source"] = {**empty_state()["data_source"], **(s.get("data_source") or {})}
    clients, groups = _by(reg["clients"], "client_id"), _by(reg["groups"], "group_id")
    devices = _by(reg["devices"], "euicc_id")
    networks, rsps = _by(reg["networks"], "network_id"), _by(reg["rsp_environments"], "rsp_env_id")
    ds = _by(dsets, "id")
    p = patch or {}

    def clear_mismatched_dataset() -> None:
        d = ds.get(s["data_source"]["dataset_id"] or "")
        if d and (d["client_id"] != s["client_id"] or (s["device_id"] and d["euicc_id"] != s["device_id"])):
            s["data_source"]["dataset_id"] = None

    def set_device(dev: Dict[str, Any], keep_ctx: bool = False) -> None:
        changed = s["device_id"] != dev["euicc_id"]
        s["device_id"], s["group_id"], s["client_id"] = dev["euicc_id"], dev["group_id"], dev["client_id"]
        if changed and not keep_ctx:
            s["rsp_env_id"], s["network_id"] = dev["rsp_env_id"], dev["network_id"]
        if changed and s["incident_id"] and incident_device(s["incident_id"]) != dev["euicc_id"]:
            s["incident_id"] = None
        clear_mismatched_dataset()

    if "client_id" in p:
        cid = p["client_id"] or None
        if cid is not None and cid not in clients:
            raise InputError(f"Unknown client {cid}.")
        if cid != s["client_id"]:
            s.update(client_id=cid, group_id=None, device_id=None, incident_id=None,
                     rsp_env_id=None, network_id=None)
            clear_mismatched_dataset()

    if "group_id" in p:
        gid = p["group_id"] or None
        if gid is not None:
            if gid not in groups:
                raise InputError(f"Unknown group {gid}.")
            if s["client_id"] and groups[gid]["client_id"] != s["client_id"]:
                raise InputError(f"Group {gid} belongs to another client.")
            s["client_id"] = groups[gid]["client_id"]
        if gid != s["group_id"]:
            s["group_id"] = gid
            dev = devices.get(s["device_id"] or "")
            if dev and dev["group_id"] != gid:
                s.update(device_id=None, incident_id=None, rsp_env_id=None, network_id=None)
                clear_mismatched_dataset()

    if "device_id" in p:
        eid = p["device_id"] or None
        if eid is None:
            s.update(device_id=None, incident_id=None, rsp_env_id=None, network_id=None)
        else:
            dev = devices.get(eid)
            if dev is None:
                raise InputError(f"Device {eid} is not in the client registry.")
            if s["client_id"] and dev["client_id"] != s["client_id"]:
                raise InputError(f"Device {eid} belongs to another client.")
            set_device(dev)

    if "data_source" in p:
        src = p["data_source"] or {}
        if "mode" in src:
            if src["mode"] not in (None, *MODES):
                raise InputError(f"Data source must be one of: {', '.join(MODES)}.")
            s["data_source"]["mode"] = src["mode"]
        if "dataset_id" in src:
            did = src["dataset_id"] or None
            if did is not None:
                d = ds.get(did)
                if d is None:
                    raise InputError(f"Unknown dataset {did}.")
                if s["client_id"] and d["client_id"] != s["client_id"]:
                    raise InputError(f"Dataset {did} belongs to another client.")
                set_device(devices[d["euicc_id"]])
            s["data_source"]["dataset_id"] = did
        if "upload_id" in src:
            uid = src["upload_id"] or None
            s["data_source"]["upload_id"] = uid
            if uid is None:
                s["data_source"]["upload"] = None
            else:
                snap = src.get("upload")
                if not snap or snap.get("id") != uid:
                    raise InputError("The uploaded file was not checked; choose it again.")
                if s["client_id"] and snap.get("client_id") != s["client_id"]:
                    raise InputError("That file belongs to another client.")
                s["data_source"]["upload"] = snap
                s["data_source"]["mode"] = "upload"

    if "incident_id" in p:
        iid = p["incident_id"] or None
        if iid is not None:
            dev_id = incident_device(iid)
            if dev_id is None:
                raise InputError(f"Incident {iid} is not in the running session.")
            if dev_id not in devices:
                raise InputError(f"Incident {iid} is on device {dev_id}, which is not in the client registry.")
            if s["client_id"] and devices[dev_id]["client_id"] != s["client_id"]:
                raise InputError(f"Incident {iid} belongs to another client.")
            set_device(devices[dev_id])
        s["incident_id"] = iid

    if "rsp_env_id" in p:
        rid = p["rsp_env_id"] or None
        if rid is not None and rid not in rsps:
            raise InputError(f"Unknown RSP environment {rid}.")
        s["rsp_env_id"] = rid
    if "network_id" in p:
        nid = p["network_id"] or None
        if nid is not None and nid not in networks:
            raise InputError(f"Unknown network context {nid}.")
        s["network_id"] = nid
    return s


# ---------------------------------------------------------------------------
# describing a selection
# ---------------------------------------------------------------------------
def attachments_for(state: Dict[str, Any], uploads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Uploads that belong to this input: same client, and either not tied to a
    device / incident or tied to the ones selected now."""
    if not state.get("client_id"):
        return []
    out = []
    for u in uploads:
        if u["client_id"] != state["client_id"]:
            continue
        if u["device_id"] and u["device_id"] != state.get("device_id"):
            continue
        if u["incident_id"] and u["incident_id"] != state.get("incident_id"):
            continue
        out.append(u)
    return out


def resolve(state: Dict[str, Any], reg: Dict[str, Any], dsets: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The selection expanded into the objects it names."""
    get = lambda rows, key, val: next((r for r in rows if r[key] == val), None)  # noqa: E731
    src = state.get("data_source") or {}
    snap = src.get("upload") if src.get("mode") == "upload" else None
    return {
        "client": get(reg["clients"], "client_id", state.get("client_id")),
        "group": get(reg["groups"], "group_id", state.get("group_id")),
        "device": get(reg["devices"], "euicc_id", state.get("device_id")),
        "rsp_environment": get(reg["rsp_environments"], "rsp_env_id", state.get("rsp_env_id")),
        "network": get(reg["networks"], "network_id", state.get("network_id")),
        "dataset": ({
            "id": f"upload:{snap['id']}", "title": snap["filename"], "path": f"uploads/csv/{snap['stored_name']}",
            "kind": "upload", "provenance": UPLOAD_LABEL, "rows": snap["rows"],
            "fields": list(FEATURE_NAMES), "time_range": {"from": snap["time_from"], "to": snap["time_to"]},
            "client_id": snap["client_id"], "euicc_id": snap["device_id"], "sha256": snap["sha256"],
            "generator": "Operator upload", "integrity": "checked at upload",
            "assumed_time": snap["assumed_time"], "has_fault_labels": snap["has_fault_labels"],
        } if snap else get(dsets, "id", src.get("dataset_id"))),
    }


def readiness(state: Dict[str, Any], *, reg: Dict[str, Any], dsets: List[Dict[str, Any]],
              realtime: Dict[str, Any], attachments: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Is the input complete enough to process? Checks marked `blocking` stop
    PROCESS DATA; the others are advice."""
    r = resolve(state, reg, dsets)
    mode = (state.get("data_source") or {}).get("mode")
    grid = set(realtime.get("devices") or [])
    checks: List[Dict[str, Any]] = []

    def add(key: str, label: str, ok: bool, detail: str, blocking: bool = True) -> None:
        checks.append({"key": key, "label": label, "ok": bool(ok), "detail": detail, "blocking": blocking})

    add("client", "Client selected", r["client"], r["client"]["name"] if r["client"] else "Choose a client")
    add("device", "Device selected", r["device"],
        r["device"]["device_label"] if r["device"] else "Choose a device")
    add("esim", "eSIM identity resolved", r["device"],
        f"ICCID {r['device']['iccid']}" if r["device"] else "Comes with the device")
    add("rsp", "RSP environment", r["rsp_environment"],
        r["rsp_environment"]["name"] if r["rsp_environment"] else "Choose an RSP environment")
    add("network", "Network and authentication context", r["network"],
        r["network"]["name"] if r["network"] else "Choose a network context")
    add("source", "Data source chosen", mode in MODES,
        {"realtime": "Real-time feed", "synthetic": "Stored dataset (synthetic)",
         "upload": "Your uploaded CSV"}.get(mode, "Choose real-time, a stored dataset or your own CSV"))
    if mode == "upload":
        snap = (state.get("data_source") or {}).get("upload")
        same = bool(snap) and snap.get("device_id") == state.get("device_id")
        add("upload", "Uploaded CSV checked for this device", same,
            (f"{snap['filename']}: {snap['rows']} rows, {snap['time_from']} to {snap['time_to']}"
             + (" (no time column: readings assumed one second apart)" if snap.get("assumed_time") else ""))
            if same else ("The file was checked for a different device; choose it again" if snap
                          else "Choose one of your uploaded CSVs"))
    if mode == "synthetic":
        add("dataset", "Dataset selected", r["dataset"],
            r["dataset"]["title"] if r["dataset"] else "Choose a dataset for this client")
    if mode == "realtime":
        dev_id = state.get("device_id")
        add("grid", "Device is in the real-time grid", dev_id in grid if dev_id else False,
            "Streaming from the grid" if dev_id in grid else
            "Only grid devices can stream; pick one, or use a stored dataset")
        add("feed", "Real-time feed is running", realtime.get("running"),
            "Running" if realtime.get("running") else "Start real-time data before processing",
            blocking=False)
    add("attachments", "Operator uploads", True,
        f"{len(attachments)} attached" if attachments else "None attached (optional)", blocking=False)
    return {"ready": all(c["ok"] for c in checks if c["blocking"]), "checks": checks}


def provenance(state: Dict[str, Any], *, reg: Dict[str, Any], dsets: List[Dict[str, Any]],
               realtime: Dict[str, Any], attachments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One row per piece of input: source, dataset/file, type, client/group,
    relevant fields, time range. Real-time, synthetic and uploaded are always
    distinguishable by `type_key`."""
    r = resolve(state, reg, dsets)
    client = r["client"]["name"] if r["client"] else None
    group = r["group"]["name"] if r["group"] else None
    device = r["device"]["euicc_id"] if r["device"] else None
    mode = (state.get("data_source") or {}).get("mode")
    rows: List[Dict[str, Any]] = []

    if mode == "realtime":
        rows.append({
            "role": "Data source", "type_key": "realtime", "type": REALTIME_LABEL,
            "source": realtime.get("source_label") or "Prototype Server / Grid Telemetry",
            "dataset": "Live stream (no file)", "client": client, "group": group, "device": device,
            "fields": list(FEATURE_NAMES),
            "time_range": ({"from": realtime.get("started_at"), "to": realtime.get("last_ts")}
                           if realtime.get("started_at") else None),
            "note": "Generated by this server's prototype grid feed. It is not a connection to live telecom infrastructure.",
        })
    elif mode == "upload" and r["dataset"]:
        d = r["dataset"]
        rows.append({
            "role": "Data source", "type_key": "upload", "type": UPLOAD_LABEL,
            "source": "Your uploaded CSV", "dataset": d["path"], "client": client, "group": group,
            "device": device, "fields": d["fields"], "time_range": d["time_range"],
            "upload_id": d["id"].split(":", 1)[1],
            "note": (f"{d['rows']} rows. Unverified: supplied by the operator, checked for format only."
                     + (" No time column, so readings were assumed one second apart." if d["assumed_time"] else "")
                     + (" Carries simulator fault labels." if d["has_fault_labels"] else "")),
        })
    elif mode == "synthetic" and r["dataset"]:
        d = r["dataset"]
        imported = d.get("provenance") == IMPORT_LABEL
        rows.append({
            "role": "Data source", "type_key": "upload" if imported else "synthetic",
            "type": d.get("provenance") or SYNTHETIC_LABEL,
            "source": "Imported dataset (operator supplied)" if imported else "Stored dataset (prefetched)",
            "dataset": d["path"], "client": client,
            "group": group, "device": device, "fields": d["fields"], "time_range": d["time_range"],
            "note": f"{d['rows']} rows. {d['generator']}. Checksum {d['integrity']}.",
        })

    if r["device"]:
        rows.append({
            "role": "Client and device context", "type_key": "synthetic", "type": SYNTHETIC_LABEL,
            "source": "Stored registry (prefetched)",
            "dataset": ", ".join(p for p in ("data/clients/clients.csv", "data/devices/devices.csv",
                                             "data/network/network_contexts.csv", "data/rsp/rsp_environments.csv")),
            "client": client, "group": group, "device": device,
            "fields": ["euicc_id", "eid", "iccid", "profile_name", "rsp_env_id", "network_id", "cell_id"],
            "time_range": None, "note": "Fictional client, eSIM, RSP and network records.",
        })

    chosen = (state.get("data_source") or {}).get("upload_id") if mode == "upload" else None
    for a in attachments:
        if a["id"] == chosen:
            continue                                 # already listed as the data source
        m = a["meta"]
        fields = (m.get("columns") if a["kind"] == "csv" else
                  [f"{m['size_px']['width']}x{m['size_px']['height']} px"] if a["kind"] == "screenshot" and m.get("size_px")
                  else [f"{m['lines']} lines"] if a["kind"] == "log" else [])
        rows.append({
            "role": {"screenshot": "Screenshot", "csv": "CSV upload", "log": "Text / log upload"}[a["kind"]],
            "type_key": "upload", "type": UPLOAD_LABEL, "source": f"Uploaded by {a['uploaded_by']}",
            "dataset": f"{a['folder']}/{a['stored_name']}", "client": client, "group": group,
            "device": a["device_id"] or device, "fields": fields, "time_range": None,
            "note": "Unverified: supplied by the operator." + (
                " Has all five telemetry columns." if m.get("telemetry_compatible") else ""),
            "upload_id": a["id"],
        })
    return rows


def build_commit(state: Dict[str, Any], *, reg: Dict[str, Any], dsets: List[Dict[str, Any]],
                 realtime: Dict[str, Any], attachments: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The frozen record PROCESS DATA stores. Raises InputError if not ready."""
    ready = readiness(state, reg=reg, dsets=dsets, realtime=realtime, attachments=attachments)
    if not ready["ready"]:
        missing = [c["label"] for c in ready["checks"] if c["blocking"] and not c["ok"]]
        raise InputError("Not ready to process: " + "; ".join(missing) + ".")
    mode = state["data_source"]["mode"]
    return {
        "state": copy.deepcopy(state),
        "resolved": resolve(state, reg, dsets),
        "source": {
            "mode": mode,
            "type": {"realtime": REALTIME_LABEL, "upload": UPLOAD_LABEL}.get(
                mode, (resolve(state, reg, dsets)["dataset"] or {}).get("provenance") or SYNTHETIC_LABEL),
            "realtime": ({k: realtime.get(k) for k in ("running", "started_at", "last_ts", "samples_received",
                                                        "source_label", "devices")} if mode == "realtime" else None),
        },
        "readiness": ready,
        "provenance": provenance(state, reg=reg, dsets=dsets, realtime=realtime, attachments=attachments),
        "attachments": [{k: a[k] for k in ("id", "kind", "filename", "stored_name", "folder", "mime",
                                           "size", "sha256", "client_id", "device_id", "incident_id")}
                        for a in attachments],
    }
