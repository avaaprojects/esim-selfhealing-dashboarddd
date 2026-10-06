"""HTTP surface. Every handler is a thin adapter over `agent_service.SESSION`.

No business logic lives here on purpose: the service layer is importable and
testable without FastAPI installed, and this module only translates between HTTP
and its return values.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

import hashlib

from ..services import (auth, custom_input, datasets, estate, input_state, output_view,
                        report_links, reports, scenarios, tenancy, uploads)
from ..services.agent_service import SESSION

#: PUBLIC surface: reachable with no session at all. Everything else in this
#: module requires at least a CLIENT (viewer) token; mutating endpoints
#: additionally require OWNER / ADMINISTRATOR.
public_router = APIRouter(prefix="/api")

#: Every route below requires a signed-in session (Depends at router level,
#: so a missing/expired token 401s before any handler runs). Individual
#: mutating routes add `Depends(auth.require_owner)` on top.
router = APIRouter(prefix="/api", dependencies=[Depends(auth.require_any_role)])


class RunRequest(BaseModel):
    scenario: str = Field(default=scenarios.DEFAULT_SCENARIO)
    enable_learning: Optional[bool] = None


class ResetRequest(BaseModel):
    enable_learning: bool = True


class LoginRequest(BaseModel):
    username: str
    password: str


class InjectFaultRequest(BaseModel):
    fault_class: str
    euicc_id: Optional[str] = None
    duration: int = 40


class ReportCreate(BaseModel):
    """A client report. Only `message` is required; the customer, device and
    incident are optional and, for the last two, must already exist."""

    message: str = Field(min_length=1, max_length=reports.MAX_MESSAGE)
    customer: Optional[str] = Field(default=None, max_length=reports.MAX_CUSTOMER)
    device_id: Optional[str] = Field(default=None, max_length=64)
    incident_id: Optional[str] = Field(default=None, max_length=64)


class DataSourcePatch(BaseModel):
    mode: Optional[str] = None
    dataset_id: Optional[str] = None
    upload_id: Optional[str] = None      # one of the operator's own CSV uploads


class InputPatch(BaseModel):
    """A partial change to the operator's input selection. Only the fields that
    are sent change; sending null clears one. The server keeps the rest of the
    selection consistent (see input_state.apply_patch)."""

    client_id: Optional[str] = None
    group_id: Optional[str] = None
    device_id: Optional[str] = None
    incident_id: Optional[str] = None
    rsp_env_id: Optional[str] = None
    network_id: Optional[str] = None
    data_source: Optional[DataSourcePatch] = None


class ReportNote(BaseModel):
    note: Optional[str] = Field(default=None, max_length=reports.MAX_NOTE)


# -- who is asking ------------------------------------------------------------
def _actor(entry) -> str:
    """The account a report belongs to. Normalised the same way `auth.login`
    looks accounts up (case-insensitively), so signing in as `Client` and
    `client` is the same person with the same history."""
    return str(entry["username"]).strip().lower()


def _company(entry) -> Optional[str]:
    """The company a CLIENT is limited to; None for an owner (sees every company)."""
    return tenancy.company_of(entry)


def _forbid_other_company(entry, context) -> None:
    """404 (not 403) for something that belongs to another company, so a client
    cannot tell it exists."""
    if not tenancy.owns(_company(entry), context):
        raise HTTPException(status_code=404, detail="not found")


def _device_visible(entry, euicc_id: Optional[str]) -> bool:
    return tenancy.owns(_company(entry), estate.context_for(euicc_id))


def _incident_visible(entry, incident_id: Optional[str]) -> bool:
    return _device_visible(entry, SESSION.incident_device(incident_id)) if incident_id else False


def _only_visible_incidents(entry, rows, key: str = "incident_id"):
    """Rows that name an incident are kept for a client only if that incident is
    on their company's device. Rows that name none (shared, seeded knowledge)
    stay. An owner sees everything."""
    if _company(entry) is None:
        return list(rows)
    return [r for r in rows if not (r or {}).get(key) or _incident_visible(entry, r.get(key))]


def _scope(entry) -> Optional[str]:
    """OWNER sees everything (None = no filter); a CLIENT sees only their own."""
    return None if entry.get("role") == "owner" else _actor(entry)


def _visible_report(report_id: str, entry):
    """Load a report the caller is allowed to see. A client asking for someone
    else's report gets the same 404 as for one that does not exist."""
    try:
        report = reports.get(report_id)
    except reports.ReportNotFound:
        raise HTTPException(status_code=404, detail=f"unknown report {report_id}")
    if entry.get("role") != "owner" and report["client"] != _actor(entry):
        raise HTTPException(status_code=404, detail=f"unknown report {report_id}")
    return report


# -- auth (public) -----------------------------------------------------------
@public_router.get("/health")
def health():
    return SESSION.health()


@public_router.post("/auth/login")
def login(body: LoginRequest, request: Request = None):
    """Sign in. Accounts live in backend/data/accounts.db (see services/auth.py);
    the demo pairs are in README_DASHBOARD.md and are never shown in the running
    dashboard. Repeated failures are rate-limited per account and per address."""
    try:
        result = auth.login(body.username, body.password, client_ip=auth.client_ip(request))
    except auth.TooManyAttempts as exc:
        raise HTTPException(status_code=429, detail=str(exc),
                            headers={"Retry-After": str(exc.retry_after)})
    if result is None:
        raise HTTPException(status_code=401, detail="invalid username or password")
    return result


@router.post("/auth/logout")
def logout(entry=Depends(auth.require_any_role)):
    auth.logout(entry["token"])
    return {"ok": True}


@router.get("/auth/me")
def me(entry=Depends(auth.require_any_role)):
    payload = {"username": entry["username"], "role": entry["role"]}
    if entry.get("client_id"):
        payload["client_id"] = entry["client_id"]
    return payload


@router.get("/status")
def status(entry=Depends(auth.require_any_role)):
    payload = SESSION.status()
    reading = payload.get("latest_reading")
    if _company(entry) is not None:
        if reading and not _device_visible(entry, reading.get("euicc_id")):
            payload = {**payload, "latest_reading": None}      # another company's device
        # The counters describe what THIS account can see, so the sidebar and the
        # Overview agree with the Incidents list instead of showing session totals.
        mine = [i for i in estate.attach_to_incidents(SESSION, SESSION.incidents())
                if tenancy.owns(_company(entry), i.get("context"))]
        by = lambda status: sum(1 for i in mine if i.get("status") == status)      # noqa: E731
        counters = dict(payload.get("counters") or {})
        counters.update(
            incidents_total=len(mine),
            auto_remediated=by("AUTO-REMEDIATED"),
            open_incidents=len(mine) - by("AUTO-REMEDIATED"),
            human_in_loop=by("HUMAN-IN-LOOP"),
            escalations=by("ESCALATION"),
            memory_records=len(memory(500, None, entry)["records"]),
            audit_entries=len(safety(entry).get("audit", [])),
        )
        payload = {**payload, "counters": counters}
    return payload


@router.get("/telemetry")
def telemetry(limit: int = Query(default=120, ge=10, le=600),
              euicc_id: Optional[str] = None,
              incident_id: Optional[str] = None,
              run_id: Optional[str] = None,
              entry=Depends(auth.require_any_role)):
    """Session telemetry; or one device's (`euicc_id`), optionally centred on
    the reading that opened an incident (`incident_id`), optionally only what
    one processed input (`run_id`) produced."""
    if run_id and SESSION.run_state(run_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown run {run_id}")
    if incident_id and not SESSION.has_incident(incident_id):
        raise HTTPException(status_code=404, detail=f"unknown incident {incident_id}")
    if euicc_id and not SESSION.has_device(euicc_id):
        raise HTTPException(status_code=404, detail=f"unknown device {euicc_id}")
    if euicc_id and not _device_visible(entry, euicc_id):
        raise HTTPException(status_code=404, detail=f"unknown device {euicc_id}")
    if incident_id and not _incident_visible(entry, incident_id):
        raise HTTPException(status_code=404, detail=f"unknown incident {incident_id}")
    payload = SESSION.telemetry(limit=limit, euicc_id=euicc_id, incident_id=incident_id, run_id=run_id)
    if _company(entry) is not None:
        # The session-wide stream mixes every device, and each sample names its own.
        # A client keeps the samples from their own company's devices only. (A
        # device- or incident-specific series was already checked above.)
        kept = [x for x in payload.get("samples", []) if _device_visible(entry, x.get("euicc_id"))]
        if len(kept) != len(payload.get("samples", [])):
            payload = {**payload, "samples": kept}
            if payload.get("marker_index") is not None:
                payload["marker_index"] = None
    return payload


@router.get("/incidents")
def incidents(entry=Depends(auth.require_any_role)):
    """Every incident, each with `report_count`: how many client reports point
    at it. A client's count covers their own reports only."""
    items = estate.attach_to_incidents(SESSION, SESSION.incidents())
    company = _company(entry)
    items = [i for i in items if tenancy.owns(company, i.get("context"))]
    report_links.with_counts(
        items, "incident_id", reports.counts_by("incident_id", client=_scope(entry)))
    return {"incidents": items}


@router.get("/incidents/{incident_id}")
def incident(incident_id: str, entry=Depends(auth.require_any_role)):
    payload = SESSION.incident(incident_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"unknown incident {incident_id}")
    counts = reports.counts_by("incident_id", client=_scope(entry))
    payload["report_count"] = counts.get(incident_id, 0)
    payload = estate.attach_to_incident(SESSION, payload)
    _forbid_other_company(entry, payload.get("context"))
    return payload


@router.get("/incidents/{incident_id}/reports")
def incident_reports(incident_id: str, entry=Depends(auth.require_any_role)):
    """The reports linked to one incident (a client sees only their own).
    Deliberately does not 404 for an incident the session has since lost: the
    reports about it still exist."""
    rows = reports.list_reports(client=_scope(entry), incident_id=incident_id)
    return {"incident_id": incident_id,
            "reports": report_links.enrich(SESSION, rows)}


# -- DEVICES -------------------------------------------------------------------
@router.get("/devices")
def devices(entry=Depends(auth.require_any_role)):
    """The devices the dashboard already tracks, each with `report_count`."""
    items = estate.attach_to_devices(SESSION.devices())
    company = _company(entry)
    items = [d for d in items if tenancy.owns(company, d.get("context"))]
    report_links.with_counts(
        items, "euicc_id", reports.counts_by("device_id", client=_scope(entry)))
    return {"devices": items}


@router.get("/devices/{euicc_id}")
def device(euicc_id: str, entry=Depends(auth.require_any_role)):
    payload = SESSION.device(euicc_id)
    if payload is None or not _device_visible(entry, euicc_id):
        raise HTTPException(status_code=404, detail=f"unknown device {euicc_id}")
    scope = _scope(entry)
    rows = reports.list_reports(client=scope, device_id=euicc_id)
    payload["reports"] = report_links.enrich(SESSION, rows)
    payload["report_count"] = len(rows)
    payload["context"] = estate.context_for(euicc_id)
    payload["incidents"] = estate.attach_to_incidents(SESSION, payload["incidents"])
    report_links.with_counts(
        payload["incidents"], "incident_id", reports.counts_by("incident_id", client=scope))
    return payload


@router.get("/agent/trace")
def trace(incident_id: Optional[str] = None, entry=Depends(auth.require_any_role)):
    payload = SESSION.trace(incident_id)
    if payload is not None and _company(entry) is not None:
        # the trace names its incident; a client only reads their own company's
        tid = (payload.get("incident") or {}).get("incident_id") or payload.get("incident_id")
        if not _incident_visible(entry, tid):
            payload = None
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail="no incident has been opened yet - run a scenario first",
        )
    return payload


@router.get("/agent/pipeline")
def pipeline():
    return SESSION.pipeline()


@router.get("/memory")
def memory(limit: int = Query(default=60, ge=1, le=500),
           incident_id: Optional[str] = None,
           entry=Depends(auth.require_any_role)):
    payload = SESSION.memory(limit=limit, query_incident=incident_id)
    if _company(entry) is not None:
        # Learned records are named by the incident they came from; the seeded
        # precedents are shared knowledge and are kept.
        keep = lambda r: not str((r or {}).get("record_id", "")).startswith("INC-") or _incident_visible(entry, r["record_id"])
        payload = {**payload, "records": [r for r in payload.get("records", []) if keep(r)]}
        if isinstance(payload.get("retrieved"), list):
            payload["retrieved"] = [r for r in payload["retrieved"] if keep(r)]
    return payload


@router.get("/learning")
def learning():
    return SESSION.learning()


@router.get("/safety")
def safety(entry=Depends(auth.require_any_role)):
    payload = SESSION.safety()
    if _company(entry) is not None:
        for key in ("audit", "gates", "human_queue", "escalations"):
            if isinstance(payload.get(key), list):
                payload = {**payload, key: _only_visible_incidents(entry, payload[key])}
    return payload


@router.get("/actions")
def actions():
    return SESSION.actions()


@router.get("/simulation")
def simulation():
    return SESSION.simulation()


# -- REAL-TIME DATA ----------------------------------------------------------
@router.get("/realtime/status")
def realtime_status():
    return SESSION.realtime_status()


@router.post("/realtime/start", dependencies=[Depends(auth.require_owner)])
def realtime_start():
    """START REAL-TIME DATA. OWNER only - viewers see the feed, not the switch."""
    return SESSION.start_realtime()


@router.post("/realtime/stop", dependencies=[Depends(auth.require_owner)])
def realtime_stop():
    return SESSION.stop_realtime()


@router.post("/realtime/inject", dependencies=[Depends(auth.require_owner)])
def realtime_inject(body: InjectFaultRequest):
    """SIMULATION / DEMO ONLY: feed a known synthetic fault into the live
    stream and let the real agent process it. OWNER only."""
    try:
        return SESSION.inject_realtime_fault(
            body.fault_class, euicc_id=body.euicc_id, duration=body.duration
        )
    except KeyError:
        raise HTTPException(status_code=404,
                            detail=f"unknown fault class '{body.fault_class}'")


# -- CLIENT REPORTS -----------------------------------------------------------
# SENT -> ACKNOWLEDGED -> RESOLVED (see services/reports.py). A CLIENT submits
# and follows their own reports; the OWNER sees all of them and moves them along.
# Fixed paths (/summary, /mine) are declared before /{report_id} on purpose.
@router.post("/reports")
def submit_report(body: ReportCreate, entry=Depends(auth.require_any_role)):
    """Submit a report, optionally tied to a device and/or incident that
    already exist. The client is always the signed-in account - it is never
    taken from the request body."""
    try:
        links = report_links.resolve_links(SESSION, body.device_id, body.incident_id)
        report = reports.create(client=_actor(entry), role=entry["role"],
                                message=body.message, customer=body.customer, **links)
    except ValueError as exc:            # LinkError is a ValueError
        raise HTTPException(status_code=422, detail=str(exc))
    return report_links.enrich(SESSION, [report])[0]


@router.get("/reports/summary")
def reports_summary(entry=Depends(auth.require_any_role)):
    """Counts by status - the caller's own for a client, everyone's for the owner."""
    return reports.summary(client=_scope(entry))


@router.get("/reports/mine")
def my_reports(entry=Depends(auth.require_any_role)):
    """MY REPORTS: every report the signed-in account has submitted, newest
    first, with the owner's acknowledgement and resolution."""
    rows = reports.list_reports(client=_actor(entry))
    return {"reports": report_links.enrich(SESSION, rows)}


@router.get("/reports", dependencies=[Depends(auth.require_owner)])
def get_reports(status_filter: Optional[str] = Query(default=None, alias="status"),
                device_id: Optional[str] = None,
                incident_id: Optional[str] = None):
    """The owner's inbox: every client's reports, optionally filtered."""
    if status_filter:
        status_filter = status_filter.upper()
        if status_filter not in reports.STATUSES:
            raise HTTPException(status_code=422,
                                detail=f"status must be one of {', '.join(reports.STATUSES)}")
    rows = reports.list_reports(status=status_filter, device_id=device_id,
                                incident_id=incident_id)
    return {"reports": report_links.enrich(SESSION, rows)}


@router.get("/reports/{report_id}")
def get_report(report_id: str, entry=Depends(auth.require_any_role)):
    report = _visible_report(report_id, entry)
    return report_links.enrich(SESSION, [report])[0]


def _transition(fn, report_id: str, body: Optional[ReportNote], entry):
    try:
        report = fn(report_id, _actor(entry), body.note if body else None)
    except reports.ReportNotFound:
        raise HTTPException(status_code=404, detail=f"unknown report {report_id}")
    except reports.InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return report_links.enrich(SESSION, [report])[0]


@router.post("/reports/{report_id}/acknowledge")
@router.post("/reports/{report_id}/ack")        # name used by the first version
def acknowledge_report(report_id: str, body: Optional[ReportNote] = None,
                       entry=Depends(auth.require_owner)):
    """OWNER: SENT -> ACKNOWLEDGED, with an optional response note the client sees."""
    return _transition(reports.acknowledge, report_id, body, entry)


@router.post("/reports/{report_id}/resolve")
def resolve_report(report_id: str, body: Optional[ReportNote] = None,
                   entry=Depends(auth.require_owner)):
    """OWNER: -> RESOLVED, with an optional resolution note the client sees."""
    return _transition(reports.resolve, report_id, body, entry)


# -- INPUT / CONFIGURATION ------------------------------------------------------
# What data is entering the self-healing system: which client and device, from
# which source (real-time feed or a stored synthetic dataset), plus operator
# uploads. OWNER only. DATA (./data) and UPLOADS (./uploads) are separate.
def _input_ctx(company: Optional[str] = None):
    """Registry (flagged with which devices are in the real-time grid), stored
    datasets and the real-time feed status. For a client (`company` set) the
    registry and datasets are cut down to that company's own."""
    try:
        reg = datasets.registry()
        dsets = datasets.list_datasets()
    except datasets.DatasetError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    grid = {d["euicc_id"] for d in SESSION.realtime.devices}
    for d in reg["devices"]:
        d["in_realtime_grid"] = d["euicc_id"] in grid
    return tenancy.scope_registry(reg, company), tenancy.scope_datasets(dsets, company), SESSION.realtime_status()


def _upload_record(entry, store, upload_id: str):
    """The operator's own CSV upload. Another company's file is a plain 404."""
    try:
        rec = store.get_upload(upload_id)
    except input_state.NotFound:
        raise HTTPException(status_code=404, detail="That uploaded file no longer exists.")
    if _company(entry) is not None and rec.get("client_id") != _company(entry):
        raise HTTPException(status_code=404, detail="That uploaded file no longer exists.")
    if rec.get("kind") != "csv":
        raise HTTPException(status_code=422, detail="Only a CSV upload can be used as the data source.")
    return rec


def _read_upload(rec) -> bytes:
    """The stored bytes, refused if they no longer match the checksum taken at upload."""
    try:
        data = uploads.path_for(rec["kind"], rec["stored_name"]).read_bytes()
    except FileNotFoundError:
        raise HTTPException(status_code=422, detail="The uploaded file is missing from disk; upload it again.")
    if hashlib.sha256(data).hexdigest() != rec["sha256"]:
        raise HTTPException(status_code=422, detail="The stored file no longer matches its checksum; upload it again.")
    return data


def _check_upload_choice(entry, store, reg, patch, upload_id: str) -> dict:
    """Validate a CSV against the device selected on the Input screen and return
    the snapshot the selection keeps (what was checked, and the file's checksum)."""
    rec = _upload_record(entry, store, upload_id)
    state = store.get_state(_actor(entry))
    device_id = patch.get("device_id", state.get("device_id"))
    device = next((d for d in reg["devices"] if d["euicc_id"] == device_id), None)
    if device is None:
        raise HTTPException(status_code=422, detail="Choose a device first, so the file can be checked against it.")
    try:
        parsed = custom_input.parse(_read_upload(rec), device=device)
    except custom_input.CustomInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    s = parsed["summary"]
    return {"id": rec["id"], "filename": rec["filename"], "stored_name": rec["stored_name"],
            "client_id": rec["client_id"], "device_id": device_id, "sha256": rec["sha256"],
            "rows": s["rows"], "time_from": s["time_from"], "time_to": s["time_to"],
            "assumed_time": s["assumed_time"], "has_fault_labels": s["has_fault_labels"]}


def _pin_company(entry, ctx=None) -> None:
    """A client's working selection always starts on their own company, so they
    never have to (and cannot) pick one."""
    company = _company(entry)
    if company is None:
        return
    reg, dsets, _ = ctx or _input_ctx(company)
    store = input_state.get_store()
    state = store.get_state(_actor(entry))
    if state.get("client_id") == company:
        return
    try:
        new = input_state.apply_patch(state, {"client_id": company}, reg=reg, dsets=dsets,
                                      incident_device=SESSION.incident_device)
    except input_state.InputError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    store.put_state(_actor(entry), new)


def _input_view(username: str, state=None, ctx=None, company: Optional[str] = None):
    """The working selection plus everything derived from it."""
    reg, dsets, rt = ctx or _input_ctx(company)
    store = input_state.get_store()
    state = state if state is not None else store.get_state(username)
    all_uploads = store.list_uploads(client_id=state.get("client_id")) if state.get("client_id") else []
    attached = input_state.attachments_for(state, all_uploads)
    attached_ids = {a["id"] for a in attached}
    return {
        "state": state,
        "resolved": input_state.resolve(state, reg, dsets),
        "readiness": input_state.readiness(state, reg=reg, dsets=dsets, realtime=rt, attachments=attached),
        "provenance": input_state.provenance(state, reg=reg, dsets=dsets, realtime=rt, attachments=attached),
        "uploads": [{**u, "attached": u["id"] in attached_ids} for u in all_uploads],
    }


@router.get("/input/registry")
def input_registry(entry=Depends(auth.require_any_role)):
    reg, dsets, rt = _input_ctx(_company(entry))
    return {"registry": reg, "datasets": dsets, "realtime": rt}


@router.get("/input/datasets/{dataset_id}")
def input_dataset(dataset_id: str, rows: int = Query(default=12, ge=1, le=50),
                  entry=Depends(auth.require_any_role)):
    """One stored dataset: provenance, per-feature stats and a row preview."""
    company = _company(entry)
    if company is not None and dataset_id not in {d["id"] for d in tenancy.scope_datasets(
            datasets.list_datasets(), company)}:
        raise HTTPException(status_code=404, detail=f"unknown dataset {dataset_id}")
    try:
        return datasets.get_dataset(dataset_id, preview_rows=rows)
    except datasets.UnknownDataset:
        raise HTTPException(status_code=404, detail=f"unknown dataset {dataset_id}")
    except datasets.DatasetError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/input/state")
def get_input_state(entry=Depends(auth.require_any_role)):
    company = _company(entry)
    ctx = _input_ctx(company)
    _pin_company(entry, ctx)
    return _input_view(_actor(entry), ctx=ctx, company=company)


@router.put("/input/state")
def put_input_state(body: InputPatch, entry=Depends(auth.require_any_role)):
    """Change the selection. Persisted per operator, so it survives a page
    refresh and a backend restart."""
    company = _company(entry)
    ctx = _input_ctx(company)
    reg, dsets, _ = ctx
    store = input_state.get_store()
    patch = body.model_dump(exclude_unset=True)
    if company is not None:
        if patch.get("client_id") not in (None, company):
            raise HTTPException(status_code=403, detail="This account belongs to a different company.")
        patch.pop("client_id", None)
        _pin_company(entry, ctx)
    src = patch.get("data_source") or {}
    if src.get("upload_id"):
        src["upload"] = _check_upload_choice(entry, store, reg, patch, src["upload_id"])
        patch["data_source"] = src
    try:
        new = input_state.apply_patch(
            store.get_state(_actor(entry)), patch,
            reg=reg, dsets=dsets, incident_device=SESSION.incident_device)
    except input_state.InputError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    store.put_state(_actor(entry), new)
    return _input_view(_actor(entry), state=new, ctx=ctx, company=company)


@router.post("/input/commit")
def commit_input(entry=Depends(auth.require_any_role)):
    """PROCESS DATA: freeze the current input as an immutable snapshot (client,
    device, eSIM, RSP, network, source, provenance, attachments) that the
    output stage reads. Refused with 422 until the blocking checks pass."""
    company = _company(entry)
    ctx = _input_ctx(company)
    _pin_company(entry, ctx)
    reg, dsets, rt = ctx
    store = input_state.get_store()
    state = store.get_state(_actor(entry))
    attached = input_state.attachments_for(state, store.list_uploads(client_id=state.get("client_id")))
    try:
        payload = input_state.build_commit(state, reg=reg, dsets=dsets, realtime=rt, attachments=attached)
    except input_state.InputError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return store.add_commit(_actor(entry), payload)


@router.post("/input/process")
def process_input(entry=Depends(auth.require_any_role)):
    """PROCESS DATA: freeze the current input (as /input/commit does) and run it
    through the real self-healing loop. A stored dataset is replayed through
    MONITOR -> REASON -> PLAN -> SAFETY -> ACT -> VERIFY -> LEARN; a real-time
    input attaches to the live feed (starting it if it is stopped). Returns the
    commit and the run; the Output screen reads the result from /output/current."""
    company = _company(entry)
    ctx = _input_ctx(company)
    _pin_company(entry, ctx)
    reg, dsets, rt = ctx
    store = input_state.get_store()
    state = store.get_state(_actor(entry))
    attached = input_state.attachments_for(state, store.list_uploads(client_id=state.get("client_id")))
    try:
        payload = input_state.build_commit(state, reg=reg, dsets=dsets, realtime=rt, attachments=attached)
        if company is not None and payload["source"]["mode"] == "realtime" and not rt.get("running"):
            # Starting or stopping the live feed is the owner's control; a client
            # can attach to it once it is running.
            raise input_state.InputError(
                "The live feed is stopped. Ask the owner to start it, or use a stored dataset.")
        resolved = payload["resolved"]
        observations = None
        if payload["source"]["mode"] == "upload":
            # Read the file again and check it is still the one that was chosen.
            snap = state["data_source"]["upload"]
            rec = _upload_record(entry, store, snap["id"])
            if rec["sha256"] != snap["sha256"]:
                raise input_state.InputError("The uploaded file changed since it was chosen; choose it again.")
            device = next(d for d in reg["devices"] if d["euicc_id"] == state["device_id"])
            try:
                observations = custom_input.parse(_read_upload(rec), device=device)["observations"]
            except custom_input.CustomInputError as exc:
                raise input_state.InputError(str(exc))
        elif payload["source"]["mode"] == "synthetic":
            # Load (and checksum-verify) the data BEFORE recording the commit, so
            # an edited or missing file cannot leave a half-processed input.
            observations = datasets.dataset_observations(resolved["dataset"]["id"])
    except (input_state.InputError, datasets.DatasetError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    commit = store.add_commit(_actor(entry), payload)
    device = resolved["device"]
    if observations is not None:
        d = resolved["dataset"]
        run = SESSION.process_dataset(
            commit["id"], observations,
            {x["euicc_id"]: x["fleet_size"] for x in reg["devices"]},
            {"dataset_id": d["id"], "dataset_title": d["title"], "dataset_path": d["path"]})
    else:
        run = SESSION.process_realtime(commit["id"], device["euicc_id"], {})
    return {"commit": commit, "run": SESSION.run_state(run["id"])}


@router.get("/output/current")
def output_current(incident_id: Optional[str] = None, entry=Depends(auth.require_any_role)):
    """The Output screen: what was detected, decided and done for the operator's
    latest processed input. `available` is false until something was processed,
    and again if the agent session that produced the result no longer exists.

    `incident_id` selects one incident of that run - and if the incident came
    out of an *earlier* input (following "View self-healing output" from the
    Incidents screen), the run that actually produced it is shown instead of
    silently answering about a different one.
    """
    store = input_state.get_store()
    if incident_id and _company(entry) is not None and not _incident_visible(entry, incident_id):
        incident_id = None                    # another company's incident: ignore it
    owned = estate.run_for(SESSION, incident_id) if incident_id else None
    if owned is not None and owned.get("mode") == "scenario":
        run = SESSION.run_state(owned["run_id"])
        return output_view.build_output(
            SESSION, output_view.scenario_commit(SESSION, run), run, incident_id)
    if owned is not None:
        try:
            commit = store.get_commit(owned["run_id"])
        except input_state.NotFound:
            commit = None
        if commit is not None and _company(entry) is not None and commit.get("by") != _actor(entry):
            commit = None                     # someone else's run
            incident_id = None
        if commit is not None:
            return output_view.build_output(
                SESSION, commit, SESSION.run_state(owned["run_id"]), incident_id)
    # A scenario run (the Run self-healing button) has no operator input behind
    # it, so it carries its own commit-shaped record.
    latest = SESSION.run_state(SESSION.latest_run_id) if SESSION.latest_run_id else None
    if latest and latest.get("mode") == "scenario":
        if _company(entry) is None or any(
                _incident_visible(entry, i) for i in latest.get("incident_ids", [])):
            return output_view.build_output(
                SESSION, output_view.scenario_commit(SESSION, latest), latest, incident_id)
    commit = store.latest_commit(_actor(entry))
    if commit is None:
        return {"available": False, "reason": "nothing_processed",
                "message": "Nothing has been processed yet. Choose an input and press Process data."}
    run = SESSION.run_state(commit["id"])
    if run is None:
        return {"available": False, "reason": "not_in_session",
                "commit": {k: commit.get(k) for k in ("id", "created_at", "resolved", "source")},
                "message": "This input was processed before the agent session was reset or the server restarted, "
                           "so its results are no longer in memory. Go back to the input and process it again."}
    return output_view.build_output(SESSION, commit, run, incident_id)


@router.get("/input/commits/latest")
def latest_input_commit(entry=Depends(auth.require_any_role)):
    return {"commit": input_state.get_store().latest_commit(_actor(entry))}


@router.get("/input/commits/{commit_id}")
def input_commit(commit_id: str, entry=Depends(auth.require_any_role)):
    try:
        commit = input_state.get_store().get_commit(commit_id)
    except input_state.NotFound:
        raise HTTPException(status_code=404, detail=f"unknown input {commit_id}")
    if _company(entry) is not None and commit.get("by") != _actor(entry):
        raise HTTPException(status_code=404, detail=f"unknown input {commit_id}")
    return commit


@router.post("/uploads")
async def upload_file(request: Request, kind: str, filename: str,
                      entry=Depends(auth.require_any_role)):
    """Upload a screenshot, CSV or text/log file. The bytes are the request body
    (no multipart dependency). The file is tied to the operator's current
    client / group / device / incident, so a client must be selected first."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > uploads.MAX_BYTES:
        raise HTTPException(status_code=413, detail="The file is larger than 10 MB.")
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > uploads.MAX_BYTES:
            raise HTTPException(status_code=413, detail="The file is larger than 10 MB.")
        chunks.append(chunk)
    data = b"".join(chunks)

    store = input_state.get_store()
    _pin_company(entry)
    state = store.get_state(_actor(entry))
    if not state.get("client_id"):
        raise HTTPException(status_code=422,
                            detail="Choose a client first, so the file is kept with that client.")
    try:
        info = uploads.inspect(kind, filename, data)
        record = store.add_upload(
            kind=kind, filename=filename, mime=info["mime"], size=len(data),
            sha256=hashlib.sha256(data).hexdigest(), uploaded_by=_actor(entry),
            client_id=state["client_id"], group_id=state.get("group_id"),
            device_id=state.get("device_id"), incident_id=state.get("incident_id"),
            meta=info["meta"], data=data)
    except uploads.UploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"upload": record, "input": _input_view(_actor(entry), company=_company(entry))}


@router.get("/uploads/{upload_id}/file")
def upload_file_content(upload_id: str, entry=Depends(auth.require_any_role)):
    try:
        rec = input_state.get_store().get_upload(upload_id)
        if _company(entry) is not None and rec.get("client_id") != _company(entry):
            raise input_state.NotFound(upload_id)
        content = uploads.path_for(rec["kind"], rec["stored_name"]).read_bytes()
    except (input_state.NotFound, FileNotFoundError):
        raise HTTPException(status_code=404, detail=f"unknown upload {upload_id}")
    is_image = rec["kind"] == "screenshot"
    return Response(
        content=content,
        media_type=rec["mime"] if is_image else "text/plain; charset=utf-8",
        headers={"X-Content-Type-Options": "nosniff",
                 "Content-Disposition": ("inline" if is_image else "attachment")
                                        + f'; filename="{uploads.safe_name(rec["filename"])}"'})


@router.delete("/uploads/{upload_id}")
def delete_upload(upload_id: str, entry=Depends(auth.require_any_role)):
    try:
        store = input_state.get_store()
        if _company(entry) is not None and store.get_upload(upload_id).get("client_id") != _company(entry):
            raise input_state.NotFound(upload_id)
        store.delete_upload(upload_id)
    except input_state.NotFound:
        raise HTTPException(status_code=404, detail=f"unknown upload {upload_id}")
    return {"deleted": upload_id, "input": _input_view(_actor(entry), company=_company(entry))}


# -- run / reset. Any signed-in account can run a scenario; RESET and the live
# feed controls are OWNER only.
@router.post("/agent/run")
def agent_run(body: RunRequest):
    """RUN SELF-HEALING. Executes Algorithm 1 over the chosen scenario."""
    try:
        return SESSION.run(body.scenario, enable_learning=body.enable_learning)
    except KeyError:
        raise HTTPException(status_code=404,
                            detail=f"unknown scenario '{body.scenario}'")


@router.post("/simulation/{scenario}/run")
def simulation_run(scenario: str, body: Optional[RunRequest] = None):
    try:
        return SESSION.run(
            scenario,
            enable_learning=body.enable_learning if body else None,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown scenario '{scenario}'")


@router.post("/agent/reset", dependencies=[Depends(auth.require_owner)])
def agent_reset(body: Optional[ResetRequest] = None):
    SESSION.reset(enable_learning=body.enable_learning if body else True)
    return SESSION.health()
