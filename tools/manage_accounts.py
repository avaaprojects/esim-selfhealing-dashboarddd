"""Add, list, change and remove dashboard accounts.

    python tools/manage_accounts.py list
    python tools/manage_accounts.py add alice --role client --client CL-001
    python tools/manage_accounts.py add ops2 --role owner
    python tools/manage_accounts.py passwd alice
    python tools/manage_accounts.py disable alice      # keeps the account, ends its sessions
    python tools/manage_accounts.py enable  alice
    python tools/manage_accounts.py remove  alice

Accounts live in backend/data/accounts.db (or `ACCOUNTS_DB_PATH`). Passwords are
never stored or printed: they are read without echo (or from `--password` /
`DASHBOARD_NEW_PASSWORD` for scripted setup) and kept as a salted PBKDF2 hash.

A client account needs the `client_id` of the company whose data it may see -
`list --companies` shows them. An owner account sees every company.

Changing or disabling an account signs that account out everywhere.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.services import auth                                       # noqa: E402


def _ask_password(args, *, confirm: bool = True) -> str:
    supplied = args.password or os.environ.get("DASHBOARD_NEW_PASSWORD")
    if supplied:
        return supplied
    if not sys.stdin.isatty():
        raise SystemExit("error: no terminal to read a password from; pass --password or set DASHBOARD_NEW_PASSWORD")
    first = getpass.getpass("New password: ")
    if confirm and first != getpass.getpass("Repeat password: "):
        raise SystemExit("error: the passwords do not match")
    return first


def _companies():
    try:
        from backend.services import datasets
        return {c["client_id"]: c["name"] for c in datasets.registry()["clients"]}
    except Exception:                                                   # noqa: BLE001
        return {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="show the accounts")
    p.add_argument("--companies", action="store_true", help="also list the companies a client can belong to")

    p = sub.add_parser("add", help="create an account")
    p.add_argument("username")
    p.add_argument("--role", choices=auth.ROLES, required=True)
    p.add_argument("--client", dest="client_id", help="client_id for a client account (see: list --companies)")
    p.add_argument("--password", help="avoid on a shared machine; prefer the prompt")

    p = sub.add_parser("passwd", help="change a password (signs that account out)")
    p.add_argument("username")
    p.add_argument("--password")

    for name, help_text in (("disable", "keep the account but refuse sign-in"),
                            ("enable", "allow sign-in again"),
                            ("remove", "delete the account")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("username")

    args = ap.parse_args()
    store = auth.get_store()

    try:
        if args.cmd == "list":
            rows = store.list_accounts()
            if not rows:
                print("no accounts yet")
            width = max((len(r["username"]) for r in rows), default=8)
            for r in rows:
                when = datetime.fromtimestamp(r["updated_at"], timezone.utc).strftime("%Y-%m-%d")
                print(f"  {r['username']:<{width}}  {r['role']:<6}  {r['client_id'] or '(all companies)':<16}"
                      f"  updated {when}{'  DISABLED' if r['disabled'] else ''}")
            if args.companies:
                print("\ncompanies:")
                for cid, name in sorted(_companies().items()):
                    print(f"  {cid}  {name}")
            return 0

        if args.cmd == "add":
            account = store.create_account(args.username, _ask_password(args), args.role, args.client_id)
            where = _companies().get(account["client_id"], account["client_id"]) if account["client_id"] else "every company"
            print(f"created {account['username']} ({account['role']}, {where})")
            return 0

        if args.cmd == "passwd":
            store.set_password(args.username, _ask_password(args))
            print(f"password changed for {args.username}; its sessions were ended")
            return 0

        if args.cmd in ("disable", "enable"):
            store.set_disabled(args.username, args.cmd == "disable")
            print(f"{args.username} is now {'disabled' if args.cmd == 'disable' else 'enabled'}")
            return 0

        if args.cmd == "remove":
            store.delete_account(args.username)
            print(f"removed {args.username}")
            return 0
    except auth.AccountError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
