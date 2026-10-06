"""Turn a real mobile-network trace into a dataset this dashboard can run.

    python tools/import_real_trace.py trace.csv --device 89330000000048213
    python tools/import_real_trace.py trace.csv --device 8933... --fault smdp_session_outage
    python tools/import_real_trace.py trace.csv --device 8933... --inspect   # columns only

Public radio traces (the Irish 5G dataset, ULR-MC5G, Lumos-5G, your own
G-NetTrack Pro recording) carry *measured* radio KPIs: RSRP, RSRQ, SINR, often
latency and throughput. They do not carry eSIM provisioning counters, because no
public dataset does - those live inside operators and SM-DP+ vendors.

So this tool is explicit about what it did to every column, and writes that into
the dataset's own provenance:

    MEASURED   taken from the file as recorded
    MODELLED   derived from a measured column by a stated relationship
    SYNTHETIC  generated inside its specification bounds, no measurement behind it

That distinction is the point. A reviewer asking "is this real data?" gets a
column-by-column answer instead of a yes or a no, and the dashboard shows the
same breakdown next to the run.

The modelled channels use the radio relationship the measurement literature
reports: as RSRP falls toward the noise floor, sessions drop and round-trip
latency rises. Encoding that keeps the five channels *correlated the way a real
network correlates them*, which is what MONITOR's Mahalanobis distance is
measured against - independent draws would leave the detector scoring a process
that does not exist.

Tested against the G-NetTrack Pro column layout (Timestamp, RSRP, RSRQ, SNR,
CQI, DL_bitrate, State, ...) and a plain five-column file; `--inspect` shows
what was recognised before anything is written.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np                                                      # noqa: E402

from esim_selfhealing import fields                                     # noqa: E402
from esim_selfhealing.rsp_api import FAULT_EFFECTS                      # noqa: E402
from esim_selfhealing.schemas import FEATURE_NAMES                      # noqa: E402
from esim_selfhealing.telemetry import FAULT_SIGNATURES                 # noqa: E402

#: column names seen in the public traces, lower-cased, in preference order
CANDIDATES = {
    "rsrp_dbm": ("rsrp", "rsrp_dbm", "rsrp_lte", "lte_rsrp", "ss_rsrp", "ssrsrp", "nr_rsrp"),
    "latency_ms": ("latency_ms", "latency", "rtt_ms", "rtt", "ping_ms", "ping",
                   "dl_latency", "uplink_latency_ms"),
    "drop_rate": ("drop_rate", "packet_loss", "loss_rate", "packetloss", "loss"),
    "aka_fail_rate": ("aka_fail_rate",),
    "ota_fail_rate": ("ota_fail_rate",),
    "timestamp": ("timestamp", "ts", "ts_epoch", "time", "datetime", "ts_iso"),
    "quality": ("sinr", "snr", "rsrq", "cqi"),          # supporting, not a channel
}

MEASURED, MODELLED, SYNTHETIC = "MEASURED", "MODELLED", "SYNTHETIC"


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")


def detect(header: List[str]) -> Dict[str, Optional[str]]:
    """Which source column (if any) feeds each of our channels."""
    lookup = {_norm(h): h for h in header}
    found: Dict[str, Optional[str]] = {}
    for target, options in CANDIDATES.items():
        found[target] = next((lookup[o] for o in options if o in lookup), None)
    return found


def _number(raw: Any) -> Optional[float]:
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _time(raw: Any) -> Optional[float]:
    text = str(raw).strip()
    number = _number(text)
    if number is not None and number > 1e8:
        return number / 1000.0 if number > 1e11 else number
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
                "%Y.%m.%d_%H.%M.%S", "%d/%m/%Y %H:%M:%S"):
        try:
            stamp = datetime.strptime(text.replace("Z", "+0000"), fmt)
            return stamp.replace(tzinfo=stamp.tzinfo or timezone.utc).timestamp()
        except ValueError:
            continue
    return None


def model_from_rsrp(rsrp: np.ndarray, rng: np.random.Generator) -> Dict[str, np.ndarray]:
    """Channels not in the file, derived from the measured RSRP.

    The relationship is the one field measurement reports: link quality degrades
    as RSRP approaches the noise floor, and sessions drop and round-trip latency
    rises with it. `q` is 0 at a strong signal (-70 dBm) and 1 at a weak one
    (-120 dBm); each channel is a healthy floor plus a term in q, with noise.
    Stated here so the modelling can be checked rather than taken on trust.
    """
    q = np.clip((-70.0 - rsrp) / 50.0, 0.0, 1.0)
    # The noise is deliberately large relative to the signal. A tight function of
    # RSRP would give correlations near 0.95, which no real network shows - field
    # studies report moderate coupling (roughly 0.3..0.7), because load, backhaul
    # and server state move these channels too. Each channel also gets its own
    # independent component, so they are not all the same curve.
    drop = 0.006 + 0.055 * q ** 2 + rng.normal(0, 0.012, size=q.size)
    latency = 60.0 + 190.0 * q ** 2 + rng.normal(0, 55.0, size=q.size)
    ota = 0.005 + 0.035 * q ** 2 + rng.normal(0, 0.011, size=q.size)
    aka = 0.50 + 1.8 * q ** 2 + rng.normal(0, 0.75, size=q.size)
    return {"drop_rate": drop, "latency_ms": latency, "ota_fail_rate": ota, "aka_fail_rate": aka}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", type=Path, help="the downloaded trace")
    ap.add_argument("--device", help="eUICC id from data/devices/devices.csv the readings belong to")
    ap.add_argument("--out", type=Path, help="where to write (default: alongside the input)")
    ap.add_argument("--fault", choices=sorted(FAULT_SIGNATURES), help="inject this fault over the last --fault-span rows")
    ap.add_argument("--fault-span", type=float, default=0.25, metavar="FRACTION",
                    help="fraction of the file the fault covers (default 0.25)")
    ap.add_argument("--max-rows", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--inspect", action="store_true", help="show the detected columns and stop")
    args = ap.parse_args()

    if not args.csv.is_file():
        print(f"error: {args.csv} does not exist", file=sys.stderr)
        return 2
    rows = list(csv.DictReader(args.csv.open(newline="", encoding="utf-8-sig")))
    if not rows:
        print("error: the file has no data rows", file=sys.stderr)
        return 1
    header = list(rows[0])
    found = detect(header)

    print(f"{args.csv.name}: {len(rows)} rows, {len(header)} columns")
    print("detected:")
    for target in ("rsrp_dbm", "latency_ms", "drop_rate", "aka_fail_rate", "ota_fail_rate"):
        print(f"    {target:<16} {found[target] or '(not in this file)'}")
    if found["quality"]:
        print(f"    {'(quality)':<16} {found['quality']} - kept as context, not a channel")
    if args.inspect:
        print("\ncolumns in the file: " + ", ".join(header))
        return 0

    if not found["rsrp_dbm"]:
        print("\nerror: no RSRP column found, so there is no measured radio signal to build on.\n"
              "       Use --inspect to see the columns, or pick a trace that records RSRP.", file=sys.stderr)
        return 1
    if not args.device:
        print("\nerror: --device is required (an eUICC id from data/devices/devices.csv)", file=sys.stderr)
        return 2

    # -- read the measured columns -----------------------------------------
    kept: List[Dict[str, Any]] = []
    for row in rows:
        rsrp = _number(row[found["rsrp_dbm"]])
        if rsrp is None or not (-140.0 <= rsrp <= -44.0):
            continue                                     # blank or out-of-range sample
        entry: Dict[str, Any] = {"rsrp_dbm": rsrp}
        for channel in ("latency_ms", "drop_rate", "aka_fail_rate", "ota_fail_rate"):
            if found[channel]:
                value = _number(row[found[channel]])
                if value is not None:
                    entry[channel] = value
        if found["timestamp"]:
            entry["ts"] = _time(row[found["timestamp"]])
        kept.append(entry)
        if len(kept) >= args.max_rows:
            break

    if len(kept) < 100:
        print(f"\nerror: only {len(kept)} usable rows (need 100: the detector spends its first 60 "
              "learning what normal looks like)", file=sys.stderr)
        return 1

    rng = np.random.default_rng(args.seed)
    rsrp = np.array([k["rsrp_dbm"] for k in kept])
    modelled = model_from_rsrp(rsrp, rng)

    provenance: Dict[str, str] = {"rsrp_dbm": MEASURED}
    columns: Dict[str, np.ndarray] = {"rsrp_dbm": rsrp}
    for channel in ("aka_fail_rate", "drop_rate", "latency_ms", "ota_fail_rate"):
        if all(channel in k for k in kept):
            columns[channel] = np.array([k[channel] for k in kept])
            provenance[channel] = MEASURED
        else:
            columns[channel] = modelled[channel]
            provenance[channel] = MODELLED

    # -- optional fault, so the dashboard has something to heal ------------
    labels = [""] * len(kept)
    if args.fault:
        start = int(len(kept) * (1.0 - max(0.05, min(0.9, args.fault_span))))
        signature = FAULT_SIGNATURES[args.fault]
        for j, name in enumerate(FEATURE_NAMES):
            ramp = np.zeros(len(kept))
            ramp[start:] = np.linspace(0.6, 1.0, len(kept) - start)
            columns[name] = columns[name] + ramp * signature[j]
        for i in range(start, len(kept)):
            labels[i] = args.fault
        print(f"\nfault '{args.fault}' injected from row {start + 1} of {len(kept)}")

    # -- physical limits, from the one specification -----------------------
    for name in FEATURE_NAMES:
        field = fields.TELEMETRY[name]
        columns[name] = np.clip(columns[name], field.low, field.high)

    # -- timestamps: measured if present, else one second apart ------------
    stamps = [k.get("ts") for k in kept]
    if all(s is not None for s in stamps) and len(set(stamps)) > len(stamps) // 2:
        times, ts_source = sorted(float(s) for s in stamps), MEASURED
    else:
        base = datetime(2026, 3, 2, 9, 30, tzinfo=timezone.utc).timestamp()
        times, ts_source = [base + i for i in range(len(kept))], SYNTHETIC

    out = args.out or args.csv.with_name(f"{args.csv.stem}_dashboard.csv")
    label = ("REAL / MEASURED + MODELLED" if MODELLED in provenance.values()
             else "REAL / MEASURED")
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["ts_epoch", "ts_iso", "euicc_id", "cell_id", *FEATURE_NAMES,
                         "sim_fault_label", "provenance"])
        for i in range(len(kept)):
            writer.writerow([
                f"{times[i]:.3f}",
                datetime.fromtimestamp(times[i], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                args.device, "CELL-REAL",
                *[f"{columns[name][i]:.6f}" for name in FEATURE_NAMES],
                labels[i], label])

    print(f"\nwrote {out}  ({len(kept)} rows)")
    print("\nprovenance, column by column - say this when asked whether the data is real:")
    for name in FEATURE_NAMES:
        source = provenance[name]
        note = {MEASURED: f"from '{found[name] or found['rsrp_dbm']}' in {args.csv.name}",
                MODELLED: "derived from the measured RSRP (see model_from_rsrp)",
                SYNTHETIC: "generated within specification bounds"}[source]
        print(f"    {name:<16} {source:<10} {note}")
    print(f"    {'timestamps':<16} {ts_source:<10} "
          + ("as recorded" if ts_source == MEASURED else "one second apart; the file had none"))
    if not args.fault:
        print("\nNo fault injected, so the detector should stay quiet. Re-run with "
              "--fault smdp_session_outage to give the agent something to heal.")
    print("\nNext: upload it on the Input screen under \"Your own CSV\", or make it a stored dataset:")
    print(f"    python tools/import_dataset.py {out} --device {args.device}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
