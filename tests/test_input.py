"""Input / configuration: datasets, uploads, selection state, provenance.

    python tests/test_input.py          # no pytest required
    pytest tests/test_input.py -q       # or under pytest

The service tests need only NumPy. The last group calls the route handlers
directly and is skipped when FastAPI is not installed. What they pin:

    * DATA       predefined CSVs load from ./data, are labelled SYNTHETIC /
                 SIMULATED, checksum-verified, and are real agent input
    * UPLOADS    screenshot / CSV / log accepted by content, hostile files
                 refused, stored under ./uploads and never under ./data
    * SELECTION  client -> group -> device -> eSIM/RSP/network cascades; a
                 dataset or incident for another device is cleared, not kept
    * PROVENANCE real-time, synthetic and uploaded input are always distinct
    * STATE      the selection survives a restart; PROCESS DATA is refused until
                 ready and then freezes an immutable snapshot
"""

from __future__ import annotations

import asyncio
import os
import shutil
import struct
import sys
import tempfile
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services import datasets, input_state, uploads
from backend.services.input_state import InputError, InputStore

REALTIME_IDLE = {"running": False, "devices": ["89330000000100001", "89330000000100002"],
                 "source_label": "Prototype Server / Grid Telemetry",
                 "started_at": None, "last_ts": None}
REALTIME_LIVE = {**REALTIME_IDLE, "running": True, "started_at": 1000.0, "last_ts": 1060.0}


def _png(w: int = 3, h: int = 2) -> bytes:
    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _ctx():
    return datasets.registry(), datasets.list_datasets()


def _patch(state, patch, incident_device=lambda i: None):
    reg, dsets = _ctx()
    return input_state.apply_patch(state, patch, reg=reg, dsets=dsets, incident_device=incident_device)


def _expect(exc, fn, why):
    try:
        fn()
    except exc:
        return
    raise AssertionError(why)


class _tmp_env:
    """Point UPLOADS_DIR (and optionally DATA_DIR) at a temp folder."""

    def __init__(self, data=False):
        self.data = data

    def __enter__(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.old = {k: os.environ.get(k) for k in ("UPLOADS_DIR", "DATA_DIR")}
        os.environ["UPLOADS_DIR"] = str(self.tmp / "uploads")
        if self.data:
            shutil.copytree(datasets.DEFAULT_DATA_DIR, self.tmp / "data")
            os.environ["DATA_DIR"] = str(self.tmp / "data")
        return self.tmp

    def __exit__(self, *a):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# DATA: predefined datasets
# ---------------------------------------------------------------------------
def test_predefined_datasets_load_and_are_labelled_synthetic():
    reg, dsets = _ctx()
    assert len(reg["clients"]) == 3 and len(dsets) == 6
    assert reg["provenance"] == "SYNTHETIC / SIMULATED"
    for table in ("clients", "groups", "devices", "networks", "rsp_environments"):
        assert all(r["provenance"] == "SYNTHETIC / SIMULATED" for r in reg[table]), table
        assert all(r["source_file"].startswith("data/") for r in reg[table])
    for d in dsets:
        assert d["provenance"] == "SYNTHETIC / SIMULATED" and d["path"].startswith("data/telemetry/")
        assert d["rows"] in (300, 320) and d["integrity"] == "verified"   # slow-radio scenario is 320 samples
        assert d["time_range"]["to"] > d["time_range"]["from"]
        assert d["devices"] == [d["euicc_id"]], "each dataset describes exactly its device"
        raw = (datasets.data_dir() / d["file"]).read_text().splitlines()[1]
        assert raw.endswith("SYNTHETIC / SIMULATED"), "every row carries its own label"
    ids = {x["euicc_id"] for x in reg["devices"]}
    from backend.services.agent_service import SESSION
    assert {d["euicc_id"] for d in SESSION.realtime.devices} <= ids, "real-time grid devices are in the registry"


def test_dataset_detail_and_client_filter():
    d = datasets.get_dataset("isdp_corruption_48213", preview_rows=8)
    assert len(d["preview"]) == 8 and d["stats"]["aka_fail_rate"]["max"] > 5
    assert d["fault_labels_present"] == ["isdp_corruption"] and d["first_fault_row"] == 250
    assert {x["client_id"] for x in datasets.list_datasets("CL-002")} == {"CL-002"}
    _expect(datasets.UnknownDataset, lambda: datasets.get_dataset("nope"), "unknown dataset must raise")


def test_stored_dataset_is_real_agent_input():
    from esim_selfhealing.orchestrator import build_default_orchestrator
    obs = datasets.dataset_observations("isdp_corruption_48213")
    assert len(obs) == 300 and obs[-1].ground_truth_fault == "isdp_corruption"
    report = build_default_orchestrator(enable_learning=False).run(obs)
    assert report.samples_seen == 300 and len(report.records) >= 1, "the agent opens an incident from the CSV"
    quiet = build_default_orchestrator(enable_learning=False).run(
        datasets.dataset_observations("healthy_baseline_100004"))
    assert len(quiet.records) == 0, "the healthy baseline stays quiet"


def test_edited_or_missing_dataset_is_flagged():
    with _tmp_env(data=True) as tmp:
        assert all(d["integrity"] == "verified" for d in datasets.list_datasets())
        target = tmp / "data" / "telemetry" / "isdp_corruption_48213.csv"
        target.write_text(target.read_text() + "x\n")
        (tmp / "data" / "telemetry" / "key_desync_77145.csv").unlink()
        by = {d["id"]: d for d in datasets.list_datasets()}          # must not crash on a bad file
        assert by["isdp_corruption_48213"]["integrity"] == "modified", by
        assert by["key_desync_77145"]["integrity"] == "missing" and by["key_desync_77145"]["rows"] == 0
        assert by["smdp_outage_51887"]["integrity"] == "verified", "other datasets are unaffected"
        _expect(datasets.DatasetError, lambda: datasets.dataset_observations("isdp_corruption_48213"),
                "a modified dataset must not be fed to the agent")
        _expect(datasets.DatasetError, lambda: datasets.get_dataset("key_desync_77145"), "a missing dataset has no detail")


# ---------------------------------------------------------------------------
# UPLOADS
# ---------------------------------------------------------------------------
def test_upload_validation_by_content():
    info = uploads.inspect("screenshot", "ss.png", _png(5, 4))
    assert info["mime"] == "image/png" and info["meta"]["size_px"] == {"width": 5, "height": 4}

    csv_info = uploads.inspect("csv", "t.csv", b"aka_fail_rate,rsrp_dbm,drop_rate,latency_ms,ota_fail_rate\n1,-90,0.01,120,0.01\n")
    assert csv_info["meta"]["telemetry_compatible"] and csv_info["meta"]["rows"] == 1
    other = uploads.inspect("csv", "o.csv", "a;b\n1;2\n3;4\n".encode())
    assert other["meta"]["delimiter"] == ";" and not other["meta"]["telemetry_compatible"]

    log = uploads.inspect("log", "modem.log", b"line one\nline two\n")
    assert log["meta"]["lines"] == 2 and log["meta"]["preview"][0] == "line one"

    bad = [
        ("screenshot", "fake.png", b"this is not a png"),
        ("screenshot", "x.svg", b"<svg xmlns='http://www.w3.org/2000/svg'><script>1</script></svg>"),
        ("csv", "d.csv", b"a,b\n\x00\x01\x02"),
        ("csv", "d.txt", b"a,b\n1,2\n"),
        ("csv", "empty.csv", b"   \n"),
        ("csv", "dup.csv", b"a,a\n1,2\n"),
        ("log", "b.log", b"abc\x00def"),
        ("log", "run.exe", b"hello"),
        ("nope", "a.txt", b"x"),
        ("log", "e.log", b""),
    ]
    for kind, name, data in bad:
        _expect(uploads.UploadError, lambda k=kind, n=name, d=data: uploads.inspect(k, n, d),
                f"{kind}/{name} should have been rejected")
    gif = uploads.inspect("screenshot", "really_a_gif.png", b"GIF89a" + struct.pack("<HH", 7, 9) + b"\x00" * 8)
    assert gif["mime"] == "image/gif", "the type comes from the content, not the extension"
    _expect(uploads.UploadError, lambda: uploads.inspect("log", "big.log", b"a" * (uploads.MAX_BYTES + 1)),
            "oversized must be rejected")


def test_filenames_cannot_escape_the_uploads_folder():
    assert uploads.safe_name("../../etc/passwd") == "passwd"
    assert uploads.safe_name("C:\\Users\\op\\screen shot (1).PNG") == "screen_shot_1.png"
    assert uploads.safe_name("\x00\x01") == "file"
    with _tmp_env():
        _expect(uploads.UploadError, lambda: uploads.path_for("csv", "../secrets.csv"), "traversal must be refused")
        p = uploads.write("csv", uploads.stored_name("UPL-0001", "../../x.csv"), b"a\n1\n")
        assert uploads.upload_dir().resolve() in p.resolve().parents


def test_uploads_and_data_are_separate_and_kept_by_kind():
    with _tmp_env() as tmp:
        assert uploads.upload_dir() != datasets.data_dir()
        before = sorted(str(p) for p in datasets.data_dir().rglob("*"))
        store = InputStore(":memory:")
        for kind, name, data in (("screenshot", "a.png", _png()), ("csv", "b.csv", b"x,y\n1,2\n"), ("log", "c.log", b"hi\n")):
            info = uploads.inspect(kind, name, data)
            store.add_upload(kind=kind, filename=name, mime=info["mime"], size=len(data), sha256="0",
                             uploaded_by="owner", client_id="CL-001", group_id=None, device_id=None,
                             incident_id=None, meta=info["meta"], data=data)
        found = {p.parent.name for p in (tmp / "uploads").rglob("*") if p.is_file()}
        assert found == {"screenshots", "csv", "logs"}
        assert sorted(str(p) for p in datasets.data_dir().rglob("*")) == before, "data/ is untouched by uploads"
        rec = store.list_uploads()[0]
        assert rec["folder"] == "uploads/logs" and rec["stored_name"].startswith(rec["id"] + "_")
        store.delete_upload(rec["id"])
        assert not (tmp / "uploads" / "logs" / rec["stored_name"]).exists(), "delete removes the file too"


# ---------------------------------------------------------------------------
# SELECTION
# ---------------------------------------------------------------------------
def test_selection_cascade_client_group_device():
    s = _patch(None, {"client_id": "CL-001"})
    assert s["client_id"] == "CL-001" and s["device_id"] is None
    s = _patch(s, {"device_id": "89330000000048213"})
    assert (s["group_id"], s["rsp_env_id"], s["network_id"]) == ("GRP-101", "RSP-SIM-1", "NET-A")
    s = _patch(s, {"rsp_env_id": "RSP-SIM-2"})
    assert s["rsp_env_id"] == "RSP-SIM-2", "the operator can override the RSP environment"
    s2 = _patch(s, {"client_id": "CL-002"})
    assert s2["device_id"] is None and s2["group_id"] is None and s2["network_id"] is None, "changing client clears the rest"
    _expect(InputError, lambda: _patch(s, {"device_id": "89330000000051887"}), "another client's device must be refused")
    _expect(InputError, lambda: _patch(s, {"client_id": "CL-999"}), "unknown client must be refused")
    only_device = _patch(None, {"device_id": "89330000000051887"})
    assert only_device["client_id"] == "CL-002", "choosing a device alone also selects its client"


def test_dataset_selects_its_device_and_is_cleared_when_it_no_longer_fits():
    s = _patch(None, {"client_id": "CL-001", "data_source": {"mode": "synthetic", "dataset_id": "key_desync_77145"}})
    assert s["device_id"] == "89330000000077145" and s["group_id"] == "GRP-102"
    s = _patch(s, {"device_id": "89330000000048213"})
    assert s["data_source"]["dataset_id"] is None and s["data_source"]["mode"] == "synthetic", "dataset cleared, mode kept"
    _expect(InputError, lambda: _patch(s, {"data_source": {"dataset_id": "smdp_outage_51887"}}),
            "a dataset owned by another client must be refused")
    _expect(InputError, lambda: _patch(s, {"data_source": {"mode": "carrier-pigeon"}}), "bad mode must be refused")


def test_incident_must_belong_to_the_device():
    dev_of = {"INC-A": "89330000000048213", "INC-B": "89330000000051887", "INC-X": "99999999999999999"}.get
    s = _patch(None, {"client_id": "CL-001", "incident_id": "INC-A"}, dev_of)
    assert s["device_id"] == "89330000000048213" and s["incident_id"] == "INC-A"
    s = _patch(s, {"device_id": "89330000000090031"}, dev_of)
    assert s["incident_id"] is None, "moving to another device drops the old incident"
    _expect(InputError, lambda: _patch(s, {"incident_id": "INC-B"}, dev_of), "another client's incident must be refused")
    _expect(InputError, lambda: _patch(s, {"incident_id": "INC-X"}, dev_of), "a device outside the registry must be refused")
    _expect(InputError, lambda: _patch(s, {"incident_id": "INC-NOPE"}, dev_of), "an unknown incident must be refused")


# ---------------------------------------------------------------------------
# READINESS + PROVENANCE
# ---------------------------------------------------------------------------
def _view(state, rt=REALTIME_IDLE, attachments=()):
    reg, dsets = _ctx()
    return (input_state.readiness(state, reg=reg, dsets=dsets, realtime=rt, attachments=list(attachments)),
            input_state.provenance(state, reg=reg, dsets=dsets, realtime=rt, attachments=list(attachments)))


def test_readiness_gates_processing():
    ready, _ = _view(input_state.empty_state())
    assert not ready["ready"]
    s = _patch(None, {"client_id": "CL-001", "device_id": "89330000000048213"})
    assert not _view(s)[0]["ready"], "no data source yet"
    rt = _patch(s, {"data_source": {"mode": "realtime"}})
    checks = {c["key"]: c for c in _view(rt)[0]["checks"]}
    assert not checks["grid"]["ok"] and checks["grid"]["blocking"], "48213 is not in the real-time grid"
    grid_dev = _patch(rt, {"device_id": "89330000000100001"})
    r = _view(grid_dev)[0]
    assert r["ready"], "a grid device is ready even while the feed is stopped..."
    feed = next(c for c in r["checks"] if c["key"] == "feed")
    assert not feed["ok"] and not feed["blocking"], "...but the stopped feed is flagged as advice"
    syn = _patch(s, {"data_source": {"mode": "synthetic"}})
    assert not _view(syn)[0]["ready"], "synthetic without a dataset is not ready"
    assert _view(_patch(syn, {"data_source": {"dataset_id": "isdp_corruption_48213"}}))[0]["ready"]


def test_provenance_never_mixes_real_time_synthetic_and_upload():
    s = _patch(None, {"client_id": "CL-001", "data_source": {"mode": "synthetic", "dataset_id": "isdp_corruption_48213"}})
    up_rec = {"id": "UPL-0001", "kind": "csv", "uploaded_by": "owner", "device_id": None, "folder": "uploads/csv",
              "stored_name": "UPL-0001_t.csv", "meta": {"columns": ["a", "b"], "telemetry_compatible": False}}
    _, rows = _view(s, attachments=[up_rec])
    by = {r["role"]: r for r in rows}
    src = by["Data source"]
    assert src["type"] == "SYNTHETIC / SIMULATED" and src["type_key"] == "synthetic"
    assert src["dataset"] == "data/telemetry/isdp_corruption_48213.csv" and src["client"] == "Meridian Logistics"
    assert src["group"] == "Depot trackers" and src["time_range"]["from"] < src["time_range"]["to"]
    assert "aka_fail_rate" in src["fields"]
    assert by["CSV upload"]["type"] == "OPERATOR UPLOAD" and by["CSV upload"]["type_key"] == "upload"
    assert by["CSV upload"]["dataset"].startswith("uploads/csv/")

    rt = _patch(s, {"device_id": "89330000000100001", "data_source": {"mode": "realtime"}})
    _, rows = _view(rt, rt=REALTIME_LIVE)
    src = next(r for r in rows if r["role"] == "Data source")
    assert src["type"] == "REAL-TIME" and src["type_key"] == "realtime" and src["dataset"] == "Live stream (no file)"
    assert "not a connection to live telecom" in src["note"]
    assert not any(r["type_key"] == "synthetic" and r["role"] == "Data source" for r in rows), "only the active source is listed"


# ---------------------------------------------------------------------------
# STATE persistence + commit
# ---------------------------------------------------------------------------
def test_state_survives_restart_and_uploads_stay_associated():
    with _tmp_env() as tmp:
        path = tmp / "inputs.db"
        first = InputStore(path)
        state = _patch(None, {"client_id": "CL-002", "device_id": "89330000000063902",
                              "data_source": {"mode": "synthetic", "dataset_id": "slow_radio_drift_63902"}})
        first.put_state("owner", state)
        data = b"one\ntwo\n"
        rec = first.add_upload(kind="log", filename="modem.log", mime="text/plain", size=len(data), sha256="0",
                               uploaded_by="owner", client_id="CL-002", group_id="GRP-201",
                               device_id="89330000000063902", incident_id=None, meta={"lines": 2}, data=data)
        first.close()

        second = InputStore(path)                       # "the server restarted"
        assert second.get_state("owner") == state
        assert second.get_state("someone-else") == input_state.empty_state(), "state is per operator"
        assert second.get_upload(rec["id"])["device_id"] == "89330000000063902"
        mine = input_state.attachments_for(state, second.list_uploads())
        assert [u["id"] for u in mine] == [rec["id"]]
        other_device = _patch(state, {"device_id": "89330000000051887"})
        assert input_state.attachments_for(other_device, second.list_uploads()) == [], "an upload stays with its device"
        other_client = _patch(state, {"client_id": "CL-001"})
        assert input_state.attachments_for(other_client, second.list_uploads()) == []
        second.close()


def test_commit_is_refused_until_ready_then_immutable():
    reg, dsets = _ctx()
    kw = dict(reg=reg, dsets=dsets, realtime=REALTIME_IDLE, attachments=[])
    _expect(InputError, lambda: input_state.build_commit(input_state.empty_state(), **kw), "empty input must be refused")
    s = _patch(None, {"client_id": "CL-003", "data_source": {"mode": "synthetic", "dataset_id": "healthy_baseline_100004"}})
    payload = input_state.build_commit(s, **kw)
    assert payload["resolved"]["client"]["name"] == "Northgate Health"
    assert payload["resolved"]["device"]["eid"].startswith("89049032") and len(payload["resolved"]["device"]["iccid"]) == 19
    assert payload["source"]["type"] == "SYNTHETIC / SIMULATED" and payload["provenance"]

    store = InputStore(":memory:")
    c1 = store.add_commit("owner", payload)
    assert c1["id"] == "INP-0001" and store.latest_commit("owner")["id"] == "INP-0001"
    s2 = _patch(s, {"client_id": "CL-001"})
    store.put_state("owner", s2)                        # later edits do not touch the frozen commit
    assert store.get_commit("INP-0001")["state"]["client_id"] == "CL-003"
    assert store.latest_commit("nobody") is None


# ---------------------------------------------------------------------------
# route handlers (skipped without FastAPI)
# ---------------------------------------------------------------------------
class _FakeRequest:
    def __init__(self, body: bytes):
        self.headers = {"content-length": str(len(body))}
        self._body = body

    async def stream(self):
        yield self._body


def test_handlers_end_to_end():
    try:
        from backend.api import routes
    except ImportError:
        print("        (skipped: FastAPI not installed)")
        return
    from backend.services import reports
    from backend.services.agent_service import SESSION
    owner = {"username": "Owner", "role": "owner"}
    with _tmp_env():
        input_state.use_store(InputStore(":memory:"))
        reports.use_store(reports.ReportStore(":memory:"))
        try:
            def up(kind, name, data):
                return asyncio.run(routes.upload_file(_FakeRequest(data), kind, name, owner))

            def status(fn, *a):
                try:
                    fn(*a)
                except Exception as exc:                # HTTPException
                    return getattr(exc, "status_code", None)
                return 200

            # nothing selected yet: uploads need a client
            assert status(up, "log", "a.log", b"hi") == 422
            assert routes.get_input_state(owner)["readiness"]["ready"] is False

            reg = routes.input_registry(owner)
            assert {c["client_id"] for c in reg["registry"]["clients"]} == {"CL-001", "CL-002", "CL-003"}
            assert next(d for d in reg["registry"]["devices"] if d["euicc_id"] == "89330000000100001")["in_realtime_grid"]
            assert not next(d for d in reg["registry"]["devices"] if d["euicc_id"] == "89330000000048213")["in_realtime_grid"]
            assert len(reg["datasets"]) == 6

            v = routes.put_input_state(routes.InputPatch(client_id="CL-001"), owner)
            assert v["state"]["client_id"] == "CL-001" and v["resolved"]["client"]["name"] == "Meridian Logistics"
            v = routes.put_input_state(routes.InputPatch(device_id="89330000000100002"), owner)
            assert v["state"]["group_id"] == "GRP-102"
            assert status(routes.put_input_state, routes.InputPatch(client_id="CL-404"), owner) == 422

            # real-time selection
            v = routes.put_input_state(routes.InputPatch(data_source=routes.DataSourcePatch(mode="realtime")), owner)
            assert v["readiness"]["ready"] is True
            assert [r["type_key"] for r in v["provenance"] if r["role"] == "Data source"] == ["realtime"]

            # synthetic selection (mode switch keeps everything else)
            v = routes.put_input_state(routes.InputPatch(
                data_source=routes.DataSourcePatch(mode="synthetic", dataset_id="key_desync_77145")), owner)
            assert v["state"]["device_id"] == "89330000000077145"
            assert [r["type_key"] for r in v["provenance"] if r["role"] == "Data source"] == ["synthetic"]
            detail = routes.input_dataset("key_desync_77145", 5, owner)
            assert len(detail["preview"]) == 5 and detail["provenance"] == "SYNTHETIC / SIMULATED"

            # uploads: all three kinds, tied to the current selection
            shot = up("screenshot", "modem screen.png", _png())["upload"]
            csv_ = up("csv", "counters.csv", b"a,b\n1,2\n")["upload"]
            out = up("log", "syslog.txt", b"boot\nattach fail\n")
            assert (shot["kind"], csv_["kind"], out["upload"]["kind"]) == ("screenshot", "csv", "log")
            assert shot["client_id"] == "CL-001" and shot["device_id"] == "89330000000077145"
            assert {r["type_key"] for r in out["input"]["provenance"]} == {"synthetic", "upload"}
            assert len([u for u in out["input"]["uploads"] if u["attached"]]) == 3
            assert status(up, "screenshot", "evil.png", b"<html>") == 422
            assert status(up, "log", "huge.log", b"x\n" * (uploads.MAX_BYTES // 2 + 10)) == 413, "over 10 MB is refused"

            content = routes.upload_file_content(shot["id"], owner)
            # `.body` is where Starlette puts the rendered content; there is no
            # `.content` attribute on a Response, only the constructor keyword.
            assert content.media_type == "image/png" and content.body == _png()
            assert routes.upload_file_content(out["upload"]["id"], owner).headers["Content-Disposition"].startswith("attachment")
            assert content.headers["X-Content-Type-Options"] == "nosniff"

            # moving to another device: uploads stay with the old one
            v = routes.put_input_state(routes.InputPatch(device_id="89330000000048213"), owner)
            assert not any(u["attached"] for u in v["uploads"]) and v["state"]["data_source"]["dataset_id"] is None

            # incident association: a real incident from the session
            SESSION.reset()
            SESSION.run("isdp_corruption")            # first run keeps the scenario's own device id
            inc = SESSION.incidents()[0]["incident_id"]
            v = routes.put_input_state(routes.InputPatch(incident_id=inc), owner)
            assert v["state"]["incident_id"] == inc and v["state"]["device_id"] == "89330000000048213"
            assert status(routes.put_input_state, routes.InputPatch(incident_id="INC-NOPE"), owner) == 422
            SESSION.run("isdp_corruption")            # second run re-numbers the device: outside the registry
            newest = SESSION.incidents()[0]["incident_id"]
            assert SESSION.incident_device(newest) != "89330000000048213"
            assert status(routes.put_input_state, routes.InputPatch(incident_id=newest), owner) == 422, \
                "an incident on a device the registry does not know is refused with an explanation"

            # PROCESS DATA
            routes.put_input_state(routes.InputPatch(
                data_source=routes.DataSourcePatch(mode="synthetic", dataset_id="isdp_corruption_48213")), owner)
            commit = routes.commit_input(owner)
            assert commit["id"] == "INP-0001" and commit["by"] == "owner"
            assert commit["resolved"]["device"]["euicc_id"] == "89330000000048213"
            assert commit["attachments"] == []
            assert routes.latest_input_commit(owner)["commit"]["id"] == "INP-0001"
            assert routes.input_commit("INP-0001", owner)["state"]["client_id"] == "CL-001"
            routes.put_input_state(routes.InputPatch(data_source=routes.DataSourcePatch(dataset_id=None)), owner)
            assert status(routes.commit_input, owner) == 422, "no dataset: refused"

            assert routes.delete_upload(shot["id"], owner)["deleted"] == shot["id"]
            assert status(routes.upload_file_content, shot["id"]) == 404
        finally:
            input_state.use_store(None)
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
