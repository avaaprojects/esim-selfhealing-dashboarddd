"""Non-invasive capture of the intermediate artefacts the UI needs.

The core package is deliberately left untouched. `Orchestrator.handle_incident`
returns a `LoopRecord`, which carries the incident, the diagnosis, the selected
action and the `ActResult` - but *not* the full ranked candidate list from
PLAN, the retrieved memory neighbours, or the signed `Command` that ACT built.

Those three are exactly what the decision-trace UI has to show, so this module
wraps the bound methods of a live Orchestrator's `planner` and `actuator` and
records them on the way past. Nothing in `esim_selfhealing/` is subclassed,
patched at class level, or edited: only the instance attributes of one
orchestrator object are rebound, and the wrappers delegate to the originals and
return their results unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from esim_selfhealing.memory_store import MemoryRecord
from esim_selfhealing.orchestrator import Orchestrator
from esim_selfhealing.plan import PlanResult
from esim_selfhealing.schemas import ActionType, Candidate, Command, Diagnosis


@dataclass
class StageCapture:
    """Everything observed for one incident that the LoopRecord drops."""

    incident_id: str
    plan: Optional[PlanResult] = None
    neighbours: List[Tuple[MemoryRecord, float]] = field(default_factory=list)
    fleet_size: int = 1
    command: Optional[Command] = None
    inverse: Optional[Command] = None
    #: eUICC state (as the RSP model holds it) immediately before and after ACT.
    #: Read at that instant on purpose: a stored recording keeps replaying the
    #: fault afterwards, so the end-of-run state says nothing about the action.
    device_before: Optional[Dict[str, Any]] = None
    device_after: Optional[Dict[str, Any]] = None


class CaptureBus:
    """Keyed store of StageCapture, one entry per incident."""

    def __init__(self) -> None:
        self._by_incident: Dict[str, StageCapture] = {}

    def for_incident(self, incident_id: str) -> StageCapture:
        if incident_id not in self._by_incident:
            self._by_incident[incident_id] = StageCapture(incident_id=incident_id)
        return self._by_incident[incident_id]

    def get(self, incident_id: str) -> Optional[StageCapture]:
        return self._by_incident.get(incident_id)

    def clear(self) -> None:
        self._by_incident.clear()


def instrument(orch: Orchestrator) -> CaptureBus:
    """Rebind planner.rank / actuator.build_command / actuator.execute.

    Ordering note: within `Orchestrator.handle_incident`, `planner.rank` runs
    before `actuator.execute`, and `actuator.build_command` runs *inside*
    `execute`. So the command is stashed on a scratch slot by `build_command`
    and then claimed by the `execute` wrapper, which is the first point where
    the incident id is in scope (it arrives on `diagnosis.incident_id`).
    """
    bus = CaptureBus()

    planner = orch.planner
    actuator = orch.actuator

    original_rank = planner.rank
    original_build = actuator.build_command
    original_execute = actuator.execute

    scratch: Dict[str, Optional[Command]] = {"last_command": None}

    def snapshot(euicc_id: str) -> Dict[str, Any]:
        d = orch.rsp.device(euicc_id)
        return {
            "healthy": bool(d.healthy()),
            "active_fault": orch.rsp.active_fault.get(euicc_id),
            "checksum_ok": bool(d.checksum_ok),
            "key_epoch": d.key_epoch,
            "smsr_key_epoch": d.smsr_key_epoch,
            "smdp_reachable": bool(d.smdp_reachable),
            "radio_alarm": bool(d.radio_alarm),
            "apn": d.apn,
            "active_isdp": d.active_isdp,
            "last_ota_results": list(d.last_ota_results),
        }

    def rank(diagnosis: Diagnosis,
             neighbours: Sequence[Tuple[MemoryRecord, float]],
             fleet_size: Optional[int] = None) -> PlanResult:
        result = original_rank(diagnosis, neighbours, fleet_size=fleet_size)
        cap = bus.for_incident(diagnosis.incident_id)
        cap.plan = result
        cap.neighbours = list(neighbours)
        cap.fleet_size = int(fleet_size or 1)
        return result

    def build_command(action: ActionType, euicc_id: str,
                      extra: Optional[Dict[str, Any]] = None) -> Command:
        cmd = original_build(action, euicc_id, extra=extra)
        scratch["last_command"] = cmd
        return cmd

    def execute(candidate: Candidate, diagnosis: Diagnosis, euicc_id: str):
        before = snapshot(euicc_id)
        result = original_execute(candidate, diagnosis, euicc_id)
        cap = bus.for_incident(diagnosis.incident_id)
        cap.device_before, cap.device_after = before, snapshot(euicc_id)
        cmd = scratch["last_command"]
        cap.command = cmd
        cap.inverse = cmd.inverse if cmd is not None else None
        scratch["last_command"] = None
        return result

    planner.rank = rank            # type: ignore[method-assign]
    actuator.build_command = build_command   # type: ignore[method-assign]
    actuator.execute = execute     # type: ignore[method-assign]

    return bus
