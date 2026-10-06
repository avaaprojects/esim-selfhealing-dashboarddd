"""The field specification: what may be randomised, and within what limits.

    python tests/test_fields.py

Pins the answer to "can we just randomise the inputs?" so it cannot drift:
structural fields stay un-randomisable, measured channels stay inside their
physical limits, and the shipped data obeys both.
"""

from __future__ import annotations

import csv
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from esim_selfhealing import fields
from esim_selfhealing.schemas import FEATURE_NAMES
from esim_selfhealing.telemetry import FaultWindow, TelemetryStream

FAULTS = ("isdp_corruption", "smdp_session_outage", "radio_degradation", "key_desync")


def test_every_telemetry_channel_is_specified_with_units_and_limits():
    assert set(fields.TELEMETRY) == set(FEATURE_NAMES), "a channel is missing from the spec"
    for name, field in fields.TELEMETRY.items():
        assert field.randomisable, f"{name} is a measured channel: a generator may vary it"
        assert field.low is not None and field.high is not None, f"{name} has no physical limits"
        assert field.low < field.high and field.unit and field.description
        lo, hi = field.typical
        assert field.low <= lo < hi <= field.high, f"{name}'s typical range sits outside its limits"
    # the unit that is easy to misread is stated, because the name says "rate"
    assert "per 100" in fields.TELEMETRY["aka_fail_rate"].unit
    # RSRP is always negative: a positive dBm reading cannot exist
    assert fields.TELEMETRY["rsrp_dbm"].high < 0
    # latency cannot be zero
    assert fields.TELEMETRY["latency_ms"].low > 0


def test_identity_and_structure_may_never_be_randomised():
    for name, field in fields.STRUCTURAL.items():
        assert not field.randomisable, f"{name} must not be randomisable"
        assert field.reason, f"{name} gives no reason, so a reader cannot check the claim"
    for name in ("euicc_id", "iccid", "client_id", "plmn", "fleet_size",
                 "sim_fault_label", "ts_epoch", "provenance"):
        assert not fields.randomisable(name), f"{name} is randomisable, which it must not be"
    # an unknown field is refused rather than assumed safe
    assert not fields.randomisable("something_new")


def test_the_generator_cannot_produce_an_impossible_reading():
    """Every fault, at full strength, stays inside the physical limits."""
    for fault in FAULTS:
        stream = TelemetryStream(n_samples=400, faults=[FaultWindow(fault, 80, 400)], seed=11)
        problems = []
        for obs in stream:
            problems += fields.check_row(dict(zip(FEATURE_NAMES, obs.features)))
        assert not problems, f"{fault}: {problems[:2]}"
    # and the fault still shows up: clamping must not flatten the signal
    healthy = TelemetryStream(n_samples=200, seed=11)
    faulty = TelemetryStream(n_samples=200, faults=[FaultWindow("key_desync", 100, 200)], seed=11)
    h = [o.features[0] for o in healthy][100:]
    f = [o.features[0] for o in faulty][100:]
    assert sum(f) / len(f) > sum(h) / len(h) * 3, "the key-desync signature was clamped away"


def test_the_shipped_datasets_obey_the_spec():
    for path in sorted((ROOT / "data" / "telemetry").glob("*.csv")):
        rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
        assert rows, f"{path.name} is empty"
        problems = []
        for row in rows:
            problems += fields.check_row({n: float(row[n]) for n in FEATURE_NAMES})
        assert not problems, f"{path.name}: {problems[:2]}"


def test_an_uploaded_file_with_impossible_values_is_refused():
    from backend.services import custom_input
    device = {"euicc_id": "89330000000048213", "cell_id": "CELL-4471"}
    base = [["ts_epoch", *FEATURE_NAMES]]
    for i in range(150):
        base.append([str(1772443800 + i), "0.8", "-92.0", "0.015", "120.0", "0.010"])

    def as_bytes(rows):
        import io
        out = io.StringIO()
        csv.writer(out, lineterminator="\n").writerows(rows)
        return out.getvalue().encode()

    assert custom_input.parse(as_bytes(base), device=device)["summary"]["rows"] == 150

    for column, value, expect in (("rsrp_dbm", "15.0", "above the physical maximum"),
                                  ("rsrp_dbm", "-400", "below the physical minimum"),
                                  ("drop_rate", "4.5", "above the physical maximum"),
                                  ("ota_fail_rate", "-0.2", "below the physical minimum"),
                                  ("latency_ms", "0", "below the physical minimum"),
                                  ("aka_fail_rate", "250", "above the physical maximum")):
        rows = [r[:] for r in base]
        rows[7][base[0].index(column)] = value
        try:
            custom_input.parse(as_bytes(rows), device=device)
        except custom_input.CustomInputError as exc:
            assert expect in str(exc), f"{column}={value}: {exc}"
            assert "units" in str(exc), "the message should point at the likely cause"
        else:
            raise AssertionError(f"{column} = {value} was accepted; it is physically impossible")


def test_unusual_but_possible_values_are_reported_not_refused():
    """A file may be all fault data. That is a note, not an error."""
    from backend.services import custom_input
    import io
    device = {"euicc_id": "89330000000048213", "cell_id": "CELL-4471"}
    rows = [["ts_epoch", *FEATURE_NAMES]]
    for i in range(150):
        rows.append([str(1772443800 + i), "30.0", "-120.0", "0.5", "900.0", "0.4"])
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    summary = custom_input.parse(out.getvalue().encode(), device=device)["summary"]
    assert summary["rows"] == 150
    assert summary["notes"], "a file entirely outside the healthy range should say so"
    assert any("aka_fail_rate" in n for n in summary["notes"])


def _gnettrack_stand_in(n=400):
    """A file in the G-NetTrack Pro layout the public traces use."""
    import io, math, random
    rng = random.Random(5)
    cols = ["Timestamp", "Longitude", "Latitude", "Speed", "Operatorname", "CellID",
            "NetworkMode", "RSRP", "RSRQ", "SNR", "CQI", "DL_bitrate", "UL_bitrate", "State"]
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(cols)
    for i in range(n):
        rsrp = -78 - 30 * abs(math.sin(i / 60)) - rng.gauss(0, 4)
        w.writerow([f"2019.12.16_13.{46 + i // 60:02d}.{i % 60:02d}", "-8.47", "51.89", "12",
                    "OperatorA", "2001", "5G", f"{rsrp:.0f}", "-9", "12", "8",
                    str(rng.randint(1000, 90000)), str(rng.randint(100, 9000)), "D"])
    return out.getvalue()


def test_a_real_radio_trace_converts_and_states_its_provenance():
    """tools/import_real_trace.py turns a measured trace into a dataset, and is
    explicit about which columns are measured and which are modelled."""
    import importlib.util
    import subprocess
    import tempfile

    spec = importlib.util.spec_from_file_location("irt", ROOT / "tools" / "import_real_trace.py")
    irt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(irt)

    # the G-NetTrack column names are recognised
    header = ["Timestamp", "RSRP", "RSRQ", "SNR", "CQI", "DL_bitrate"]
    found = irt.detect(header)
    assert found["rsrp_dbm"] == "RSRP" and found["timestamp"] == "Timestamp"
    assert found["latency_ms"] is None, "this layout has no latency column"
    # and so are the other public layouts
    assert irt.detect(["rsrp_lte", "latency_ms"])["rsrp_dbm"] == "rsrp_lte"
    assert irt.detect(["ssrsrp", "rtt_ms"])["latency_ms"] == "rtt_ms"

    tmp = Path(tempfile.mkdtemp())
    src = tmp / "trace.csv"
    src.write_text(_gnettrack_stand_in())
    out = tmp / "converted.csv"
    res = subprocess.run([sys.executable, str(ROOT / "tools" / "import_real_trace.py"), str(src),
                          "--device", "89330000000048213", "--fault", "smdp_session_outage",
                          "--out", str(out)], capture_output=True, text=True, cwd=str(ROOT))
    assert res.returncode == 0, res.stderr
    assert re.search(r"rsrp_dbm\s+MEASURED", res.stdout), "RSRP must be reported as measured"
    assert "MODELLED" in res.stdout, "channels absent from the file must be reported as modelled"

    rows = list(csv.DictReader(out.open(newline="")))
    assert len(rows) >= 100
    assert all(r["provenance"].startswith("REAL /") for r in rows), "the label must not claim pure synthetic"
    # every value stays physical
    problems = []
    for r in rows:
        problems += fields.check_row({n: float(r[n]) for n in FEATURE_NAMES})
    assert not problems, problems[:2]
    # the fault is present and labelled
    assert any(r["sim_fault_label"] == "smdp_session_outage" for r in rows)

    # the channels are correlated the way a network correlates them, and NOT so
    # tightly that they are one curve (a tell-tale of a naive model)
    import numpy as np
    X = np.array([[float(r[n]) for n in FEATURE_NAMES] for r in rows if not r["sim_fault_label"]])
    C = np.corrcoef(X.T)
    rsrp, drop, latency = FEATURE_NAMES.index("rsrp_dbm"), FEATURE_NAMES.index("drop_rate"), FEATURE_NAMES.index("latency_ms")
    assert C[rsrp, drop] < -0.2, "drop rate should rise as RSRP falls"
    assert C[rsrp, latency] < -0.2, "latency should rise as RSRP falls"
    off = [abs(C[i, j]) for i in range(5) for j in range(5) if i != j]
    assert max(off) < 0.9, f"correlation {max(off):.2f} is unrealistically tight"

    # a file with no RSRP is refused rather than invented
    bare = tmp / "nothing.csv"
    bare.write_text("a,b\n1,2\n3,4\n")
    res = subprocess.run([sys.executable, str(ROOT / "tools" / "import_real_trace.py"), str(bare),
                          "--device", "89330000000048213"], capture_output=True, text=True, cwd=str(ROOT))
    assert res.returncode == 1 and "no RSRP column" in res.stdout + res.stderr

    shutil.rmtree(tmp, ignore_errors=True)


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
