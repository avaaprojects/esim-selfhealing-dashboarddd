"""Algorithm 1 - the Agentic ReAct-MAPE-K self-healing loop, wired end-to-end.

This module is the deck's slide-13 pseudocode with nothing added and nothing
removed: MONITOR -> REASON -> PLAN -> ACT -> LEARN, with the safety envelope
gating the ACT stage and the same constraint costs flowing into LEARN.

    from esim_selfhealing.orchestrator import build_default_orchestrator
    from esim_selfhealing.telemetry import scenario_isdp_corruption

    orch = build_default_orchestrator()
    report = orch.run(scenario_isdp_corruption())
    print(report.summary())
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np

from .act import Actuator
from .config import Config, DEFAULT
from .crypto_pqc import Signer, build_signer
from .learn import PPOLagrangian, ReplayBuffer
from .memory_store import IncidentMemory, MemoryRecord, seed_memory
from .monitor import FAULT_CLASSES, Monitor
from .plan import Planner
from .reason import ReActLLM, Reasoner, ToolRegistry
from .rsp_api import RSPClient
from .safety import SafetyEnvelope
from .schemas import (
    ACTION_SPACE,
    ActResult,
    ActionType,
    Diagnosis,
    Incident,
    Observation,
    Status,
    Transition,
)


# ---- CLASS: LoopRecord ----
@dataclass
class LoopRecord:
    """One full iteration of Algorithm 1, kept for audit and KPI computation."""

    incident: Incident
    diagnosis: Diagnosis
    selected_action: Optional[ActionType]
    act_result: Optional[ActResult]
    stage_latency_ms: Dict[str, float]
    status: Status
    reward: float
    costs: Tuple[float, ...]

    # ---- METHOD: LoopRecord.total_latency_ms ----
    def total_latency_ms(self) -> float:
        return sum(self.stage_latency_ms.values())


# ---- CLASS: RunReport ----
@dataclass
class RunReport:
    """KPIs of deck slide 16, computed over one run."""

    records: List[LoopRecord] = field(default_factory=list)
    samples_seen: int = 0
    false_positives: int = 0

    # -- KPIs --------------------------------------------------------------
    # ---- METHOD: RunReport.auto_remediation_rate ----
    def auto_remediation_rate(self) -> float:
        if not self.records:
            return 0.0
        n = sum(1 for r in self.records if r.status is Status.AUTO_REMEDIATED)
        return n / len(self.records)

    # ---- METHOD: RunReport.status_mix ----
    def status_mix(self) -> Dict[str, int]:
        out = {s.value: 0 for s in Status}
        for r in self.records:
            out[r.status.value] += 1
        return out

    # ---- METHOD: RunReport.mttr_s ----
    def mttr_s(self) -> float:
        if not self.records:
            return 0.0
        return float(np.mean([r.total_latency_ms() for r in self.records]) / 1000.0)

    # ---- METHOD: RunReport.constraint_violation_rate ----
    def constraint_violation_rate(self) -> float:
        dispatched = [r for r in self.records if r.act_result and r.act_result.dispatched]
        if not dispatched:
            return 0.0
        return sum(1 for r in dispatched if r.costs and r.costs[0] > 0) / len(dispatched)

    # ---- METHOD: RunReport.rollback_success_rate ----
    def rollback_success_rate(self) -> Optional[float]:
        attempts = [r for r in self.records
                    if r.act_result and r.act_result.dispatched and not r.act_result.success]
        if not attempts:
            return None
        return sum(1 for r in attempts if r.act_result.rolled_back) / len(attempts)

    # ---- METHOD: RunReport.stage_latency_profile ----
    def stage_latency_profile(self) -> Dict[str, float]:
        if not self.records:
            return {}
        keys = self.records[0].stage_latency_ms.keys()
        return {k: float(np.mean([r.stage_latency_ms[k] for r in self.records])) for k in keys}

    # ---- METHOD: RunReport.summary ----
    def summary(self) -> str:
        mix = self.status_mix()
        lat = self.stage_latency_profile()
        rb = self.rollback_success_rate()
        lines = [
            f"samples processed        : {self.samples_seen}",
            f"incidents opened         : {len(self.records)}",
            f"  AUTO-REMEDIATED        : {mix['AUTO-REMEDIATED']}",
            f"  HUMAN-IN-LOOP          : {mix['HUMAN-IN-LOOP']}",
            f"  ESCALATION             : {mix['ESCALATION']}",
            f"auto-remediation rate    : {self.auto_remediation_rate():.2%}",
            f"constraint violation rate: {self.constraint_violation_rate():.2%}  (target ~0)",
            f"rollback success rate    : {'n/a' if rb is None else f'{rb:.2%}'}",
            f"mean loop latency        : {self.mttr_s():.2f} s",
        ]
        if lat:
            lines.append("mean stage latency (ms)  : " +
                         "  ".join(f"{k}={v:.1f}" for k, v in lat.items()))
        return "\n".join(lines)


# ---- CLASS: Orchestrator ----
class Orchestrator:
    """Wires the five stages together and runs the loop over a telemetry stream."""

    # ---- METHOD: Orchestrator.__init__ ----
    def __init__(
        self,
        monitor: Monitor,
        reasoner: Reasoner,
        planner: Planner,
        actuator: Actuator,
        memory: IncidentMemory,
        learner: Optional[PPOLagrangian] = None,
        cfg: Optional[Config] = None,
        update_every: int = 8,
        verbose: bool = False,
    ) -> None:
        self.monitor = monitor
        self.reasoner = reasoner
        self.planner = planner
        self.actuator = actuator
        self.memory = memory
        self.learner = learner
        self.cfg = cfg or DEFAULT
        self.update_every = update_every
        self.verbose = verbose
        self.rsp: RSPClient = actuator.rsp
        self._pending = 0

    # -- one iteration of Algorithm 1 --------------------------------------
    # ---- METHOD: Orchestrator.handle_incident ----
    def handle_incident(self, incident: Incident) -> LoopRecord:
        obs = incident.observation
        lat: Dict[str, float] = {}

        # --- REASON -------------------------------------------------------
        t = time.perf_counter()
        top = self.monitor.top_features(obs)
        diagnosis = self.reasoner.diagnose(incident, top_features=top)
        lat["reason"] = (time.perf_counter() - t) * 1000.0

        # --- PLAN ---------------------------------------------------------
        t = time.perf_counter()
        neighbours = self.memory.retrieve(
            obs, top_n=self.cfg.reason.top_n,
            min_similarity=self.cfg.reason.min_similarity,
            hint=diagnosis.fault_class,
        )
        plan = self.planner.rank(diagnosis, neighbours,
                                 fleet_size=self.rsp.fleet_size(obs.euicc_id))
        lat["plan"] = (time.perf_counter() - t) * 1000.0

        # --- ACT ----------------------------------------------------------
        t = time.perf_counter()
        if plan.selected is None:
            # No feasible candidate at all -> escalate with the full trace.
            self.actuator.escalations.append({
                "incident_id": incident.incident_id,
                "reason": "empty feasible set after constraint filtering",
                "diagnosis": diagnosis.text,
            })
            status, act_result, costs, reward = Status.ESCALATION, None, (0.0, 0.0), -0.3
            selected_action = None
        else:
            act_result = self.actuator.execute(plan.selected, diagnosis, obs.euicc_id)
            status = act_result.status
            selected_action = plan.selected.action
            costs = self.actuator.envelope.constraint_costs(plan.selected)
            reward = self._reward(act_result, plan.selected)
        # Split the ACT stage: the gate is what the 50 ms budget governs; the
        # RSP round trip is physics and is reported separately.
        lat["act_gate"] = act_result.gate_latency_ms if act_result else \
            (time.perf_counter() - t) * 1000.0
        lat["act_dispatch"] = (act_result.latency_ms - act_result.gate_latency_ms) \
            if act_result else 0.0
        lat["monitor"] = 0.0  # filled by the caller from the MonitorReading

        # --- LEARN --------------------------------------------------------
        t = time.perf_counter()
        incident.resolved_at = obs.ts + sum(lat.values()) / 1000.0
        self._write_back(incident, diagnosis, selected_action, status, act_result, costs, reward)
        lat["learn"] = (time.perf_counter() - t) * 1000.0

        if self.verbose:
            print(f"  {incident.incident_id}  {diagnosis.fault_class:<20} "
                  f"-> {selected_action.value if selected_action else '(none)':<22} {status.value}")

        return LoopRecord(
            incident=incident,
            diagnosis=diagnosis,
            selected_action=selected_action,
            act_result=act_result,
            stage_latency_ms=lat,
            status=status,
            reward=reward,
            costs=costs,
        )

    # -- reward shaping ----------------------------------------------------
    # ---- METHOD: Orchestrator._reward ----
    @staticmethod
    def _reward(result: ActResult, candidate) -> float:
        r = 1.0 if result.success else (-0.3 if result.dispatched else -0.1)
        r -= 0.30 * candidate.cost
        r -= 0.004 * candidate.sla_cost
        r -= 0.0001 * result.latency_ms
        return float(r)

    # -- write-back to D, the replay buffer, and the policy ----------------
    # ---- METHOD: Orchestrator._write_back ----
    def _write_back(self, incident: Incident, diagnosis: Diagnosis,
                    action: Optional[ActionType], status: Status,
                    result: Optional[ActResult], costs: Tuple[float, ...],
                    reward: float) -> None:
        # D <- D u {(I_t, a*, status)}
        self.memory.add(MemoryRecord(
            record_id=incident.incident_id,
            features=list(incident.observation.features),
            fault_class=diagnosis.fault_class,
            diagnosis=diagnosis.text,
            action=action.value if action else ActionType.ESCALATE.value,
            status=status.value,
            success=bool(result and result.success),
            resolution_s=float(incident.mttr_seconds() or 0.0),
            tags=[diagnosis.fault_class, status.value],
        ))

        if self.learner is None or action is None or action not in ACTION_SPACE:
            return

        b = np.array([incident.belief[c] for c in FAULT_CLASSES] + [1.0])
        a_idx = ACTION_SPACE.index(action)
        logp = float(np.log(max(self.learner.policy.probs(b)[a_idx], 1e-12)))
        self.learner.buffer.add(Transition(
            belief_vector=b, action_index=a_idx, reward=reward, costs=costs,
            next_belief_vector=b, done=True, log_prob=logp,
            value=float(self.learner.policy.value(b)),
        ))
        self._pending += 1

        # Batched, asynchronous by design - never on the ACT critical path.
        if self._pending >= self.update_every:
            self._pending = 0
            stats = self.learner.update()
            self.planner.set_weights(*self.learner.plan_weights(
                (self.cfg.plan.w1, self.cfg.plan.w2, self.cfg.plan.w3)
            ))
            if self.verbose:
                print(f"  [learn] {stats.line()}")

    # -- the outer loop ----------------------------------------------------
    # ---- METHOD: Orchestrator.run ----
    def run(self, stream: Iterable[Observation], sync_fault_to_rsp: bool = True) -> RunReport:
        report = RunReport()
        for obs in stream:
            report.samples_seen += 1
            reading = self.monitor.update(obs)

            # Keep the simulated RSP consistent with the injected fault so the
            # REASON stage's tool calls see the same reality the telemetry shows.
            if sync_fault_to_rsp:
                self.rsp.inject_fault(obs.euicc_id, obs.ground_truth_fault) \
                    if self.rsp.active_fault.get(obs.euicc_id) != obs.ground_truth_fault else None

            if not reading.triggered:
                continue
            if obs.ground_truth_fault is None:
                report.false_positives += 1

            rec = self.handle_incident(reading.incident)
            rec.stage_latency_ms["monitor"] = reading.latency_ms
            report.records.append(rec)

            # Close the loop back onto the telemetry. The RSP clears
            # `active_fault` only when the dispatched command actually
            # succeeded, so that is the one truth both the simulated estate
            # and the samples should follow - otherwise a run that remediates
            # correctly keeps emitting the fault signature to the end and the
            # dashboard shows a successful remediation that changed nothing.
            # Duck-typed: a plain list of Observations is still a valid stream.
            mark = getattr(stream, "mark_remediated", None)
            if (callable(mark)
                    and rec.act_result is not None
                    and rec.act_result.success
                    and not rec.act_result.rolled_back
                    and self.rsp.active_fault.get(obs.euicc_id) is None):
                mark()
        return report


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
# ---- FUNCTION: build_default_orchestrator ----
def build_default_orchestrator(
    cfg: Optional[Config] = None,
    llm: Optional[ReActLLM] = None,
    signer: Optional[Signer] = None,
    memory: Optional[IncidentMemory] = None,
    enable_learning: bool = True,
    update_every: int = 8,
    verbose: bool = False,
    seed: int = 5,
) -> Orchestrator:
    """Assemble a fully wired loop with the offline backends."""
    cfg = cfg or DEFAULT
    signer = signer or build_signer(prefer_pq=True)
    mem = memory if memory is not None else seed_memory(dim=cfg.reason.embed_dim)

    rsp = RSPClient(verifier=signer, seed=seed)
    tools = ToolRegistry(rsp, mem, cfg.reason)
    reasoner = Reasoner(tools, mem, llm=llm, cfg=cfg.reason)
    planner = Planner(mem, cfg.plan)
    envelope = SafetyEnvelope(verifier=signer, cfg=cfg.envelope)
    actuator = Actuator(rsp, envelope, signer)
    monitor = Monitor(cfg.monitor)
    learner = PPOLagrangian(state_dim=len(FAULT_CLASSES) + 1, cfg=cfg.learn) \
        if enable_learning else None

    return Orchestrator(monitor, reasoner, planner, actuator, mem,
                        learner=learner, cfg=cfg, update_every=update_every,
                        verbose=verbose)
