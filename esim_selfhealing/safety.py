"""The safety envelope Sigma = <Verify, B_max, tau_rollback, rho_max>.

    Admissible(a) <=>  Vf_PK(cmd_a, sigma_a) = 1
                   AND BlastRadius(a) <= B_max
                   AND exists a^-1 : Apply(a^-1, Apply(a,s)) ~ s within tau_rollback
                   AND Risk(a) <= rho_max

Two properties this module is built to preserve:

1. **Independence.** Each clause is evaluated separately and reported
   separately, so a rejection always names which guarantee failed. Never
   collapse them into one boolean - the audit trail needs the reason.
2. **Pre-dispatch rollback.** The inverse command is constructed and checked
   BEFORE the forward command is sent, not synthesised after a failure. An
   action with no constructible inverse is inadmissible by definition.

Routing (deck slide 12):
    not Admissible(a*)  ->  HUMAN-IN-LOOP  if Risk(a*) <= rho_human
                        ->  ESCALATION     otherwise
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .config import DEFAULT, EnvelopeConfig
from .crypto_pqc import SignatureVerifier
from .schemas import (
    IRREVERSIBLE_ACTIONS,
    ActionType,
    AdmissibilityReport,
    Candidate,
    Command,
    Status,
)

#: Worst-case time to apply the inverse of each action, seconds.
ROLLBACK_TIME_S = {
    ActionType.NO_OP: 0.0,
    ActionType.PROFILE_REPUSH: 12.0,
    ActionType.ISDP_SWITCH: 5.0,
    ActionType.BEARER_FAILOVER: 8.0,
    ActionType.KEY_ROTATION: 45.0,
}


# ---- CLASS: SafetyEnvelope ----
@dataclass
class SafetyEnvelope:
    """Sigma. Owns the admissibility predicate and the routing rule."""

    verifier: SignatureVerifier
    cfg: EnvelopeConfig = DEFAULT.envelope

    # -- individual clauses ------------------------------------------------
    # ---- METHOD: SafetyEnvelope._check_signature ----
    def _check_signature(self, cmd: Command) -> tuple[bool, str]:
        if not self.cfg.require_signature:
            return True, "signature check disabled by config"
        if cmd.signature is None:
            return False, "command carries no signature"
        ok = self.verifier.verify(cmd)
        return ok, (
            f"{self.verifier.algorithm} verify="
            f"{'PASS' if ok else 'FAIL'} "
            f"(post_quantum={self.verifier.is_post_quantum})"
        )

    # ---- METHOD: SafetyEnvelope._check_blast ----
    def _check_blast(self, cand: Candidate) -> tuple[bool, str]:
        ok = cand.blast_radius <= self.cfg.b_max
        return ok, f"BlastRadius={cand.blast_radius} vs B_max={self.cfg.b_max}"

    # ---- METHOD: SafetyEnvelope._check_rollback ----
    def _check_rollback(self, cand: Candidate, cmd: Command) -> tuple[bool, str]:
        if cand.action in IRREVERSIBLE_ACTIONS:
            return False, f"{cand.action.value} has no inverse - irreversible by construction"
        if cmd.inverse is None:
            return False, "inverse command was not pre-computed before dispatch"
        t = ROLLBACK_TIME_S.get(cand.action, float("inf"))
        ok = t <= self.cfg.tau_rollback_s
        return ok, f"rollback via {cmd.inverse.endpoint} in ~{t:.0f}s vs tau={self.cfg.tau_rollback_s:.0f}s"

    # ---- METHOD: SafetyEnvelope._check_risk ----
    def _check_risk(self, cand: Candidate) -> tuple[bool, str]:
        ok = cand.risk <= self.cfg.rho_max
        return ok, f"Risk={cand.risk:.3f} vs rho_max={self.cfg.rho_max:.2f}"

    # -- the predicate -----------------------------------------------------
    # ---- METHOD: SafetyEnvelope.admissible ----
    def admissible(self, cand: Candidate, cmd: Command) -> AdmissibilityReport:
        sig_ok, sig_msg = self._check_signature(cmd)
        blast_ok, blast_msg = self._check_blast(cand)
        rb_ok, rb_msg = self._check_rollback(cand, cmd)
        risk_ok, risk_msg = self._check_risk(cand)
        return AdmissibilityReport(
            signature_ok=sig_ok,
            blast_ok=blast_ok,
            rollback_ok=rb_ok,
            risk_ok=risk_ok,
            detail={
                "Verify(cmd,sigma)": sig_msg,
                "BlastRadius<=B_max": blast_msg,
                "exists a^-1 within tau_rollback": rb_msg,
                "Risk<=rho_max": risk_msg,
            },
        )

    # -- routing rule ------------------------------------------------------
    # ---- METHOD: SafetyEnvelope.route ----
    def route(self, cand: Candidate, report: AdmissibilityReport) -> Status:
        if report.admissible:
            return Status.AUTO_REMEDIATED
        if cand.risk <= self.cfg.rho_human:
            return Status.HUMAN_IN_LOOP
        return Status.ESCALATION

    # -- costs fed back to LEARN ------------------------------------------
    # ---- METHOD: SafetyEnvelope.constraint_costs ----
    def constraint_costs(self, cand: Candidate) -> tuple[float, ...]:
        """c_i(s,a) - the same terms LEARN is constrained by (deck slide 11).

        c_1: envelope violation indicator (0/1)
        c_2: normalised blast radius above B_max
        """
        c1 = 0.0 if (cand.blast_radius <= self.cfg.b_max and cand.risk <= self.cfg.rho_max) else 1.0
        c2 = max(0.0, cand.blast_radius - self.cfg.b_max) / max(1, self.cfg.b_max)
        return (c1, c2)


# ---- FUNCTION: format_report ----
def format_report(report: AdmissibilityReport) -> str:
    lines = []
    for clause, msg in report.detail.items():
        ok = clause not in report.failed_clauses()
        lines.append(f"  [{'PASS' if ok else 'FAIL'}] {clause:<32} {msg}")
    verdict = "ADMISSIBLE" if report.admissible else "NOT ADMISSIBLE"
    lines.append(f"  => Admissible(a*, Sigma) = {int(report.admissible)}  ({verdict})")
    return "\n".join(lines)
