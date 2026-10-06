"""Client reports: lifecycle, persistence, links to devices/incidents, scoping.

    python tests/test_reports.py          # no pytest required
    pytest tests/test_reports.py -q       # or under pytest

The store and linking tests need only NumPy (they never import FastAPI). The
last group calls the route handlers directly and is skipped if FastAPI is not
installed. What they pin:

    * lifecycle    SENT -> ACKNOWLEDGED -> RESOLVED, terminal, notes kept
    * persistence  a report survives a restart (new store on the same file)
    * links        a report only links to a device/incident that exists, an
                   incident implies its device, and a vanished incident keeps
                   its snapshot instead of dangling
    * devices      the device list is built from existing session objects
    * telemetry    device-scoped, and centred on the incident's own reading
    * scoping      a client sees only their own reports; the owner sees all
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services import report_links, reports
from backend.services.agent_service import AgentSession
from backend.services.reports import (
    ACKNOWLEDGED, RESOLVED, SENT, InvalidTransition, ReportNotFound, ReportStore,
)


def _store() -> ReportStore:
    return ReportStore(":memory:")


def _session_with_incident():
    session = AgentSession()
    session.run("isdp_corruption")
    inc = session.incidents()[0]
    return session, inc["incident_id"], inc["observation"]["euicc_id"]


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------
def test_lifecycle_sent_acknowledged_resolved():
    st = _store()
    r = st.create(client="client", role="client", message="link is down")
    assert r["id"] == "RPT-0001" and r["status"] == SENT
    assert [e["key"] for e in r["timeline"]] == ["sent"]

    r = st.acknowledge(r["id"], "owner", "on it")
    assert r["status"] == ACKNOWLEDGED and r["response_note"] == "on it"
    assert r["acknowledged_by"] == "owner" and r["resolved_at"] is None

    r = st.resolve(r["id"], "owner", "fixed by re-push")
    assert r["status"] == RESOLVED and r["resolution_note"] == "fixed by re-push"
    assert [e["key"] for e in r["timeline"]] == ["sent", "acknowledged", "resolved"]
    assert r["response_note"] == "on it"          # the earlier note is not overwritten


def test_transitions_are_enforced():
    st = _store()
    rid = st.create(client="c", role="client", message="x")["id"]
    st.acknowledge(rid, "owner")
    for bad in (lambda: st.acknowledge(rid, "owner"),):
        try:
            bad()
        except InvalidTransition:
            pass
        else:
            raise AssertionError("acknowledging twice must fail")
    st.resolve(rid, "owner")
    try:
        st.resolve(rid, "owner")
    except InvalidTransition:
        pass
    else:
        raise AssertionError("RESOLVED is terminal")
    try:
        st.acknowledge(rid, "owner")
    except InvalidTransition:
        pass
    else:
        raise AssertionError("a resolved report cannot go back to acknowledged")


def test_resolving_from_sent_records_the_acknowledgement_too():
    st = _store()
    rid = st.create(client="c", role="client", message="x")["id"]
    r = st.resolve(rid, "owner")
    assert r["status"] == RESOLVED and r["acknowledged_at"] is not None
    assert [e["key"] for e in r["timeline"]] == ["sent", "acknowledged", "resolved"]


def test_input_validation():
    st = _store()
    for msg in ("", "   "):
        try:
            st.create(client="c", role="client", message=msg)
        except ValueError:
            pass
        else:
            raise AssertionError("blank message must be rejected")
    try:
        st.create(client="c", role="client", message="x" * (reports.MAX_MESSAGE + 1))
    except ValueError:
        pass
    else:
        raise AssertionError("oversized message must be rejected, not truncated")
    for bad in ("RPT-9999", "nonsense", ""):
        try:
            st.get(bad)
        except ReportNotFound:
            pass
        else:
            raise AssertionError(f"{bad!r} should be unknown")


def test_list_is_newest_first_and_filterable():
    st = _store()
    a = st.create(client="alice", role="client", message="1", incident_id="INC-A", device_id="D1")
    b = st.create(client="bob", role="client", message="2", incident_id="INC-A", device_id="D2")
    c = st.create(client="alice", role="client", message="3")
    st.acknowledge(a["id"], "owner")
    assert [r["id"] for r in st.list()] == [c["id"], b["id"], a["id"]]
    assert [r["id"] for r in st.list(client="alice")] == [c["id"], a["id"]]
    assert [r["id"] for r in st.list(incident_id="INC-A")] == [b["id"], a["id"]]
    assert [r["id"] for r in st.list(status=ACKNOWLEDGED)] == [a["id"]]
    assert st.counts_by("incident_id") == {"INC-A": 2}
    assert st.counts_by("incident_id", client="bob") == {"INC-A": 1}
    assert st.summary() == {"total": 3, "sent": 2, "acknowledged": 1, "resolved": 0, "open": 3}
    assert st.summary(client="bob")["total"] == 1


def test_reports_survive_a_restart():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "nested" / "reports.db"
        first = ReportStore(path)
        rid = first.create(client="client", role="client", message="persist me",
                           incident_id="INC-1", incident_snapshot={"fault_label": "X"})["id"]
        first.acknowledge(rid, "owner", "seen")
        first.close()

        second = ReportStore(path)                    # "the server restarted"
        r = second.get(rid)
        assert r["message"] == "persist me" and r["status"] == ACKNOWLEDGED
        assert r["response_note"] == "seen" and r["incident_snapshot"] == {"fault_label": "X"}
        assert second.create(client="c", role="client", message="next")["id"] == "RPT-0002"
        second.close()


# ---------------------------------------------------------------------------
# links to devices / incidents, built from the real session
# ---------------------------------------------------------------------------
def test_devices_are_built_from_existing_objects():
    session, _, dev = _session_with_incident()
    ids = [d["euicc_id"] for d in session.devices()]
    assert len(ids) == len(set(ids)), "no duplicates"
    assert dev in ids, "the device an incident was opened on is listed"
    for live in session.realtime.devices:
        assert live["euicc_id"] in ids, "the real-time grid is listed even when idle"
    row = next(d for d in session.devices() if d["euicc_id"] == dev)
    assert row["incident_count"] == 1 and row["samples"] > 0
    detail = session.device(dev)
    assert detail["euicc"] is not None and len(detail["incidents"]) == 1
    assert session.device("no-such-device") is None


def test_incident_implies_device_and_mismatch_is_rejected():
    session, iid, dev = _session_with_incident()
    links = report_links.resolve_links(session, None, iid)
    assert links["device_id"] == dev and links["incident_snapshot"]["incident_id"] == iid
    assert report_links.resolve_links(session, dev, None)["incident_id"] is None
    other = next(d["euicc_id"] for d in session.devices() if d["euicc_id"] != dev)
    for args in ((other, iid), (None, "INC-NOPE"), ("no-such-device", None)):
        try:
            report_links.resolve_links(session, *args)
        except report_links.LinkError:
            pass
        else:
            raise AssertionError(f"{args} should be rejected")
    empty = report_links.resolve_links(session, None, None)
    assert empty["device_id"] is None and empty["incident_id"] is None


def test_report_keeps_a_snapshot_when_the_incident_disappears():
    session, iid, dev = _session_with_incident()
    st = _store()
    links = report_links.resolve_links(session, None, iid)
    r = st.create(client="c", role="client", message="x", **links)

    live = report_links.enrich(session, [r])[0]
    assert live["incident"]["live"] is True and live["device"]["live"] is True
    assert live["incident"]["selected_label"], "remedial action is part of the chain"
    assert "incident_snapshot" not in live and "device_snapshot" not in live

    session.reset()                                       # incidents are gone
    gone = report_links.enrich(session, [st.get(r["id"])])[0]
    assert gone["incident"]["live"] is False
    assert gone["incident"]["incident_id"] == iid
    assert gone["incident"]["fault_label"] == live["incident"]["fault_label"]
    assert gone["device"]["euicc_id"] == dev


# ---------------------------------------------------------------------------
# telemetry
# ---------------------------------------------------------------------------
def test_telemetry_is_device_scoped_and_centred_on_the_incident():
    session, iid, dev = _session_with_incident()
    plain = session.telemetry(limit=100)
    assert "marker_index" not in plain and len(plain["samples"]) == 100

    scoped = session.telemetry(limit=90, euicc_id=dev, incident_id=iid)
    assert scoped["samples"] and all(s["euicc_id"] == dev for s in scoped["samples"])
    m = scoped["marker_index"]
    assert m is not None and scoped["samples"][m]["incident_id"] == iid
    assert 0 < m < len(scoped["samples"]) - 1, "samples exist on both sides of the trigger"

    idle = next(d["euicc_id"] for d in session.devices() if d["euicc_id"] != dev)
    none_yet = session.telemetry(limit=50, euicc_id=idle)
    assert none_yet["samples"] == [] and "no telemetry" in none_yet["source"]


# ---------------------------------------------------------------------------
# route handlers (skipped without FastAPI)
# ---------------------------------------------------------------------------
def test_handlers_scope_reports_to_their_owner():
    try:
        from backend.api import routes
    except ImportError:
        print("        (skipped: FastAPI not installed)")
        return
    from backend.services.agent_service import SESSION
    reports.use_store(_store())
    try:
        SESSION.reset()
        SESSION.run("isdp_corruption")
        iid = SESSION.incidents()[0]["incident_id"]
        # both clients belong to the company that owns the incident's device
        from backend.services import estate
        company = estate.context_for(SESSION.incident_device(iid))["client_id"]
        alice = {"username": "Alice", "role": "client", "client_id": company}
        bob = {"username": "bob", "role": "client", "client_id": company}
        owner = {"username": "owner", "role": "owner"}

        a = routes.submit_report(routes.ReportCreate(message="mine", incident_id=iid), alice)
        routes.submit_report(routes.ReportCreate(message="bobs"), bob)
        assert a["client"] == "alice" and a["device"]["live"]

        assert [r["message"] for r in routes.my_reports(alice)["reports"]] == ["mine"]
        assert routes.reports_summary(bob)["total"] == 1
        assert routes.reports_summary(owner)["total"] == 2
        assert routes.get_report(a["id"], owner)["id"] == a["id"]
        try:
            routes.get_report(a["id"], bob)
        except Exception as exc:                          # HTTPException 404
            assert getattr(exc, "status_code", None) == 404
        else:
            raise AssertionError("a client must not read another client's report")

        counts = {i["incident_id"]: i["report_count"] for i in routes.incidents(alice)["incidents"]}
        assert counts[iid] == 1
        assert {i["incident_id"]: i["report_count"]
                for i in routes.incidents(bob)["incidents"]}[iid] == 0, "counts are per-client"
        assert len(routes.incident_reports(iid, owner)["reports"]) == 1

        done = routes.resolve_report(a["id"], routes.ReportNote(note="ok"), owner)
        assert done["status"] == RESOLVED and done["resolution_note"] == "ok"
        assert routes.get_report(a["id"], alice)["resolution_note"] == "ok"
    finally:
        reports.use_store(None)


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
