"""Upload this project to a GitHub repository, without installing Git.

    python tools/upload_to_github.py --repo YOUR-USERNAME/esim-selfhealing-dashboard
    python tools/upload_to_github.py --repo you/name --dry-run     # list, upload nothing

Uses GitHub's REST API over the standard library only: nothing to `pip install`
and no `git` on the machine. It makes ONE commit containing every file, the same
result a `git push` would give, so Render (see DEPLOY.md) can build from it.

You need a personal access token: GitHub -> Settings -> Developer settings ->
Personal access tokens -> Tokens (classic) -> Generate new token, tick the
`repo` scope. Paste it when asked, or set GITHUB_TOKEN. The token is never
written to disk or printed, and is not stored in the repository.

What is skipped (never uploaded): .venv, node_modules, __pycache__, .git,
frontend/dist, backend/data (runtime databases), uploaded operator files, and
anything else listed in SKIP_DIRS / SKIP_NAMES below. Everything the deploy
needs is included.

Re-running updates the repository: files you changed are committed, and files
you deleted locally are removed there too, because the whole tree is replaced.
"""

from __future__ import annotations

import argparse
import base64
import fnmatch
import getpass
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.github.com"

#: never uploaded - big, machine-specific, or runtime state
SKIP_DIRS = {".venv", "venv", "env", "node_modules", "__pycache__", ".git",
             ".idea", ".vscode", ".pytest_cache", ".ruff_cache", "dist", "build"}
SKIP_NAMES = {".DS_Store", "Thumbs.db"}
SKIP_GLOBS = ("*.pyc", "*.pyo", "*.log", "*.db", "*.db-wal", "*.db-shm", "*.sqlite3")
#: paths (relative, posix style) whose *contents* are runtime state
SKIP_PREFIXES = ("backend/data/", "uploads/screenshots/", "uploads/csv/", "uploads/logs/")
#: ...except these, which keep the empty folders in place
KEEP_ANYWAY = (".gitkeep", "uploads/README.md")

MAX_FILE_BYTES = 25 * 1024 * 1024


class GitHubError(RuntimeError):
    pass


def _request(method: str, url: str, token: str, body: Optional[Dict[str, Any]] = None) -> Any:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "esim-dashboard-uploader")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read()).get("message", "")
        except Exception:                                        # noqa: BLE001
            pass
        if exc.code == 401:
            raise GitHubError("GitHub rejected the token (401). Check it is correct and has the `repo` scope.")
        if exc.code == 404:
            raise GitHubError(
                "Not found (404). Check the --repo spelling (owner/name), that the repository exists, "
                "and that the token has the `repo` scope.")
        if exc.code == 403 and "rate limit" in detail.lower():
            raise GitHubError("GitHub rate limit reached. Wait a few minutes and run it again.")
        raise GitHubError(f"GitHub said {exc.code}: {detail or exc.reason}")
    except urllib.error.URLError as exc:
        raise GitHubError(f"Could not reach GitHub: {exc.reason}. Check your internet connection.")


def _skipped(rel: str, name: str) -> bool:
    if any(rel == k or rel.endswith("/" + k) or name == k for k in KEEP_ANYWAY):
        return False
    if name in SKIP_NAMES or any(fnmatch.fnmatch(name, g) for g in SKIP_GLOBS):
        return True
    return any(rel.startswith(p) for p in SKIP_PREFIXES)


def collect(root: Path) -> List[Path]:
    """Every file to upload, as paths relative to `root`."""
    out: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if not _skipped(rel, name):
                out.append(path)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, metavar="OWNER/NAME",
                    help="the GitHub repository to upload into, e.g. alice/esim-selfhealing-dashboard")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--message", default="eSIM self-healing dashboard")
    ap.add_argument("--dry-run", action="store_true", help="list what would be uploaded, then stop")
    args = ap.parse_args()

    if args.repo.count("/") != 1 or not all(args.repo.split("/")):
        print("error: --repo must look like OWNER/NAME, e.g. alice/esim-dashboard", file=sys.stderr)
        return 2

    files = collect(ROOT)
    total = sum(f.stat().st_size for f in files)
    print(f"{len(files)} files, {total / 1024:.0f} KB from {ROOT}")
    oversized = [f for f in files if f.stat().st_size > MAX_FILE_BYTES]
    if oversized:
        print("error: too large to upload: " + ", ".join(str(f.relative_to(ROOT)) for f in oversized), file=sys.stderr)
        return 1
    by_top: Dict[str, int] = {}
    for f in files:
        rel = f.relative_to(ROOT).as_posix()
        by_top[rel.split("/")[0] if "/" in rel else "(root files)"] = by_top.get(
            rel.split("/")[0] if "/" in rel else "(root files)", 0) + 1
    for name, count in sorted(by_top.items()):
        print(f"    {name:<22} {count}")
    if args.dry_run:
        print("\ndry run: nothing was uploaded")
        return 0

    token = os.environ.get("GITHUB_TOKEN") or getpass.getpass("GitHub token (hidden): ").strip()
    if not token:
        print("error: no token given", file=sys.stderr)
        return 2

    who = _request("GET", f"{API}/user", token).get("login", "?")
    repo = _request("GET", f"{API}/repos/{args.repo}", token)
    print(f"signed in as {who}; uploading to {repo['full_name']}")

    # the branch may not exist yet (a brand-new, empty repository)
    parent = None
    try:
        ref = _request("GET", f"{API}/repos/{args.repo}/git/ref/heads/{args.branch}", token)
        parent = ref["object"]["sha"]
    except GitHubError:
        print(f"branch '{args.branch}' does not exist yet; it will be created")

    print("uploading files...")
    tree: List[Dict[str, Any]] = []
    for i, path in enumerate(files, 1):
        rel = path.relative_to(ROOT).as_posix()
        blob = _request("POST", f"{API}/repos/{args.repo}/git/blobs", token, {
            "content": base64.b64encode(path.read_bytes()).decode("ascii"),
            "encoding": "base64",
        })
        tree.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        if i % 20 == 0 or i == len(files):
            print(f"    {i}/{len(files)}")

    new_tree = _request("POST", f"{API}/repos/{args.repo}/git/trees", token, {"tree": tree})
    commit = _request("POST", f"{API}/repos/{args.repo}/git/commits", token, {
        "message": args.message, "tree": new_tree["sha"],
        **({"parents": [parent]} if parent else {}),
    })
    if parent:
        _request("PATCH", f"{API}/repos/{args.repo}/git/refs/heads/{args.branch}", token,
                 {"sha": commit["sha"], "force": False})
    else:
        _request("POST", f"{API}/repos/{args.repo}/git/refs", token,
                 {"ref": f"refs/heads/{args.branch}", "sha": commit["sha"]})

    print(f"\ndone: {len(files)} files committed to {args.repo} ({args.branch})")
    print(f"    https://github.com/{args.repo}")
    print("\nNext: render.com -> New -> Blueprint -> pick this repository (see DEPLOY.md).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GitHubError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("\ncancelled", file=sys.stderr)
        raise SystemExit(130)
