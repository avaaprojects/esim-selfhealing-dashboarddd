# Autonomous Recovery Agent — dashboard

A frontend and API layer for the self-healing eSIM/eUICC project.

The core was originally left byte-identical, and that is no longer true: the
realism and detector work described in **[REALISM.md](REALISM.md)** changed
`monitor.py`, `telemetry.py`, `config.py`, `schemas.py`, `fields.py` and
`orchestrator.py`, and added `real_profiles.py`. Each change is documented
there with the measurement that motivated it. The additions are the
two-timescale drift test, the per-device variance floor, post-remediation
recovery, and a severity basis that carries magnitude. The smoke tests and
all six console use cases still pass unchanged.

This revision adds the second half of the brief on top of the already-working
backend: the DEMO ACCESS login screen, the OWNER-only real-time data controls
in the UI, the categorised Simulation catalogue, per-scenario gated-output
display and whole-estate summaries. See **"What changed in this pass"** below
for the exact file list and **"Honesty note on testing"** for what has and has
not been run end-to-end.

---

## Where the numbers come from

The telemetry is generated from environment profiles calibrated against three
public measurement datasets, and every run draws fresh data rather than
replaying one fixed recording — so the health figure, the anomaly scores and
the per-channel ranges move from run to run.

**[REALISM.md](REALISM.md)** documents that work in full: the profiles and
their sources, how each run varies, and the four defects the calibration
exposed in the detector and the severity model (a ramped fault the EWMA could
not see, a variance floor that was fair to only one device, remediation that
changed nothing on screen, and a severity score that could not tell a
120-device outage from a blip).

Reproduce any of it:

```bash
python tools/calibrate_profiles.py     # the profile fit against its targets
python tests/test_real_profiles.py     # assert the generator still matches
python tests/test_acceptance.py        # nothing abnormal across unseeded runs
```

## Run it

You need Python 3.10+ and Node.js 18+.

### 1. Install (once)

```bash
pip install -r requirements.txt
cd frontend && npm install && cd ..
```

### 2. Start the backend

From the project root, in its own terminal:

```bash
python -m uvicorn backend.app:app --reload --port 8000
```

Check it: <http://127.0.0.1:8000/api/health> should return JSON. Interactive API
docs are at <http://127.0.0.1:8000/docs>.

### 3. Start the frontend

In a second terminal:

```bash
cd frontend
npm run dev
```

Open <http://localhost:5173>. You'll land on the access screen — see
**Demo credentials** below.

The Vite dev server proxies `/api` to port 8000, so the browser only ever talks
to one origin and CORS never enters the picture.

### Single-process alternative (this is also how you deploy it publicly)

```bash
cd frontend && npm run build && cd ..
python -m uvicorn backend.app:app --port 8000
```

`backend/app.py` mounts `frontend/dist` when it exists, so the whole dashboard
— login screen, real-time data, simulation, everything — is then served from
one origin, e.g. <http://127.0.0.1:8000>.

---

## Demo credentials (DEMO ACCESS)

Deliberately not shown anywhere in the running dashboard — only here.

| Role | Username | Password |
|---|---|---|
| Client / Viewer | `client` | `client-demo-2026` |
| Owner / Administrator | `owner` | `owner-demo-2026` |

Sessions are opaque bearer tokens with a 12-hour TTL (`backend/services/auth.py`).
This is intentionally simple, as the brief allows for a prototype — but the
role check is enforced **server-side** on every mutating route via
`Depends(auth.require_owner)`, not just hidden in the UI.

---

## What the screens show

| Screen | Source | Client sees it? | Owner-only controls |
|---|---|---|---|
| Overview | 3-column layout: system state, agent core/pipeline, live incidents, DATA SOURCE switch | ✅ | — |
| Real-time data | `RealtimeFeed` status, live charts, live incidents, event feed | ✅ (read-only) | Start / Stop / Inject fault |
| Live monitor | `monitor.readings` — g_t against the chi-square threshold, plus the five real telemetry channels | ✅ | — |
| Incidents | Every incident with its belief distribution, and the full decision trace | ✅ | — |
| Agent reasoning | The recorded ReAct trace: rationale, tool called, arguments, result | ✅ | — |
| Actions | `config.ACTION_PROFILE` + `rsp_api.ENDPOINTS`, and the last ranked plan | ✅ | — |
| Safety | The four clauses of Σ, the operator queue, escalations, the hash-chained audit log | ✅ | — |
| Memory | Incident memory D, retrieval hits with cosine similarity, outcome rates per action | ✅ | — |
| Learning | PPO-Lagrangian: J_R, cost estimates, λ, entropy, action distribution, policy evaluation | ✅ | — |
| Simulation | Six scenarios, grouped into four categories, with gated-output and whole-estate views | ✅ (read-only results) | Run scenario, Reset session |

Telemetry is the actual 5-dimensional feature vector from `schemas.FEATURE_NAMES`
(AKA failure rate, RSRP, session drop rate, OTA latency, OTA failure rate). There
are no CPU or RAM metrics anywhere, because the project does not produce any.

---

## Real-Time Data — how it actually works

`backend/services/realtime.py` runs a background thread (`RealtimeFeed`) inside
the FastAPI process. Every ~1 second it manufactures one `Observation` per live
device (4 devices by default), using the same `NOMINAL_MEAN` / `FAULT_SIGNATURES`
telemetry model the simulation scenarios use, and pushes each one through
`AgentSession.ingest_realtime()` — which calls the *exact same*
`Monitor.update()` → `Orchestrator.handle_incident()` path a simulation run
takes, just one observation at a time instead of a finite in-memory list.
Nothing about MONITOR, REASON, PLAN, SAFETY, ACT, VERIFY or LEARN is
duplicated or special-cased for this mode.

The frontend polls `/api/realtime/status` every 1.5s while the feed is running
(a plain interval, not WebSocket/SSE — simpler and more stable, which the
brief explicitly allows) and re-pulls `/api/status`, `/api/telemetry` and
`/api/incidents` on the same tick, so charts and the incident list visibly
update as samples arrive.

**Honesty note (also in the UI footer and the API's own docstring):** this is
a prototype server/grid telemetry generator running inside this process. It
does **not** connect to a telecom operator, a live cellular network, or
production eSIM infrastructure. "Inject fault" is explicitly labelled
"Simulation / demo only".

---

## Simulation — categories, gated output, whole estate

`backend/services/scenarios.py` assigns each of the six scenarios to one of
four categories (`CATEGORY_ORDER`), and `/api/simulation` returns both the
catalogue and the category metadata. The Simulation screen groups cards under
their category heading instead of a flat list:

| Category | Tests | Scenarios |
|---|---|---|
| Detection & Monitoring | Does MONITOR separate real faults from drift? | ISD-P corruption, Slow radio degradation |
| Diagnosis & Reasoning | Does REASON find the true failure source? | SM-DP+ outage, Key desync |
| Planning & Safety / Gated Output | Does PLAN pick soundly, does SAFETY block correctly? | Shared-profile fleet |
| End-to-End / Whole Estate | Does the full loop hold up across many devices at once? | Unattended night shift |

**Gated output** is rendered with the existing `SafetyGate` component (not a
new fake status pill): for a single-incident run it shows the actual
`admissibility` object from the backend — which clause passed or failed, the
final decision, the endpoint and signature. This is the same component the
Incidents/Reasoning decision trace already used; Simulation now surfaces it
inline on the result card too.

**Whole estate** (`shared_fleet`, `night_shift`): the backend's
`_estate_summary()` reads devices monitored, total samples, incident counts,
and the *registered* fleet size off the RSP client (not a static config value)
to compute blast radius. The new `EstateSummary` component renders this above
the individual incident list, so a multi-device run reads as one estate
outcome first, with the per-incident detail underneath — not as isolated rows.

Client sessions see the identical categorised catalogue and the identical
results (Run/Reset buttons are simply absent); the 403 from the backend is the
actual boundary if a client token is used directly against `/api/simulation/*/run`.

---

## Architecture

```
frontend/src            React + Vite + Tailwind + Recharts + lucide-react
  services/api.js       the only module that knows about HTTP + the bearer token
  hooks/useAgent.js     data fetching + the run state machine
  components/           StatusCard MetricCard AgentCore AgentPipeline IncidentCard
                        DecisionTrace TelemetryChart SafetyGate ActionCard
                        MemoryCard LearningCard ActivityFeed Primitives
                        DataSourceSwitch (NEW) EstateSummary (NEW)
  pages/                Login (NEW) Overview RealTime (NEW) LiveMonitor
                        Incidents Reasoning Actions Safety Memory Learning
                        Simulation
  layouts/              DashboardLayout (sidebar, role badge, sign-out)
        │
        ▼  /api/*  (Authorization: Bearer <token>)
backend/
  app.py                FastAPI app, CORS incl. CORS_ALLOW_ORIGINS, static serving
  api/routes.py         thin HTTP adapters — public_router (health, login) +
                        router (any signed-in role, some routes owner-only)
  services/
    auth.py             demo role-based auth, bearer tokens, require_owner
    realtime.py         RealtimeFeed — background thread, real pipeline
    agent_service.py    owns one live Orchestrator; every read model;
                        ingest_realtime(); _estate_summary(); record_source
    instrumentation.py  captures PlanResult / Command without touching the core
    scenarios.py        the catalogue + CATEGORY_ORDER, built from real
                        TelemetryStreams
    serialization.py    dataclass → JSON
        │
        ▼
esim_selfhealing/       orchestrator, monitor, reason, plan, safety, act,
                        rsp_api, memory_store, learn, telemetry, schemas,
                        config, crypto_pqc, fields, real_profiles
                        (see REALISM.md for what the realism work changed)
```

### API

| Method | Path | Auth | Returns |
|---|---|---|---|
| GET | `/api/health` | public | session state |
| POST | `/api/auth/login` | public | `{token, role, username}` |
| POST | `/api/auth/logout` | any role | `{ok: true}` |
| GET | `/api/auth/me` | any role | `{username, role}` |
| GET | `/api/status` | any role | system panels, counters, `data_source` |
| GET | `/api/telemetry?limit=` | any role | monitor readings |
| GET | `/api/incidents` | any role | incident list, each tagged `source` |
| GET | `/api/incidents/{id}` | any role | full decision trace with the 7 stages |
| GET | `/api/agent/trace?incident_id=` | any role | same, defaulting to the newest |
| GET | `/api/agent/pipeline` | any role | stage metadata + config + fault classes |
| GET | `/api/memory?incident_id=` | any role | D, plus retrieval hits for one incident |
| GET | `/api/learning` | any role | PPO-Lagrangian state |
| GET | `/api/safety` | any role | envelope, gate decisions, audit log |
| GET | `/api/actions` | any role | action space and RSP endpoints |
| GET | `/api/simulation` | any role | scenario catalogue + categories |
| GET | `/api/realtime/status` | any role | feed counters, running state |
| POST | `/api/realtime/start` | **owner** | starts the background feed |
| POST | `/api/realtime/stop` | **owner** | stops it |
| POST | `/api/realtime/inject` | **owner** | `{fault_class, euicc_id?, duration?}` |
| POST | `/api/agent/run` | **owner** | `{scenario, enable_learning}` — runs Algorithm 1 |
| POST | `/api/simulation/{scenario}/run` | **owner** | same, scenario in the path |
| POST | `/api/agent/reset` | **owner** | fresh session |

---

## Two design decisions worth knowing

**The orchestrator is instrumented, not edited.** `LoopRecord` keeps the
incident, diagnosis, act result and latencies, but discards the `PlanResult`,
the retrieved memory neighbours and the signed `Command` — the three things the
decision trace most needs. `backend/services/instrumentation.py` rebinds three
instance methods on one live orchestrator object (`planner.rank`,
`actuator.build_command`, `actuator.execute`), records what passes through, and
returns the originals' results unchanged. No class is subclassed or patched and
no file in `esim_selfhealing/` is touched.

**Each run targets fresh eUICCs.** `Monitor` keys its EWMA baseline and
refractory window per eUICC, so replaying a device it has already adapted to
would legitimately open no incident — clicking the button twice would look
broken. Rather than resetting the detector (which would discard memory D, the
audit chain and the policy), each run increments the device ids. Memory and the
learner keep accumulating across runs, which is what makes those screens
meaningful. Real-time mode uses its own fixed device pool (`CELL-LIVE-1..4`)
so it doesn't collide with simulation device ids.

---

## Scenarios

| Key | Category | What it exercises | Typical outcome |
|---|---|---|---|
| `isdp_corruption` | Detection & Monitoring | drift absorbed, real fault caught | profile re-push, auto-remediated |
| `slow_radio_drift` | Detection & Monitoring | ramped rather than step change | radio diagnosis |
| `smdp_outage` | Diagnosis & Reasoning | looks like a profile fault, isn't | bearer failover |
| `key_desync` | Diagnosis & Reasoning | AKA fails, OTA fine | routed to an operator |
| `shared_fleet` | Planning & Safety / Gated Output | same diagnosis, 120-device profile | blocked on B_max, escalated |
| `night_shift` | End-to-End / Whole Estate | five devices, five failure modes | a mix of all three outcomes |

`shared_fleet` is the one to demo for gated output: identical diagnosis to the
first scenario, but the shared profile pushes blast radius past `B_max = 1`,
PLAN hands ACT an empty feasible set, and it escalates rather than acting —
the Simulation screen shows this as `BLOCKED` / `HUMAN-IN-LOOP` with the exact
failed clause named.

---

## Deploying publicly as one link

1. `cd frontend && npm run build` — produces `frontend/dist`.
2. Set `CORS_ALLOW_ORIGINS` only if you ever serve the frontend from a
   *different* origin than the API (uncommon for this setup — see below).
3. Run `python -m uvicorn backend.app:app --host 0.0.0.0 --port 8000` (or behind
   your platform's process manager / reverse proxy) on the host you're
   deploying to.
4. Because `backend/app.py` mounts `frontend/dist` and serves `index.html` for
   any unmatched path (SPA routing), the same process serves the UI and the
   API from one origin — the browser calls `/api/...` relative to wherever it
   loaded the page from, so **no localhost URL is hardcoded anywhere** and no
   CORS configuration is needed for the normal case.
5. Share the one public URL your host/platform gives that process. Anyone who
   opens it lands on the DEMO ACCESS screen first.

If you ever do split the frontend onto a separate static host (e.g. a CDN)
from the API, set `VITE_API_BASE` at frontend build time to the API's full
origin, and add that frontend origin to `CORS_ALLOW_ORIGINS` (comma-separated)
as an environment variable on the backend process.

---

## What changed in this pass

Building on the already-verified backend (auth.py, realtime.py, the updated
agent_service.py/scenarios.py/routes.py/app.py — all pre-existing and smoke
tested), this pass adds the frontend half:

**New files**
- `frontend/src/pages/Login.jsx` — the CLIENT/OWNER access-choice screen and sign-in form
- `frontend/src/pages/RealTime.jsx` — the Real-Time Data screen (status, start/stop, inject, live charts, live incidents, event feed)
- `frontend/src/components/DataSourceSwitch.jsx` — the REAL-TIME / SIMULATED toggle shown on Overview
- `frontend/src/components/EstateSummary.jsx` — the whole-estate card

**Modified files**
- `frontend/src/services/api.js` — bearer-token storage/injection; `login`/`logout`/`me`; `realtimeStatus`/`Start`/`Stop`/`Inject`
- `frontend/src/App.jsx` — auth gating and session restore; polling loop while real-time is running; wiring for the new screen and handlers
- `frontend/src/layouts/DashboardLayout.jsx` — added the Real-time nav entry, role badge, sign-out control, hid owner-only controls (Run self-healing) from client sessions
- `frontend/src/pages/Simulation.jsx` — grouped scenarios by category, added the gated-output (`SafetyGate`) display and `EstateSummary` to each result, hid Run/Reset for client sessions

Everything under `esim_selfhealing/` and `backend/` is untouched by this pass.

---

## Honesty note on testing

This sandbox has no outbound network access (`npm install` / `pip install`
both hit a 403 from the egress proxy), so I could not run `npm run build`,
`npm run dev`, or start `uvicorn` end-to-end here to verify the frontend
changes in a real browser. What I did do:

- Read every existing component/page/hook before writing new code, to match
  the established prop shapes, Tailwind classes, and data contracts exactly
  (e.g. `SafetyGate`'s `admissibility`/`command` props, `Panel`/`Field`/`Badge`
  from `Primitives.jsx`, the `loop_record_dict` JSON shape from
  `serialization.py`).
- Verified every new/edited `.jsx`/`.js` file has balanced braces/parens/brackets.
- Cross-checked every new API call in `api.js` against the exact route paths
  and request/response shapes in `backend/api/routes.py` and
  `backend/services/agent_service.py`.

What I could **not** do here, and what you should run once before your demo:

```bash
cd frontend && npm install && npm run build   # catches any remaining typo
python -m uvicorn backend.app:app --port 8000  # then open the built app
```

Please run through the checklist below once against a live instance; if
anything doesn't render, the most likely spot is a prop name mismatch in one
of the four new-or-rewritten frontend files listed above, not the backend.

**Test checklist**
1. Login as `client` → confirm read-only Overview, no Run/Reset/Start-Stop buttons.
2. Login as `owner` → confirm all controls are present.
3. Owner: Data Source → Real-Time Data → Start → watch samples/timestamp climb.
4. Owner: Inject fault → confirm an incident appears and the pipeline/safety trace is real (not placeholder text).
5. Owner: Stop real-time data.
6. Owner: Simulation → run each of the six scenarios once; confirm each lands in its stated category.
7. Confirm `shared_fleet` shows a BLOCKED/escalated gated-output card.
8. Confirm `night_shift` shows the EstateSummary card above its incident list.
9. Check Incidents/Reasoning/Actions/Safety/Memory/Learning still populate as before.
10. `npm run build` then serve via uvicorn; confirm the built app loads at `/`.

---

## Demo script (2–3 minutes)

1. **Open the link.** Land on the access screen — pick **Owner / Administrator**, sign in.
2. **Overview** — point out the DATA SOURCE switch and the live system-health panel.
3. **Real-Time Data** — click **Start real-time data**; narrate: "this is a genuine backend
   generator feeding the same MONITOR→...→LEARN loop, one sample at a time." Click
   **Inject anomaly**, watch an incident open and resolve live. Click **Stop**.
4. **Simulation → Planning & Safety / Gated Output** — run `shared_fleet`; show the
   `BLOCKED` safety card with the named failed clause and `HUMAN-IN-LOOP` outcome.
5. **Simulation → End-to-End / Whole Estate** — run `night_shift`; show the
   `EstateSummary` card (devices, blast radius, overall state) above the mixed-outcome incident list.
6. **Sign out, sign back in as `client`** — show the same catalogue and results with every
   mutating control gone, to demonstrate the role boundary is real, not cosmetic.

---

## Notes

- Everything runs against the bundled RSP simulator. No connection is made to
  live telecom infrastructure, and the interface says so.
- The signer falls back to an HMAC stub when `oqs` (liboqs-python) is absent.
  That is not post-quantum and the Safety screen labels it plainly. Install
  `oqs` for real ML-DSA.
- The REASON screen shows only recorded, auditable evidence — the published
  rationale for each tool call, the tool, its arguments and the result. These
  are hashed into the audit entry, so nothing private is exposed.
- The console use cases still work unchanged: `python usecases/uc6_end_to_end_night_shift.py`.


---

## Client reports (My reports / Report inbox)

Reports are a two-way channel, not a mailbox. Lifecycle: **SENT → ACKNOWLEDGED → RESOLVED**.

| Role | Screen | What it does |
|---|---|---|
| Client | **My reports** | Every report they have ever sent, with status, the owner's acknowledgement, response note and resolution time. New report tab: optional customer, device and incident. |
| Owner | **Reports** | Everyone's reports, filterable by status. Acknowledge, add a note, resolve; jump to the device, the incident, its reasoning and telemetry. |

* **Storage:** SQLite at `backend/data/reports.db` (override with `REPORTS_DB_PATH`). Created on first use, survives restarts. Back it up by copying the file.
* **Links:** a report's device and incident must exist in the running session (the form only offers those). Picking an incident fills in its device. Devices and incidents live in the agent session and vanish on restart/reset, so each report also keeps a snapshot of what it pointed at; the UI marks it "recorded when this report was sent".
* **Two-way:** Incidents show "N Client Reports" (a client's count is their own reports only) and list them; a report links to its device, incident, reasoning and telemetry. A new **Devices** screen is the landing page for "View device".
* **Access:** enforced server-side. A client only ever receives their own reports (someone else's is a 404); acknowledge/resolve/inbox are owner-only.
* **API:** `POST /api/reports`, `GET /api/reports/mine`, `GET /api/reports` (owner), `GET /api/reports/{id}`, `GET /api/reports/summary`, `POST /api/reports/{id}/acknowledge|resolve`, `GET /api/incidents/{id}/reports`, `GET /api/devices[/{id}]`, and `/api/telemetry?euicc_id=&incident_id=`.
* **Tests:** `python tests/test_reports.py`.

---

## Input / configuration screen

**Input** (first item in the sidebar; both roles — a client works on their own company's record, see "Roles and companies") shows *what data is entering the self-healing system*. Top to bottom: the selected client and context, the client/group/device pickers with eSIM identity, RSP environment and network/authentication context, the data source, the upload area, a provenance table, and **Process data**.

| Concept | Where it lives | Notes |
|---|---|---|
| **Data** (predefined) | `data/` | Synthetic CSVs + `manifest.json`. Read-only, checksum-verified. See `data/README.md` |
| **Uploads** (operator) | `uploads/` | Screenshots, CSVs, logs. Separate from `data/`. See `uploads/README.md` |
| Selection state | `backend/data/inputs.db` | Saved per operator on the server; survives refresh and restart |

* **Data source** is one of two visibly separate panels: **REAL-TIME** (the server's existing prototype grid feed; only devices in the grid can stream) or **SYNTHETIC / SIMULATED** (stored datasets for the selected client). Uploaded files are always **OPERATOR UPLOAD**, never mixed into either.
* **Selection rules** are enforced by the server: choosing a device fills its client, group, RSP environment and network; choosing a dataset selects its device; anything that no longer fits (another device's dataset or incident) is cleared.
* **Uploads** are tied to the client / group / device / incident selected when they were added, and are part of an input only while those match.
* **Process data** is refused (422) until the blocking checks pass, then stores an immutable snapshot (`INP-0001`, ...) with the resolved client, device, eSIM, RSP, network, source, provenance and attachments. It does **not** run the agent yet; the output stage reads the snapshot via `GET /api/input/commits/latest`.
* **API:** `GET /api/input/registry`, `GET /api/input/datasets/{id}`, `GET|PUT /api/input/state`, `POST /api/input/commit`, `GET /api/input/commits/latest|{id}`, `POST /api/uploads?kind=&filename=` (file bytes as the body; no new dependency), `GET|DELETE /api/uploads/{id}`.
* **Tests:** `python tests/test_input.py`. Regenerate the datasets with `python tools/generate_datasets.py`.

---

## Output / gated self-healing screen

**Process data** on the Input screen switches to a separate full-screen **Output** page (no sidebar, never beside or below Input). It shows the flow

`INPUT -> PROCESSING -> INCIDENT -> SELF-HEALING -> SAFETY GATE -> REMEDIATION -> RECOVERY`

as a sticky strip (each node's state comes from the backend) above seven sections. **Back to input** returns to the configuration exactly as it was left; the selection lives on the server and above the router, so nothing is lost. The sidebar's "Self-healing output" reopens the last result.

* **What runs.** A stored dataset is replayed through the *real* loop (MONITOR, REASON, PLAN, SAFETY, ACT, VERIFY, LEARN) with fleet sizes taken from the client registry. A real-time input attaches to the live feed for the selected device (starting the feed if it is stopped); the screen then updates every 2 s, and offers a *demo-only* fault injector.
* **Nothing is faked.** The diagnosis, plan, safety report, act result, audit entry and eUICC state are the loop's own objects. Four fields are *derived*, and the screen says how: **severity** (score vs. threshold, shared-profile size, whether the agent resolved it), the **gate state** (Proposed / Approved or Blocked / Executed, read off the safety report and act result), the **remediation steps** (with the audit entry's real timestamp) and **recovery**.
* **Recovery is judged on the eUICC state captured immediately before and after the action** (`backend/services/instrumentation.py`). A stored dataset is a fixed recording whose remaining rows still carry the fault, so the orchestrator re-corrupts the simulated device after a successful fix; end-of-run state would call a success a failure. For a stored dataset the screen says its telemetry cannot react; for real-time it also checks that recent samples are back under the threshold.
* **Processing the same dataset twice works.** `Monitor.reset_device` (a small additive method in `esim_selfhealing/monitor.py`) clears that device's baseline and refractory window first; without it the second pass would be silently suppressed.
* **Results live in the agent session.** After a server restart or `agent/reset` the Output says the result is gone and offers Back to input; the input itself survives.
* **API:** `POST /api/input/process` (commit + run), `GET /api/output/current[?incident_id=]`, and `GET /api/telemetry?run_id=` (only what that processed input produced).
* **Tests:** `python tests/test_output.py`.

---

## One connected system

The dashboard had two halves that only met on the Input -> Output path: the
**registry** under `data/` (clients, groups, device labels, ICCIDs, profiles,
networks, RSP environments) and the **live agent session** (eUICC state,
telemetry, incidents, actions). Both are keyed by the eUICC id, so
`backend/services/estate.py` joins them once and every screen reads the result.

* **`context`** is attached to each device, incident and report snapshot by the
  routes, so Incidents, Devices, Telemetry and Reports name the same client and
  the same device label as Input and Output — not a bare 17-digit number. A
  device that is *not* in the registry keeps its id rather than being given an
  invented client, and a broken registry degrades those screens to "no context"
  instead of failing (the Input screen is where integrity is reported).
* **`run`** is attached to each incident: which processed input (`INP-nnnn`)
  opened it. That is what "View self-healing output" on the Incidents screen
  follows, and `GET /api/output/current?incident_id=` now serves *that* run's
  commit rather than whatever ran most recently.
* **The chain ends where it should.** The Output screen's Recovery section is
  followed by "Where this goes next": file a client report against this
  incident (RECOVERY -> REPORT -> RESOLUTION), open the incident, the full
  decision trace, or the device history. Every one of those is an existing
  screen opened on the existing id.
* **Navigation** is grouped by the flow — Input, Output, Monitoring,
  Self-healing, Reports — and the sections are the same for both roles; a
  client simply sees fewer items inside them.
* **Tests:** `python tests/test_integration.py` follows one incident from input
  to a resolved report and checks every screen agrees about it.

### Sessions and stale state

The demo token lives in memory on the server and in `sessionStorage` in the
browser, so a backend restart ends the session. When the server rejects a
request the app returns to the sign-in screen and says why, rather than
leaving each screen showing an authorisation error.

A self-healing result belongs to the agent session that produced it. Signing
out, losing a session and `Reset session` all drop it, so the Output screen
never shows a result that no longer exists on the server. Reports and the
input selection are SQLite-backed and do survive a restart.


---

## Roles and companies

There are two roles, and a client works on **their own company's** record.

| | Owner | Client |
|---|---|---|
| Company | sees all three | belongs to one (`client_id` on the account; the demo `client` is **CL-001, Meridian Logistics**). Any number of accounts can share a company. |
| Input -> Process data -> Output | yes, any company | yes, their own company's devices and stored datasets only |
| Incidents, devices, telemetry, reasoning, memory, safety | everything | their company's only |
| Run a scenario (Simulation, sidebar Run) | yes | yes |
| Reset session | yes | no |
| Start / stop / inject into the live feed | yes | no (a client can attach to it while it is running) |
| Reports | inbox: acknowledge and resolve | file and follow their own |

This is enforced on the server (`backend/services/tenancy.py` and the routes),
not by hiding things in the frontend: another company's incident, device,
telemetry, run or upload is a 404 to a client, and the Overview, Memory and
Safety screens leave out its entries. A client account with no company sees
nothing rather than everything. The set of owner-only routes is asserted by
`tests/test_tenancy.py`, so opening one to clients has to be a deliberate change.

Known limits of the demo: the session is shared, so a scenario a client runs also
creates incidents for other companies' devices (the owner sees them; the client
does not). The sidebar and Overview counters, memory and audit counts are worked
out from what the signed-in account can see, so they agree with the Incidents list.

Signing out (or losing the session) remounts the dashboard, so the next account
never briefly sees the previous account's data.


---

## Running the agent on your own CSV

On the Input screen, **Your own CSV** is a third data source next to the live feed
and the stored datasets. Upload a CSV in the Upload area, pick the device it is for,
and choose the file; **Process data** then runs the same loop as for any other source
(detect, diagnose, plan, safety gate, remediate, verify) and the Output labels the
source **OPERATOR UPLOAD**.

The file (see `backend/services/custom_input.py`) must have the five telemetry
columns `aka_fail_rate, rsrp_dbm, drop_rate, latency_ms, ota_fail_rate`, every value a
finite number, and between 100 and 5000 rows (the detector spends its first 60
readings learning what normal looks like). Time may come from `ts_epoch` (seconds or
milliseconds), `ts_iso`, `timestamp` or `ts`; without one, readings are assumed one
second apart and the screen says so. It is for **one device**: an `euicc_id` column, if
present, must name the selected device, which also stops a client feeding readings into
another company's device. The stored file is re-hashed at processing time, so an edited
file is refused.

**What a file cannot give you:** whether a fix *worked*. That comes from the simulated
RSP server, which only knows about a fault if the file carries the optional
`sim_fault_label` column (as the built-in datasets do). Without it the agent still
detects and diagnoses correctly and plans the right fix, but the fix cannot show as
recovered, and the Output says so. `meridian_yard_tracker_smdp_outage.csv` is a ready
sample for Meridian's Yard tracker Y-48213.

### Replacing the stored data with your own

Everything under `data/` is checksummed in `data/manifest.json`, so a file edited
or dropped in by hand is refused by the dashboard as **modified** — a tamper check,
not a bug. There are two supported ways in:

* **One-off:** upload the CSV and pick it under **Your own CSV** on the Input screen.
* **Permanent:** `python tools/import_dataset.py mine.csv --device <euicc_id>` copies
  it into `data/telemetry/` and records it in the manifest with a fresh checksum, so
  it appears on the Input screen alongside the built-in ones. `--replace <dataset_id>`
  overwrites one instead of adding. The file is validated exactly as the Input screen
  validates it, and the other datasets are untouched (the tool does **not** run
  `tools/generate_datasets.py`, which would regenerate them all).

An imported dataset is labelled **OPERATOR IMPORT** everywhere the dashboard shows
provenance — never `SYNTHETIC / SIMULATED`, which is reserved for data this project
generated itself.


---

## Accounts and sign-in

Accounts live in `backend/data/accounts.db` (or `ACCOUNTS_DB_PATH`), next to the
reports and input stores. Passwords are never stored: each account keeps a random
salt and a PBKDF2-HMAC-SHA256 hash, compared in constant time. Sessions are rows,
not memory, so **a backend restart no longer signs everyone out**; a token still
expires after 12 hours, and signing out deletes it server-side.

Manage accounts with the tool (passwords are read without echo):

    python tools/manage_accounts.py list --companies
    python tools/manage_accounts.py add alice --role client --client CL-001
    python tools/manage_accounts.py add ops2  --role owner
    python tools/manage_accounts.py passwd alice
    python tools/manage_accounts.py disable alice     # keeps it, ends its sessions
    python tools/manage_accounts.py remove  alice

A client account needs the company it belongs to; an owner sees every company. The
last remaining owner cannot be removed. Changing or disabling an account signs it
out everywhere.

The two demo accounts are created **once**, the first time the store is made, so a
password you change is not undone by a restart. Set `DEMO_OWNER_PASSWORD` /
`DEMO_CLIENT_PASSWORD` to seed different ones, or `DEMO_ACCOUNTS=off` to start with
no accounts at all and create your own.

Repeated failed sign-ins are rate-limited per account **and** per address: after 5
failures within 5 minutes, that account (or address) is refused for 5 minutes, and
the sign-in screen says how long to wait. The correct password is refused too while
the lock holds, so guessing cannot be finished off.

## Deployment settings

`config.json` in the project root (or `DASHBOARD_CONFIG`) holds the few numbers a
team may want to change; it is optional, and every setting has a working default.
Today that is the severity rules, which are a judgement, not a fact:

    { "severity": { "high_sigma": 45.0, "medium_sigma": 18.0,
                    "shared_profile_raises": true, "unresolved_raises": true } }

The two thresholds are in standard deviations of how far the device sat
outside its own normal range at detection — not multiples of the anomaly
score, which a threshold-crossing detector reports just above the threshold
whatever the fault. REALISM.md section 3 has the measured figures the bands
sit between.

Each can also be set as an environment variable (`SEVERITY_HIGH_SIGMA`, and so on),
which wins over the file. A malformed file is reported and the defaults are used
rather than taking the dashboard down, and the Output's severity verdict carries the
rules that produced it.

## Running it in Docker

    docker compose up --build        # http://localhost:8000

One container: the frontend is built and served by the backend, so there is a single
port and no separate dev server. Two named volumes keep `backend/data` (accounts,
reports, input selections) and `uploads/` across restarts — without them the
container starts empty every time. `docker build -t esim-dashboard .` and
`docker run` work the same way if you would rather not use compose.


---

## Two ways to open the Output screen

The Output screen is not tied to the Input screen: it shows **a run**, wherever
that run came from.

| Button | What it runs | Back goes to |
|---|---|---|
| **Process data** (Input screen) | the client / device / data source the operator selected | Back to input |
| **Run self-healing** (sidebar) | the built-in scenario selected on the Simulation screen | Back to dashboard |

Both show the same seven stages and the same stage animation, and both write a
run into the session (`INP-nnnn` for a processed input, `RUN-nnnn` for a
scenario), so the incident links back to its own output from the Incidents
screen either way.

A scenario run has no operator input behind it, so `output_view.scenario_commit`
gives it the same commit-shaped record a processed input would have had, filled
from the scenario and from the registry context of the device the incident was
opened on. It is labelled `SYNTHETIC / SIMULATED`, because that is what a
built-in scenario is.


---

## Which fields may be randomised (and which may not)

`esim_selfhealing/fields.py` is the one definition the generator, the CSV
validator and `tools/check_fields.py` all read. Run it to see the audit:

    python tools/check_fields.py            # the table
    python tools/check_fields.py mine.csv   # check a file against it

**Measured channels** (`aka_fail_rate`, `rsrp_dbm`, `drop_rate`, `latency_ms`,
`ota_fail_rate`) may be generated, but only within physical limits, and **not
independently of each other**. MONITOR scores a sample with the Mahalanobis
distance against an *estimated covariance* matrix: it asks how unusual this
combination is, given how the channels normally move together. Draw them
independently and that covariance describes a process that does not exist - the
score still computes and stops meaning anything. A real fault moves several
channels at once in a fixed pattern (`FAULT_SIGNATURES`); independent noise does
not, so the detector could no longer separate the two.

**Structural fields** (`euicc_id`, `eid`, `iccid`, `cell_id`, `client_id`,
`group_id`, `network_id`, `plmn`, `rsp_env_id`, `profile_name`, `fleet_size`,
`sim_fault_label`, `ts_epoch`, `provenance`) are never randomised, and each
carries its reason in the spec. They are identities and structure: an ICCID ends
in a Luhn check digit, a PLMN names a real operator, `client_id` decides who may
see a record, `fleet_size` drives the safety envelope's blast-radius limit, and
`ts_epoch` ordering is what EWMA and CUSUM are computed over. A random value
there is not a different device, it is an invalid record.

### Units, stated once

| Channel | Unit | Physical range | Typical healthy |
|---|---|---|---|
| `aka_fail_rate` | failures **per 100 attach attempts** (a percentage, *not* 0..1) | 0 .. 100 | 0 .. 5 |
| `rsrp_dbm` | dBm, always negative (3GPP *reports* -140..-44; real equipment exceeds it at close range, and the forest calibration data reaches -35) | -140 .. -20 | -110 .. -80 |
| `drop_rate` | fraction 0..1 | 0 .. 1 | 0 .. 0.10 |
| `latency_ms` | milliseconds | 1 .. 30000 | 40 .. 400 |
| `ota_fail_rate` | fraction 0..1 | 0 .. 1 | 0 .. 0.10 |

A value outside the physical range is **refused** (wrong units or wrong column
order, and clipping it would hide the mistake while corrupting the covariance).
A value that is merely outside the typical range is **reported, not refused** -
a file may be entirely fault data.


---

## Using real measured data

No public dataset contains eSIM provisioning counters - operators and SM-DP+
vendors hold that data. Public *radio* traces do exist, and `tools/import_real_trace.py`
turns one into a dataset this dashboard can run:

1. Download a trace. The Irish 5G dataset (<https://github.com/uccmisl/5Gdataset>,
   GPL-3.0, `5G-production-dataset.zip`) is real measurements from a major Irish
   operator, recorded with G-NetTrack Pro. ULR-MC5G, Lumos-5G and IEEE DataPort /
   Zenodo traces work too, as does a recording you make yourself with a
   signal-logging app on an Android phone.
2. Convert it:

       python tools/import_real_trace.py trace.csv --device 89330000000048213 --inspect
       python tools/import_real_trace.py trace.csv --device 89330000000048213 \
           --fault smdp_session_outage

3. Upload the result under **Your own CSV**, or make it a stored dataset with
   `tools/import_dataset.py`.

### What it claims, column by column

The tool prints a provenance line per channel and will not overstate it:

| | meaning |
|---|---|
| **MEASURED** | taken from the file as recorded (RSRP always; latency, loss and failure rates when the trace has them) |
| **MODELLED** | derived from the measured RSRP by the stated relationship in `model_from_rsrp` |
| **SYNTHETIC** | generated inside its specification bounds, with no measurement behind it |

That is the answer to "is this real data?" - not yes or no, but *which parts*.

The modelled channels are deliberately **not** tight functions of RSRP. Link
quality does drive drops and latency, but load, backhaul and server state drive
them too, so each channel carries a large independent component. The result sits
at roughly 0.3..0.7 correlation, the band field studies report; a naive model
gives 0.95 and is obvious to anyone who checks. `tests/test_fields.py` asserts
both that the correlations point the right way and that they are not too tight.

This matters for MONITOR specifically: the Mahalanobis distance is measured
against an estimated covariance, so getting the correlation structure roughly
right is what makes the detector's score mean anything.
