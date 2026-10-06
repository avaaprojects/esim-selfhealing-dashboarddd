"""Running the agent on the operator's own telemetry CSV.

    python tests/test_custom_input.py     # no pytest required

The parser is checked on its own, then the whole flow through the real route
handlers: choose a CSV as the data source, Process data, read the Output.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services import auth, custom_input as C, input_state, reports
from backend.services.input_state import InputStore

YARD = "89330000000048213"                      # Meridian (CL-001) Yard tracker Y-48213
GATEWAY = "89330000000051887"                   # Harbourline (CL-002)
DEV = {"euicc_id": YARD, "cell_id": "CELL-4471"}
SAMPLE = Path("/mnt/user-data/outputs/meridian_yard_tracker_smdp_outage.csv")


def _sample_rows():
    """The shipped sample if present, otherwise generated the same way."""
    if SAMPLE.exists():
        return list(csv.reader(SAMPLE.open(newline="")))
    from datetime import datetime, timezone
    from esim_selfhealing.schemas import FEATURE_NAMES
    from esim_selfhealing.telemetry import FaultWindow, TelemetryStream
    st = TelemetryStream(n_samples=300, faults=[FaultWindow("smdp_session_outage", 230, 300)],
                         drift_per_sample=1.0, seed=101)
    st.euicc_id, st.cell_id = YARD, "CELL-4471"
    st.t0 = datetime(2026, 3, 2, 9, 30, tzinfo=timezone.utc).timestamp()
    rows = [["ts_epoch", "ts_iso", "euicc_id", "cell_id", *FEATURE_NAMES, "sim_fault_label", "provenance"]]
    for o in st:
        rows.append([f"{o.ts:.3f}", "", o.euicc_id, o.cell_id, *[f"{v:.6f}" for v in o.features],
                     o.ground_truth_fault or "", "SYNTHETIC / SIMULATED"])
    return rows


def _to_bytes(rows):
    b = io.StringIO()
    csv.writer(b, lineterminator="\n").writerows(rows)
    return b.getvalue().encode()


def _mut(rows, fn):
    rr = [r[:] for r in rows]
    fn(rr)
    return _to_bytes(rr)


def _routes():
    try:
        from backend.api import routes
    except ImportError:
        return None
    return routes


def _account(user, pw):
    return auth.require_any_role(f"Bearer {auth.login(user, pw)['token']}")


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
        self.client = _account("client", "client-demo-2026")            # CL-001

    def upload(self, data: bytes, *, name="mine.csv", client="CL-001", by="client", device=YARD):
        return input_state.get_store().add_upload(
            kind="csv", filename=name, mime="text/csv", size=len(data), sha256=hashlib.sha256(data).hexdigest(),
            uploaded_by=by, client_id=client, group_id=None, device_id=device, incident_id=None,
            meta={"columns": [], "rows": 0, "telemetry_compatible": True}, data=data)

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


def _status(fn, *a, **k):
    try:
        fn(*a, **k)
    except Exception as exc:
        return getattr(exc, "status_code", None) or type(exc).__name__
    return 200


def _detail(fn, *a, **k):
    try:
        fn(*a, **k)
    except Exception as exc:
        return str(getattr(exc, "detail", exc))
    return ""


# ---------------------------------------------------------------------------
def test_the_parser_accepts_a_good_file_and_reports_what_it_found():
    r = C.parse(_to_bytes(_sample_rows()), device=DEV)
    s = r["summary"]
    assert s["rows"] == 300 and len(r["observations"]) == 300
    assert s["has_fault_labels"] and not s["assumed_time"] and s["device_column"]
    assert r["observations"][0].euicc_id == YARD and len(r["observations"][0].features) == 5


def test_the_parser_takes_a_bare_five_column_file_and_says_time_was_assumed():
    rows = [r[4:9] for r in _sample_rows()]
    r = C.parse(_to_bytes(rows), device=DEV)
    assert r["summary"]["assumed_time"] and not r["summary"]["has_fault_labels"]
    ts = [o.ts for o in r["observations"]]
    assert all(b - a == 1.0 for a, b in zip(ts, ts[1:]))
    assert {o.euicc_id for o in r["observations"]} == {YARD}, "the selected device is used"


def test_the_parser_reads_other_time_formats_and_delimiters():
    rows = _sample_rows()
    iso = [["ts_iso", *rows[0][4:9]]] + [[f"2026-03-02T09:{30 + i // 60:02d}:{i % 60:02d}Z", *r[4:9]] for i, r in enumerate(rows[1:])]
    assert C.parse(_to_bytes(iso), device=DEV)["summary"]["time_from"] == "2026-03-02T09:30:00Z"
    semi = _to_bytes(rows).decode().replace(",", ";").encode()
    assert C.parse(semi, device=DEV)["summary"]["rows"] == 300
    ms = [["ts_epoch", *rows[0][4:9]]] + [[str(int(float(r[0]) * 1000)), *r[4:9]] for r in rows[1:]]
    assert C.parse(_to_bytes(ms), device=DEV)["summary"]["time_from"] == "2026-03-02T09:30:00Z", "milliseconds are understood"


def test_the_parser_rejects_bad_files_with_a_reason():
    rows = _sample_rows()
    col = rows[0].index

    def rejected(data, device=DEV):
        try:
            C.parse(data, device=device)
        except C.CustomInputError as exc:
            return str(exc)
        return None

    assert "missing telemetry column" in rejected(_mut(rows, lambda r: r[0].__setitem__(col("rsrp_dbm"), "x")))
    assert "at least 100" in rejected(_to_bytes(rows[:60]))
    assert "not a number" in rejected(_mut(rows, lambda r: r[9].__setitem__(col("latency_ms"), "abc")))
    for bad in ("nan", "inf", "1e30"):
        assert "usable number" in rejected(_mut(rows, lambda r, b=bad: r[9].__setitem__(col("latency_ms"), b)))
    assert "is for device" in rejected(_mut(rows, lambda r: r[5].__setitem__(col("euicc_id"), GATEWAY)))
    assert "simulator knows" in rejected(_mut(rows, lambda r: r[240].__setitem__(col("sim_fault_label"), "made_up")))
    assert "not a time" in rejected(_mut(rows, lambda r: r[4].__setitem__(col("ts_epoch"), "yesterday")))
    assert "no header" in rejected(b"")
    assert "Choose a device" in rejected(_to_bytes(rows), device=None)
    assert "more than" in rejected(_to_bytes([rows[0]] + [rows[1]] * (C.MAX_ROWS + 1)))


def test_the_owner_can_run_the_agent_on_their_csv():
    routes = _routes()
    if routes is None:
        print("        (skipped: FastAPI not installed)")
        return
    h = Harness()
    try:
        P, D = routes.InputPatch, routes.DataSourcePatch
        up = h.upload(_to_bytes(_sample_rows()), by="owner")
        routes.put_input_state(P(client_id="CL-001", device_id=YARD), h.owner)
        view = routes.put_input_state(P(data_source=D(upload_id=up["id"])), h.owner)
        assert view["state"]["data_source"]["mode"] == "upload"
        assert view["resolved"]["dataset"]["provenance"] == "OPERATOR UPLOAD"
        assert view["readiness"]["ready"] is True, view["readiness"]
        assert [p["type"] for p in view["provenance"] if p["role"] == "Data source"] == ["OPERATOR UPLOAD"]

        res = routes.process_input(h.owner)
        assert res["commit"]["source"]["type"] == "OPERATOR UPLOAD"
        out = routes.output_current(None, h.owner)
        assert out["available"] and out["incident"]["fault_label"] == "SM-DP+ session outage"
        assert out["recovery"]["verdict"] == "RECOVERED"
        assert out["run"]["dataset_title"] == "mine.csv"
    finally:
        h.close()


def test_a_file_without_the_simulator_label_is_still_detected_and_diagnosed():
    routes = _routes()
    if routes is None:
        print("        (skipped: FastAPI not installed)")
        return
    h = Harness()
    try:
        P, D = routes.InputPatch, routes.DataSourcePatch
        bare = _to_bytes([r[4:9] for r in _sample_rows()])
        up = h.upload(bare, by="owner")
        routes.put_input_state(P(client_id="CL-001", device_id=YARD), h.owner)
        routes.put_input_state(P(data_source=D(upload_id=up["id"])), h.owner)
        routes.process_input(h.owner)
        out = routes.output_current(None, h.owner)
        assert out["available"] and out["incident"]["fault_label"] == "SM-DP+ session outage"
        # the simulated RSP server was never told about the fault, so the fix cannot "recover" it
        assert out["recovery"]["verdict"] != "RECOVERED"
    finally:
        h.close()


def test_a_client_can_use_their_own_csv_but_not_reach_anothers():
    routes = _routes()
    if routes is None:
        print("        (skipped: FastAPI not installed)")
        return
    h = Harness()
    try:
        P, D = routes.InputPatch, routes.DataSourcePatch
        mine = h.upload(_to_bytes(_sample_rows()), name="meridian.csv", client="CL-001", by="client")
        theirs = h.upload(_to_bytes(_sample_rows()), name="harbour.csv", client="CL-002", by="owner")
        routes.get_input_state(h.client)
        routes.put_input_state(P(device_id=YARD), h.client)
        # another company's file is a 404, not a 403 or 422: they cannot tell it exists
        assert _status(routes.put_input_state, P(data_source=D(upload_id=theirs["id"])), h.client) == 404
        routes.put_input_state(P(data_source=D(upload_id=mine["id"])), h.client)
        res = routes.process_input(h.client)
        assert res["commit"]["by"] == "client" and routes.output_current(None, h.client)["available"]

        # a file that names a device from another company is refused
        other = h.upload(_mut(_sample_rows(), lambda r: [row.__setitem__(2, GATEWAY) for row in r[1:]]),
                         name="wrong.csv", client="CL-001", by="client")
        assert "is for device" in _detail(routes.put_input_state, P(data_source=D(upload_id=other["id"])), h.client)
        # ...and so is a non-CSV upload
        log = input_state.get_store().add_upload(
            kind="log", filename="a.log", mime="text/plain", size=3, sha256=hashlib.sha256(b"abc").hexdigest(),
            uploaded_by="client", client_id="CL-001", group_id=None, device_id=None, incident_id=None,
            meta={}, data=b"abc")
        assert _status(routes.put_input_state, P(data_source=D(upload_id=log["id"])), h.client) == 422
    finally:
        h.close()


def test_the_choice_must_still_match_the_device_and_the_file():
    routes = _routes()
    if routes is None:
        print("        (skipped: FastAPI not installed)")
        return
    h = Harness()
    try:
        P, D = routes.InputPatch, routes.DataSourcePatch
        up = h.upload(_to_bytes(_sample_rows()), by="owner")
        assert "Choose a device" in _detail(routes.put_input_state, P(data_source=D(upload_id=up["id"])), h.owner)
        routes.put_input_state(P(client_id="CL-001", device_id=YARD), h.owner)
        routes.put_input_state(P(data_source=D(upload_id=up["id"])), h.owner)

        # switching to another device makes the checked file no longer apply
        v = routes.put_input_state(P(device_id="89330000000077145"), h.owner)
        assert not v["readiness"]["ready"] and "different device" in str(v["readiness"]["checks"])
        routes.put_input_state(P(device_id=YARD), h.owner)

        # the file is edited on disk after it was chosen: refused, not silently used
        path = Path(h.tmp) / "csv"
        stored = next(path.iterdir())
        stored.write_bytes(stored.read_bytes() + b"1,2,3,4,5\n")
        assert "checksum" in _detail(routes.process_input, h.owner)
        # and a removed file is a clear error, not a crash
        input_state.get_store().delete_upload(up["id"])
        assert _status(routes.process_input, h.owner) in (404, 422)
    finally:
        h.close()


# ---------------------------------------------------------------------------
# tools/import_dataset.py - putting your own CSV in data/ as a stored dataset
# ---------------------------------------------------------------------------
def _run_import(args):
    """Run the tool in-process against a temporary copy of ./data."""
    import importlib, json, subprocess
    root = Path(__file__).resolve().parents[1]
    return subprocess.run([sys.executable, str(root / "tools" / "import_dataset.py"), *args],
                          capture_output=True, text=True, cwd=str(root),
                          env={**os.environ, "PYTHONPATH": os.environ.get("PYTHONPATH", "")})


def test_import_tool_adds_a_dataset_the_dashboard_accepts():
    """A hand-dropped file is refused as 'modified'; the tool's is verified."""
    import json
    from backend.services import datasets
    root = Path(__file__).resolve().parents[1]
    data = root / "data"
    manifest = data / "manifest.json"
    backup = manifest.read_bytes()
    src = Path(tempfile.mkdtemp()) / "my_readings.csv"
    src.write_bytes(_to_bytes(_sample_rows()))
    made = []
    try:
        # hand-dropping a file into data/ is refused: the checksum no longer matches
        target = data / "telemetry" / "isdp_corruption_48213.csv"
        original = target.read_bytes()
        try:
            target.write_bytes(_to_bytes(_sample_rows()))
            datasets._MANIFEST_CACHE = None if hasattr(datasets, "_MANIFEST_CACHE") else None
            entry = [d for d in datasets.list_datasets() if d["id"] == "isdp_corruption_48213"][0]
            assert entry["integrity"] == "modified", entry["integrity"]
            try:
                datasets.dataset_observations("isdp_corruption_48213")
                raise AssertionError("a modified dataset must not load")
            except datasets.DatasetError as exc:
                assert "checksum" in str(exc)
        finally:
            target.write_bytes(original)

        # the tool's import is accepted, labelled as the operator's, and runs
        res = _run_import([str(src), "--device", YARD, "--title", "My readings"])
        assert res.returncode == 0, res.stderr
        made.append(data / "telemetry" / "my_readings.csv")
        entry = [d for d in datasets.list_datasets() if d["id"] == "my_readings"][0]
        assert entry["integrity"] == "verified"
        assert entry["provenance"] == "OPERATOR IMPORT", "never labelled as this project's synthetic data"
        assert entry["client_id"] == "CL-001" and entry["euicc_id"] == YARD
        assert len(datasets.dataset_observations("my_readings")) == 300

        # a bad file is refused with the same reason the screen gives, and nothing is written
        bad = src.with_name("bad.csv")
        bad.write_bytes(_to_bytes(_sample_rows()[:60]))
        res = _run_import([str(bad), "--device", YARD])
        assert res.returncode == 1 and "at least 100" in res.stdout + res.stderr
        assert not (data / "telemetry" / "bad.csv").exists()

        # an unknown device is refused and the known ones are listed
        res = _run_import([str(src), "--device", "89330000000000000", "--id", "nope"])
        assert res.returncode == 2 and YARD in res.stderr

        # adding the same id twice needs --replace
        res = _run_import([str(src), "--device", YARD])
        assert res.returncode == 2 and "--replace" in res.stderr
        res = _run_import([str(src), "--device", YARD, "--replace", "my_readings", "--title", "Second"])
        assert res.returncode == 0 and [d for d in datasets.list_datasets()
                                        if d["id"] == "my_readings"][0]["title"] == "Second"
    finally:
        manifest.write_bytes(backup)
        for p in made:
            p.unlink(missing_ok=True)
        shutil.rmtree(src.parent, ignore_errors=True)


def test_an_imported_dataset_is_not_presented_as_synthetic():
    routes = _routes()
    if routes is None:
        print("        (skipped: FastAPI not installed)")
        return
    import json
    root = Path(__file__).resolve().parents[1]
    data = root / "data"
    manifest = data / "manifest.json"
    backup = manifest.read_bytes()
    src = Path(tempfile.mkdtemp()) / "imported_run.csv"
    src.write_bytes(_to_bytes(_sample_rows()))
    h = Harness()
    try:
        assert _run_import([str(src), "--device", YARD]).returncode == 0
        P, D = routes.InputPatch, routes.DataSourcePatch
        routes.put_input_state(P(client_id="CL-001", device_id=YARD), h.owner)
        routes.put_input_state(P(data_source=D(mode="synthetic", dataset_id="imported_run")), h.owner)
        commit = routes.process_input(h.owner)["commit"]
        assert commit["source"]["type"] == "OPERATOR IMPORT"
        row = next(p for p in commit["provenance"] if p["role"] == "Data source")
        assert row["type"] == "OPERATOR IMPORT" and "operator supplied" in row["source"]
        assert routes.output_current(None, h.owner)["available"]

        # a built-in dataset is still labelled as this project's synthetic data
        routes.put_input_state(P(data_source=D(dataset_id="isdp_corruption_48213")), h.owner)
        assert routes.process_input(h.owner)["commit"]["source"]["type"] == "SYNTHETIC / SIMULATED"
    finally:
        h.close()
        manifest.write_bytes(backup)
        (data / "telemetry" / "imported_run.csv").unlink(missing_ok=True)
        shutil.rmtree(src.parent, ignore_errors=True)


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
