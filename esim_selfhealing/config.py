"""Every tunable constant of the loop in one place.

Values are engineering defaults chosen to make the reference implementation
runnable and self-consistent. They are NOT measured field results - a pilot
deployment is expected to recalibrate alpha, B_max, rho_max and w1..w3 against
its own incident history.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple

from .schemas import ActionType


# ---- CLASS: MonitorConfig ----
@dataclass(frozen=True)
class MonitorConfig:
    """Stage 1 - EWMA baseline + Mahalanobis change-point detection."""

    lam: float = 0.05                # lambda, EWMA forgetting factor
    alpha: float = 0.01              # false-alarm budget -> chi2_{d,1-alpha}
    warmup_samples: int = 60         # samples before the detector may fire
    ridge: float = 1e-6              # covariance regularisation
    #: Per-channel variance floor, in the channel's own units squared, applied
    #: to the diagonal of Sigma. Real telemetry is quantised - a modem reports
    #: whole dBm and holds the same value for most samples - so during a hold
    #: run the EWMA estimate of that channel's variance collapses toward zero
    #: and the next 1-2 dB step scores as a 6-sigma event. Flooring the
    #: variance at the channel's measurement resolution stops that without
    #: touching channels that genuinely are quiet. None keeps the original
    #: behaviour; `esim_selfhealing.fields.variance_floors()` supplies the
    #: measured resolutions.
    variance_floor: tuple[float, ...] | None = None
    refractory_s: float = 30.0       # min gap between incidents on one eUICC
    min_consecutive: int = 2         # m-of-m persistence: exceedances required to fire
    #: Two-sided CUSUM cross-check - the route by which a slow ramp opens an
    #: incident at all. The EWMA baseline tracks gradual change by design, so
    #: a fault arriving over minutes never scores a large Mahalanobis
    #: distance; what it leaves is a residual with a persistent sign, which
    #: this accumulates. `cusum_h = 0` disables the cross-check and restores
    #: chi-square-only triggering. Values calibrated in
    #: tools/calibrate_detector.py against the measured profiles: the pair has
    #: to catch the ramped radio fault while absorbing the benign firmware
    #: drift the demo scenarios deliberately carry.
    cusum_k: float = 0.5             # slack, in prior standard deviations
    cusum_h: float = 0.0             # 0 = reported only, see drift_h below
    cusum_clip: float = 3.0          # per-sample winsorisation of z
    #: Two-timescale drift test - how a ramped fault opens an incident.
    #:
    #: The chi-square test cannot see a slow ramp, and this is structural
    #: rather than a tuning failure: the EWMA baseline has a time constant of
    #: 1/lam = 20 samples, so anything arriving more slowly than that is
    #: tracked by mu and leaves no residual to score. Measured on the
    #: calibrated profiles, detection of a ramped radio fault falls from
    #: 16/16 at a 10-sample onset to 0/16 by 30 samples.
    #:
    #: A per-sample CUSUM is the textbook answer and does not work here: the
    #: detector residual is autocorrelated at lag-1 ~0.9 (the channels are
    #: AR(1)), which violates the independent-increments assumption, and the
    #: slack needed to stop it firing on correlated noise also made it blind
    #: to the ramp - 9 false alarms for 0/10 ramp detections at the quietest
    #: setting swept.
    #:
    #: What does work is a second, slower baseline. The fast EWMA tracks the
    #: ramp, the slow one does not, so the gap between them IS the drift, and
    #: both sides are already smoothed. Measured separation on the profiles:
    #: healthy peaks at 6.7 floored standard deviations, the ramped fault
    #: bottoms out at 21.7 - a factor of three, where the per-sample residual
    #: gave none. The gap is reset when an incident fires, otherwise the slow
    #: baseline would still be catching up for ~100 samples afterwards and
    #: would re-fire on the device's own recovery.
    drift_lam_slow: float = 0.01     # slow baseline forgetting factor
    drift_h: float = 12.0            # gap, in floored standard deviations
    #: Per-device variance floor, learned from the device's own history as a
    #: quantile of its deviation from its slow baseline. This is what keeps
    #: the detector fair across environments: `variance_floor` above is
    #: measured on a fixed depot tracker, and judging a roaming vehicle or a
    #: gateway on broken terrain against that device's variability flags
    #: their ordinary behaviour as a fault. Refreshed every
    #: `floor_refresh` samples rather than every sample, since a quantile
    #: over the window is the one part of the update that is not O(d).
    floor_window: int = 120          # samples of history behind the quantile
    floor_quantile: float = 0.95     # which quantile becomes the floor
    floor_refresh: int = 10          # recompute cadence, in samples
    #: Samples after an incident during which the drift test is held off.
    #: Resetting the slow baseline at the moment of the trigger is not
    #: enough on its own: the fast baseline has only just begun absorbing
    #: the change, so over the next few dozen samples it races ahead and
    #: the gap reopens on the fault that was already caught and acted on -
    #: measured, a second incident at exactly refractory_s after the first.
    #: Three fast time constants is what the fast baseline needs to settle,
    #: and until it has, the gap between the two is not a drift measurement.
    drift_settle: int = 60


# ---- CLASS: ReasonConfig ----
@dataclass(frozen=True)
class ReasonConfig:
    """Stage 2 - ReAct loop bounds and RAG retrieval."""

    k_max: int = 6                   # K_max, hard cap on thought/action steps
    top_n: int = 3                   # N in N(e_t)
    min_similarity: float = 0.80     # cosine floor for a precedent to count
    embed_dim: int = 16              # p in e_t in R^p
    llm_timeout_s: float = 5.0


# ---- CLASS: PlanConfig ----
@dataclass(frozen=True)
class PlanConfig:
    """Stage 3 - constrained utility ranking."""

    w1: float = 1.00                 # weight on P_success
    w2: float = 0.35                 # weight on Cost
    w3: float = 0.25                 # weight on BlastRadius (normalised)
    d_sla: float = 30.0              # c_SLA(a) <= d_sla, seconds of added outage
    d_sec: float = 0.50              # c_sec(a) <= d_sec, security exposure score
    rho_max: float = 0.30            # Risk(a) <= rho_max
    b_max: int = 1                   # BlastRadius(a) <= B_max, mirrors the envelope
    laplace_prior: float = 1.0       # smoothing for P_success from precedent
    prior_success: Dict[ActionType, float] = field(default_factory=lambda: {
        ActionType.NO_OP: 0.20,
        ActionType.PROFILE_REPUSH: 0.70,
        ActionType.ISDP_SWITCH: 0.55,
        ActionType.BEARER_FAILOVER: 0.60,
        ActionType.KEY_ROTATION: 0.65,
        ActionType.ISDP_DELETE_REPROVISION: 0.90,
    })


# ---- CLASS: EnvelopeConfig ----
@dataclass(frozen=True)
class EnvelopeConfig:
    """Sigma = <Verify, B_max, tau_rollback, rho_max> - deck slide 12."""

    b_max: int = 1                   # devices/profiles an autonomous action may touch
    tau_rollback_s: float = 60.0     # inverse command must complete within this
    rho_max: float = 0.30            # residual risk bound
    rho_human: float = 0.65          # above this -> escalate instead of queueing
    require_signature: bool = True


# ---- CLASS: LearnConfig ----
@dataclass(frozen=True)
class LearnConfig:
    """Stage 5 - PPO-Lagrangian."""

    gamma: float = 0.95
    lam_gae: float = 0.95
    clip_eps: float = 0.2            # epsilon in the clipped surrogate
    lr_policy: float = 0.10          # alpha
    lr_value: float = 0.10
    lr_dual: float = 0.02            # beta, dual ascent step on lambda_i
    epochs_per_batch: int = 8
    batch_size: int = 128
    entropy_coef: float = 0.01
    d_limits: Tuple[float, ...] = (0.02, 0.05)   # d_i for (violation, blast) costs
    seed: int = 7


# ---- CLASS: LatencyBudget ----
@dataclass(frozen=True)
class LatencyBudget:
    """Deck slide 15 - per-stage target latency budget in milliseconds."""

    monitor_ms: float = 10.0
    reason_ms: float = 5000.0
    plan_ms: float = 5.0
    act_ms: float = 50.0


# ---- CLASS: Config ----
@dataclass(frozen=True)
class Config:
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    reason: ReasonConfig = field(default_factory=ReasonConfig)
    plan: PlanConfig = field(default_factory=PlanConfig)
    envelope: EnvelopeConfig = field(default_factory=EnvelopeConfig)
    learn: LearnConfig = field(default_factory=LearnConfig)
    latency: LatencyBudget = field(default_factory=LatencyBudget)


def _measured_variance_floor() -> tuple:
    """The detector's per-channel variance floor, from the measured profiles.

    Imported lazily so `config` stays free of heavier imports. Falls back to
    no floor if the profiles cannot be loaded, which keeps the original
    behaviour rather than failing at import time.
    """
    try:
        from .real_profiles import PROFILES, DEFAULT_PROFILE
        return PROFILES[DEFAULT_PROFILE].variance_floor()
    except Exception:                                   # noqa: BLE001
        return None


DEFAULT = Config(monitor=MonitorConfig(variance_floor=_measured_variance_floor()))

#: Static per-action properties used by PLAN and the safety envelope.
#: blast_radius is the count of profiles/devices/sessions the action touches.
ACTION_PROFILE: Dict[ActionType, Dict[str, float]] = {
    ActionType.NO_OP: dict(cost=0.00, blast=0, risk=0.05, sla=0.0, sec=0.00),
    ActionType.PROFILE_REPUSH: dict(cost=0.20, blast=1, risk=0.12, sla=6.0, sec=0.10),
    ActionType.ISDP_SWITCH: dict(cost=0.10, blast=1, risk=0.10, sla=3.0, sec=0.15),
    ActionType.BEARER_FAILOVER: dict(cost=0.15, blast=1, risk=0.18, sla=8.0, sec=0.20),
    ActionType.KEY_ROTATION: dict(cost=0.55, blast=2, risk=0.42, sla=25.0, sec=0.55),
    ActionType.ISDP_DELETE_REPROVISION: dict(cost=0.85, blast=3, risk=0.78, sla=120.0, sec=0.70),
}
