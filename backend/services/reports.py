"""Client reports: the persistent record of what clients told the owner, and
what the owner did about it.

Lifecycle (enforced here, not in the UI):

    SENT  ->  ACKNOWLEDGED  ->  RESOLVED

* A report is created SENT by whoever submits it.
* The owner ACKNOWLEDGES it (optionally leaving a response note).
* The owner RESOLVES it (optionally leaving a resolution note). Resolving a
  report that was never acknowledged acknowledges it in the same step, so a
  resolved report always carries all three timestamps.
* RESOLVED is terminal.

Storage
-------
Reports live in a small SQLite file (`backend/data/reports.db`, or wherever
`REPORTS_DB_PATH` points), not in a Python list, so a client's history
survives a server restart. Devices and incidents, by contrast, are objects of
the running agent session and are *not* persisted (incident ids are random per
run and `agent/reset` clears them). Each report therefore stores the id it was
linked to **plus a snapshot** of that device/incident at submission time, so an
old report never turns into a dangling id: the UI can say "this incident is no
longer in the running session" and still show what it was.

This module knows nothing about FastAPI, the agent, or who is calling. Access
control and link validation live in `routes.py` / `report_links.py`, which keeps
this file importable and testable on its own.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

SENT = "SENT"
ACKNOWLEDGED = "ACKNOWLEDGED"
RESOLVED = "RESOLVED"
STATUSES = (SENT, ACKNOWLEDGED, RESOLVED)

MAX_MESSAGE = 2000
MAX_NOTE = 2000
MAX_CUSTOMER = 120

_ID_RE = re.compile(r"^RPT-(\d{1,9})$")

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "reports.db"


class ReportError(Exception):
    """Base class for store errors that callers map onto HTTP responses."""


class ReportNotFound(ReportError):
    pass


class InvalidTransition(ReportError):
    pass


def format_id(seq: int) -> str:
    return f"RPT-{seq:04d}"


def parse_id(report_id: str) -> int:
    match = _ID_RE.match((report_id or "").strip().upper())
    if not match:
        raise ReportNotFound(report_id)
    return int(match.group(1))


def _clean(text: Optional[str], limit: int, what: str) -> Optional[str]:
    text = (text or "").strip()
    if not text:
        return None
    if len(text) > limit:
        raise ValueError(f"{what} is longer than {limit} characters")
    return text


class ReportStore:
    """One SQLite connection guarded by one lock.

    Report volume is tiny (a note or two per client per incident), so a single
    serialised connection is simpler and safer than a pool. FastAPI runs sync
    handlers on a thread pool, hence `check_same_thread=False` + the lock.
    """

    def __init__(self, path: Any = DEFAULT_DB_PATH) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS reports (
                    seq              INTEGER PRIMARY KEY AUTOINCREMENT,
                    client           TEXT    NOT NULL,
                    role             TEXT    NOT NULL,
                    customer         TEXT,
                    message          TEXT    NOT NULL,
                    created_at       REAL    NOT NULL,
                    updated_at       REAL    NOT NULL,
                    status           TEXT    NOT NULL DEFAULT 'SENT'
                                     CHECK (status IN ('SENT','ACKNOWLEDGED','RESOLVED')),
                    device_id        TEXT,
                    incident_id      TEXT,
                    device_snapshot  TEXT,
                    incident_snapshot TEXT,
                    acknowledged_at  REAL,
                    acknowledged_by  TEXT,
                    response_note    TEXT,
                    resolved_at      REAL,
                    resolved_by      TEXT,
                    resolution_note  TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_reports_client   ON reports(client);
                CREATE INDEX IF NOT EXISTS idx_reports_incident ON reports(incident_id);
                CREATE INDEX IF NOT EXISTS idx_reports_device   ON reports(device_id);
                CREATE INDEX IF NOT EXISTS idx_reports_status   ON reports(status);
                """
            )
            self._db.commit()

    # -- row -> dict --------------------------------------------------------
    @staticmethod
    def _loads(blob: Optional[str]) -> Optional[Dict[str, Any]]:
        if not blob:
            return None
        try:
            return json.loads(blob)
        except ValueError:
            return None

    def _to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        r = dict(row)
        timeline: List[Dict[str, Any]] = [
            {"key": "sent", "label": "Sent", "at": r["created_at"],
             "by": r["client"], "note": None},
        ]
        if r["acknowledged_at"] is not None:
            timeline.append({"key": "acknowledged", "label": "Acknowledged",
                             "at": r["acknowledged_at"], "by": r["acknowledged_by"],
                             "note": r["response_note"]})
        if r["resolved_at"] is not None:
            timeline.append({"key": "resolved", "label": "Resolved",
                             "at": r["resolved_at"], "by": r["resolved_by"],
                             "note": r["resolution_note"]})
        return {
            "id": format_id(r["seq"]),
            "client": r["client"],
            "submitted_by_role": r["role"],
            "customer": r["customer"],
            "message": r["message"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "status": r["status"],
            "device_id": r["device_id"],
            "incident_id": r["incident_id"],
            "device_snapshot": self._loads(r["device_snapshot"]),
            "incident_snapshot": self._loads(r["incident_snapshot"]),
            "acknowledged_at": r["acknowledged_at"],
            "acknowledged_by": r["acknowledged_by"],
            "response_note": r["response_note"],
            "resolved_at": r["resolved_at"],
            "resolved_by": r["resolved_by"],
            "resolution_note": r["resolution_note"],
            "timeline": timeline,
        }

    # -- writes -------------------------------------------------------------
    def create(self, *, client: str, role: str, message: str,
               customer: Optional[str] = None,
               device_id: Optional[str] = None,
               incident_id: Optional[str] = None,
               device_snapshot: Optional[Dict[str, Any]] = None,
               incident_snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        message = _clean(message, MAX_MESSAGE, "message")
        if not message:
            raise ValueError("message must not be empty")
        customer = _clean(customer, MAX_CUSTOMER, "customer")
        now = time.time()
        with self._lock:
            cur = self._db.execute(
                """INSERT INTO reports
                   (client, role, customer, message, created_at, updated_at, status,
                    device_id, incident_id, device_snapshot, incident_snapshot)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (client, role, customer, message, now, now, SENT,
                 device_id or None, incident_id or None,
                 json.dumps(device_snapshot) if device_snapshot else None,
                 json.dumps(incident_snapshot) if incident_snapshot else None),
            )
            self._db.commit()
            return self.get(format_id(cur.lastrowid))

    def acknowledge(self, report_id: str, by: str, note: Optional[str] = None) -> Dict[str, Any]:
        note = _clean(note, MAX_NOTE, "note")
        seq = parse_id(report_id)
        with self._lock:
            current = self.get(report_id)
            if current["status"] != SENT:
                raise InvalidTransition(
                    f"{report_id} is already {current['status'].lower()}; "
                    "only a sent report can be acknowledged")
            now = time.time()
            self._db.execute(
                """UPDATE reports SET status=?, acknowledged_at=?, acknowledged_by=?,
                   response_note=?, updated_at=? WHERE seq=?""",
                (ACKNOWLEDGED, now, by, note, now, seq),
            )
            self._db.commit()
            return self.get(report_id)

    def resolve(self, report_id: str, by: str, note: Optional[str] = None) -> Dict[str, Any]:
        note = _clean(note, MAX_NOTE, "note")
        seq = parse_id(report_id)
        with self._lock:
            current = self.get(report_id)
            if current["status"] == RESOLVED:
                raise InvalidTransition(f"{report_id} is already resolved")
            now = time.time()
            if current["status"] == SENT:
                # Resolving straight from SENT: record the acknowledgement too
                # so the history always reads Sent -> Acknowledged -> Resolved.
                self._db.execute(
                    "UPDATE reports SET acknowledged_at=?, acknowledged_by=? WHERE seq=?",
                    (now, by, seq),
                )
            self._db.execute(
                """UPDATE reports SET status=?, resolved_at=?, resolved_by=?,
                   resolution_note=?, updated_at=? WHERE seq=?""",
                (RESOLVED, now, by, note, now, seq),
            )
            self._db.commit()
            return self.get(report_id)

    # -- reads --------------------------------------------------------------
    def get(self, report_id: str) -> Dict[str, Any]:
        seq = parse_id(report_id)
        with self._lock:
            row = self._db.execute("SELECT * FROM reports WHERE seq=?", (seq,)).fetchone()
        if row is None:
            raise ReportNotFound(report_id)
        return self._to_dict(row)

    def list(self, *, client: Optional[str] = None, status: Optional[str] = None,
             device_id: Optional[str] = None,
             incident_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Newest first. `client` restricts to one account's own reports."""
        where, args = [], []
        for column, value in (("client", client), ("status", status),
                              ("device_id", device_id), ("incident_id", incident_id)):
            if value:
                where.append(f"{column} = ?")
                args.append(value)
        sql = "SELECT * FROM reports"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC, seq DESC"
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [self._to_dict(r) for r in rows]

    def counts_by(self, column: str, client: Optional[str] = None) -> Dict[str, int]:
        """report count per device_id / incident_id (only rows that have one)."""
        if column not in ("device_id", "incident_id"):
            raise ValueError(column)
        sql = f"SELECT {column} AS k, COUNT(*) AS n FROM reports WHERE {column} IS NOT NULL"
        args: List[Any] = []
        if client:
            sql += " AND client = ?"
            args.append(client)
        sql += f" GROUP BY {column}"
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return {r["k"]: r["n"] for r in rows}

    def summary(self, client: Optional[str] = None) -> Dict[str, int]:
        sql = "SELECT status, COUNT(*) AS n FROM reports"
        args: List[Any] = []
        if client:
            sql += " WHERE client = ?"
            args.append(client)
        sql += " GROUP BY status"
        with self._lock:
            rows = {r["status"]: r["n"] for r in self._db.execute(sql, args).fetchall()}
        sent, ack, done = rows.get(SENT, 0), rows.get(ACKNOWLEDGED, 0), rows.get(RESOLVED, 0)
        return {"total": sent + ack + done, "sent": sent, "acknowledged": ack,
                "resolved": done, "open": sent + ack}

    def close(self) -> None:
        with self._lock:
            self._db.close()


# ---------------------------------------------------------------------------
# Process-wide default store (lazy, so importing this module has no side effect
# and tests can swap in an in-memory one with `use_store`).
# ---------------------------------------------------------------------------
_store: Optional[ReportStore] = None
_store_lock = threading.Lock()


def get_store() -> ReportStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = ReportStore(os.environ.get("REPORTS_DB_PATH") or DEFAULT_DB_PATH)
        return _store


def use_store(store: Optional[ReportStore]) -> None:
    global _store
    with _store_lock:
        _store = store


def create(**kwargs: Any) -> Dict[str, Any]:
    return get_store().create(**kwargs)


def get(report_id: str) -> Dict[str, Any]:
    return get_store().get(report_id)


def list_reports(**filters: Any) -> List[Dict[str, Any]]:
    return get_store().list(**filters)


def acknowledge(report_id: str, by: str, note: Optional[str] = None) -> Dict[str, Any]:
    return get_store().acknowledge(report_id, by, note)


def resolve(report_id: str, by: str, note: Optional[str] = None) -> Dict[str, Any]:
    return get_store().resolve(report_id, by, note)


def counts_by(column: str, client: Optional[str] = None) -> Dict[str, int]:
    return get_store().counts_by(column, client=client)


def summary(client: Optional[str] = None) -> Dict[str, int]:
    return get_store().summary(client=client)
