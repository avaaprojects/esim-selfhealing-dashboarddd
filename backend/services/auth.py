"""Accounts, sessions and access control.

Accounts live in a small SQLite file (`backend/data/accounts.db`, or wherever
`ACCOUNTS_DB_PATH` points) alongside the reports and input stores:

* **passwords are never stored.** Each account keeps a random per-account salt
  and a PBKDF2-HMAC-SHA256 hash, compared in constant time;
* **sessions survive a restart,** because tokens are rows, not a dict in
  memory. A token still expires after TOKEN_TTL_S, and signing out deletes it;
* **any number of accounts per company.** An account has a role (`owner` or
  `client`) and, for a client, the `client_id` of the company whose data it may
  see (enforced in services/tenancy.py, not here);
* **repeated failed sign-ins are slowed down** per username and per client
  address, so the demo credentials cannot be brute-forced.

On first use the two demo accounts are seeded, with passwords from
`DEMO_OWNER_PASSWORD` / `DEMO_CLIENT_PASSWORD` if those are set. Seeding happens
once: changing a password later (`tools/manage_accounts.py`) is not undone by a
restart, and `DEMO_ACCOUNTS=off` skips the demo accounts entirely.

Nothing here reaches into the agent. It answers one question for the routes
layer: "who is this request from, and are they allowed to do this?"
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, Header, HTTPException, Request

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "accounts.db"

TOKEN_TTL_S = 12 * 3600
ROLES = ("owner", "client")

#: PBKDF2 cost. High enough to be worth something, low enough for a demo login.
_PBKDF2_ROUNDS = 240_000

#: Failed sign-ins: after LOCK_AFTER failures within LOCK_WINDOW_S, further
#: attempts for that username (or from that address) are refused for LOCK_FOR_S.
LOCK_AFTER = 5
LOCK_WINDOW_S = 300
LOCK_FOR_S = 300

_DEMO_SEED = (
    ("owner", "owner-demo-2026", "owner", None, "DEMO_OWNER_PASSWORD"),
    ("client", "client-demo-2026", "client", "CL-001", "DEMO_CLIENT_PASSWORD"),
)


class AccountError(ValueError):
    """A change that cannot be made. The message says what to fix."""


def hash_password(password: str, *, salt: Optional[bytes] = None) -> tuple[bytes, bytes]:
    salt = salt if salt is not None else secrets.token_bytes(16)
    return salt, hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS)


def _norm(username: str) -> str:
    return (username or "").strip().lower()


class AccountStore:
    """Accounts and live sessions. Safe for FastAPI's threadpool."""

    def __init__(self, path: Any = DEFAULT_DB_PATH):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                username   TEXT PRIMARY KEY,
                role       TEXT NOT NULL,
                client_id  TEXT,
                salt       BLOB NOT NULL,
                pwhash     BLOB NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                disabled   INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token      TEXT PRIMARY KEY,
                username   TEXT NOT NULL,
                issued_at  REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS login_failures (
                key        TEXT NOT NULL,
                at         REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_failures ON login_failures(key, at);
            CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
            """)
        self._db.commit()
        self._seed_demo_accounts()

    # -- accounts ----------------------------------------------------------
    def _seed_demo_accounts(self) -> None:
        """Create the demo accounts once, on a store that has none."""
        with self._lock:
            done = self._db.execute("SELECT v FROM meta WHERE k='demo_seeded'").fetchone()
            if done or os.environ.get("DEMO_ACCOUNTS", "").lower() in ("off", "0", "false", "no"):
                if not done:
                    self._db.execute("INSERT OR REPLACE INTO meta VALUES ('demo_seeded','skipped')")
                    self._db.commit()
                return
            now = time.time()
            for username, default, role, client_id, env_var in _DEMO_SEED:
                password = os.environ.get(env_var) or default
                salt, pwhash = hash_password(password)
                self._db.execute(
                    "INSERT OR IGNORE INTO accounts "
                    "(username, role, client_id, salt, pwhash, created_at, updated_at, disabled) "
                    "VALUES (?,?,?,?,?,?,?,0)",
                    (username, role, client_id, salt, pwhash, now, now))
            self._db.execute("INSERT OR REPLACE INTO meta VALUES ('demo_seeded','yes')")
            self._db.commit()

    # -- guarded access -----------------------------------------------------
    # One sqlite3 connection is shared by every request. Under a real server
    # those requests arrive on different threads at the same time, and sqlite3
    # raises InterfaceError if two of them touch the connection at once - which
    # looked to the browser like the session being rejected. So EVERY query,
    # read as well as write, goes through the lock.
    def _fetchone(self, sql: str, args: tuple = ()) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, args).fetchone()

    def _fetchall(self, sql: str, args: tuple = ()) -> List[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def get_account(self, username: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone("SELECT * FROM accounts WHERE username=?", (_norm(username),))
        return dict(row) if row else None

    def list_accounts(self) -> List[Dict[str, Any]]:
        rows = self._fetchall(
            "SELECT username, role, client_id, created_at, updated_at, disabled "
            "FROM accounts ORDER BY role, username")
        return [dict(r) for r in rows]

    def create_account(self, username: str, password: str, role: str,
                       client_id: Optional[str] = None) -> Dict[str, Any]:
        username = _norm(username)
        if not username:
            raise AccountError("A username is required.")
        if role not in ROLES:
            raise AccountError(f"Role must be one of: {', '.join(ROLES)}.")
        if role == "client" and not client_id:
            raise AccountError("A client account needs the client_id of the company it belongs to.")
        if role == "owner" and client_id:
            raise AccountError("An owner account sees every company, so it takes no client_id.")
        if len(password or "") < 8:
            raise AccountError("The password must be at least 8 characters.")
        salt, pwhash = hash_password(password)
        now = time.time()
        with self._lock:
            if self._db.execute("SELECT 1 FROM accounts WHERE username=?", (username,)).fetchone():
                raise AccountError(f"An account called {username} already exists.")
            self._db.execute(
                "INSERT INTO accounts (username, role, client_id, salt, pwhash, created_at, updated_at, disabled) "
                "VALUES (?,?,?,?,?,?,?,0)", (username, role, client_id, salt, pwhash, now, now))
            self._db.commit()
        return {"username": username, "role": role, "client_id": client_id}

    def set_password(self, username: str, password: str) -> None:
        """Change a password and end that account's sessions."""
        if len(password or "") < 8:
            raise AccountError("The password must be at least 8 characters.")
        username = _norm(username)
        salt, pwhash = hash_password(password)
        with self._lock:
            cur = self._db.execute("UPDATE accounts SET salt=?, pwhash=?, updated_at=? WHERE username=?",
                                   (salt, pwhash, time.time(), username))
            if cur.rowcount == 0:
                raise AccountError(f"No account called {username}.")
            self._db.execute("DELETE FROM sessions WHERE username=?", (username,))
            self._db.commit()

    def set_disabled(self, username: str, disabled: bool) -> None:
        username = _norm(username)
        with self._lock:
            cur = self._db.execute("UPDATE accounts SET disabled=?, updated_at=? WHERE username=?",
                                   (1 if disabled else 0, time.time(), username))
            if cur.rowcount == 0:
                raise AccountError(f"No account called {username}.")
            if disabled:
                self._db.execute("DELETE FROM sessions WHERE username=?", (username,))
            self._db.commit()

    def delete_account(self, username: str) -> None:
        username = _norm(username)
        with self._lock:
            if self._db.execute("SELECT COUNT(*) c FROM accounts WHERE role='owner' AND disabled=0"
                                ).fetchone()["c"] <= 1:
                row = self._db.execute("SELECT role FROM accounts WHERE username=?", (username,)).fetchone()
                if row and row["role"] == "owner":
                    raise AccountError("That is the last owner account; create another before removing it.")
            cur = self._db.execute("DELETE FROM accounts WHERE username=?", (username,))
            if cur.rowcount == 0:
                raise AccountError(f"No account called {username}.")
            self._db.execute("DELETE FROM sessions WHERE username=?", (username,))
            self._db.commit()

    # -- rate limiting -----------------------------------------------------
    def _locked_until(self, keys: List[str], now: float) -> Optional[float]:
        rows = self._fetchall(
            "SELECT key, at FROM login_failures WHERE at > ? AND key IN (%s)"
            % ",".join("?" * len(keys)), (now - LOCK_WINDOW_S, *keys))
        by: Dict[str, List[float]] = {}
        for r in rows:
            by.setdefault(r["key"], []).append(r["at"])
        for times in by.values():
            if len(times) >= LOCK_AFTER:
                until = max(times) + LOCK_FOR_S
                if until > now:
                    return until
        return None

    def _record_failure(self, keys: List[str], now: float) -> None:
        with self._lock:
            self._db.executemany("INSERT INTO login_failures (key, at) VALUES (?,?)",
                                 [(k, now) for k in keys])
            self._db.execute("DELETE FROM login_failures WHERE at < ?", (now - LOCK_WINDOW_S - LOCK_FOR_S,))
            self._db.commit()

    def _clear_failures(self, keys: List[str]) -> None:
        with self._lock:
            self._db.execute("DELETE FROM login_failures WHERE key IN (%s)" % ",".join("?" * len(keys)), keys)
            self._db.commit()

    # -- sessions ----------------------------------------------------------
    def login(self, username: str, password: str, *, client_ip: Optional[str] = None) -> Dict[str, Any]:
        now = time.time()
        keys = [f"u:{_norm(username)}"] + ([f"ip:{client_ip}"] if client_ip else [])
        until = self._locked_until(keys, now)
        if until is not None:
            raise TooManyAttempts(int(until - now))

        account = self.get_account(username)
        ok = False
        if account and not account["disabled"]:
            _salt, candidate = hash_password(password or "", salt=account["salt"])
            ok = hmac.compare_digest(candidate, account["pwhash"])
        else:
            # Spend the same work on an unknown username, so the response time
            # does not reveal which accounts exist.
            hash_password(password or "", salt=b"\x00" * 16)
        if not ok:
            self._record_failure(keys, now)
            return {}
        self._clear_failures(keys)

        token = secrets.token_urlsafe(32)
        with self._lock:
            self._db.execute("INSERT INTO sessions (token, username, issued_at) VALUES (?,?,?)",
                             (token, account["username"], now))
            self._db.execute("DELETE FROM sessions WHERE issued_at < ?", (now - TOKEN_TTL_S,))
            self._db.commit()
        result = {"token": token, "role": account["role"], "username": account["username"]}
        if account["client_id"]:
            result["client_id"] = account["client_id"]
        return result

    def lookup(self, token: Optional[str]) -> Optional[Dict[str, Any]]:
        if not token:
            return None
        row = self._fetchone(
            "SELECT s.token, s.issued_at, a.username, a.role, a.client_id, a.disabled "
            "FROM sessions s JOIN accounts a ON a.username = s.username WHERE s.token = ?",
            (token,))
        if row is None:
            return None
        if row["disabled"] or time.time() - float(row["issued_at"]) > TOKEN_TTL_S:
            self.logout(token)
            return None
        entry: Dict[str, Any] = {"role": row["role"], "username": row["username"],
                                 "issued_at": row["issued_at"]}
        if row["client_id"]:
            entry["client_id"] = row["client_id"]
        return entry

    def logout(self, token: Optional[str]) -> None:
        if not token:
            return
        with self._lock:
            self._db.execute("DELETE FROM sessions WHERE token=?", (token,))
            self._db.commit()

    def close(self) -> None:
        self._db.close()


class TooManyAttempts(Exception):
    """Too many failed sign-ins. `retry_after` is in seconds."""

    def __init__(self, retry_after: int):
        super().__init__(f"Too many failed sign-in attempts. Try again in {max(1, retry_after // 60)} minute(s).")
        self.retry_after = retry_after


# ---------------------------------------------------------------------------
# module-level store (one per process; tests swap it out)
# ---------------------------------------------------------------------------
_STORE: Optional[AccountStore] = None
_STORE_LOCK = threading.Lock()


def get_store() -> AccountStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = AccountStore(os.environ.get("ACCOUNTS_DB_PATH") or DEFAULT_DB_PATH)
        return _STORE


def use_store(store: Optional[AccountStore]) -> None:
    """Swap the store (tests). Passing None returns to the default."""
    global _STORE
    with _STORE_LOCK:
        _STORE = store


# -- the API the routes use --------------------------------------------------
def login(username: str, password: str, *, client_ip: Optional[str] = None) -> Optional[Dict[str, str]]:
    """A new session, or None if the credentials are wrong.
    Raises TooManyAttempts when the account or address is rate-limited."""
    return get_store().login(username, password, client_ip=client_ip) or None


def logout(token: str) -> None:
    get_store().logout(token)


def _extract_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return authorization.strip()


def require_any_role(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    """FastAPI dependency: any signed-in CLIENT or OWNER session."""
    entry = get_store().lookup(_extract_token(authorization))
    if entry is None:
        raise HTTPException(status_code=401, detail="sign in required")
    return {**entry, "token": _extract_token(authorization)}


def require_owner(entry: Dict[str, Any] = Depends(require_any_role)) -> Dict[str, Any]:
    """FastAPI dependency: OWNER / ADMINISTRATOR session only.

    Resolves `require_any_role` first (so an expired/missing token still
    reports 401, not a misleading 403), then checks the role.
    """
    if entry.get("role") != "owner":
        raise HTTPException(status_code=403, detail="OWNER / ADMINISTRATOR access required")
    return entry


def client_ip(request: Optional[Request]) -> Optional[str]:
    """The caller's address for rate limiting, or None behind an unknown proxy."""
    if request is None:
        return None
    return getattr(getattr(request, "client", None), "host", None)
