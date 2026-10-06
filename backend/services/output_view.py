"""The OUTPUT side: what the system detected, decided and did.

INPUT -> PROCESSING -> INCIDENT -> SELF-HEALING -> SAFETY GATE -> REMEDIATION -> RECOVERY

`build_output` turns a processed operator input (the frozen commit from the Input
screen plus the run that consumed it) and the incident the agent opened into one
view. It invents nothing: the diagnosis, plan, safety report, act result, audit
entry and eUICC before/after snapshots are the objects the existing loop already
produced; this module only arranges them and adds four *derived* fields, each of
which says how it was derived:

    severity   from how far the anomaly score is past the detection threshold,
               the fleet size (blast radius) and whether the agent resolved it
    gate       Proposed / Approved / Blocked / Executed, read off the safety
               report and the act result
    remediation the steps of the signed, gated dispatch with the audit entry's
               real timestamp
    recovery   whether the eUICC returned to its expected state, judged on the
               state captured immediately before and after the action

No FastAPI imports: importable and testable on its own.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from esim_selfhealing.schemas import FEATURE_NAMES

from . import settings

#: what the Output screen calls data that came from a built-in scenario
SCENARIO_LABEL = "SYNTHETIC / SIMULATED"

FLOW = [
    ("input", "Input"),
    ("processing", "Processing"),
    ("incident", "Incident"),
    ("self_healing", "Self-healing"),
    ("safety_gate", "Safety gate"),
    ("remediation", "Remediation"),
    ("recovery", "Recovery"),
]

SEVERITY_LEVELS = ["low", "medium", "high", "critical"]
STATE_LABEL = {
    "RESOLVED": ("Resolved by the agent", "pass"),
    "OPEN": ("Open: the fix did not hold", "hold"),
    "AWAITING_OPERATOR": ("Awaiting operator approval", "hold"),
    "ESCALATED": ("Escalated to the NOC", "block"),
}


# ---------------------------------------------------------------------------
# derived: severity
# ---------------------------------------------------------------------------
def severity(exceedance: Optional[float], fleet_size: int, unresolved: bool,
             deviation: Optional[float] = None) -> Dict[str, Any]:
    """low / medium / high / critical, with the working shown.

    Based on how far the device sits outside its own normal operating range
    at detection - `deviation`, in standard deviations of the worst channel -
    then raised by scope and outcome: +1 if the profile is shared by more
    than one device, +1 if the agent did not resolve it. Capped at critical.

    Deliberately NOT based on the anomaly score. The detector opens an
    incident at the onset of a persistent excursion, which means it fires on
    the first sample to cross the threshold and the score it reports is, by
    construction, just above that threshold whatever the fault. Measured
    across the four fault classes the ratio spans 1.0 to 1.7 - it records how
    abruptly a fault arrived, not how serious it is, and a severity built on
    it put an ISD-P corruption affecting 120 devices in the same band as a
    marginal blip. Deviation separates them: 19 sigma for a ramped radio
    fault, 38 for an ISD-P corruption, 82 for an SM-DP+ outage, against a
    healthy peak of 17. The ratio is still reported, as context.

    The thresholds and both escalations are deployment settings
    (`config.json` -> `severity`, see services/settings.py), because where one
    team draws these lines is not something this project can know. `rules` is
    returned with the verdict so the screen can show what was applied.
    """
    rules = settings.severity_rules()
    ratio = float(exceedance or 0.0)
    dev = float(deviation or 0.0)
    level = 2 if dev >= rules["high_sigma"] else 1 if dev >= rules["medium_sigma"] else 0
    parts = [f"{dev:.0f} sigma outside the device's normal range"]
    if fleet_size > 1 and rules["shared_profile_raises"]:
        level += 1
        parts.append(f"profile shared by {fleet_size} devices")
    if unresolved and rules["unresolved_raises"]:
        level += 1
        parts.append("not resolved by the agent")
    name = SEVERITY_LEVELS[min(level, 3)]
    return {"level": name, "ratio": ratio, "deviation_sigma": dev, "fleet_size": fleet_size,
            "basis": "; ".join(parts), "derived": True, "rules": dict(rules)}


# ---------------------------------------------------------------------------
# derived: safety gate
# ---------------------------------------------------------------------------
def gate_view(session: Any, trace: Dict[str, Any]) -> Dict[str, Any]:
    plan, act = trace.get("plan"), trace.get("act")
    selected = (plan or {}).get("selected")
    env = session.orch.actuator.envelope.cfg
    limits = {"b_max": env.b_max, "rho_max": env.rho_max, "rho_human": env.rho_human,
              "tau_rollback_s": env.tau_rollback_s, "require_signature": bool(env.require_signature)}
    route = {"AUTO-REMEDIATED": "auto-dispatch", "HUMAN-IN-LOOP": "operator-queue",
             "ESCALATION": "noc-escalation"}.get(trace["status"], "noc-escalation")

    if selected is None or act is None:
        return {
            "state": "NONE", "tone": "block", "route": "noc-escalation", "limits": limits,
            "headline": "No action was proposed",
            "detail": "PLAN found no feasible action after constraint filtering, so nothing reached the gate. "
                      "The incident was escalated with the full reasoning trace.",
            "proposed": None, "checks": [], "command": None, "gate_latency_ms": None,
            "steps": [
                {"key": "proposed", "label": "Proposed", "state": "blocked", "detail": "No feasible action"},
                {"key": "decision", "label": "Approved or blocked", "state": "skipped", "detail": "Never reached"},
                {"key": "executed", "label": "Executed", "state": "skipped", "detail": "Nothing to execute"},
            ],
        }

    adm = act["admissibility"]
    approved, executed = bool(adm["admissible"]), bool(act["dispatched"])
    failed = ", ".join(adm["failed_clauses"])
    if executed:
        state = "EXECUTED"
        tone = "pass" if act["success"] else "hold"
        headline = "Approved and executed" if act["success"] else "Executed, but the fault persisted"
        detail = ("All four clauses of the safety envelope passed and the signed command was dispatched to the RSP endpoint."
                  if act["success"] else
                  "The action was admissible and dispatched, did not clear the fault, and was rolled back. "
                  "It was handed to an operator rather than retried.")
    elif approved:
        state, tone = "APPROVED", "agent"
        headline, detail = "Approved, not yet executed", "The action passed the gate and is waiting to be dispatched."
    else:
        state, tone = "BLOCKED", "block"
        headline = ("Blocked, waiting for an operator" if route == "operator-queue"
                    else "Blocked and escalated to the NOC")
        detail = (f"Failed: {failed}. The action was not dispatched"
                  + ("; it is queued for operator approval." if route == "operator-queue"
                     else "; the risk is above the operator limit, so it was escalated with the full trace."))

    return {
        "state": state, "tone": tone, "route": route, "limits": limits,
        "headline": headline, "detail": detail,
        "proposed": {"action": selected["action"], "label": selected["label"]},
        "checks": adm["checks"], "command": trace.get("command"),
        "gate_latency_ms": act.get("gate_latency_ms"),
        "steps": [
            {"key": "proposed", "label": "Proposed", "state": "done", "detail": selected["label"]},
            {"key": "decision", "label": "Approved" if approved else "Blocked",
             "state": "done" if approved else "blocked",
             "detail": "4 of 4 clauses passed" if approved else f"Failed: {failed}"},
            {"key": "executed", "label": "Executed" if executed else "Not executed",
             "state": ("done" if act["success"] else "attention") if executed else "skipped",
             "detail": (act.get("message") or "") if executed else
                       ("Queued for operator" if route == "operator-queue" else "Escalated to NOC")},
        ],
    }


# ---------------------------------------------------------------------------
# derived: remediation
# ---------------------------------------------------------------------------
def remediation_view(session: Any, trace: Dict[str, Any], gate: Dict[str, Any]) -> Dict[str, Any]:
    act, cmd = trace.get("act"), trace.get("command")
    if act is None:
        return {"performed": False, "headline": "No remedial action was taken", "steps": [], "command": None}

    audit = session.audit_for(trace["incident"]["incident_id"])
    ts = audit[-1]["ts"] if audit else None          # the audit entry's own wall-clock time
    adm = act["admissibility"]
    steps: List[Dict[str, Any]] = []

    if cmd:
        steps.append({
            "key": "sign", "action": f"Sign command {cmd['command_id']}",
            "status": "Signed" if cmd["signature_present"] else "Unsigned",
            "tone": "pass" if cmd["signature_present"] else "block", "ts": ts,
            "result": f"{session.signer_algorithm()} · {cmd['signature_preview']}", "latency_ms": None})
    steps.append({
        "key": "gate", "action": f"Safety gate: {act['label']}",
        "status": "Approved" if adm["admissible"] else "Blocked",
        "tone": "pass" if adm["admissible"] else "block", "ts": ts,
        "result": "All four clauses passed" if adm["admissible"] else f"Failed: {', '.join(adm['failed_clauses'])}",
        "latency_ms": act.get("gate_latency_ms")})

    endpoint = (cmd or {}).get("endpoint", "RSP endpoint")
    if act["dispatched"]:
        steps.append({"key": "dispatch", "action": f"{act['label']} → {endpoint}", "status": "Executed",
                      "tone": "agent", "ts": ts, "result": act.get("message") or "", "latency_ms": act.get("dispatch_latency_ms")})
        steps.append({"key": "verify", "action": "Verify the outcome",
                      "status": "Resolved" if act["success"] else "Not resolved",
                      "tone": "pass" if act["success"] else "hold", "ts": ts,
                      "result": "The fault cleared" if act["success"] else "The fault persisted", "latency_ms": None})
        if act["rolled_back"]:
            steps.append({"key": "rollback", "action": f"Roll back via {(cmd or {}).get('inverse_endpoint') or 'inverse command'}",
                          "status": "Rolled back", "tone": "hold", "ts": ts,
                          "result": "Pre-computed inverse dispatched", "latency_ms": None})
    else:
        steps.append({"key": "dispatch", "action": f"{act['label']} → {endpoint}", "status": "Not executed",
                      "tone": "block", "ts": None, "result": act.get("message") or "", "latency_ms": None})

    if trace["status"] == "HUMAN-IN-LOOP":
        steps.append({"key": "handoff", "action": "Hand over to an operator", "status": "Queued", "tone": "hold",
                      "ts": None, "result": "In the operator approval queue" if session.in_human_queue(
                          trace["incident"]["incident_id"]) else "Awaiting operator", "latency_ms": None})
    elif trace["status"] == "ESCALATION":
        steps.append({"key": "handoff", "action": "Escalate to the NOC", "status": "Escalated", "tone": "block",
                      "ts": None, "result": "Full reasoning trace attached", "latency_ms": None})

    return {"performed": bool(act["dispatched"]), "headline": gate["headline"], "steps": steps,
            "command": cmd, "audit": audit[-1] if audit else None, "recorded_at": ts}


# ---------------------------------------------------------------------------
# derived: recovery
# ---------------------------------------------------------------------------
def _expected(after: Dict[str, Any], before: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = [
        ("Profile checksum", "OK", lambda d: (d["checksum_ok"], "OK" if d["checksum_ok"] else "Mismatch")),
        ("Key epochs (eUICC / SM-SR)", "In sync",
         lambda d: (d["key_epoch"] == d["smsr_key_epoch"], f"{d['key_epoch']} / {d['smsr_key_epoch']}")),
        ("SM-DP+ reachable", "Yes", lambda d: (d["smdp_reachable"], "Yes" if d["smdp_reachable"] else "No")),
        ("Radio alarm", "Clear", lambda d: (not d["radio_alarm"], "Clear" if not d["radio_alarm"] else "Raised")),
        ("eUICC health (RSP model)", "Healthy", lambda d: (d["healthy"], "Healthy" if d["healthy"] else "Unhealthy")),
    ]
    out = []
    for label, expected, f in rows:
        ok_after, shown_after = f(after)
        ok_before, shown_before = f(before) if before else (None, "—")
        out.append({"label": label, "expected": expected, "before": shown_before, "after": shown_after,
                    "ok_before": ok_before, "ok_after": ok_after})
    return out


def _live_telemetry_check(session: Any, run: Dict[str, Any], incident_id: str, euicc_id: str) -> Dict[str, Any]:
    """After the incident, are the device's own recent samples back under the threshold?"""
    lo, hi = run["reading_range"]
    readings = [r for r in list(session.orch.monitor.readings)[lo:hi] if r.observation.euicc_id == euicc_id]
    idx = next((i for i, r in enumerate(readings)
                if r.incident is not None and r.incident.incident_id == incident_id), None)
    after = readings[idx + 1:] if idx is not None else []
    recent = after[-10:]
    if len(recent) < 5:
        return {"applicable": True, "state": "waiting", "samples": len(after),
                "note": f"Waiting for more samples after the action ({len(after)} of 5 needed)."}
    ok = all((not r.triggered) and r.score <= r.threshold for r in recent)
    return {"applicable": True, "state": "back_to_normal" if ok else "still_abnormal", "samples": len(after),
            "note": ("The last 10 samples are back under the detection threshold." if ok
                     else "Recent samples still exceed the detection threshold.")}


def recovery_view(session: Any, run: Dict[str, Any], trace: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if trace is None:
        return {"verdict": "HEALTHY" if run["status"] == "complete" else "MONITORING",
                "tone": "pass" if run["status"] == "complete" else "agent",
                "headline": ("No incident, nothing to recover" if run["status"] == "complete"
                             else "Monitoring: no incident yet"),
                "detail": (f"The detector stayed quiet across {run['samples']} samples." if run["status"] == "complete"
                           else "The live feed is being watched. If a fault appears the agent will act and this section will show whether the device recovered."),
                "checks": [], "telemetry": None, "loop_time_ms": None}

    act, status = trace.get("act"), trace["status"]
    cap = session.incident_capture(trace["incident"]["incident_id"])
    euicc_id = trace["incident"]["observation"]["euicc_id"]
    before = cap.device_before if cap else None
    after = cap.device_after if cap else None
    checks = _expected(after, before) if after else []

    if act and act["dispatched"] and act["success"] and after and after["healthy"]:
        verdict, tone, headline = "RECOVERED", "pass", "Recovered: the eUICC is back in its expected state"
        detail = "The action cleared the fault and every expected condition below holds."
    elif act and act["dispatched"] and act["success"]:
        verdict, tone, headline = "PARTIAL", "hold", "Fault cleared, but the eUICC is not fully healthy"
        detail = "The RSP reported success, but at least one expected condition below still fails."
    elif act and act["dispatched"]:
        verdict, tone, headline = "NOT_RECOVERED", "block", "Not recovered: the fault persisted"
        detail = "The action was rolled back and the incident handed to an operator. The eUICC is still in its faulted state."
    elif status == "HUMAN-IN-LOOP":
        verdict, tone, headline = "AWAITING_OPERATOR", "hold", "Not attempted: waiting for an operator"
        detail = "The gate blocked autonomous execution, so nothing was changed and the eUICC is still faulted."
    else:
        verdict, tone, headline = "ESCALATED", "block", "Not attempted: escalated to the NOC"
        detail = "No action was executed. The eUICC is still faulted and the NOC has the full reasoning trace."

    if run["mode"] == "realtime":
        tele = _live_telemetry_check(session, run, trace["incident"]["incident_id"], euicc_id)
    else:
        tele = {"applicable": False, "state": "not_applicable",
                "note": "A stored dataset is a fixed recording, so its telemetry cannot react to the action. "
                        "Recovery is judged on the simulated eUICC state captured immediately before and after it."}
    return {"verdict": verdict, "tone": tone, "headline": headline, "detail": detail,
            "checks": checks, "telemetry": tele,
            "state_source": "eUICC state read from the RSP model immediately before and after the action",
            "loop_time_ms": trace.get("total_latency_ms")}


# ---------------------------------------------------------------------------
# the whole view
# ---------------------------------------------------------------------------
def _flow(commit: Dict[str, Any], run: Dict[str, Any], incident: Optional[Dict[str, Any]],
          gate: Optional[Dict[str, Any]], remediation: Optional[Dict[str, Any]],
          recovery: Dict[str, Any], trace: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    r = commit["resolved"]
    live = run["mode"] == "realtime"
    S = lambda key, state, headline, detail="": {  # noqa: E731
        "key": key, "label": dict(FLOW)[key], "state": state, "headline": headline, "detail": detail}
    flow = [
        S("input", "done", (r.get("client") or {}).get("name") or "Built-in scenario",
          f"{(r.get('device') or {}).get('device_label') or (r.get('device') or {}).get('euicc_id') or 'Unregistered device'} · {commit['source']['type']}"),
        S("processing", "active" if (live and run["status"] == "monitoring") else "done",
          f"{run['samples']} samples {'so far' if live else 'processed'}",
          "Real-time feed" if live else f"{fmt_ms(run.get('wall_ms'))} through the agent loop"),
    ]
    if incident is None:
        flow.append(S("incident", "active" if live else "done",
                      "Monitoring, no incident yet" if live else "No incident detected",
                      "Waiting for the detector to fire" if live else "The detector stayed quiet"))
        for key in ("self_healing", "safety_gate", "remediation"):
            flow.append(S(key, "skipped", "Not needed", "No incident"))
        flow.append(S("recovery", "done" if not live else "pending",
                      "Nothing to recover" if not live else "Pending", ""))
        return flow

    flow.append(S("incident", "done", f"{incident['incident_id']} · {incident['severity']['level']} severity",
                  incident["fault_label"]))
    conf = trace["diagnosis"]["confidence"]
    flow.append(S("self_healing", "done", trace["selected_label"] or "No feasible action",
                  f"Diagnosed {incident['fault_label']}" + (f" at {conf:.0%}" if conf is not None else "")))
    gstate = {"EXECUTED": "done" if gate["tone"] == "pass" else "attention", "APPROVED": "done",
              "BLOCKED": "blocked", "NONE": "blocked"}[gate["state"]]
    flow.append(S("safety_gate", gstate, gate["headline"], gate["proposed"]["label"] if gate["proposed"] else ""))
    performed = remediation["performed"]
    flow.append(S("remediation", ("done" if trace["act"] and trace["act"]["success"] else "attention") if performed else "blocked",
                  "Executed" if performed else "Not executed",
                  (trace["act"] or {}).get("message", "") if performed else "No change was made to the device"))
    rstate = {"RECOVERED": "done", "PARTIAL": "attention", "NOT_RECOVERED": "blocked",
              "AWAITING_OPERATOR": "attention", "ESCALATED": "blocked"}[recovery["verdict"]]
    flow.append(S("recovery", rstate, recovery["headline"].split(":")[0], ""))
    return flow


def fmt_ms(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{v / 1000:.2f} s" if v >= 1000 else f"{v:.0f} ms"


def _incident_header(session: Any, commit: Dict[str, Any], run: Dict[str, Any], trace: Dict[str, Any]) -> Dict[str, Any]:
    inc, r = trace["incident"], commit["resolved"]
    cap = session.incident_capture(inc["incident_id"])
    fleet = int(cap.fleet_size) if cap else 1
    status = trace["status"]
    act = trace.get("act")
    if status == "AUTO-REMEDIATED" and act and act["success"]:
        code = "RESOLVED"
    elif status == "HUMAN-IN-LOOP" and act and act["dispatched"]:
        code = "OPEN"
    elif status == "HUMAN-IN-LOOP":
        code = "AWAITING_OPERATOR"
    else:
        code = "ESCALATED"
    label, tone = STATE_LABEL[code]
    return {
        "incident_id": inc["incident_id"], "fault_class": trace["diagnosis"]["fault_class"],
        "fault_label": trace["diagnosis"]["fault_label"], "status": status,
        "state": {"code": code, "label": label, "tone": tone},
        "severity": severity(inc.get("exceedance"), fleet, status != "AUTO-REMEDIATED",
                             deviation=inc.get("deviation_sigma")),
        "detected_at": inc["opened_at"],
        "detected_basis": "live" if run["mode"] == "realtime" else "dataset timeline",
        "client": r["client"], "group": r["group"], "device": r["device"], "cell_id": inc["observation"]["cell_id"],
        "source": trace.get("source"), "fleet_size": fleet,
    }


def scenario_commit(session: Any, run: Dict[str, Any]) -> Dict[str, Any]:
    """The commit-shaped record a SCENARIO run stands on.

    "Process data" starts from an operator's input selection, which is committed
    and then processed. "Run self-healing" has no such selection: it replays a
    built-in scenario. The Output screen shows both, so a scenario run is given
    the same shape here, filled from the scenario and from the registry context
    of the device the incident was opened on. Nothing is invented: where a
    processed input would name the operator's choices, this names the scenario.
    """
    from . import estate                                    # local: avoids a cycle
    first = (run.get("incident_ids") or [None])[0]
    euicc_id = session.incident_device(first) if first else None
    ctx = estate.context_for(euicc_id) or {}
    scenario = run.get("scenario") or {}
    return {
        "id": run["id"], "created_at": run.get("started_at"), "by": None,
        "state": None,
        "resolved": {
            "client": ({"client_id": ctx.get("client_id"), "name": ctx.get("client_name")}
                       if ctx.get("client_name") else None),
            "group": ({"group_id": ctx.get("group_id"), "name": ctx.get("group_name")}
                      if ctx.get("group_name") else None),
            "device": ({"euicc_id": euicc_id, "device_label": ctx.get("device_label"),
                        "iccid": ctx.get("iccid"), "profile_name": ctx.get("profile_name"),
                        "cell_id": ctx.get("cell_id"), "fleet_size": ctx.get("fleet_size")}
                       if euicc_id else None),
            "network": ({"network_id": ctx.get("network_id"), "name": ctx.get("network_name"),
                         "plmn": ctx.get("plmn")} if ctx.get("network_name") else None),
            "rsp": ({"rsp_env_id": ctx.get("rsp_env_id"), "name": ctx.get("rsp_env_name")}
                    if ctx.get("rsp_env_name") else None),
            "dataset": {
                "id": run.get("dataset_id"), "title": run.get("dataset_title"),
                "path": None, "kind": "scenario", "provenance": SCENARIO_LABEL,
                "rows": run.get("samples"), "fields": list(FEATURE_NAMES),
                "generator": "esim_selfhealing.telemetry (built-in scenario)",
                "description": scenario.get("description"),
            },
        },
        "source": {"mode": "scenario", "type": SCENARIO_LABEL,
                   "label": run.get("dataset_title") or run.get("dataset_id")},
        "provenance": [{
            "role": "Data source", "type_key": "synthetic", "type": SCENARIO_LABEL,
            "source": "Built-in scenario (Run self-healing)",
            "dataset": run.get("dataset_id"),
            "client": (ctx.get("client_name") or None), "device": ctx.get("device_label") or euicc_id,
            "fields": list(FEATURE_NAMES),
            "note": scenario.get("description") or
                    "A scenario generated by this project, run through the same loop as a processed input.",
        }],
        "attachments": [], "readiness": None,
    }


def build_output(session: Any, commit: Dict[str, Any], run: Dict[str, Any],
                 incident_id: Optional[str] = None) -> Dict[str, Any]:
    """Everything the Output screen shows, for one processed input."""
    ids = list(run["incident_ids"])
    chosen = incident_id if incident_id in ids else (ids[-1] if ids else None)
    briefs = {i["incident_id"]: i for i in session.incidents()}
    trace = session.incident(chosen) if chosen else None

    incident = gate = remediation = None
    if trace is not None:
        incident = _incident_header(session, commit, run, trace)
        gate = gate_view(session, trace)
        remediation = remediation_view(session, trace, gate)
    recovery = recovery_view(session, run, trace)

    return {
        "available": True,
        "commit": {k: commit.get(k) for k in ("id", "created_at", "by", "state", "resolved", "source",
                                              "provenance", "attachments", "readiness")},
        "run": {k: run.get(k) for k in ("id", "mode", "status", "started_at", "finished_at", "wall_ms",
                                        "samples", "false_positives", "dataset_id", "dataset_title",
                                        "dataset_path", "device_ids", "feed_running", "started_feed")},
        "flow": _flow(commit, run, incident, gate, remediation, recovery, trace),
        "incidents": [{"incident_id": i, "fault_label": briefs[i]["fault_label"], "status": briefs[i]["status"],
                       "opened_at": briefs[i]["opened_at"]} for i in ids if i in briefs],
        "selected_incident_id": chosen,
        "incident": incident, "trace": trace, "gate": gate, "remediation": remediation, "recovery": recovery,
        "top_features": _top_features(session, chosen) if chosen else [],
    }


def _top_features(session: Any, incident_id: str) -> List[Dict[str, Any]]:
    """Which telemetry channels moved most at detection (the monitor's own z-scores)."""
    for rec in session.records:
        if rec.incident.incident_id == incident_id:
            try:
                return [{"feature": f, "z": float(z)}
                        for f, z in session.orch.monitor.top_features(rec.incident.observation, k=3)]
            except Exception:                              # noqa: BLE001 - advisory only
                return []
    return []
