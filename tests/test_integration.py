"""One connected system: the same data and the same relationships everywhere.

    python tests/test_integration.py     # no pytest required
    pytest tests/test_integration.py -q  # or under pytest

The other suites each test one screen. This one follows a single incident the
whole way and insists every screen tells the same story about it:

    INPUT -> DATA SOURCE -> DEVICE / NETWORK -> TELEMETRY -> INCIDENT
          -> SELF-HEALING -> SAFETY GATE -> REMEDIATION -> RECOVERY
          -> CLIENT REPORT -> RESOLUTION

Everything goes through the route handlers, so it exercises what the browser
actually calls. Skipped without FastAPI.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services import estate, input_state, reports
from backend.services.input_state import InputStore

OWNER = {"username": "owner", "role": "owner"}
CLIENT = {"username": "client", "role": "client", "client_id": "CL-001"}   # as auth.login builds it


def _routes():
    try:
        from backend.api import routes
    except ImportError:
        return None
    return routes


class Harness:
    """A clean input/report store and agent session per test."""

    def __init__(self):
        self.tmp = tempfile.mkdtemp()
        self._old = os.environ.get("UPLOADS_DIR")
        os.environ["UPLOADS_DIR"] = self.tmp
        input_state.use_store(InputStore(":memory:"))
        reports.use_store(reports.ReportStore(":memory:"))
        from backend.services.agent_service import SESSION
        self.session = SESSION
        SESSION.reset()

    def close(self):
        input_state.use_store(None)
        reports.use_store(None)
        self.session.reset()
        if self._old is None:
            os.environ.pop("UPLOADS_DIR", None)
        else:
            os.environ["UPLOADS_DIR"] = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)


def _process(routes, dataset_id="isdp_corruption_48213", client_id="CL-001"):
    P, D = routes.InputPatch, routes.DataSourcePatch
    routes.put_input_state(P(client_id=client_id), OWNER)
    routes.put_input_state(P(data_source=D(mode="synthetic", dataset_id=dataset_id)), OWNER)
    res = routes.process_input(OWNER)
    return res["commit"], res["run"]


# ---------------------------------------------------------------------------
def test_one_incident_keeps_its_identity_on_every_screen():
    routes = _routes()
    if routes is None:
        print("        (skipped: FastAPI not installed)")
        return
    h = Harness()
    try:
        commit, run = _process(routes)
        out = routes.output_current(None, OWNER)
        incident_id = out["selected_incident_id"]
        euicc_id = out["incident"]["device"]["euicc_id"]

        # INPUT / OUTPUT
        assert out["incident"]["client"]["name"] == "Meridian Logistics"
        assert out["incident"]["device"]["device_label"] == "Yard tracker Y-48213"

        # INCIDENTS: the same client and device, not a bare number
        listed = next(i for i in routes.incidents(OWNER)["incidents"] if i["incident_id"] == incident_id)
        assert listed["context"]["client_name"] == "Meridian Logistics"
        assert listed["context"]["device_label"] == "Yard tracker Y-48213"
        assert listed["context"]["iccid"] == out["commit"]["resolved"]["device"]["iccid"]
        assert listed["run"]["run_id"] == commit["id"], "the incident points back at the input that opened it"
        assert listed["observation"]["euicc_id"] == euicc_id

        # one incident's full trace carries the same join
        trace = routes.incident(incident_id, OWNER)
        assert trace["context"]["client_name"] == "Meridian Logistics"
        assert trace["run"]["run_id"] == commit["id"]

        # DEVICES: same device, same client, and it knows this incident
        dev = next(d for d in routes.devices(OWNER)["devices"] if d["euicc_id"] == euicc_id)
        assert dev["context"]["device_label"] == "Yard tracker Y-48213"
        detail = routes.device(euicc_id, OWNER)
        assert detail["context"]["client_name"] == "Meridian Logistics"
        assert detail["context"]["network_name"] and detail["context"]["profile_name"]
        assert incident_id in [i["incident_id"] for i in detail["incidents"]]
        assert detail["incidents"][0]["context"]["client_name"] == "Meridian Logistics"

        # TELEMETRY: the same readings the Output screen charted
        tele = routes.telemetry(120, None, incident_id, commit["id"], OWNER)
        assert tele["euicc_id"] == euicc_id and tele["marker_index"] is not None
        assert tele["samples"][tele["marker_index"]]["incident_id"] == incident_id
        by_device = routes.telemetry(120, euicc_id, None, None, OWNER)
        assert by_device["euicc_id"] == euicc_id and by_device["samples"], "the device screen reads the same series"
    finally:
        h.close()


def test_the_chain_runs_from_input_all_the_way_to_a_resolved_report():
    routes = _routes()
    if routes is None:
        print("        (skipped: FastAPI not installed)")
        return
    h = Harness()
    try:
        commit, _ = _process(routes)
        out = routes.output_current(None, OWNER)
        incident_id = out["selected_incident_id"]
        euicc_id = out["incident"]["device"]["euicc_id"]
        assert out["recovery"]["verdict"] == "RECOVERED"

        # RECOVERY -> CLIENT REPORT, against the very ids the Output screen showed
        created = routes.submit_report(
            routes.ReportCreate(
                message="Yard tracker dropped off the network; raised from the self-healing output.",
                device_id=euicc_id, incident_id=incident_id),
            CLIENT)
        report_id = created["id"]

        # the report is joined to the same device and incident, with the same context
        full = routes.get_report(report_id, CLIENT)
        assert full["device"]["euicc_id"] == euicc_id
        assert full["incident"]["incident_id"] == incident_id
        assert full["device"]["context"]["device_label"] == "Yard tracker Y-48213"
        assert full["incident"]["context"]["client_name"] == "Meridian Logistics"
        assert full["incident"]["fault_label"] == out["incident"]["fault_label"]
        assert full["incident"]["selected_label"] == out["trace"]["selected_label"], \
            "the report shows the action the agent actually took"

        # the count shows up where the incident and device are listed
        listed = next(i for i in routes.incidents(CLIENT)["incidents"] if i["incident_id"] == incident_id)
        assert listed["report_count"] == 1
        dev = next(d for d in routes.devices(CLIENT)["devices"] if d["euicc_id"] == euicc_id)
        assert dev["report_count"] == 1
        assert routes.incident_reports(incident_id, CLIENT)["reports"][0]["id"] == report_id

        # RESOLUTION: the owner acknowledges and resolves it
        routes.acknowledge_report(report_id, routes.ReportNote(note="Confirmed the re-push."), OWNER)
        resolved = routes.resolve_report(
            report_id, routes.ReportNote(note="Profile checksum back to OK."), OWNER)
        assert resolved["status"] == "RESOLVED"
        assert [t["key"] for t in resolved["timeline"]] == ["sent", "acknowledged", "resolved"]
        assert resolved["resolution_note"] == "Profile checksum back to OK."
        assert routes.reports_summary(CLIENT)["open"] == 0
    finally:
        h.close()


def test_an_incident_from_an_earlier_input_opens_its_own_output():
    """"View self-healing output" from the Incidents screen must answer about
    the run that opened *that* incident, not whatever ran most recently."""
    routes = _routes()
    if routes is None:
        print("        (skipped: FastAPI not installed)")
        return
    h = Harness()
    try:
        first_commit, _ = _process(routes, "isdp_corruption_48213", "CL-001")
        first = routes.output_current(None, OWNER)["selected_incident_id"]
        second_commit, _ = _process(routes, "smdp_outage_51887", "CL-002")
        second = routes.output_current(None, OWNER)["selected_incident_id"]
        assert first != second and first_commit["id"] != second_commit["id"]

        # latest, with no incident asked for
        assert routes.output_current(None, OWNER)["selected_incident_id"] == second

        # the earlier incident brings its own run and commit back
        back = routes.output_current(first, OWNER)
        assert back["selected_incident_id"] == first
        assert back["run"]["id"] == first_commit["id"]
        assert back["commit"]["resolved"]["client"]["name"] == "Meridian Logistics"
        assert back["run"]["dataset_id"] == "isdp_corruption_48213"
        assert back["incident"]["fault_label"] == "ISD-P corruption"

        # and the incident list is what pointed there
        listed = next(i for i in routes.incidents(OWNER)["incidents"] if i["incident_id"] == first)
        assert listed["run"]["run_id"] == first_commit["id"]
    finally:
        h.close()


def test_reports_still_name_the_client_after_the_session_is_reset():
    """Devices and incidents live in the agent session; reports outlive it. The
    snapshot keeps the same registry context the live screens showed."""
    routes = _routes()
    if routes is None:
        print("        (skipped: FastAPI not installed)")
        return
    h = Harness()
    try:
        _process(routes)
        out = routes.output_current(None, OWNER)
        incident_id = out["selected_incident_id"]
        euicc_id = out["incident"]["device"]["euicc_id"]
        report_id = routes.submit_report(
            routes.ReportCreate(message="Tracker offline; please check this unit.",
                                device_id=euicc_id, incident_id=incident_id), CLIENT)["id"]

        h.session.reset()
        after = routes.get_report(report_id, CLIENT)
        assert after["device"]["live"] is False and after["incident"]["live"] is False
        assert after["device"]["context"]["device_label"] == "Yard tracker Y-48213"
        assert after["incident"]["context"]["client_name"] == "Meridian Logistics"
        assert after["incident"]["incident_id"] == incident_id
        assert routes.incident_reports(incident_id, CLIENT)["reports"][0]["id"] == report_id
    finally:
        h.close()


def test_the_registry_is_one_estate_not_a_second_device_list():
    """Every registered device the Input screen offers is the same device the
    rest of the dashboard tracks, joined on the eUICC id."""
    routes = _routes()
    if routes is None:
        print("        (skipped: FastAPI not installed)")
        return
    h = Harness()
    try:
        registered = {d["euicc_id"] for d in routes.input_registry(OWNER)["registry"]["devices"]}
        live = {d["euicc_id"] for d in routes.devices(OWNER)["devices"]}
        assert registered & live, "the real-time grid devices are registry devices"
        for d in routes.devices(OWNER)["devices"]:
            ctx = d["context"]
            assert (ctx is not None) == (d["euicc_id"] in registered), \
                "context exactly when the device is in the registry, never invented"
            if ctx:
                assert ctx["client_name"] and ctx["device_label"]

        # the built-in scenarios run on those same registered devices: one estate,
        # whether the run came from the Input screen or from Run self-healing
        routes.agent_run(routes.RunRequest(scenario="night_shift", enable_learning=False))
        after = routes.devices(OWNER)["devices"]
        assert {d["euicc_id"] for d in after} <= registered, \
            "a scenario must not introduce devices the registry has never heard of"
        assert all(d["context"] for d in after)

        # an eUICC that genuinely is not registered gets no invented client
        assert estate.context_for("89330000000999999") is None
        assert estate.label_for("89330000000999999") == "89330000000999999"
    finally:
        h.close()


def test_a_broken_registry_does_not_take_the_other_screens_down():
    """The Input screen is where registry integrity is reported. Everywhere else
    degrades to "no context" rather than failing."""
    routes = _routes()
    if routes is None:
        print("        (skipped: FastAPI not installed)")
        return
    h = Harness()
    from backend.services import datasets
    real = datasets.registry
    try:
        _process(routes)
        datasets.registry = lambda: (_ for _ in ()).throw(datasets.DatasetError("checksum mismatch"))
        items = routes.incidents(OWNER)["incidents"]
        assert items and all(i["context"] is None for i in items)
        assert all(d["context"] is None for d in routes.devices(OWNER)["devices"])
        assert routes.devices(OWNER)["devices"], "devices still list"
    finally:
        datasets.registry = real
        h.close()


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
