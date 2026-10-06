"""Regenerate the predefined datasets in ./data (deterministic).

    python tools/generate_datasets.py

Everything in data/ is SYNTHETIC / SIMULATED. Telemetry is written by the
project's own `esim_selfhealing.telemetry.TelemetryStream` generators with fixed
seeds and a fixed start time, so the CSVs are byte-for-byte reproducible and are
exactly what the built-in simulation scenarios stream. The client, group,
device, network, authentication and RSP registries are hand-written fictional
tables (endpoints use the reserved `.example` domain, the PLMN is the ITU test
network 001-01). None of it describes a real operator, customer or device.

data/manifest.json records, per file: what it is, how it was made, which client
it belongs to and a SHA-256, so the dashboard can show provenance and notice if
a file was edited after generation. Uploaded operator files never go here; they
live in ./uploads.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from esim_selfhealing.schemas import FEATURE_NAMES                      # noqa: E402
from esim_selfhealing.telemetry import (                                # noqa: E402
    FaultWindow,
    TelemetryStream,
    scenario_isdp_corruption,
    scenario_slow_radio_drift,
    scenario_smdp_outage,
)

DATA = ROOT / "data"
PROVENANCE = "SYNTHETIC / SIMULATED"

#: Fixed start of every dataset: 2026-01-05 02:00:00 UTC, one sample per second.
T0 = datetime(2026, 1, 5, 2, 0, 0, tzinfo=timezone.utc).timestamp()

# ---------------------------------------------------------------------------
# Registries: who the input belongs to. Fictional.
# ---------------------------------------------------------------------------
CLIENTS = [
    ("CL-001", "Meridian Logistics", "Logistics and cold chain"),
    ("CL-002", "Harbourline Utilities", "Smart metering and grid"),
    ("CL-003", "Northgate Health", "Connected medical devices"),
]

GROUPS = [
    ("GRP-101", "CL-001", "Depot trackers", "Asset trackers on the depot and yard network"),
    ("GRP-102", "CL-001", "Cold-chain fleet", "Refrigerated units on roaming profiles"),
    ("GRP-201", "CL-002", "Substation gateways", "Metering gateways behind the grid access network"),
    ("GRP-301", "CL-003", "Ward monitors", "Bedside monitors on a private slice"),
]

AUTH_CONTEXTS = [
    # key epochs match esim_selfhealing.rsp_api.EuiccState defaults (7 / 7).
    ("AUTH-EPS", "EPS-AKA (4G)", 7, 7, 0.8,
     "Mutual authentication with a per-profile key epoch shared by eUICC and SM-SR"),
    ("AUTH-5G", "5G-AKA", 7, 7, 0.8,
     "5G primary authentication; same eUICC / SM-SR key-epoch rule"),
]

NETWORKS = [
    ("NET-A", "Metro core (simulated)", "001-01", "LTE / NR-NSA", "Depot and highway corridor",
     "CELL-4471;CELL-6602", "AUTH-EPS"),
    ("NET-B", "Grid access network (simulated)", "001-01", "NR SA", "Substation footprint",
     "CELL-2210;CELL-8814", "AUTH-5G"),
    ("NET-LIVE", "Prototype live grid", "001-01", "Simulated", "Real-time telemetry generator",
     "CELL-LIVE-1;CELL-LIVE-2;CELL-LIVE-3;CELL-LIVE-4", "AUTH-EPS"),
]

RSP_ENVS = [
    ("RSP-SIM-1", "Bundled RSP model, primary", "smdp.sim.example", "smsr.sim.example",
     "ES2+;ES9+;ES10x", "Simulated (esim_selfhealing.rsp_api.RSPClient)",
     "Default environment; SM-DP+ reachable, key epochs in sync"),
    ("RSP-SIM-2", "Bundled RSP model, bootstrap bearer", "smdp-b.sim.example", "smsr-b.sim.example",
     "ES2+;ES9+;ES10x", "Simulated (esim_selfhealing.rsp_api.RSPClient)",
     "Secondary environment used when a bearer failover is planned"),
]

#: euicc_id, label, client, group, cell, network, rsp, fleet_size, origin
DEVICES = [
    ("89330000000048213", "Yard tracker Y-48213", "CL-001", "GRP-101", "CELL-4471", "NET-A", "RSP-SIM-1", 1, "scenario"),
    ("89330000000090031", "Shared-profile fleet (120 devices)", "CL-001", "GRP-101", "CELL-6602", "NET-A", "RSP-SIM-1", 120, "scenario"),
    ("89330000000100001", "Live grid device 1", "CL-001", "GRP-101", "CELL-LIVE-1", "NET-LIVE", "RSP-SIM-1", 1, "realtime-grid"),
    ("89330000000077145", "Reefer unit R-77145", "CL-001", "GRP-102", "CELL-4471", "NET-A", "RSP-SIM-1", 1, "scenario"),
    ("89330000000100002", "Live grid device 2", "CL-001", "GRP-102", "CELL-LIVE-2", "NET-LIVE", "RSP-SIM-1", 1, "realtime-grid"),
    ("89330000000051887", "Gateway G-51887", "CL-002", "GRP-201", "CELL-2210", "NET-B", "RSP-SIM-2", 1, "scenario"),
    ("89330000000063902", "Gateway G-63902", "CL-002", "GRP-201", "CELL-8814", "NET-B", "RSP-SIM-2", 1, "scenario"),
    ("89330000000100003", "Live grid device 3", "CL-002", "GRP-201", "CELL-LIVE-3", "NET-LIVE", "RSP-SIM-2", 1, "realtime-grid"),
    ("89330000000100004", "Live grid device 4", "CL-003", "GRP-301", "CELL-LIVE-4", "NET-LIVE", "RSP-SIM-1", 1, "realtime-grid"),
]

PROFILE_BY_CLIENT = {"CL-001": "Meridian-IoT-Global", "CL-002": "Harbourline-Metering", "CL-003": "Northgate-Care"}


def _eid(euicc_id: str) -> str:
    return f"89049032{int(euicc_id):024d}"          # 32 digits, synthetic


def _iccid(euicc_id: str) -> str:
    return f"8944{euicc_id[-15:]}"                  # 19 digits, synthetic


# ---------------------------------------------------------------------------
# Telemetry datasets: the same generators the Simulation screen runs.
# ---------------------------------------------------------------------------
def _fixed(stream: TelemetryStream, euicc_id: str, cell_id: str) -> TelemetryStream:
    stream.euicc_id, stream.cell_id, stream.t0 = euicc_id, cell_id, T0
    return stream


def _env(euicc_id: str) -> str:
    """The measured radio environment this device sits in.

    Imported from the simulation catalogue rather than restated, so a
    stored dataset and the live scenario for the same device cannot drift
    apart. Datasets stay byte-reproducible: the environment changes what
    the healthy baseline looks like, not whether the seed determines it.
    """
    from backend.services.scenarios import environment_for
    return environment_for(euicc_id)


def _telemetry_specs() -> List[Dict[str, Any]]:
    return [
        dict(id="isdp_corruption_48213", client="CL-001", group="GRP-101", euicc="89330000000048213",
             cell="CELL-4471", fault="isdp_corruption", seed=42, fleet=1,
             generator="esim_selfhealing.telemetry.scenario_isdp_corruption(seed=42, environment=rural_static)",
             title="ISD-P corruption on a yard tracker",
             description="Clean baseline with slow drift, then a profile checksum mismatch from sample 250.",
             make=lambda: _fixed(scenario_isdp_corruption(seed=42, environment=_env("89330000000048213")), "89330000000048213", "CELL-4471")),
        dict(id="key_desync_77145", client="CL-001", group="GRP-102", euicc="89330000000077145",
             cell="CELL-4471", fault="key_desync", seed=45, fleet=1,
             generator="esim_selfhealing.telemetry.TelemetryStream(key_desync 240-300, seed=45, environment=urban_mobile)",
             title="eUICC / SM-SR key desync on a reefer unit",
             description="AKA failures rise from sample 240 while OTA downloads and latency stay normal.",
             make=lambda: _fixed(TelemetryStream(n_samples=300, faults=[FaultWindow("key_desync", 240, 300)],
                                                 drift_per_sample=1.0, seed=45,
                                                 environment=_env("89330000000077145")),
                                 "89330000000077145", "CELL-4471")),
        dict(id="shared_fleet_90031", client="CL-001", group="GRP-101", euicc="89330000000090031",
             cell="CELL-6602", fault="isdp_corruption", seed=46, fleet=120,
             generator="esim_selfhealing.telemetry.TelemetryStream(isdp_corruption 240-300, seed=46, environment=rural_static)",
             title="ISD-P corruption on a shared profile (120 devices)",
             description="Same fault as the yard tracker, but the profile is shared by 120 devices.",
             make=lambda: _fixed(TelemetryStream(n_samples=300, faults=[FaultWindow("isdp_corruption", 240, 300)],
                                                 drift_per_sample=1.0, seed=46,
                                                 environment=_env("89330000000090031")),
                                 "89330000000090031", "CELL-6602")),
        dict(id="smdp_outage_51887", client="CL-002", group="GRP-201", euicc="89330000000051887",
             cell="CELL-2210", fault="smdp_session_outage", seed=44, fleet=1,
             generator="esim_selfhealing.telemetry.scenario_smdp_outage(seed=44, environment=rural_static)",
             title="SM-DP+ session outage on a metering gateway",
             description="OTA latency spikes and sessions time out from sample 240; AKA stays broadly healthy.",
             make=lambda: _fixed(scenario_smdp_outage(seed=44, environment=_env("89330000000051887")), "89330000000051887", "CELL-2210")),
        dict(id="slow_radio_drift_63902", client="CL-002", group="GRP-201", euicc="89330000000063902",
             cell="CELL-8814", fault="radio_degradation", seed=43, fleet=1,
             generator="esim_selfhealing.telemetry.scenario_slow_radio_drift(seed=43, environment=forest_obstructed)",
             title="Slow radio degradation on a metering gateway",
             description="RSRP collapses gradually from sample 200; a ramped fault with no OTA signature.",
             make=lambda: _fixed(scenario_slow_radio_drift(seed=43, environment=_env("89330000000063902")), "89330000000063902", "CELL-8814")),
        dict(id="healthy_baseline_100004", client="CL-003", group="GRP-301", euicc="89330000000100004",
             cell="CELL-LIVE-4", fault=None, seed=7, fleet=1,
             generator="esim_selfhealing.telemetry.TelemetryStream(no faults, seed=7, environment=rural_static)",
             title="Healthy baseline on a ward monitor",
             description="300 nominal samples. No fault; the detector should stay quiet.",
             make=lambda: _fixed(TelemetryStream(n_samples=300, seed=7,
                                                 environment=_env("89330000000100004")),
                                 "89330000000100004", "CELL-LIVE-4")),
    ]


TELEMETRY_COLUMNS = ["ts_epoch", "ts_iso", "euicc_id", "cell_id", *FEATURE_NAMES,
                     "sim_fault_label", "provenance"]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_csv(path: Path, header: List[str], rows: List[List[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def main() -> int:
    registry: Dict[str, Dict[str, str]] = {}

    def reg(key: str, rel: str, header: List[str], rows: List[List[Any]], description: str) -> None:
        path = DATA / rel
        _write_csv(path, header, [[*r, PROVENANCE] for r in rows])
        registry[key] = {"file": rel, "sha256": _sha256(path), "rows": len(rows), "description": description}

    reg("clients", "clients/clients.csv", ["client_id", "name", "sector", "provenance"],
        [list(c) for c in CLIENTS], "Fictional customers")
    reg("groups", "clients/groups.csv", ["group_id", "client_id", "name", "description", "provenance"],
        [list(g) for g in GROUPS], "Network groups per client")
    reg("devices", "devices/devices.csv",
        ["euicc_id", "device_label", "client_id", "group_id", "cell_id", "network_id", "rsp_env_id",
         "fleet_size", "origin", "eid", "iccid", "profile_name", "provenance"],
        [[e, lab, cl, gr, cell, net, rsp, fleet, origin, _eid(e), _iccid(e), PROFILE_BY_CLIENT[cl]]
         for e, lab, cl, gr, cell, net, rsp, fleet, origin in DEVICES],
        "Devices and eSIM identities. Includes the 4 devices of the real-time grid and the "
        "devices the built-in scenarios use")
    reg("network", "network/network_contexts.csv",
        ["network_id", "name", "plmn", "rat", "region", "cells", "auth_id", "provenance"],
        [list(n) for n in NETWORKS], "Network contexts: PLMN, radio access and cells")
    reg("authentication", "authentication/auth_contexts.csv",
        ["auth_id", "method", "key_epoch", "smsr_key_epoch", "nominal_aka_fail_per_100", "note", "provenance"],
        [list(a) for a in AUTH_CONTEXTS], "Authentication contexts referenced by network contexts")
    reg("rsp", "rsp/rsp_environments.csv",
        ["rsp_env_id", "name", "smdp_endpoint", "smsr_endpoint", "interfaces", "mode", "notes", "provenance"],
        [list(r) for r in RSP_ENVS], "RSP environments (SM-DP+ / SM-SR); the bundled simulated model")

    datasets = []
    for spec in _telemetry_specs():
        obs = spec["make"]().collect()
        rel = f"telemetry/{spec['id']}.csv"
        rows = []
        for o in obs:
            rows.append([f"{o.ts:.3f}", datetime.fromtimestamp(o.ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                         o.euicc_id, o.cell_id, *[f"{v:.6f}" for v in o.features],
                         o.ground_truth_fault or "", PROVENANCE])
        _write_csv(DATA / rel, TELEMETRY_COLUMNS, rows)
        datasets.append({
            "id": spec["id"], "kind": "telemetry", "file": rel, "title": spec["title"],
            "description": spec["description"], "provenance": PROVENANCE,
            "generator": spec["generator"], "seed": spec["seed"],
            "client_id": spec["client"], "group_id": spec["group"],
            "euicc_id": spec["euicc"], "cell_id": spec["cell"],
            "fault_class": spec["fault"], "fleet_size": spec["fleet"],
            "sha256": _sha256(DATA / rel),
        })

    manifest = {
        "schema_version": 1,
        "provenance": PROVENANCE,
        "generated_by": "tools/generate_datasets.py",
        "generated_from": "esim_selfhealing.telemetry (TelemetryStream, fixed seeds)",
        "t0_epoch": T0,
        "sample_period_s": 1.0,
        "features": list(FEATURE_NAMES),
        "registry": registry,
        "datasets": datasets,
    }
    (DATA / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(registry)} registry files and {len(datasets)} telemetry datasets to {DATA}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
