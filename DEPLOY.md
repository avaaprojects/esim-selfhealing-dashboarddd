# Putting the dashboard on a public link

The dashboard is a Python backend plus a built frontend, so it needs somewhere that
runs a container — not static hosting. The `Dockerfile` builds both into one image
that serves everything on one port.

Two ways, depending on how long the link needs to live.

---

## Getting the code onto GitHub without installing Git

`tools/upload_to_github.py` does it with the standard library, so there is
nothing to install:

1. On GitHub: **+** -> **New repository**, name it, leave it empty (no README).
2. Make a token: **Settings -> Developer settings -> Personal access tokens ->
   Tokens (classic) -> Generate new token**, tick the **repo** scope, copy it.
3. From the project folder:

       python tools/upload_to_github.py --repo YOUR-USERNAME/YOUR-REPO --dry-run
       python tools/upload_to_github.py --repo YOUR-USERNAME/YOUR-REPO

   The dry run lists what would go up and uploads nothing. The real run asks for
   the token (it is not shown as you type, not saved, and not committed).

It skips `.venv`, `node_modules`, `__pycache__`, `frontend/dist`, the runtime
databases in `backend/data/` and any operator uploads, so only the project goes
up. Run it again after changes: it replaces the whole tree in one commit, so
files you deleted locally are removed there too.

If you do have Git, `git init && git add . && git commit -m "..." && git push`
is equivalent — but add `.venv/` to `.gitignore` first.

## A. A permanent link (Render free plan)

1. Put this project in a **GitHub** repository (public or private) — see above.
2. Sign up at <https://render.com> — the free plan needs no card.
3. **New → Blueprint**, pick the repository. Render reads `render.yaml` and builds
   the `Dockerfile`.
4. When it asks for `DEMO_OWNER_PASSWORD` and `DEMO_CLIENT_PASSWORD`, set your own.
   **Do this before sharing the link** — the ones in the README are published.
5. After the first build you get `https://<name>.onrender.com`. That is the link.

What the free plan means in practice:

* it **sleeps after 15 minutes** of no traffic, and the next visit takes about a
  minute to wake. Warn whoever you send it to, or open it yourself a minute before;
* **750 instance-hours a month**, which is roughly one service running continuously;
* **no persistent disk**: `backend/data` is rebuilt whenever the service restarts, so
  reports, uploads and any accounts you created are lost and the demo accounts are
  recreated from the passwords you set. Fine for a demo; add a paid disk otherwise.

Rebuild on every push: Render redeploys when you push to the connected branch.

## B. A temporary link (your own machine, no account)

Quicker, but it only works while your PC is on and the command is running.

    # terminal 1 — build once, then serve
    cd frontend && npm run build && cd ..
    python -m uvicorn backend.app:app --host 0.0.0.0 --port 8000

    # terminal 2 — a public URL that forwards to it
    cloudflared tunnel --url http://localhost:8000

`cloudflared` prints a `https://something.trycloudflare.com` address. Anyone can open
it until you press Ctrl+C. Install it from Cloudflare's site; `ngrok http 8000` does
the same thing with an account.

---

## Before you share either one

* **Change the demo passwords.** `DEMO_OWNER_PASSWORD` / `DEMO_CLIENT_PASSWORD` are
  read the first time the accounts store is created. On a machine that has already
  run the dashboard, use `python tools/manage_accounts.py passwd owner` instead.
* **Make an account for the person you are sending it to**, rather than handing over
  the owner login:

      python tools/manage_accounts.py add professor --role client --client CL-001

* The dashboard is a **simulation** and says so on screen; there is nothing
  confidential in it. Repeated failed sign-ins are rate-limited, but the link is
  public, so treat it as a demo, not a private system.
