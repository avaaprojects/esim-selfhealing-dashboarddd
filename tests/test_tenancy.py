"""A client works on their own company's record, and only sees their own.

    python tests/test_tenancy.py     # no pytest required

A CLIENT account belongs to one company and can run the whole flow itself -
choose their record, Process data, read the Output, run scenarios, file
reports - but everything they can see is limited to that company. Checked
through the real route handlers, with accounts built by the real `auth.login`.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services import auth, estate, input_state, reports, tenancy
from backend.services.input_state import InputStore


def _routes():
    try:
        from backend.api import routes
    except ImportError:
        return None
    return routes


def _account(username, password):
    """A signed-in account entry exactly as `require_any_role` hands it to a route."""
    res = auth.login(username, password)
    return auth.require_any_role(f"Bearer {res['token']}")


class Harness:
    def __init__(self):
        self.tmp = tempfile.mkdtemp()
        self._old = os.environ.get("UPLOADS_DIR")
        os.environ["UPLOADS_DIR"] = self.tmp
        input_state.use_store(InputStore(":memory:"))
        reports.use_store(reports.ReportStore(":memory:"))
        auth.use_store(auth.AccountStore(":memory:"))      # never touch the real accounts db
        from backend.services.agent_service import SESSION
        self.session = SESSION
        SESSION.reset()
        self.owner = _account("owner", "owner-demo-2026")
        self.client = _account("client", "client-demo-2026")           # belongs to CL-001

    def close(self):
        input_state.use_store(None)
        reports.use_store(None)
        auth.use_store(None)
        self.session.reset()
        if self._old is None:
            os.environ.pop("UPLOADS_DIR", None)
        else:
            os.environ["UPLOADS_DIR"] = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)


def _http_status(fn, *a, **k):
    try:
        fn(*a, **k)
    except Exception as exc:                                            # HTTPException
        return getattr(exc, "status_code", None)
    return 200


def _pick(routes, entry, dataset_id):
    P, D = routes.InputPatch, routes.DataSourcePatch
    routes.put_input_state(P(data_source=D(mode="synthetic", dataset_id=dataset_id)), entry)


# ---------------------------------------------------------------------------
def test_the_demo_client_belongs_to_one_company_and_fails_closed_without_one():
    h = Harness()
    try:
        assert h.client["client_id"] == "CL-001" and "client_id" not in h.owner
        assert tenancy.company_of(h.owner) is None, "an owner is not limited to a company"
        assert tenancy.company_of(h.client) == "CL-001"
        # a client account with no company must not be treated like an owner
        orphan = {"username": "x", "role": "client"}
        assert tenancy.company_of(orphan) == tenancy.NO_COMPANY
        assert not tenancy.owns(tenancy.company_of(orphan), estate.context_for("89330000000048213"))
        assert not tenancy.owns("CL-001", None), "something with no registry record is never a client's"
    finally:
        h.close()


def test_a_client_only_sees_their_own_company_when_choosing_a_record():
    routes = _routes()
    if routes is None:
        print("        (skipped: FastAPI not installed)")
        return
    h = Harness()
    try:
        # the first visit already has their company selected: they never pick one
        view = routes.get_input_state(h.client)
        assert view["state"]["client_id"] == "CL-001"
        reg = routes.input_registry(h.client)
        assert [c["client_id"] for c in reg["registry"]["clients"]] == ["CL-001"]
        assert {d["client_id"] for d in reg["registry"]["devices"]} == {"CL-001"}
        assert {g["client_id"] for g in reg["registry"]["groups"]} == {"CL-001"}
        assert reg["datasets"] and {d["client_id"] for d in reg["datasets"]} == {"CL-001"}
        # ...while the owner still sees all three
        full = routes.input_registry(h.owner)
        assert len(full["registry"]["clients"]) == 3 and len(full["datasets"]) == 6

        # they cannot switch to another company, or fetch another company's data
        P = routes.InputPatch
        assert _http_status(routes.put_input_state, P(client_id="CL-002"), h.client) == 403
        assert _http_status(routes.input_dataset, "smdp_outage_51887", 5, h.client) == 404   # CL-002's
        assert _http_status(routes.input_dataset, "isdp_corruption_48213", 5, h.client) == 200
        # naming a dataset that is not theirs is refused the same way as an unknown one
        assert _http_status(_pick, routes, h.client, "smdp_outage_51887") in (404, 422)
    finally:
        h.close()


def test_a_client_can_enter_their_record_and_the_output_opens_for_them():
    routes = _routes()
    if routes is None:
        print("        (skipped: FastAPI not installed)")
        return
    h = Harness()
    try:
        routes.get_input_state(h.client)
        _pick(routes, h.client, "isdp_corruption_48213")
        res = routes.process_input(h.client)
        assert res["commit"]["by"] == "client" and res["run"]["mode"] == "synthetic"

        out = routes.output_current(None, h.client)
        assert out["available"] and out["incident"]["client"]["name"] == "Meridian Logistics"
        assert out["recovery"]["verdict"] == "RECOVERED"
        iid = out["selected_incident_id"]
        # the same chain the owner gets: telemetry, incident, device, trace
        assert routes.telemetry(120, None, iid, res["run"]["id"], h.client)["samples"]
        assert any(i["incident_id"] == iid for i in routes.incidents(h.client)["incidents"])
        assert routes.trace(iid, h.client)["incident"]["incident_id"] == iid
        # and the client can file the report from it
        rep = routes.submit_report(routes.ReportCreate(message="Please confirm the fix.",
                                                       incident_id=iid), h.client)
        assert rep["incident"]["incident_id"] == iid
    finally:
        h.close()


def test_a_client_never_sees_another_companys_incidents_devices_or_results():
    routes = _routes()
    if routes is None:
        print("        (skipped: FastAPI not installed)")
        return
    h = Harness()
    try:
        # the OWNER processes a Harbourline (CL-002) record...
        routes.put_input_state(routes.InputPatch(client_id="CL-002"), h.owner)
        _pick(routes, h.owner, "smdp_outage_51887")
        res = routes.process_input(h.owner)
        other = routes.output_current(None, h.owner)["selected_incident_id"]
        other_dev = h.session.incident_device(other)
        assert routes.incidents(h.owner)["incidents"], "the owner sees it"

        # ...the Meridian client sees none of it
        assert routes.incidents(h.client)["incidents"] == []
        assert other_dev not in [d["euicc_id"] for d in routes.devices(h.client)["devices"]]
        assert _http_status(routes.incident, other, h.client) == 404
        assert _http_status(routes.device, other_dev, h.client) == 404
        assert _http_status(routes.telemetry, 120, other_dev, None, None, h.client) == 404
        assert _http_status(routes.telemetry, 120, None, other, None, h.client) == 404
        assert _http_status(routes.trace, other, h.client) == 404
        # the session-wide stream carries the other company's samples; the client gets none of them
        assert routes.telemetry(120, None, None, None, h.owner)["samples"], "the owner sees the stream"
        assert routes.telemetry(120, None, None, None, h.client)["samples"] == []
        # the global screens (Overview, Memory, Safety) do not name it either
        blob = lambda o: json.dumps(o, default=str)
        for label, payload in (("status", routes.status(h.client)),
                               ("memory", routes.memory(60, None, h.client)),
                               ("safety", routes.safety(h.client))):
            assert other not in blob(payload) and other_dev not in blob(payload), f"{label} leaks it"
        assert other in blob(routes.memory(60, None, h.owner)) and other in blob(routes.safety(h.owner)), \
            "...while the owner still sees it there"
        assert routes.memory(60, None, h.client)["records"], "shared, seeded precedents stay for the client"
        # another operator's run, by id, or by the incident it opened
        assert _http_status(routes.input_commit, res["commit"]["id"], h.client) == 404
        assert routes.output_current(other, h.client)["available"] is False
        assert routes.output_current(None, h.client)["reason"] == "nothing_processed"
    finally:
        h.close()


def test_scenarios_run_for_a_client_but_only_their_company_shows_up():
    routes = _routes()
    if routes is None:
        print("        (skipped: FastAPI not installed)")
        return
    h = Harness()
    try:
        routes.agent_run(routes.RunRequest(scenario="night_shift", enable_learning=False))
        everyone = routes.incidents(h.owner)["incidents"]
        mine = routes.incidents(h.client)["incidents"]
        companies = {(i["context"] or {}).get("client_id") for i in everyone}
        assert len(companies) > 1, "the scenario spans companies"
        assert mine and {(i["context"] or {}).get("client_id") for i in mine} == {"CL-001"}
        assert len(mine) < len(everyone)
        # the counters describe what this account can see, so they agree with the list
        c, oc = routes.status(h.client)["counters"], routes.status(h.owner)["counters"]
        assert c["incidents_total"] == len(mine) and oc["incidents_total"] == len(everyone)
        assert c["auto_remediated"] + c["open_incidents"] == c["incidents_total"]
        assert c["incidents_total"] < oc["incidents_total"], "not the session-wide total"
        assert c["memory_records"] < oc["memory_records"], "other companies' learned records are not counted"
        stream = routes.telemetry(120, None, None, None, h.client)["samples"]
        assert stream and all(x["euicc_id"] in {d["euicc_id"] for d in routes.devices(h.client)["devices"]}
                              for x in stream), "their own stream is intact and contains only their devices"
    finally:
        h.close()


def test_what_stays_owner_only():
    routes = _routes()
    if routes is None:
        print("        (skipped: FastAPI not installed)")
        return
    # The owner-only set is exactly this list. If a route is added to it or (worse)
    # quietly opened to clients, this fails and someone has to decide on purpose.
    import ast
    tree = ast.parse(Path(routes.__file__).read_text())
    owner_only = sorted(
        fn.name for fn in tree.body if isinstance(fn, ast.FunctionDef)
        and "require_owner" in " ".join(ast.unparse(d) for d in fn.decorator_list) + ast.unparse(fn.args))
    assert owner_only == sorted([
        "agent_reset", "realtime_start", "realtime_stop", "realtime_inject",
        "get_reports", "acknowledge_report", "resolve_report"]), owner_only
    h = Harness()
    try:
        assert _http_status(auth.require_owner, h.client) == 403
        assert _http_status(auth.require_owner, h.owner) == 200
        # a client cannot start the live feed indirectly through Process data
        routes.get_input_state(h.client)
        P, D = routes.InputPatch, routes.DataSourcePatch
        try:
            routes.put_input_state(P(data_source=D(mode="realtime")), h.client)
        except Exception:
            pass
        else:
            h.session.realtime.stop() if h.session.realtime_status().get("running") else None
            assert _http_status(routes.process_input, h.client) == 422, \
                "with the feed stopped a client is told to ask the owner"
    finally:
        h.close()


def test_a_clients_uploads_are_theirs_alone():
    routes = _routes()
    if routes is None:
        print("        (skipped: FastAPI not installed)")
        return
    h = Harness()
    try:
        store = input_state.get_store()
        mine = store.add_upload(kind="log", filename="a.log", mime="text/plain", size=3, sha256="x" * 64,
                                uploaded_by="client", client_id="CL-001", group_id=None, device_id=None,
                                incident_id=None, meta={}, data=b"abc") if hasattr(store, "add_upload") else None
        theirs = store.add_upload(kind="log", filename="b.log", mime="text/plain", size=3, sha256="y" * 64,
                                  uploaded_by="owner", client_id="CL-002", group_id=None, device_id=None,
                                  incident_id=None, meta={}, data=b"xyz")
        assert _http_status(routes.upload_file_content, theirs["id"], h.client) == 404
        assert _http_status(routes.delete_upload, theirs["id"], h.client) == 404
        assert _http_status(routes.upload_file_content, mine["id"], h.client) == 200
        assert _http_status(routes.delete_upload, mine["id"], h.client) == 200
    finally:
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
