"""Output / gated self-healing: process an input, then show what was detected,
decided and done.

    python tests/test_output.py          # no pytest required
    pytest tests/test_output.py -q       # or under pytest

Everything here runs the REAL loop on the stored datasets (no mocks). Each
dataset takes a different branch, so together they cover every outcome:

    isdp_corruption_48213    auto-remediated        approved -> executed -> recovered
    key_desync_77145         dispatched, failed     executed -> rolled back -> handed to an operator
    shared_fleet_90031       escalated              no feasible action, blast radius 120
    healthy_baseline_100004  no incident            nothing to detect or recover

The last group calls the route handlers and is skipped without FastAPI.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services import datasets, input_state, output_view
from backend.services.agent_service import AgentSession
from backend.services.input_state import InputStore

FLOW_KEYS = ["input", "processing", "incident", "self_healing", "safety_gate", "remediation", "recovery"]


def _process(session: AgentSession, dataset_id: str, run_id: str = "INP-0001"):
    """What POST /api/input/process does, without HTTP."""
    reg, dsets = datasets.registry(), datasets.list_datasets()
    ds = next(d for d in dsets if d["id"] == dataset_id)
    state = input_state.apply_patch(
        None, {"client_id": ds["client_id"], "data_source": {"mode": "synthetic", "dataset_id": dataset_id}},
        reg=reg, dsets=dsets, incident_device=lambda i: None)
    rt = {"running": False, "devices": []}
    commit = {**input_state.build_commit(state, reg=reg, dsets=dsets, realtime=rt, attachments=[]),
              "id": run_id, "created_at": time.time(), "by": "owner"}
    run = session.process_dataset(
        run_id, datasets.dataset_observations(dataset_id),
        {d["euicc_id"]: d["fleet_size"] for d in reg["devices"]},
        {"dataset_id": ds["id"], "dataset_title": ds["title"], "dataset_path": ds["path"]})
    return commit, session.run_state(run["id"]), output_view.build_output(session, commit, session.run_state(run["id"]))


def _steps(view, key):
    return {s["key"]: s for s in view[key]["steps"]}


# ---------------------------------------------------------------------------
def test_auto_remediated_flows_from_input_to_recovery():
    s = AgentSession()
    commit, run, v = _process(s, "isdp_corruption_48213")

    assert [f["key"] for f in v["flow"]] == FLOW_KEYS, "the seven stages, in order"
    assert run["status"] == "complete" and run["samples"] == 300 and len(run["incident_ids"]) == 1

    # the selected input context is what the output reports
    inc = v["incident"]
    assert inc["client"]["name"] == "Meridian Logistics" and inc["group"]["name"] == "Depot trackers"
    assert inc["device"]["euicc_id"] == "89330000000048213" and inc["device"]["iccid"].startswith("8944")
    assert v["commit"]["resolved"]["dataset"]["id"] == "isdp_corruption_48213"
    assert v["commit"]["source"]["type"] == "SYNTHETIC / SIMULATED"
    assert v["run"]["dataset_path"] == "data/telemetry/isdp_corruption_48213.csv"

    # incident header
    assert inc["incident_id"].startswith("INC-") and inc["fault_label"] == "ISD-P corruption"
    assert inc["state"]["code"] == "RESOLVED" and inc["severity"]["level"] in ("low", "medium", "high", "critical")
    assert inc["severity"]["derived"] and "sigma" in inc["severity"]["basis"]
    assert inc["severity"]["deviation_sigma"] > inc["severity"]["rules"]["medium_sigma"], (
        "severity reports how far outside its normal range the device sat, not the "
        "anomaly score - which sits just above the threshold for every fault")
    assert inc["detected_at"] > 1.7e9 and inc["detected_basis"] == "dataset timeline"

    # the agent's real trace
    t = v["trace"]
    assert t["diagnosis"]["fault_class"] == "isdp_corruption" and t["plan"]["selected"]["action"] == "profile_repush"
    assert len(t["diagnosis"]["trace"]) >= 2 and t["stages"], "the recorded ReAct steps and stage trace are carried through"

    # gate
    g = v["gate"]
    assert g["state"] == "EXECUTED" and g["tone"] == "pass" and g["route"] == "auto-dispatch"
    assert [x["state"] for x in g["steps"]] == ["done", "done", "done"]
    assert [x["label"] for x in g["steps"]] == ["Proposed", "Approved", "Executed"]
    assert len(g["checks"]) == 4 and all(c["passed"] for c in g["checks"])

    # remediation: action, status, timestamp, result
    r = v["remediation"]
    assert r["performed"] and [x["key"] for x in r["steps"]] == ["sign", "gate", "dispatch", "verify"]
    for step in r["steps"]:
        assert step["action"] and step["status"] and step["ts"] > 1.7e9 and step["result"]
    assert r["steps"][2]["status"] == "Executed" and r["steps"][3]["status"] == "Resolved"
    assert abs(r["recorded_at"] - time.time()) < 60, "the timestamp is the audit entry's real wall-clock time"

    # recovery: judged on the state right before / after the action
    rec = v["recovery"]
    assert rec["verdict"] == "RECOVERED" and rec["tone"] == "pass"
    by = {c["label"]: c for c in rec["checks"]}
    assert by["Profile checksum"]["before"] == "Mismatch" and by["Profile checksum"]["after"] == "OK"
    assert by["eUICC health (RSP model)"]["ok_before"] is False and by["eUICC health (RSP model)"]["ok_after"] is True
    assert rec["telemetry"]["applicable"] is False, "a recording cannot react to the action"
    assert [f["state"] for f in v["flow"]] == ["done"] * 7


def test_recovery_is_not_fooled_by_the_recording_replaying_the_fault():
    """After the fix, the rest of the recording keeps carrying the fault label and the
    orchestrator re-corrupts the simulated device. Judging recovery on end-of-run state
    would call a successful fix a failure."""
    s = AgentSession()
    _, _, v = _process(s, "isdp_corruption_48213")
    end_state = s.orch.rsp.devices["89330000000048213"].healthy()
    assert end_state is False, "the end-of-run device state does show the replayed fault"
    assert v["recovery"]["verdict"] == "RECOVERED", "but recovery uses the state right after the action"


def test_failed_action_is_rolled_back_and_handed_over():
    s = AgentSession()
    _, _, v = _process(s, "key_desync_77145")
    g = v["gate"]
    assert g["state"] == "EXECUTED" and g["tone"] == "hold" and g["route"] == "operator-queue"
    assert g["steps"][2]["state"] == "attention"
    keys = [x["key"] for x in v["remediation"]["steps"]]
    assert keys == ["sign", "gate", "dispatch", "verify", "rollback", "handoff"], keys
    verify = next(x for x in v["remediation"]["steps"] if x["key"] == "verify")
    assert verify["status"] == "Not resolved"
    assert v["incident"]["state"]["code"] == "OPEN"
    rec = v["recovery"]
    assert rec["verdict"] == "NOT_RECOVERED" and rec["tone"] == "block"
    assert any(c["ok_after"] is False for c in rec["checks"]), "at least one expected condition still fails"
    assert v["flow"][-1]["state"] == "blocked" and v["flow"][4]["state"] == "attention"


def test_escalation_when_no_action_is_feasible():
    s = AgentSession()
    _, _, v = _process(s, "shared_fleet_90031")
    assert v["incident"]["fleet_size"] == 120, "the registry's fleet size reached PLAN"
    assert v["incident"]["severity"]["level"] == "critical"
    assert "120 devices" in v["incident"]["severity"]["basis"]
    g = v["gate"]
    assert g["state"] in ("NONE", "BLOCKED") and g["route"] == "noc-escalation" and g["tone"] == "block"
    assert v["remediation"]["performed"] is False
    assert v["incident"]["state"]["code"] == "ESCALATED"
    assert v["recovery"]["verdict"] == "ESCALATED"
    assert [f["state"] for f in v["flow"]][4:] == ["blocked", "blocked", "blocked"]


def test_healthy_dataset_shows_no_incident_and_nothing_to_recover():
    s = AgentSession()
    _, run, v = _process(s, "healthy_baseline_100004")
    assert run["incident_ids"] == [] and v["incident"] is None and v["trace"] is None
    assert v["recovery"]["verdict"] == "HEALTHY" and "300 samples" in v["recovery"]["detail"]
    states = {f["key"]: f["state"] for f in v["flow"]}
    assert states["incident"] == "done" and states["self_healing"] == "skipped" and states["recovery"] == "done"


def test_processing_the_same_input_twice_gives_the_same_answer():
    s = AgentSession()
    _, _, first = _process(s, "isdp_corruption_48213", "INP-0001")
    _, _, second = _process(s, "isdp_corruption_48213", "INP-0002")
    assert first["incident"] and second["incident"], "without the per-device reset the second pass finds nothing"
    assert first["incident"]["incident_id"] != second["incident"]["incident_id"]
    assert first["gate"]["state"] == second["gate"]["state"] == "EXECUTED"
    assert second["run"]["samples"] == 300 and s.latest_run_id == "INP-0002"


def test_telemetry_is_scoped_to_the_processed_run():
    s = AgentSession()
    _process(s, "isdp_corruption_48213", "INP-0001")
    _, run2, v2 = _process(s, "isdp_corruption_48213", "INP-0002")
    inc = v2["selected_incident_id"]
    tele = s.telemetry(limit=90, incident_id=inc, run_id="INP-0002")
    assert tele["samples"] and tele["marker_index"] is not None
    assert tele["samples"][tele["marker_index"]]["incident_id"] == inc
    lo, hi = run2["reading_range"]
    assert len(s.orch.monitor.readings) >= hi > lo
    whole = s.telemetry(limit=600, run_id="INP-0002")
    assert len(whole["samples"]) == 300, "only this run's readings, not the earlier pass"
    assert {x["euicc_id"] for x in whole["samples"]} == {"89330000000048213"}


def test_real_time_input_attaches_to_the_live_feed():
    s = AgentSession()
    s.realtime.running = True                    # pretend the feed thread is up; we feed samples by hand
    dev = "89330000000100001"
    reg, dsets = datasets.registry(), datasets.list_datasets()
    state = input_state.apply_patch(None, {"client_id": "CL-001", "device_id": dev,
                                           "data_source": {"mode": "realtime"}},
                                    reg=reg, dsets=dsets, incident_device=lambda i: None)
    rt = {"running": True, "devices": [d["euicc_id"] for d in s.realtime.devices]}
    commit = {**input_state.build_commit(state, reg=reg, dsets=dsets, realtime=rt, attachments=[]),
              "id": "INP-0009", "created_at": time.time(), "by": "owner"}
    run = s.process_realtime("INP-0009", dev, {})
    assert run["mode"] == "realtime" and run["started_feed"] is False and run["status"] == "monitoring"

    device = next(d for d in s.realtime.devices if d["euicc_id"] == dev)
    v0 = output_view.build_output(s, commit, s.run_state("INP-0009"))
    assert v0["incident"] is None and v0["recovery"]["verdict"] == "MONITORING"
    assert v0["flow"][1]["state"] == "active", "processing is still going on"

    for _ in range(80):
        s.ingest_realtime(s.realtime._sample(device))
    s.realtime.inject_fault("isdp_corruption", euicc_id=dev, duration=30)
    for _ in range(45):
        s.ingest_realtime(s.realtime._sample(device))
    for _ in range(15):
        s.ingest_realtime(s.realtime._sample(device))

    v = output_view.build_output(s, commit, s.run_state("INP-0009"))
    assert v["run"]["mode"] == "realtime" and v["run"]["samples"] == 140
    assert v["incident"] and v["incident"]["detected_basis"] == "live"
    assert v["incident"]["device"]["euicc_id"] == dev and v["incident"]["client"]["name"] == "Meridian Logistics"
    assert v["gate"]["state"] in ("EXECUTED", "BLOCKED", "NONE") and v["recovery"]["verdict"]
    tele = v["recovery"]["telemetry"]
    assert tele["applicable"] and tele["state"] in ("back_to_normal", "still_abnormal", "waiting")
    assert v["flow"][1]["state"] == "active"


# ---------------------------------------------------------------------------
# route handlers (skipped without FastAPI)
# ---------------------------------------------------------------------------
def test_handlers_process_then_output_and_survive_going_back():
    try:
        from backend.api import routes
    except ImportError:
        print("        (skipped: FastAPI not installed)")
        return
    import os
    import shutil
    import tempfile
    from backend.services import reports
    from backend.services.agent_service import SESSION
    owner = {"username": "owner", "role": "owner"}
    tmp = tempfile.mkdtemp()
    old = os.environ.get("UPLOADS_DIR")
    os.environ["UPLOADS_DIR"] = tmp
    input_state.use_store(InputStore(":memory:"))
    reports.use_store(reports.ReportStore(":memory:"))
    P, D = routes.InputPatch, routes.DataSourcePatch

    def status(fn, *a, **k):
        try:
            fn(*a, **k)
        except Exception as exc:                          # HTTPException
            return getattr(exc, "status_code", None)
        return 200

    try:
        SESSION.reset()
        assert routes.output_current(None, owner)["reason"] == "nothing_processed"
        assert status(routes.process_input, owner) == 422, "an empty input cannot be processed"

        routes.put_input_state(P(client_id="CL-001"), owner)
        routes.put_input_state(P(data_source=D(mode="synthetic", dataset_id="isdp_corruption_48213")), owner)
        routes.put_input_state(P(rsp_env_id="RSP-SIM-2"), owner)         # an override the operator made
        before = routes.get_input_state(owner)["state"]

        res = routes.process_input(owner)
        assert res["commit"]["id"] == "INP-0001" and res["run"]["status"] == "complete"
        out = routes.output_current(None, owner)
        assert out["available"] and out["incident"]["client"]["name"] == "Meridian Logistics"
        assert out["commit"]["resolved"]["rsp_environment"]["rsp_env_id"] == "RSP-SIM-2", "the operator's override is reflected"
        assert out["gate"]["state"] == "EXECUTED" and out["recovery"]["verdict"] == "RECOVERED"
        tele = routes.telemetry(120, None, out["selected_incident_id"], out["run"]["id"], owner)
        assert tele["marker_index"] is not None and len(tele["samples"]) > 60

        # "BACK TO INPUT": nothing was lost
        after = routes.get_input_state(owner)
        assert after["state"] == before, "the selection is exactly as the operator left it"
        assert after["readiness"]["ready"] and routes.latest_input_commit(owner)["commit"]["id"] == "INP-0001"

        # process again from the restored input: same answer, new incident
        again = routes.process_input(owner)
        assert again["commit"]["id"] == "INP-0002"
        out2 = routes.output_current(None, owner)
        assert out2["incident"]["incident_id"] != out["incident"]["incident_id"] and out2["gate"]["state"] == "EXECUTED"

        # the session is reset: the result is gone, and the output says so plainly
        SESSION.reset()
        gone = routes.output_current(None, owner)
        assert gone["available"] is False and gone["reason"] == "not_in_session" and gone["commit"]["id"] == "INP-0002"
        assert routes.get_input_state(owner)["state"] == before, "the input survives the reset"

        # a tampered dataset is refused before anything is committed
        routes.put_input_state(P(data_source=D(mode="synthetic", dataset_id="isdp_corruption_48213")), owner)
        n_before = routes.latest_input_commit(owner)["commit"]["id"]
        real = datasets.dataset_observations
        datasets.dataset_observations = lambda i: (_ for _ in ()).throw(datasets.DatasetError("checksum mismatch"))
        try:
            assert status(routes.process_input, owner) == 422
        finally:
            datasets.dataset_observations = real
        assert routes.latest_input_commit(owner)["commit"]["id"] == n_before, "no commit was recorded"
    finally:
        input_state.use_store(None)
        reports.use_store(None)
        SESSION.reset()
        if old is None:
            os.environ.pop("UPLOADS_DIR", None)
        else:
            os.environ["UPLOADS_DIR"] = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_scenario_run_opens_the_same_output_screen():
    """"Run self-healing" has no operator input behind it, but drives the same
    seven-stage Output view, named as the built-in scenario."""
    session = AgentSession()
    rep = session.run("isdp_corruption", enable_learning=False)
    run = session.run_state(rep["run_id"])
    assert run["mode"] == "scenario" and run["incident_ids"]

    commit = output_view.scenario_commit(session, run)
    assert commit["source"]["mode"] == "scenario"
    assert commit["source"]["type"] == "SYNTHETIC / SIMULATED"
    # the registry context still names the client and the device
    assert commit["resolved"]["client"]["name"] == "Meridian Logistics"
    assert commit["resolved"]["device"]["device_label"] == "Yard tracker Y-48213"
    assert commit["provenance"][0]["role"] == "Data source"
    assert commit["attachments"] == [] and commit["readiness"] is None

    view = output_view.build_output(session, commit, run)
    assert [f["key"] for f in view["flow"]] == FLOW_KEYS
    assert view["available"] and view["incident"] and view["gate"] and view["remediation"] and view["recovery"]
    assert view["trace"]["status"] in ("AUTO-REMEDIATED", "HUMAN-IN-LOOP", "ESCALATION")
    assert view["incident"]["client"]["name"] == "Meridian Logistics"
    assert view["selected_incident_id"] in run["incident_ids"]
    # the telemetry the screen charts is this run's
    assert session.telemetry(limit=120, run_id=run["id"])["samples"]

    # a second scenario run gets its own id and does not disturb the first
    first_id = run["id"]
    second = session.run_state(session.run("smdp_outage", enable_learning=False)["run_id"])
    assert second["id"] != first_id and session.latest_run_id == second["id"]
    assert session.run_state(first_id)["incident_ids"] == run["incident_ids"]


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
