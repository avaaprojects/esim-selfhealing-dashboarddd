# Code review notes

I read every line of all 13 core modules (`act.py`, `config.py`, `crypto_pqc.py`,
`learn.py`, `memory_store.py`, `monitor.py`, `orchestrator.py`, `plan.py`,
`reason.py`, `rsp_api.py`, `safety.py`, `schemas.py`, `telemetry.py`), plus
`test_smoke.py` and all six `uc*.py` use-case scripts. Findings below, in the
order I'd prioritise them.

## 1. Structural bug (blocks everything) — FIXED

As delivered in the zip, every file sat flat in one folder. But every module
uses **relative package imports** (`from .schemas import ...`, `from .config
import ...`), and the use-case / test files import an **absolute package**
`esim_selfhealing` and insert `parents[1]` onto `sys.path`:

```python
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from esim_selfhealing.act import Actuator
```

That only resolves if the layout is:

```
repo_root/
    esim_selfhealing/      <- the 13 core modules + __init__.py
    usecases/               <- uc1..uc6
    tests/
        test_smoke.py
```

Flattened into one folder, **every single import fails**
(`ModuleNotFoundError: No module named 'esim_selfhealing'`), so nothing in
the zip runs at all as-is.

**Fix applied:** restored the folder layout above (see this delivery). Also
added an empty `esim_selfhealing/__init__.py` so it's an importable package
in every Python version (harmless even where implicit namespace packages
would work).

## 2. Real functional bug: broken reproducibility in `learn.py` — FIXED

`RemediationEnv.rollout(policy, n, rng)`, `.evaluate(policy, n)`, and
`.action_mix(policy, n)` all *look* like they take/create a seeded
`np.random.Generator` for reproducible results. In practice:

* `sample_state()` (the fault + belief draw) always used `self.rng` — the
  environment's own instance-level generator — and ignored any `rng` passed
  or created by the caller.
* `step()` (the "did the action resolve the fault" draw) had the same
  problem.
* In `evaluate()` and `action_mix()`, the local `rng = np.random.default_rng(...)`
  was **dead code** — created, never used.

Net effect: `evaluate()` and `action_mix()` gave different numbers across
runs depending on how much unrelated code had already pulled from
`self.rng` (e.g. earlier training rollouts). This directly contradicts
`uc5_learn_ppo_lagrangian.py`'s own comment on its two training runs:

> "same rollouts, same seeds - the only difference is the constraint"

I verified this concretely: running one unrelated `rollout()` call before
`evaluate()` changed `evaluate()`'s reported mean reward / violation rate /
resolution rate, with everything else identical.

**Fix applied:** `sample_state()` and `step()` now accept an optional `rng`
and use it when supplied (falling back to `self.rng` only if the caller
doesn't pass one); `rollout()`, `evaluate()`, and `action_mix()` now pass
their generator through consistently. Verified with a before/after
reproducibility test — after the fix, `evaluate()` gives byte-identical
results regardless of prior random draws elsewhere. All labelled inline
with `# BUGFIX (labelled)` comments at each change site in `learn.py`.

## 3. Minor cleanup (not a bug): redundant import in `reason.py`

`AnthropicReActLLM.__init__` imported the `anthropic` package twice — once
inside the `try/except ImportError` availability check, once again
immediately after. Harmless (Python caches modules), but redundant. Removed
the second import and left a labelled comment explaining why.

## What I did *not* need to change

* All dataclasses/schemas (`schemas.py`) are consistent and match how every
  other module uses them.
* The statistics in `monitor.py` (Wilson-Hilferty chi-square quantile,
  incomplete-gamma CDF, Acklam's inverse-normal approximation) are
  mathematically standard implementations and check out.
* `crypto_pqc.py`, `safety.py`, `act.py`, `rsp_api.py`, `plan.py`,
  `memory_store.py`, `config.py`, `telemetry.py`, `orchestrator.py` ran
  clean: no syntax errors, no bare excepts, no mutable-default-argument
  bugs, no off-by-one issues found after tracing every call site.

## 4. Full labelling pass (every file, not just the fixed ones)

Every `class`, top-level `def`, and method in all 13 core modules, all 6
`usecases/uc*.py` scripts, and `tests/test_smoke.py` now has a one-line
label comment directly above it, in a consistent format:

```python
# ---- CLASS: Monitor ----
class Monitor:
    ...
    # ---- METHOD: Monitor._state ----
    def _state(self, key, x):
        ...

# ---- FUNCTION: build_default_orchestrator ----
def build_default_orchestrator(...):
    ...
```

Methods are labelled `ClassName.method_name` so it's unambiguous which
class a method belongs to when scanning the file. This is on top of (not a
replacement for) the existing docstrings and inline comments, which were
left untouched. Labels sit above decorators (`@staticmethod`, `@property`,
`@dataclass`, etc.) rather than between the decorator and the definition.

Applied and verified compile-clean + test-clean on every file:

| File | classes/functions/methods labelled |
|---|---|
| `act.py` | 14 |
| `config.py` | 7 |
| `crypto_pqc.py` | 15 |
| `learn.py` | 30 |
| `memory_store.py` | 16 |
| `monitor.py` | 17 |
| `orchestrator.py` | 17 |
| `plan.py` | 15 |
| `reason.py` | 25 |
| `rsp_api.py` | 16 |
| `safety.py` | 9 |
| `schemas.py` | 17 |
| `telemetry.py` | 11 |

plus every `usecases/uc*.py` script and `tests/test_smoke.py`.

## Verification performed

```
python3 -m py_compile <every file>        # no syntax errors
pytest tests/test_smoke.py -q             # 17/17 pass
python3 usecases/uc1_..6_*.py             # all 6 run to completion, exit 0
```

plus a standalone script proving the `learn.py` reproducibility fix (shown
above) — before the fix, `evaluate()` results diverged after an unrelated
`rollout()` call; after the fix, they are identical.

# Client reports rework

The one-way, in-memory mailbox was replaced by a persistent SENT → ACKNOWLEDGED → RESOLVED
system with a client-side "My reports" view, an owner inbox with responses, and two-way links
to devices and incidents. See "Client reports" in README_DASHBOARD.md.

# Input / configuration screen

A dedicated owner-only Input screen, `data/` (predefined synthetic datasets, generated reproducibly by
`tools/generate_datasets.py`) and `uploads/` (operator files) as separate folders, and a server-side input
state with provenance and an immutable PROCESS DATA snapshot. The output screens are unchanged.

# Output / gated self-healing screen

Process data now runs the committed input through the real loop and opens a separate full-screen Output page
(flow strip, incident, agent, safety gate, remediation, recovery) with Back to input. Additive changes to the core:
`Monitor.reset_device`. Recovery is judged on eUICC state captured before and after the action, because a stored
recording keeps replaying the fault afterwards.

# One connected system

Joined the registry and the live agent session on the eUICC id (`backend/services/estate.py`): incidents, devices
and report snapshots now carry the same `context` (client, group, device label, ICCID, profile, network) the Input
and Output screens use, plus the `run` that opened each incident. Output now hands off to Incidents / reasoning /
Devices / a new client report; Incidents links back to the run's Output. Sidebar regrouped by the flow for both roles.

# Full-flow test pass

Tested the whole dashboard end to end and fixed what it turned up:

* **Stale self-healing output.** The Output result outlived the agent session that produced it, so after a backend
  restart or "Reset session" the screen showed the previous session's incident as if it were current. Signing out,
  losing a session and resetting now drop the result and the other session-scoped selections (`useOutput.reset()`).
* **Race when opening the Output for one incident.** `select(id)` and the screen's own first load could both be in
  flight and the unfocused one could win, showing a different run. Loads are now ticketed so only the newest writes,
  and `select` leaves `idle` synchronously (new `loading` phase).
* **No recovery from a rejected session.** A 401 threw but nothing signed the operator out, leaving every screen
  erroring with no way back. `api.js` now reports a rejected session once and the app returns to sign-in with
  "Your session ended. Please sign in again." Sign-in failures are unaffected.
* Polish: "Live monitor" no longer shares the Real-time data icon.

# Robustness pass

* `pages/Overview.jsx`: the status panels were spread into `StatusCard` including a payload field named `key`, which
  React warns about (and reserves). It is now passed as a real key. This was the one console warning the app emitted.
* Checked with the TypeScript compiler that no frontend file references an undefined name or imports a member the
  target module does not export (covers branches the click-through tests do not reach).
* Verified route access control: every route sits behind the sign-in guard; owner-only routes return 403 to a client;
  invalid or logged-out tokens return 401.
* Browser-tested the Input upload area with real files (PNG, CSV, log), hostile files (an executable renamed .png, a
  binary renamed .csv), separation from the stored datasets, persistence across reload, removal, and that attached
  uploads never change what the run processes.

# The client works on their own record

* Input, Process data and Output are now open to the client role, scoped to one company (`services/tenancy.py`;
  the demo `client` belongs to CL-001). Scenario runs are open to clients too; Reset session and the live-feed
  controls stay owner-only.
* Server-side scoping of incidents, devices, telemetry (including the session-wide stream), reasoning trace,
  memory, safety, output, runs and uploads. Another company's item is a 404; a client with no company sees nothing.
* Fixed: the app kept the previous account's data in memory after sign-out; it now remounts on sign-out.
* Tests: `tests/test_tenancy.py` (7), and the browser flow of a client entering their record.

# Your own CSV, and per-account counters

* New data source "Your own CSV" (`services/custom_input.py`): run the agent on an uploaded telemetry CSV, strictly
  validated, tied to one device, re-hashed at processing time, labelled OPERATOR UPLOAD end to end. Tests:
  `tests/test_custom_input.py` (8) and a browser flow.
* Fixed: the "a client cannot start the live feed" rule also blocked the new upload source.
* The sidebar/Overview counters, memory and audit counts now describe what the signed-in account can see.

# Importing your own data as a stored dataset

* `tools/import_dataset.py`: copy a validated telemetry CSV into data/telemetry/ and register it in the manifest
  with a fresh checksum (`--replace` to overwrite one). Hand-dropping a file is still refused as "modified".
* A stored dataset now carries its own provenance: an imported one reads OPERATOR IMPORT (amber), not
  SYNTHETIC / SIMULATED (violet), on the Input banner, the provenance table and the Output summary.
* Tests: 2 more in tests/test_custom_input.py (10 total), covering the tamper check, a refused bad file,
  an unknown device, --replace, and that an import is never presented as this project's synthetic data.

# Accounts, settings and Docker

* Accounts moved out of the source into SQLite (`backend/data/accounts.db`): salted PBKDF2 password hashes,
  sessions that survive a restart, any number of accounts per company, disable/enable, and a protected last owner.
  `tools/manage_accounts.py` manages them; the demo pair is seeded once and can be re-passworded or turned off
  (`DEMO_ACCOUNTS=off`).
* Failed sign-ins are rate-limited per account and per address (5 in 5 minutes -> locked for 5); the route answers
  429 with Retry-After and the sign-in screen shows the wait.
* `config.json` / environment for deployment settings; the severity thresholds and both escalations are now
  configurable, and the verdict carries the rules applied (`services/settings.py`).
* Dockerfile, docker-compose.yml and .dockerignore: one container, frontend built and served by the backend,
  volumes for the stores and uploads.
* Tests: `tests/test_accounts.py` (10) and a browser sign-in suite (9). Test harnesses now isolate the accounts
  store, so running the tests no longer leaves a real accounts.db in the project.

# Run self-healing opens the Output screen

"Run self-healing" (and a scenario's own Run button) now behaves like Process data: the stage animation, then the
full-screen result. Scenario runs are recorded in the session as runs (`RUN-nnnn`) alongside processed inputs, and
`output_view.scenario_commit` gives them the commit-shaped record the Output screen needs, labelled
SYNTHETIC / SIMULATED and naming the client and device from the registry. The Output's back button reads
"Back to dashboard" for a scenario run, since there is no input selection to return to.
Tests: 1 more in tests/test_output.py (10) and a browser suite (13).

# Run self-healing names its scenario

The Output's Input panel printed `dataset_path`, which a built-in scenario does not have, leaving the row blank
after "Run self-healing". It now shows the scenario's name and says the telemetry was generated for that run.

# Fix: concurrent requests could reject a valid session

The account store shared one sqlite3 connection and guarded only its writes. A real server answers the dashboard's
dozen parallel requests on separate threads, so two of them could touch the connection at once and sqlite3 raised
`InterfaceError: bad parameter or other API misuse` — which surfaced as 401s on every screen and "Your session
ended. Please sign in again." Every query, read as well as write, now holds the lock (`_fetchone` / `_fetchall`).
`tests/test_accounts.py` gained a load test that fails without the lock (it shortens the thread switch interval,
because the default one hides the race — which is why the earlier single-threaded tests missed it).

# Uploading without Git

`tools/upload_to_github.py` uploads the project to a GitHub repository over the REST API using only the standard
library (no git, nothing to pip install), in one commit, skipping .venv / node_modules / __pycache__ / dist /
backend/data / operator uploads. `--dry-run` lists what would go up. `.gitignore` now also covers .venv.

# Field specification: what may be randomised

* `esim_selfhealing/fields.py` records every field's unit, physical limits, typical healthy range, and whether a
  generator may vary it at all - with a stated reason for each field that may not (identities, check digits,
  tenancy, blast radius, time ordering, provenance).
* Fixed: `rsrp_dbm` and `latency_ms` had no physical bounds in the generator, so noise could produce a positive
  RSRP (impossible) or a sub-millisecond latency. The generator now clamps every channel from the spec.
* Uploaded CSVs are refused if they carry physically impossible values, with the unit named in the message
  (`aka_fail_rate` is per 100 attach attempts, not a 0..1 fraction - the name is the trap). Values that are
  possible but outside the healthy range are reported as notes instead.
* `tools/check_fields.py` prints the audit and checks a CSV against it. Tests: `tests/test_fields.py` (6).

# Real measured data

`tools/import_real_trace.py` converts a public radio trace (Irish 5G dataset, ULR-MC5G, Lumos-5G, or a phone
recording) into a dashboard dataset, detecting the column layouts those traces use, and prints a MEASURED /
MODELLED / SYNTHETIC provenance line per channel rather than claiming the whole file is real. Channels absent from
the trace are derived from the measured RSRP by a stated relationship, with enough independent noise to land at the
0.3..0.7 correlation field studies report instead of a tell-tale 0.95. Verified end to end: the converted trace is
detected, diagnosed (SM-DP+ session outage), auto-dispatched and recovered by the real agent.
