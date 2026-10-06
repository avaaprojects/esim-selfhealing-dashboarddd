"""Dataclass -> JSON-safe dict conversion.

One rule: every field emitted here is read off a real object produced by the
core package. Where the UI wants something the core does not compute, it is
derived arithmetically from fields that do exist (and the derivation is noted),
never invented.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from esim_selfhealing.act import AuditEntry
from esim_selfhealing.config import ACTION_PROFILE, Config
from esim_selfhealing.memory_store import MemoryRecord
from esim_selfhealing.monitor import FAULT_CLASSES, MonitorReading
from esim_selfhealing.orchestrator import LoopRecord, RunReport
from esim_selfhealing.plan import PlanResult
from esim_selfhealing.safety import ROLLBACK_TIME_S
from esim_selfhealing.schemas import (
    ACTION_SPACE,
    ActResult,
    ActionType,
    AdmissibilityReport,
    Candidate,
    Command,
    Diagnosis,
    FEATURE_NAMES,
    Incident,
    Observation,
)

#: Display metadata for the five telemetry channels. Units and directions are
#: read from the docstrings of schemas.FEATURE_NAMES.
FEATURE_META: Dict[str, Dict[str, Any]] = {
    "aka_fail_rate": {"label": "AKA failure rate", "unit": "per 100 attach",
                      "precision": 2, "worse": "up"},
    "rsrp_dbm": {"label": "RSRP", "unit": "dBm", "precision": 1, "worse": "down"},
    "drop_rate": {"label": "Session drop rate", "unit": "fraction",
                  "precision": 3, "worse": "up"},
    "latency_ms": {"label": "OTA latency", "unit": "ms", "precision": 0, "worse": "up"},
    "ota_fail_rate": {"label": "OTA failure rate", "unit": "fraction",
                      "precision": 3, "worse": "up"},
}

FAULT_LABELS: Dict[str, str] = {
    "nominal": "Nominal",
    "isdp_corruption": "ISD-P corruption",
    "smdp_session_outage": "SM-DP+ session outage",
    "radio_degradation": "Radio degradation",
    "key_desync": "Key desync",
}

ACTION_LABELS: Dict[str, str] = {
    "no_op": "No-op",
    "profile_repush": "Profile re-push",
    "isdp_switch": "ISD-P switch",
    "bearer_failover": "Bearer failover",
    "key_rotation": "Key rotation",
    "isdp_delete": "ISD-P delete + reprovision",
    "escalate": "Escalate to NOC",
}

#: The four clauses of Sigma, in the order safety.AdmissibilityReport reports
#: them, with the short labels the Safety screen renders.
CLAUSE_LABELS: List[Tuple[str, str]] = [
    ("Verify(cmd,sigma)", "Command authentication"),
    ("BlastRadius<=B_max", "Blast radius"),
    ("exists a^-1 within tau_rollback", "Rollback available"),
    ("Risk<=rho_max", "Risk limit"),
]


def _f(x: Any) -> Any:
    """Coerce numpy scalars and guard against NaN/inf, which is not valid JSON."""
    if x is None:
        return None
    if isinstance(x, bool):
        return x
    try:
        v = float(x)
    except (TypeError, ValueError):
        return x
    if math.isnan(v) or math.isinf(v):
        return None
    return v


# ---------------------------------------------------------------------------
# MONITOR
# ---------------------------------------------------------------------------
def observation_dict(obs: Observation) -> Dict[str, Any]:
    return {
        "ts": _f(obs.ts),
        "euicc_id": obs.euicc_id,
        "cell_id": obs.cell_id,
        "features": {k: _f(v) for k, v in obs.as_dict().items()},
        # ground_truth_fault is simulator-only and never read by the agent; it
        # is exposed solely so the UI can label a run as a known scenario.
        "ground_truth_fault": obs.ground_truth_fault,
    }


def reading_dict(reading: MonitorReading) -> Dict[str, Any]:
    return {
        "ts": _f(reading.observation.ts),
        "euicc_id": reading.observation.euicc_id,
        "cell_id": reading.observation.cell_id,
        "features": {k: _f(v) for k, v in reading.observation.as_dict().items()},
        "z_scores": {k: _f(v) for k, v in reading.z_scores.items()},
        "score": _f(reading.score),
        "threshold": _f(reading.threshold),
        "triggered": bool(reading.triggered),
        "warming_up": bool(reading.warming_up),
        "cusum_max": _f(reading.cusum_max),
        "drift_score": _f(getattr(reading, "drift_score", 0.0)),
        "deviation_sigma": _f(getattr(reading, "deviation_sigma", 0.0)),
        # The detector's own EWMA baseline and band at this sample, so the
        # channel plots can show what "normal" meant when it was scored
        # rather than a line recomputed in the browser.
        "baseline": {k: _f(v) for k, v in getattr(reading, "baseline", {}).items()},
        "band": {k: _f(v) for k, v in getattr(reading, "band", {}).items()},
        "latency_ms": _f(reading.latency_ms),
        "belief": {k: _f(v) for k, v in reading.belief.items()},
        "incident_id": reading.incident.incident_id if reading.incident else None,
    }


def belief_list(belief: Dict[str, float]) -> List[Dict[str, Any]]:
    """Belief distribution as a sorted, display-ready list."""
    items = [
        {"fault_class": c, "label": FAULT_LABELS.get(c, c), "p": _f(belief.get(c, 0.0))}
        for c in FAULT_CLASSES
    ]
    return sorted(items, key=lambda d: -(d["p"] or 0.0))


def incident_dict(inc: Incident) -> Dict[str, Any]:
    return {
        "incident_id": inc.incident_id,
        "opened_at": _f(inc.opened_at),
        "resolved_at": _f(inc.resolved_at),
        "mttr_s": _f(inc.mttr_seconds()),
        "anomaly_score": _f(inc.anomaly_score),
        "threshold": _f(inc.threshold),
        # How far past the chi-square threshold the sample landed. Derived, and
        # only used for the severity bar.
        "exceedance": _f(inc.anomaly_score / inc.threshold) if inc.threshold else None,
        # How far outside its own normal range the device sat at detection.
        # This is the magnitude signal severity is built on; see
        # output_view.severity and schemas.Incident.deviation_sigma.
        "deviation_sigma": _f(getattr(inc, "deviation_sigma", 0.0)),
        "belief": belief_list(inc.belief),
        "observation": observation_dict(inc.observation),
    }


# ---------------------------------------------------------------------------
# REASON
# ---------------------------------------------------------------------------
def diagnosis_dict(diag: Diagnosis) -> Dict[str, Any]:
    """Auditable evidence only.

    `ReActStep.thought` is the agent's own published rationale for the tool it
    then called - it is part of the recorded trace H_k that the audit entry is
    hashed over, not hidden scratchpad reasoning.
    """
    return {
        "incident_id": diag.incident_id,
        "text": diag.text,
        "fault_class": diag.fault_class,
        "fault_label": FAULT_LABELS.get(diag.fault_class, diag.fault_class),
        "confidence": _f(diag.confidence),
        "candidate_actions": [
            {"action": a.value, "label": ACTION_LABELS.get(a.value, a.value)}
            for a in diag.candidate_actions
        ],
        "retrieved_ids": list(diag.retrieved_ids),
        "llm_calls": diag.llm_calls,
        "tool_calls": len([s for s in diag.trace if s.tool != "FINISH"]),
        "trace": [
            {
                "k": s.k,
                "rationale": s.thought,
                "tool": s.tool,
                "tool_args": {k: str(v) for k, v in s.tool_args.items()},
                "observation": s.observation,
            }
            for s in diag.trace
        ],
    }


# ---------------------------------------------------------------------------
# PLAN
# ---------------------------------------------------------------------------
def candidate_dict(c: Candidate, selected: bool = False) -> Dict[str, Any]:
    return {
        "action": c.action.value,
        "label": ACTION_LABELS.get(c.action.value, c.action.value),
        "p_success": _f(c.p_success),
        "cost": _f(c.cost),
        "blast_radius": int(c.blast_radius),
        "risk": _f(c.risk),
        "sla_cost": _f(c.sla_cost),
        "sec_cost": _f(c.sec_cost),
        "utility": _f(c.utility),
        "feasible": bool(c.feasible),
        "violated": list(c.violated),
        "proposer": c.proposer,
        "selected": selected,
    }


def plan_dict(plan: Optional[PlanResult], fleet_size: int = 1) -> Optional[Dict[str, Any]]:
    if plan is None:
        return None
    sel = plan.selected
    ranked = [candidate_dict(c, selected=(sel is not None and c is sel)) for c in plan.ranked]
    infeasible = [candidate_dict(c) for c in plan.infeasible]
    w1, w2, w3 = plan.weights
    return {
        "ranked": ranked,
        "infeasible": infeasible,
        "selected": candidate_dict(sel, selected=True) if sel is not None else None,
        "weights": {"w1": _f(w1), "w2": _f(w2), "w3": _f(w3)},
        "fleet_size": int(fleet_size),
        "latency_ms": _f(plan.latency_ms),
    }


def neighbours_dict(pairs: Sequence[Tuple[MemoryRecord, float]]) -> List[Dict[str, Any]]:
    return [{**memory_record_dict(r), "similarity": _f(s)} for r, s in pairs]


# ---------------------------------------------------------------------------
# SAFETY + ACT
# ---------------------------------------------------------------------------
def admissibility_dict(report: AdmissibilityReport) -> Dict[str, Any]:
    failed = set(report.failed_clauses())
    return {
        "admissible": bool(report.admissible),
        "failed_clauses": sorted(failed),
        "checks": [
            {
                "clause": clause,
                "label": label,
                "passed": clause not in failed,
                "detail": report.detail.get(clause, ""),
            }
            for clause, label in CLAUSE_LABELS
        ],
    }


def command_dict(cmd: Optional[Command]) -> Optional[Dict[str, Any]]:
    if cmd is None:
        return None
    sig = cmd.signature or b""
    return {
        "command_id": cmd.command_id,
        "action": cmd.action.value,
        "endpoint": cmd.endpoint,
        "payload": {k: str(v) for k, v in cmd.payload.items()},
        "signature_present": cmd.signature is not None,
        # Truncated on purpose: the full signature is an authenticator, not a
        # display field.
        "signature_preview": sig.hex()[:32] + ("..." if sig else ""),
        "issued_at": _f(cmd.issued_at),
        "inverse_endpoint": cmd.inverse.endpoint if cmd.inverse else None,
        "rollback_seconds": _f(ROLLBACK_TIME_S.get(cmd.action)),
    }


def act_dict(result: Optional[ActResult]) -> Optional[Dict[str, Any]]:
    if result is None:
        return None
    return {
        "incident_id": result.incident_id,
        "action": result.action.value,
        "label": ACTION_LABELS.get(result.action.value, result.action.value),
        "status": result.status.value,
        "dispatched": bool(result.dispatched),
        "success": bool(result.success),
        "rolled_back": bool(result.rolled_back),
        "latency_ms": _f(result.latency_ms),
        "gate_latency_ms": _f(result.gate_latency_ms),
        "dispatch_latency_ms": _f(result.latency_ms - result.gate_latency_ms),
        "message": result.message,
        "admissibility": admissibility_dict(result.report),
    }


def audit_dict(entry: AuditEntry) -> Dict[str, Any]:
    return {
        "entry_id": entry.entry_id,
        "incident_id": entry.incident_id,
        "command_id": entry.command_id,
        "action": entry.action,
        "endpoint": entry.endpoint,
        "signature_hex": entry.signature_hex,
        "trace_digest": entry.trace_digest,
        "status": entry.status,
        "admissible": bool(entry.admissible),
        "failed_clauses": list(entry.failed_clauses),
        "ts": _f(entry.ts),
        "prev_hash": entry.prev_hash[:16],
        "entry_hash": entry.entry_hash[:16],
    }


# ---------------------------------------------------------------------------
# MEMORY
# ---------------------------------------------------------------------------
def memory_record_dict(r: MemoryRecord) -> Dict[str, Any]:
    return {
        "record_id": r.record_id,
        "fault_class": r.fault_class,
        "fault_label": FAULT_LABELS.get(r.fault_class, r.fault_class),
        "diagnosis": r.diagnosis,
        "action": r.action,
        "action_label": ACTION_LABELS.get(r.action, r.action),
        "status": r.status,
        "success": bool(r.success),
        "resolution_s": _f(r.resolution_s),
        "tags": list(r.tags),
        "features": {k: _f(v) for k, v in zip(FEATURE_NAMES, r.features)},
    }


# ---------------------------------------------------------------------------
# Loop records and run reports
# ---------------------------------------------------------------------------
def loop_record_dict(rec: LoopRecord, capture=None) -> Dict[str, Any]:
    """Full, expandable decision trace for one incident."""
    plan = plan_dict(capture.plan, capture.fleet_size) if capture else None
    return {
        "incident": incident_dict(rec.incident),
        "diagnosis": diagnosis_dict(rec.diagnosis),
        "plan": plan,
        "retrieved": neighbours_dict(capture.neighbours) if capture else [],
        "command": command_dict(capture.command) if capture else None,
        "act": act_dict(rec.act_result),
        "selected_action": rec.selected_action.value if rec.selected_action else None,
        "selected_label": (ACTION_LABELS.get(rec.selected_action.value)
                           if rec.selected_action else None),
        "status": rec.status.value,
        "reward": _f(rec.reward),
        "costs": [_f(c) for c in rec.costs],
        "stage_latency_ms": {k: _f(v) for k, v in rec.stage_latency_ms.items()},
        "total_latency_ms": _f(rec.total_latency_ms()),
    }


def run_report_dict(report: RunReport) -> Dict[str, Any]:
    return {
        "samples_seen": report.samples_seen,
        "incidents": len(report.records),
        "false_positives": report.false_positives,
        "false_positive_rate": _f(report.false_positives / report.samples_seen)
        if report.samples_seen else None,
        "auto_remediation_rate": _f(report.auto_remediation_rate()),
        "constraint_violation_rate": _f(report.constraint_violation_rate()),
        "rollback_success_rate": _f(report.rollback_success_rate()),
        "mean_loop_latency_s": _f(report.mttr_s()),
        "status_mix": report.status_mix(),
        "stage_latency_ms": {k: _f(v) for k, v in report.stage_latency_profile().items()},
        "summary": report.summary(),
    }


# ---------------------------------------------------------------------------
# Static reference data
# ---------------------------------------------------------------------------
def action_space_dict() -> List[Dict[str, Any]]:
    out = []
    for a in ACTION_SPACE:
        prof = ACTION_PROFILE[a]
        out.append({
            "action": a.value,
            "label": ACTION_LABELS.get(a.value, a.value),
            "cost": _f(prof["cost"]),
            "blast_radius": int(prof["blast"]),
            "risk": _f(prof["risk"]),
            "sla_cost": _f(prof["sla"]),
            "sec_cost": _f(prof["sec"]),
            "reversible": a.value in {k.value for k in ROLLBACK_TIME_S},
            "rollback_seconds": _f(ROLLBACK_TIME_S.get(a)),
        })
    return out


def config_dict(cfg: Config) -> Dict[str, Any]:
    return {
        "monitor": {
            "lam": _f(cfg.monitor.lam),
            "alpha": _f(cfg.monitor.alpha),
            "warmup_samples": cfg.monitor.warmup_samples,
            "refractory_s": _f(cfg.monitor.refractory_s),
            "min_consecutive": cfg.monitor.min_consecutive,
        },
        "reason": {
            "k_max": cfg.reason.k_max,
            "top_n": cfg.reason.top_n,
            "min_similarity": _f(cfg.reason.min_similarity),
            "embed_dim": cfg.reason.embed_dim,
        },
        "plan": {
            "w1": _f(cfg.plan.w1), "w2": _f(cfg.plan.w2), "w3": _f(cfg.plan.w3),
            "d_sla": _f(cfg.plan.d_sla), "d_sec": _f(cfg.plan.d_sec),
            "rho_max": _f(cfg.plan.rho_max), "b_max": cfg.plan.b_max,
        },
        "envelope": {
            "b_max": cfg.envelope.b_max,
            "tau_rollback_s": _f(cfg.envelope.tau_rollback_s),
            "rho_max": _f(cfg.envelope.rho_max),
            "rho_human": _f(cfg.envelope.rho_human),
            "require_signature": bool(cfg.envelope.require_signature),
        },
        "learn": {
            "clip_eps": _f(cfg.learn.clip_eps),
            "lr_policy": _f(cfg.learn.lr_policy),
            "lr_dual": _f(cfg.learn.lr_dual),
            "entropy_coef": _f(cfg.learn.entropy_coef),
            "d_limits": [_f(d) for d in cfg.learn.d_limits],
        },
        "latency_budget_ms": {
            "monitor": _f(cfg.latency.monitor_ms),
            "reason": _f(cfg.latency.reason_ms),
            "plan": _f(cfg.latency.plan_ms),
            "act": _f(cfg.latency.act_ms),
        },
    }
