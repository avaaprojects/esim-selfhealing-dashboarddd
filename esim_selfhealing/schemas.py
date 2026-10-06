"""Domain types shared by every stage of the ReAct-MAPE-K loop.

Mirrors the notation of the deck:
    o_t  -> Observation
    I_t  -> Incident
    a    -> Action / Candidate
    Sigma-> SafetyEnvelope (see safety.py)
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

# Feature vector ordering for x_t = [AKAfail, RSRP, drop, lat, OTAfail]^T
FEATURE_NAMES: tuple[str, ...] = (
    "aka_fail_rate",   # AKA authentication failures per 100 attach attempts
    "rsrp_dbm",        # Reference Signal Received Power (negative dBm)
    "drop_rate",       # session drop rate, fraction 0..1
    "latency_ms",      # OTA round-trip latency
    "ota_fail_rate",   # SM-DP+ / SM-SR session failures, fraction 0..1
)
FEATURE_DIM = len(FEATURE_NAMES)


# ---- CLASS: ActionType ----
class ActionType(str, Enum):
    """Remediation action space A of the constrained POMDP (deck slide 5)."""

    NO_OP = "no_op"
    PROFILE_REPUSH = "profile_repush"          # ES9+/ES10x re-download + re-enable
    ISDP_SWITCH = "isdp_switch"                # enable/disable an installed profile
    BEARER_FAILOVER = "bearer_failover"        # move to backup bootstrap / APN
    KEY_ROTATION = "key_rotation"              # rotate eUICC / SM-DP+ session keys
    ISDP_DELETE_REPROVISION = "isdp_delete"    # irreversible
    ESCALATE = "escalate"                      # hand to NOC


#: Action ordering used by the RL policy in learn.py (index == logit index).
ACTION_SPACE: tuple[ActionType, ...] = (
    ActionType.NO_OP,
    ActionType.PROFILE_REPUSH,
    ActionType.ISDP_SWITCH,
    ActionType.BEARER_FAILOVER,
    ActionType.KEY_ROTATION,
    ActionType.ISDP_DELETE_REPROVISION,
)

#: Actions with no pre-computable inverse command a^-1.
IRREVERSIBLE_ACTIONS: frozenset[ActionType] = frozenset(
    {ActionType.ISDP_DELETE_REPROVISION}
)


# ---- CLASS: Status ----
class Status(str, Enum):
    """Terminal branch of one loop iteration (deck slides 4 and 16)."""

    AUTO_REMEDIATED = "AUTO-REMEDIATED"
    HUMAN_IN_LOOP = "HUMAN-IN-LOOP"
    ESCALATION = "ESCALATION"


# ---- CLASS: Observation ----
@dataclass(slots=True)
class Observation:
    """o_t - one telemetry sample for one eUICC on one cell."""

    ts: float
    euicc_id: str
    cell_id: str
    features: Sequence[float]           # x_t, ordered per FEATURE_NAMES
    ground_truth_fault: Optional[str] = None   # simulator only; never read by agent

    # ---- METHOD: Observation.as_dict ----
    def as_dict(self) -> Dict[str, float]:
        return dict(zip(FEATURE_NAMES, self.features))


# ---- CLASS: Incident ----
@dataclass(slots=True)
class Incident:
    """I_t - opened by MONITOR when g_t exceeds the chi-square threshold."""

    incident_id: str
    opened_at: float
    observation: Observation
    anomaly_score: float                # g_t
    threshold: float                    # chi2_{d,1-alpha}
    belief: Dict[str, float]            # b_t over latent fault classes
    resolved_at: Optional[float] = None
    #: How far the device sits outside its own normal operating range at
    #: detection, in standard deviations of the worst channel, measured
    #: against its SLOW baseline.
    #:
    #: This is the magnitude signal; `anomaly_score` is not one. A detector
    #: that fires on a threshold crossing reports, by construction, a score
    #: just above the threshold whatever the fault - measured across four
    #: fault classes the ratio spans 1.0 to 1.7, so it says how abruptly the
    #: fault arrived and nothing about how bad it is. Distance from the slow
    #: baseline does say: it is large for a step fault and large for a ramp
    #: the fast baseline has already absorbed, which are exactly the two
    #: cases that have to be comparable.
    deviation_sigma: float = 0.0

    # ---- METHOD: Incident.new ----
    @staticmethod
    def new(observation: Observation, score: float, threshold: float,
            belief: Dict[str, float], deviation_sigma: float = 0.0) -> "Incident":
        return Incident(
            incident_id=f"INC-{uuid.uuid4().hex[:8].upper()}",
            opened_at=observation.ts,
            observation=observation,
            anomaly_score=score,
            threshold=threshold,
            belief=belief,
            deviation_sigma=deviation_sigma,
        )

    # ---- METHOD: Incident.mttr_seconds ----
    def mttr_seconds(self) -> Optional[float]:
        if self.resolved_at is None:
            return None
        return self.resolved_at - self.opened_at


# ---- CLASS: ReActStep ----
@dataclass(slots=True)
class ReActStep:
    """One (Thought_k, Action_k, Observation_k) triple of the ReAct trace H_k."""

    k: int
    thought: str
    tool: str
    tool_args: Dict[str, Any]
    observation: str


# ---- CLASS: Diagnosis ----
@dataclass(slots=True)
class Diagnosis:
    """(d_t, A_cand) = Parse(H_k) - the REASON stage output."""

    incident_id: str
    text: str                            # d_t
    fault_class: str
    confidence: float
    candidate_actions: List[ActionType]  # A_cand
    trace: List[ReActStep] = field(default_factory=list)
    retrieved_ids: List[str] = field(default_factory=list)   # N(e_t)
    llm_calls: int = 0


# ---- CLASS: Candidate ----
@dataclass(slots=True)
class Candidate:
    """A scored element of A_cand after PLAN's multi-criteria ranking."""

    action: ActionType
    p_success: float
    cost: float
    blast_radius: int
    risk: float
    sla_cost: float
    sec_cost: float
    utility: float = 0.0
    feasible: bool = True
    violated: List[str] = field(default_factory=list)
    proposer: str = "orchestrator"

    # ---- METHOD: Candidate.summary ----
    def summary(self) -> str:
        flag = "" if self.feasible else f"  [infeasible: {','.join(self.violated)}]"
        return (
            f"{self.action.value:<22} U={self.utility:+.3f}  "
            f"P_succ={self.p_success:.2f} cost={self.cost:.2f} "
            f"blast={self.blast_radius} risk={self.risk:.2f}{flag}"
        )


# ---- CLASS: Command ----
@dataclass(slots=True)
class Command:
    """cmd_a - the concrete RSP call, plus its PQC signature and inverse."""

    command_id: str
    action: ActionType
    endpoint: str                        # e.g. "ES9+/ES10x:DownloadProfile"
    payload: Dict[str, Any]
    signature: Optional[bytes] = None    # sigma_a, ML-DSA
    inverse: Optional["Command"] = None  # a^-1
    issued_at: float = field(default_factory=time.time)


# ---- CLASS: AdmissibilityReport ----
@dataclass(slots=True)
class AdmissibilityReport:
    """Per-clause result of Admissible(a, Sigma) - deck slide 12."""

    signature_ok: bool
    blast_ok: bool
    rollback_ok: bool
    risk_ok: bool
    detail: Dict[str, str] = field(default_factory=dict)

    # ---- METHOD: AdmissibilityReport.admissible ----
    @property
    def admissible(self) -> bool:
        return self.signature_ok and self.blast_ok and self.rollback_ok and self.risk_ok

    # ---- METHOD: AdmissibilityReport.failed_clauses ----
    def failed_clauses(self) -> List[str]:
        pairs = (
            ("Verify(cmd,sigma)", self.signature_ok),
            ("BlastRadius<=B_max", self.blast_ok),
            ("exists a^-1 within tau_rollback", self.rollback_ok),
            ("Risk<=rho_max", self.risk_ok),
        )
        return [name for name, ok in pairs if not ok]


# ---- CLASS: ActResult ----
@dataclass(slots=True)
class ActResult:
    """Outcome of the ACT stage for one incident."""

    incident_id: str
    action: ActionType
    status: Status
    report: AdmissibilityReport
    dispatched: bool
    success: bool
    latency_ms: float          # total: gate + RSP round trip
    gate_latency_ms: float = 0.0   # signing + the four clause checks
    rolled_back: bool = False
    message: str = ""


# ---- CLASS: Transition ----
@dataclass(slots=True)
class Transition:
    """(b_t, a*, r_t, c_t, o_{t+1}) written to the replay buffer each iteration."""

    belief_vector: Sequence[float]
    action_index: int
    reward: float
    costs: Sequence[float]               # c_i(s_t, a_t), one per constraint
    next_belief_vector: Sequence[float]
    done: bool = True
    log_prob: float = 0.0
    value: float = 0.0
