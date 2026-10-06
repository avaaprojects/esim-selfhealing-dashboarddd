"""The simulation catalogue exposed at /api/simulation.

Every entry is a real `TelemetryStream` from `esim_selfhealing.telemetry`, or a
small composition of them, so a scenario card in the UI runs the same code path
the console use cases in `usecases/` run. Nothing here is mocked.

`shared_fleet` and `night_shift` need inventory set up on the RSP client before
the stream is consumed (a profile shared across N devices multiplies PLAN's
blast radius), which is what `fleet` expresses.
"""

from __future__ import annotations

import itertools
import os
import time

from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from esim_selfhealing.schemas import Observation
from esim_selfhealing.telemetry import (
    FaultWindow,
    TelemetryStream,
    scenario_isdp_corruption,
    scenario_slow_radio_drift,
    scenario_smdp_outage,
)


#: Ordered simulation categories shown on the Simulation screen. Each entry is
#: what that group of scenarios is testing, not decoration - the category a
#: scenario is assigned to below determines which pipeline stage it stresses.
CATEGORY_ORDER: List[Dict[str, str]] = [
    {"key": "detection", "label": "Detection & Monitoring",
     "blurb": "Does MONITOR's EWMA/chi-square detector separate real faults from drift?"},
    {"key": "diagnosis", "label": "Diagnosis & Reasoning",
     "blurb": "Does REASON identify the true failure source instead of reacting to the alert text?"},
    {"key": "planning_safety", "label": "Planning & Safety / Gated Output",
     "blurb": "Does PLAN pick a sound remediation, and does the SAFETY envelope block what it should?"},
    {"key": "end_to_end", "label": "End-to-End / Whole Estate",
     "blurb": "Does the full loop hold up across many devices and failure modes at once?"},
]


@dataclass(frozen=True)
class Scenario:
    key: str
    name: str
    description: str
    expectation: str
    fault_class: str
    streams: Callable[[], List[TelemetryStream]]
    #: euicc_id -> number of devices sharing the profile, applied before the run.
    fleet: Dict[str, int] = field(default_factory=dict)
    use_case: str = ""
    #: key into CATEGORY_ORDER - which capability this scenario is testing.
    category: str = "detection"


#: eUICC each single-device scenario factory reports under. The factories in
#: esim_selfhealing.telemetry leave `euicc_id` at its default, so this is that
#: default - named here because the environment lookup below needs it.
_FACTORY_DEVICE = "89330000000048213"


def _single(
    factory: Callable[..., TelemetryStream],
    base_seed: int,
    euicc_id: str = _FACTORY_DEVICE,
) -> Callable[[], List[TelemetryStream]]:
    """Wrap a stream factory, not a stream instance.

    `TelemetryStream` owns a stateful `np.random.Generator`, so iterating the
    *same* instance twice yields different samples. Every scenario therefore
    builds a fresh stream per run.

    The seed comes from `_run_seed`, so successive runs of one scenario draw
    different telemetry rather than repeating a single fixed stream, and the
    environment comes from the device, so "normal" means what it measurably
    means where that device sits.
    """
    return lambda: [factory(
        seed=_run_seed(base_seed),
        environment=environment_for(euicc_id),
    )]


#: Which measured radio environment each demo device sits in. Environment is a
#: property of where a device is, and it changes what "normal" means: a yard
#: tracker under tree cover genuinely sits 10-15 dB lower than a rooftop
#: gateway, and calling that unhealthy would be wrong. The profiles and their
#: measurements are in esim_selfhealing/real_profiles.py.
DEVICE_ENVIRONMENT: Dict[str, str] = {
    "89330000000048213": "rural_static",        # depot yard tracker, fixed
    "89330000000051887": "rural_static",        # substation gateway, fixed
    "89330000000063902": "forest_obstructed",   # gateway under tree cover
    "89330000000077145": "urban_mobile",        # reefer unit, roaming - a vehicle
    "89330000000090031": "rural_static",        # shared-profile fleet, fixed
    "89330000000100004": "rural_static",        # ward monitor, fixed indoor
}
DEFAULT_ENVIRONMENT = "rural_static"


def environment_for(euicc_id: str) -> str:
    """The measured environment this device sits in."""
    return DEVICE_ENVIRONMENT.get(euicc_id, DEFAULT_ENVIRONMENT)


#: Counts runs in this process so each one draws its own telemetry.
_run_counter = itertools.count()


def _run_seed(base: int) -> int:
    """A seed that differs every run.

    A fixed seed made every run of a scenario produce byte-identical
    telemetry, so the dashboard reported the same anomaly score every time -
    which is what made the health figure look frozen. The scenario's own base
    seed is kept as an offset so runs stay distinguishable in a log, but each
    run draws a fresh stream: a different level from the measured between-run
    spread, and different samples within it. `SCENARIO_SEED=fixed` in the
    environment restores the old reproducible behaviour, which is what the
    test suite uses.
    """
    if os.environ.get("SCENARIO_SEED", "").strip().lower() == "fixed":
        return base
    return base * 1000 + next(_run_counter) * 7919 + (int(time.time() * 1000) % 7919)


def _key_desync_stream() -> List[TelemetryStream]:
    return [TelemetryStream(
        euicc_id="89330000000077145",
        cell_id="CELL-4471",
        n_samples=300,
        faults=[FaultWindow("key_desync", start=240, end=300)],
        drift_per_sample=1.0,
        seed=_run_seed(45),
        environment=environment_for("89330000000077145"),
    )]


def _shared_fleet_stream() -> List[TelemetryStream]:
    return [TelemetryStream(
        euicc_id="89330000000090031",
        cell_id="CELL-6602",
        n_samples=300,
        faults=[FaultWindow("isdp_corruption", start=240, end=300)],
        drift_per_sample=1.0,
        seed=_run_seed(46),
        environment=environment_for("89330000000090031"),
    )]


def _night_shift_streams() -> List[TelemetryStream]:
    """Five devices, five failure modes - the uc6 estate."""
    plan: List[Tuple[str, str, str, int]] = [
        ("89330000000048213", "CELL-4471", "isdp_corruption", 42),
        ("89330000000051887", "CELL-2210", "smdp_session_outage", 44),
        ("89330000000063902", "CELL-8814", "radio_degradation", 43),
        ("89330000000077145", "CELL-4471", "key_desync", 45),
        ("89330000000090031", "CELL-6602", "isdp_corruption", 46),
    ]
    return [
        TelemetryStream(
            euicc_id=euicc, cell_id=cell, n_samples=300,
            faults=[FaultWindow(fault, start=240, end=300)],
            drift_per_sample=1.0, seed=_run_seed(seed),
            environment=environment_for(euicc),
        )
        for euicc, cell, fault, seed in plan
    ]


CATALOGUE: Dict[str, Scenario] = {
    s.key: s for s in [
        Scenario(
            key="isdp_corruption",
            name="ISD-P corruption",
            description=(
                "A clean overnight baseline with slow firmware drift, then a sudden "
                "profile checksum mismatch at sample 250. The detector has to absorb "
                "the drift and still catch the real fault."
            ),
            expectation="Expect PROFILE_REPUSH, admissible, AUTO-REMEDIATED.",
            fault_class="isdp_corruption",
            streams=_single(scenario_isdp_corruption, 42),
            use_case="uc1 / uc2",
            category="detection",
        ),
        Scenario(
            key="smdp_outage",
            name="SM-DP+ session outage",
            description=(
                "OTA latency spikes and SM-DP+ sessions start timing out while AKA "
                "stays broadly healthy. Looks like a profile fault in the alert text; "
                "isn't one."
            ),
            expectation="Expect BEARER_FAILOVER rather than a re-push.",
            fault_class="smdp_session_outage",
            streams=_single(scenario_smdp_outage, 44),
            use_case="uc2",
            category="diagnosis",
        ),
        Scenario(
            key="slow_radio_drift",
            name="Slow radio degradation",
            description=(
                "RSRP collapses gradually over 120 samples with rising session drops "
                "and no OTA signature - a ramped fault rather than a step change."
            ),
            expectation="Expect a radio diagnosis, not a profile-layer action.",
            fault_class="radio_degradation",
            streams=_single(scenario_slow_radio_drift, 43),
            use_case="uc1",
            category="detection",
        ),
        Scenario(
            key="key_desync",
            name="eUICC / SM-SR key desync",
            description=(
                "AKA authentication fails hard while OTA downloads still complete and "
                "latency is untouched. The profile is intact; the session keys are not."
            ),
            expectation="Expect KEY_ROTATION to be considered against its risk bound.",
            fault_class="key_desync",
            streams=_key_desync_stream,
            use_case="uc2",
            category="diagnosis",
        ),
        Scenario(
            key="shared_fleet",
            name="Shared-profile fleet",
            description=(
                "The same ISD-P corruption, but on a profile shared by 120 logistics "
                "devices. The diagnosis is identical; the blast radius is not."
            ),
            expectation="Expect the envelope to refuse autonomous dispatch on B_max.",
            fault_class="isdp_corruption",
            streams=_shared_fleet_stream,
            fleet={"89330000000090031": 120},
            use_case="uc3 / uc4",
            category="planning_safety",
        ),
        Scenario(
            key="night_shift",
            name="Unattended night shift",
            description=(
                "Five devices, five failure modes, one shift, nobody watching the NOC "
                "console. Algorithm 1 end to end across the whole estate."
            ),
            expectation="Expect a mix of auto-remediated, queued and escalated outcomes.",
            fault_class="mixed",
            streams=_night_shift_streams,
            fleet={"89330000000090031": 120},
            use_case="uc6",
            category="end_to_end",
        ),
    ]
}

DEFAULT_SCENARIO = "isdp_corruption"


def get(key: str) -> Optional[Scenario]:
    return CATALOGUE.get(key)


_CATEGORY_LABELS: Dict[str, str] = {c["key"]: c["label"] for c in CATEGORY_ORDER}


def category_label(key: str) -> str:
    return _CATEGORY_LABELS.get(key, key)


def observations(scenario: Scenario) -> Iterable[Observation]:
    for stream in scenario.streams():
        yield from stream
