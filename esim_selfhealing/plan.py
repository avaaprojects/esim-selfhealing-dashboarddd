"""Stage 3 - PLAN: constrained utility ranking over A_cand.

    P_success(a) ~= (1/|N(e_t)|) sum_{i in N(e_t)} 1[outcome_i(a) = success]
    U(a)         = w1 P_success(a) - w2 Cost(a) - w3 BlastRadius(a)
    a*           = argmax_{a in A_cand} U(a)
                   s.t. c_SLA(a) <= d_SLA, c_sec(a) <= d_sec, Risk(a) <= rho_max

Three specialist proposers contribute candidates (network / security / SLA-ops)
and the orchestrator ranks the union. This is a weighted multi-criteria
ranking, not a game-theoretic bargaining protocol - the deliberate
simplification of slide 9, chosen so every selection is reproducible and
auditable.

Constraint handling is *hard filtering, then ranking*: an infeasible candidate
is never selected on utility grounds, no matter how attractive its utility is.
PLAN filters on the same B_max the envelope enforces at dispatch - the deck's
"Plan optimizes with respect to Sigma, Act enforces it exactly". Duplicating the
bound is deliberate: PLAN can then hand ACT an empty feasible set and let the
orchestrator escalate, instead of proposing something the gate will refuse.

Tie-break is minimum blast radius - the conservative prior LEARN may later
revise through w1, w2, w3.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .config import ACTION_PROFILE, DEFAULT, PlanConfig
from .memory_store import IncidentMemory, MemoryRecord
from .schemas import ActionType, Candidate, Diagnosis

#: Normaliser so BlastRadius enters U(a) on the same scale as the other terms.
BLAST_NORMALISER = 4.0


# ---------------------------------------------------------------------------
# Specialist proposers
# ---------------------------------------------------------------------------
# ---- CLASS: SpecialistAgent ----
class SpecialistAgent:
    """Base class: proposes a subset of A_cand and may annotate risk."""

    name = "agent"
    owns: Tuple[ActionType, ...] = ()

    # ---- METHOD: SpecialistAgent.propose ----
    def propose(self, diagnosis: Diagnosis) -> List[ActionType]:
        return [a for a in diagnosis.candidate_actions if a in self.owns]


# ---- CLASS: NetworkAgent ----
class NetworkAgent(SpecialistAgent):
    """Connectivity-layer fixes."""

    name = "network"
    owns = (ActionType.BEARER_FAILOVER, ActionType.ISDP_SWITCH, ActionType.NO_OP)


# ---- CLASS: SecurityAgent ----
class SecurityAgent(SpecialistAgent):
    """Integrity-layer fixes; flags irreversible actions."""

    name = "security"
    owns = (
        ActionType.PROFILE_REPUSH,
        ActionType.KEY_ROTATION,
        ActionType.ISDP_DELETE_REPROVISION,
    )

    # ---- METHOD: SecurityAgent.risk_multiplier ----
    def risk_multiplier(self, action: ActionType) -> float:
        return 1.35 if action == ActionType.ISDP_DELETE_REPROVISION else 1.0


# ---- CLASS: SLAOpsAgent ----
class SLAOpsAgent(SpecialistAgent):
    """Estimates cost and blast radius from live inventory."""

    name = "sla_ops"
    owns = ()

    # ---- METHOD: SLAOpsAgent.__init__ ----
    def __init__(self, fleet_size: int = 1) -> None:
        self.fleet_size = fleet_size

    # ---- METHOD: SLAOpsAgent.blast_radius ----
    def blast_radius(self, action: ActionType, fleet_size: Optional[int] = None) -> int:
        """Devices an action would touch, read from live inventory.

        A profile shared across a fleet multiplies the blast radius of any
        profile-layer action - which is why this is looked up per incident
        rather than fixed at construction.
        """
        n = self.fleet_size if fleet_size is None else fleet_size
        base = int(ACTION_PROFILE[action]["blast"])
        return base if n <= 1 else base * n


# ---- CLASS: PlanResult ----
@dataclass
class PlanResult:
    """Everything PLAN produces, including the rejected candidates."""

    ranked: List[Candidate]
    selected: Optional[Candidate]
    infeasible: List[Candidate]
    weights: Tuple[float, float, float]
    latency_ms: float = 0.0

    # ---- METHOD: PlanResult.table ----
    def table(self) -> str:
        rows = [f"    {c.summary()}" for c in self.ranked]
        rows += [f"    {c.summary()}" for c in self.infeasible]
        return "\n".join(rows)


# ---- CLASS: Planner ----
class Planner:
    """Ranks A_cand under the SLA / security / risk constraint set."""

    # ---- METHOD: Planner.__init__ ----
    def __init__(self, memory: IncidentMemory, cfg: Optional[PlanConfig] = None,
                 fleet_size: int = 1) -> None:
        self.memory = memory
        self.cfg = cfg or DEFAULT.plan
        self.network = NetworkAgent()
        self.security = SecurityAgent()
        self.sla_ops = SLAOpsAgent(fleet_size=fleet_size)
        self.weights = (self.cfg.w1, self.cfg.w2, self.cfg.w3)

    # -- weights are the learnable part (updated by LEARN) -----------------
    # ---- METHOD: Planner.set_weights ----
    def set_weights(self, w1: float, w2: float, w3: float) -> None:
        self.weights = (w1, w2, w3)

    # -- P_success from retrieved precedent --------------------------------
    # ---- METHOD: Planner.estimate_success ----
    def estimate_success(self, action: ActionType,
                         neighbours: Sequence[Tuple[MemoryRecord, float]]) -> float:
        prior = self.cfg.prior_success.get(action, 0.5)
        return self.memory.success_rate(
            action, neighbours, laplace=self.cfg.laplace_prior, prior=prior
        )

    # -- main entry point --------------------------------------------------
    # ---- METHOD: Planner.rank ----
    def rank(self, diagnosis: Diagnosis,
             neighbours: Sequence[Tuple[MemoryRecord, float]],
             fleet_size: Optional[int] = None) -> PlanResult:
        import time as _time
        t0 = _time.perf_counter()

        # 1. Specialist candidate generation (union, order-preserving).
        proposals: Dict[ActionType, str] = {}
        for agent in (self.network, self.security):
            for a in agent.propose(diagnosis):
                proposals.setdefault(a, agent.name)
        for a in diagnosis.candidate_actions:
            proposals.setdefault(a, "orchestrator")

        w1, w2, w3 = self.weights
        feasible: List[Candidate] = []
        infeasible: List[Candidate] = []

        for action, proposer in proposals.items():
            prof = ACTION_PROFILE[action]
            blast = self.sla_ops.blast_radius(action, fleet_size)
            risk = float(prof["risk"]) * self.security.risk_multiplier(action) \
                if action in self.security.owns else float(prof["risk"])
            # Uncertainty in the diagnosis inflates residual risk.
            risk = min(1.0, risk * (1.0 + 0.5 * (1.0 - diagnosis.confidence)))

            cand = Candidate(
                action=action,
                p_success=self.estimate_success(action, neighbours),
                cost=float(prof["cost"]),
                blast_radius=blast,
                risk=risk,
                sla_cost=float(prof["sla"]),
                sec_cost=float(prof["sec"]),
                proposer=proposer,
            )
            cand.utility = (
                w1 * cand.p_success
                - w2 * cand.cost
                - w3 * (cand.blast_radius / BLAST_NORMALISER)
            )

            violated: List[str] = []
            if cand.blast_radius > self.cfg.b_max:
                violated.append(
                    f"BlastRadius={cand.blast_radius}>B_max={self.cfg.b_max}")
            if cand.sla_cost > self.cfg.d_sla:
                violated.append(f"c_SLA={cand.sla_cost:.0f}>d_SLA={self.cfg.d_sla:.0f}")
            if cand.sec_cost > self.cfg.d_sec:
                violated.append(f"c_sec={cand.sec_cost:.2f}>d_sec={self.cfg.d_sec:.2f}")
            if cand.risk > self.cfg.rho_max:
                violated.append(f"Risk={cand.risk:.2f}>rho_max={self.cfg.rho_max:.2f}")

            cand.violated = violated
            cand.feasible = not violated
            (feasible if cand.feasible else infeasible).append(cand)

        # 2. Rank feasible set: utility desc, then blast radius asc (tie-break).
        feasible.sort(key=lambda c: (-c.utility, c.blast_radius, c.cost))
        infeasible.sort(key=lambda c: -c.utility)

        return PlanResult(
            ranked=feasible,
            selected=feasible[0] if feasible else None,
            infeasible=infeasible,
            weights=(w1, w2, w3),
            latency_ms=(_time.perf_counter() - t0) * 1000.0,
        )
