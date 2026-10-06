"""Accounts, sessions, rate limiting and the deployment settings.

    python tests/test_accounts.py     # no pytest required

Covers what replaced the hardcoded demo credentials: salted password hashes,
sessions that survive a restart, several accounts per company, lockout after
repeated failures, and the configurable severity rules.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services import auth, settings


def _routes():
    try:
        from backend.api import routes
    except ImportError:
        return None
    return routes


def _status(fn, *a, **k):
    try:
        fn(*a, **k)
    except Exception as exc:
        return getattr(exc, "status_code", None) or type(exc).__name__
    return 200


class Store:
    """A throwaway account store on disk, so a restart can be simulated."""

    def __init__(self):
        self.dir = tempfile.mkdtemp()
        self.path = str(Path(self.dir) / "accounts.db")
        self.store = auth.AccountStore(self.path)
        auth.use_store(self.store)

    def restart(self):
        self.store.close()
        self.store = auth.AccountStore(self.path)
        auth.use_store(self.store)
        return self.store

    def close(self):
        auth.use_store(None)
        try:
            self.store.close()
        except Exception:                                   # noqa: BLE001
            pass
        shutil.rmtree(self.dir, ignore_errors=True)


# ---------------------------------------------------------------------------
def test_passwords_are_hashed_and_never_stored():
    s = Store()
    try:
        row = s.store.get_account("owner")
        assert row["pwhash"] != b"" and len(row["salt"]) >= 16
        blob = bytes(row["pwhash"]) + bytes(row["salt"])
        assert b"owner-demo-2026" not in blob, "the password itself must not be in the row"
        # the same password hashes differently for a different account (per-account salt)
        assert s.store.get_account("client")["salt"] != row["salt"]
        # and the hash is reproducible from the salt, which is how sign-in checks it
        _salt, again = auth.hash_password("owner-demo-2026", salt=row["salt"])
        assert again == row["pwhash"]
    finally:
        s.close()


def test_sign_in_issues_a_session_that_survives_a_restart():
    s = Store()
    try:
        res = auth.login("owner", "owner-demo-2026")
        assert res and res["role"] == "owner" and "client_id" not in res
        token = res["token"]
        assert auth.require_any_role(f"Bearer {token}")["username"] == "owner"

        s.restart()                                        # the backend restarts
        entry = auth.require_any_role(f"Bearer {token}")
        assert entry["username"] == "owner", "the session is still valid after a restart"

        auth.logout(token)
        assert _status(auth.require_any_role, f"Bearer {token}") == 401
        assert _status(auth.require_any_role, "Bearer not-a-token") == 401
        assert _status(auth.require_any_role, None) == 401
    finally:
        s.close()


def test_a_client_session_carries_its_company_and_an_owner_does_not():
    s = Store()
    try:
        c = auth.login("client", "client-demo-2026")
        assert c["client_id"] == "CL-001"
        entry = auth.require_any_role(f"Bearer {c['token']}")
        assert entry["client_id"] == "CL-001"
        assert _status(auth.require_owner, entry) == 403
        owner = auth.require_any_role(f"Bearer {auth.login('owner', 'owner-demo-2026')['token']}")
        assert _status(auth.require_owner, owner) == 200 and "client_id" not in owner
    finally:
        s.close()


def test_several_accounts_can_share_a_company():
    s = Store()
    try:
        s.store.create_account("alice", "alice-password", "client", "CL-001")
        s.store.create_account("bob", "bob-password", "client", "CL-002")
        s.store.create_account("ops2", "second-owner-pw", "owner")
        a = auth.login("alice", "alice-password")
        b = auth.login("bob", "bob-password")
        assert a["client_id"] == "CL-001" and b["client_id"] == "CL-002"
        assert auth.login("ops2", "second-owner-pw")["role"] == "owner"
        # they are separate sessions: signing one out leaves the other alone
        auth.logout(a["token"])
        assert auth.require_any_role(f"Bearer {b['token']}")["username"] == "bob"
        assert {r["username"] for r in s.store.list_accounts()} == {"owner", "client", "alice", "bob", "ops2"}
    finally:
        s.close()


def test_account_rules_are_enforced():
    s = Store()
    try:
        def refused(fn, *a):
            try:
                fn(*a)
            except auth.AccountError as exc:
                return str(exc)
            return None

        assert "at least 8" in refused(s.store.create_account, "x", "short", "owner")
        assert "client_id" in refused(s.store.create_account, "x", "a-good-password", "client")
        assert "no client_id" in refused(s.store.create_account, "x", "a-good-password", "owner", "CL-001")
        assert "one of" in refused(s.store.create_account, "x", "a-good-password", "admin")
        s.store.create_account("dup", "a-good-password", "owner")
        assert "already exists" in refused(s.store.create_account, "dup", "a-good-password", "owner")
        assert refused(s.store.delete_account, "owner") is None, "with two owners, one can go"
        # "dup" is now the only owner left, so it is protected
        assert "last owner" in refused(s.store.delete_account, "dup"), "the last owner is protected"
        s.store.create_account("owner", "a-good-password", "owner")   # put the demo owner back
        assert "No account" in refused(s.store.set_password, "nobody", "a-good-password")
    finally:
        s.close()


def test_changing_or_disabling_an_account_signs_it_out():
    s = Store()
    try:
        token = auth.login("client", "client-demo-2026")["token"]
        s.store.set_password("client", "a-new-password")
        assert _status(auth.require_any_role, f"Bearer {token}") == 401, "the old session ended"
        assert auth.login("client", "client-demo-2026") is None
        fresh = auth.login("client", "a-new-password")
        assert fresh and fresh["client_id"] == "CL-001"

        s.store.set_disabled("client", True)
        assert _status(auth.require_any_role, f"Bearer {fresh['token']}") == 401
        assert auth.login("client", "a-new-password") is None, "a disabled account cannot sign in"
        s.store.set_disabled("client", False)
        assert auth.login("client", "a-new-password")
    finally:
        s.close()


def test_repeated_failures_are_rate_limited():
    s = Store()
    try:
        locked_at = None
        for attempt in range(1, auth.LOCK_AFTER + 5):
            try:
                auth.login("client", "wrong-password")
            except auth.TooManyAttempts as exc:
                locked_at = attempt
                assert exc.retry_after > 0 and "Try again" in str(exc)
                break
        assert locked_at == auth.LOCK_AFTER + 1, f"locked after {locked_at} attempts"
        # the correct password is refused too, so guessing cannot be finished off
        try:
            auth.login("client", "client-demo-2026")
            raise AssertionError("a locked account must refuse even the right password")
        except auth.TooManyAttempts:
            pass
        # another account is unaffected
        assert auth.login("owner", "owner-demo-2026")
    finally:
        s.close()


def test_the_login_route_reports_a_lockout_as_429():
    routes = _routes()
    if routes is None:
        print("        (skipped: FastAPI not installed)")
        return
    s = Store()
    try:
        body = routes.LoginRequest(username="client", password="wrong-password")
        codes = [_status(routes.login, body, None) for _ in range(auth.LOCK_AFTER + 1)]
        assert codes[0] == 401 and codes[-1] == 429, codes
        assert _status(routes.login, routes.LoginRequest(username="owner", password="owner-demo-2026"), None) == 200
    finally:
        s.close()


def test_demo_accounts_are_seeded_once_and_can_be_turned_off():
    # a changed password is not undone by a restart
    s = Store()
    try:
        s.store.set_password("owner", "changed-on-purpose")
        s.restart()
        assert auth.login("owner", "owner-demo-2026") is None, "seeding must not run again"
        assert auth.login("owner", "changed-on-purpose")
    finally:
        s.close()

    # and can be skipped entirely
    os.environ["DEMO_ACCOUNTS"] = "off"
    try:
        s = Store()
        assert s.store.list_accounts() == []
        assert auth.login("owner", "owner-demo-2026") is None
        s.close()
    finally:
        os.environ.pop("DEMO_ACCOUNTS", None)

    # the seeded passwords can come from the environment
    os.environ["DEMO_OWNER_PASSWORD"] = "from-the-environment"
    try:
        s = Store()
        assert auth.login("owner", "from-the-environment")
        assert auth.login("owner", "owner-demo-2026") is None
        s.close()
    finally:
        os.environ.pop("DEMO_OWNER_PASSWORD", None)


def test_many_requests_at_once_do_not_break_the_session():
    """The dashboard opens a dozen requests at once and a real server answers
    them on different threads. One shared sqlite3 connection raises
    InterfaceError if two threads touch it together, which looked to the browser
    like the session being rejected - so every query must hold the lock."""
    import threading
    s = Store()
    # A short switch interval makes the interleaving that a real server produces
    # happen reliably here; without it this test passes even when the lock is
    # missing, which is how the bug reached a running machine in the first place.
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        token = auth.login("owner", "owner-demo-2026")["token"]
        header = f"Bearer {token}"
        errors: list = []
        results: list = []

        def reader():
            for _ in range(300):
                try:
                    results.append(auth.require_any_role(header)["username"])
                except Exception as exc:                        # noqa: BLE001
                    errors.append(f"read {type(exc).__name__}: {exc}")

        def writer():
            for _ in range(150):
                try:
                    s.store.logout("not-a-real-token")          # a write
                    auth.login("client", "wrong-password")      # a write + a read
                    s.store.list_accounts()                     # a read
                except auth.TooManyAttempts:
                    pass                                        # expected after 5 failures
                except Exception as exc:                        # noqa: BLE001
                    errors.append(f"write {type(exc).__name__}: {exc}")

        threads = ([threading.Thread(target=reader) for _ in range(16)]
                   + [threading.Thread(target=writer) for _ in range(4)])
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, f"{len(errors)} failures under load, e.g. {errors[0]}"
        assert len(results) == 16 * 300 and set(results) == {"owner"}
    finally:
        sys.setswitchinterval(previous)
        s.close()


# ---------------------------------------------------------------------------
def test_severity_rules_can_be_configured():
    from backend.services import output_view
    path = Path(tempfile.mkdtemp()) / "config.json"
    try:
        # Severity is driven by `deviation` - how far outside its own normal
        # range the device sat - not by the anomaly ratio, which sits just
        # above the threshold for every fault and so carries no magnitude.
        # The ratio is still passed and reported, as context.
        assert settings.reload()["severity"]["high_sigma"] == 45.0
        assert output_view.severity(1.3, 1, False, deviation=5.0)["level"] == "low"
        assert output_view.severity(1.3, 1, False, deviation=20.0)["level"] == "medium"
        assert output_view.severity(1.3, 1, False, deviation=50.0)["level"] == "high"
        assert output_view.severity(1.3, 120, True, deviation=20.0)["level"] == "critical", \
            "medium, +1 for a shared profile, +1 for not being resolved"

        path.write_text(json.dumps({"severity": {"high_sigma": 30.0, "medium_sigma": 12.0,
                                                 "shared_profile_raises": False}}))
        os.environ["DASHBOARD_CONFIG"] = str(path)
        settings.reload()
        assert output_view.severity(1.3, 1, False, deviation=35.0)["level"] == "high", \
            "the new threshold applies"
        assert output_view.severity(1.3, 120, False, deviation=35.0)["level"] == "high", \
            "sharing no longer raises it"
        verdict = output_view.severity(1.3, 1, False, deviation=35.0)
        assert verdict["rules"]["high_sigma"] == 30.0, "the verdict says which rules were applied"
        assert verdict["deviation_sigma"] == 35.0, "the verdict reports what it judged"

        # an environment variable wins over the file
        os.environ["SEVERITY_HIGH_SIGMA"] = "90"
        settings.reload()
        assert output_view.severity(1.3, 1, False, deviation=35.0)["level"] == "medium"
        os.environ.pop("SEVERITY_HIGH_SIGMA")

        # nonsense is reported and the defaults are kept, rather than crashing
        path.write_text("{ not json")
        settings.reload()
        assert settings.severity_rules()["high_sigma"] == 45.0 and settings.problems()
        path.write_text(json.dumps({"severity": {"high_sigma": 10.0, "medium_sigma": 30.0}}))
        settings.reload()
        assert settings.severity_rules()["high_sigma"] == 45.0, "medium above high falls back"
        path.write_text(json.dumps({"severity": {"high_sigma": "abc"}, "_comment": "ignored"}))
        settings.reload()
        assert settings.severity_rules()["high_sigma"] == 45.0
        assert not any("_comment" in p for p in settings.problems()), "a comment key is not a problem"
    finally:
        os.environ.pop("DASHBOARD_CONFIG", None)
        os.environ.pop("SEVERITY_HIGH_SIGMA", None)
        shutil.rmtree(path.parent, ignore_errors=True)
        settings.reload()


# ---------------------------------------------------------------------------
def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {exc}")
        except Exception as exc:                          # noqa: BLE001
            failures += 1
            print(f"  ERROR {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests)-failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
