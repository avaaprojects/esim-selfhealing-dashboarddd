"""USE CASE 4 - ACT
==========================================================================
Scenario
    Four remediations reach the dispatch gate on the same evening:

      1. a re-push on one device                  - should go out
      2. a re-push on a 60-device shared profile  - blast radius too wide
      3. an ISD-P delete + reprovision            - no inverse exists
      4. a re-push whose command was tampered     - signature must fail

    Only one of them should ever reach the RSP endpoint, and every
    rejection has to say which guarantee it failed - "denied" is not an
    acceptable audit record on telecom-critical infrastructure.

What it demonstrates
    * Sigma = <Verify, B_max, tau_rollback, rho_max> evaluated clause by clause
    * the inverse command a^-1 built BEFORE dispatch, not after a failure
    * ML-DSA signing (real via liboqs when installed, stub otherwise) and
      endpoint-side verification rejecting a tampered payload
    * routing: not-admissible -> human-in-loop, or escalation above rho_human
    * a hash-chained audit log tying each command to the ReAct trace H_k

Run:  python usecases/uc4_act_safety_gated_dispatch.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esim_selfhealing.act import Actuator
from esim_selfhealing.config import DEFAULT
from esim_selfhealing.crypto_pqc import build_signer
from esim_selfhealing.memory_store import seed_memory
from esim_selfhealing.monitor import Monitor
from esim_selfhealing.plan import Planner
from esim_selfhealing.reason import Reasoner, ToolRegistry
from esim_selfhealing.rsp_api import RSPClient
from esim_selfhealing.safety import SafetyEnvelope, format_report
from esim_selfhealing.schemas import ActionType, Candidate
from esim_selfhealing.telemetry import scenario_isdp_corruption

BAR = "=" * 74


# ---- FUNCTION: section ----
def section(title: str) -> None:
    print(f"\n{BAR}\n{title}\n{BAR}")


# ---- FUNCTION: build_context ----
def build_context():
    memory = seed_memory(dim=DEFAULT.reason.embed_dim)
    signer = build_signer(prefer_pq=True)
    rsp = RSPClient(verifier=signer, seed=5)
    tools = ToolRegistry(rsp, memory, DEFAULT.reason)
    reasoner = Reasoner(tools, memory, cfg=DEFAULT.reason)
    envelope = SafetyEnvelope(verifier=signer, cfg=DEFAULT.envelope)
    actuator = Actuator(rsp, envelope, signer)
    monitor = Monitor(DEFAULT.monitor)

    for obs in scenario_isdp_corruption(seed=42):
        reading = monitor.update(obs)
        if reading.triggered:
            rsp.inject_fault(obs.euicc_id, obs.ground_truth_fault)
            diag = reasoner.diagnose(reading.incident,
                                     top_features=monitor.top_features(obs))
            return memory, signer, rsp, actuator, envelope, diag, obs.euicc_id
    raise RuntimeError("no incident opened")


# ---- FUNCTION: attempt ----
def attempt(actuator, envelope, diagnosis, euicc_id, candidate: Candidate, label: str) -> None:
    print(f"\n--- {label}")
    print(f"    action={candidate.action.value}  blast={candidate.blast_radius}  "
          f"risk={candidate.risk:.2f}")
    result = actuator.execute(candidate, diagnosis, euicc_id)
    print(format_report(result.report))
    print(f"  routed to     : {result.status.value}")
    print(f"  dispatched    : {result.dispatched}   succeeded: {result.success}")
    print(f"  latency       : {result.latency_ms:.0f} ms")
    if result.message:
        print(f"  detail        : {result.message}")


# ---- FUNCTION: main ----
def main() -> None:
    memory, signer, rsp, actuator, envelope, diagnosis, euicc_id = build_context()

    section("USE CASE 4 - ACT: four remediations, one gate")
    print(f"signer        : {signer.algorithm}")
    print(f"post-quantum  : {signer.is_post_quantum}"
          + ("" if signer.is_post_quantum else
             "   <- stub backend; install `oqs` for real FIPS 204"))
    print(f"envelope      : B_max={DEFAULT.envelope.b_max}  "
          f"tau_rollback={DEFAULT.envelope.tau_rollback_s:.0f}s  "
          f"rho_max={DEFAULT.envelope.rho_max}  rho_human={DEFAULT.envelope.rho_human}")
    print(f"incident      : {diagnosis.incident_id}  fault={diagnosis.fault_class}")

    planner = Planner(memory, DEFAULT.plan, fleet_size=1)
    neighbours = memory.retrieve(
        # reuse the incident's own observation via the diagnosis trace context
        _observation_from(actuator, euicc_id), top_n=3, min_similarity=0.0,
        hint=diagnosis.fault_class,
    )
    plan = planner.rank(diagnosis, neighbours)
    winner = plan.selected
    print(f"a* from PLAN  : {winner.action.value}  U={winner.utility:+.3f}")

    # -------------------------------------------------------------- case 1
    section("Case 1 - single-device re-push (the intended autonomous path)")
    attempt(actuator, envelope, diagnosis, euicc_id, winner, "all four clauses should pass")

    # -------------------------------------------------------------- case 2
    section("Case 2 - identical action, profile shared by 60 devices")
    wide = Candidate(
        action=winner.action, p_success=winner.p_success, cost=winner.cost,
        blast_radius=60, risk=winner.risk, sla_cost=winner.sla_cost,
        sec_cost=winner.sec_cost, utility=winner.utility, proposer="sla_ops",
    )
    attempt(actuator, envelope, diagnosis, euicc_id, wide,
            "BlastRadius clause should fail; risk still low -> human review")

    # -------------------------------------------------------------- case 3
    section("Case 3 - ISD-P delete + reprovision (irreversible)")
    irreversible = Candidate(
        action=ActionType.ISDP_DELETE_REPROVISION, p_success=0.97, cost=0.85,
        blast_radius=3, risk=0.78, sla_cost=120.0, sec_cost=0.70,
        utility=0.11, proposer="security",
    )
    attempt(actuator, envelope, diagnosis, euicc_id, irreversible,
            "no inverse exists and risk exceeds rho_human -> escalation")
    print("\n  Note: the highest P_success action in the whole candidate set is the")
    print("  one the envelope refuses outright. Success probability never buys")
    print("  its way past the rollback guarantee.")

    # -------------------------------------------------------------- case 4
    section("Case 4 - tampered command (payload edited after signing)")
    cmd = actuator.build_command(ActionType.PROFILE_REPUSH, euicc_id)
    cmd.inverse = actuator.build_inverse(cmd)
    cmd.signature = signer.sign(cmd)
    print(f"  signed cmd    : {cmd.command_id}  sigma={cmd.signature.hex()[:24]}...")
    print(f"  verify (clean): {envelope.verifier.verify(cmd)}")
    cmd.payload["euicc_id"] = "89330000000099999"      # redirect to another device
    print(f"  payload edited: euicc_id -> {cmd.payload['euicc_id']}")
    print(f"  verify (dirty): {envelope.verifier.verify(cmd)}")
    exec_result = rsp.execute(cmd)
    print(f"  RSP endpoint  : ok={exec_result.ok}  {exec_result.detail}")
    print("\n  The endpoint verifies independently of the orchestrator, so a")
    print("  compromised orchestrator cannot issue commands the RSP will honour.")

    # -------------------------------------------------------------- audit
    section("Audit trail")
    print(f"entries        : {len(actuator.audit)}")
    print(f"chain intact   : {actuator.audit.verify_chain()}")
    print(f"human queue    : {len(actuator.human_queue)}   escalations: {len(actuator.escalations)}\n")
    for e in actuator.audit.entries:
        print(f"  {e.entry_id}  {e.action:<20} {e.status:<16} "
              f"admissible={int(e.admissible)}  trace={e.trace_digest}")
        if e.failed_clauses:
            print(f"      failed: {', '.join(e.failed_clauses)}")

    # Tamper: rewrite a rejection into an approval, the edit an insider would
    # actually want to make. The chain must notice.
    rejected = next((e for e in actuator.audit.entries if not e.admissible), None)
    if rejected is not None:
        print(f"\ntampering: {rejected.entry_id} status "
              f"'{rejected.status}' -> 'AUTO-REMEDIATED', admissible 0 -> 1")
        rejected.status = "AUTO-REMEDIATED"
        rejected.admissible = True
        rejected.failed_clauses = []
        print(f"chain intact after the edit : {actuator.audit.verify_chain()}")
        print("the stored entry_hash no longer matches the entry's contents, and")
        print("every later entry chains off it - a single edit invalidates the tail")

    section("Takeaway")
    print("Every rejection names its clause, every dispatch carries a signature")
    print("and a pre-verified inverse, and the log that records both is")
    print("hash-chained. That is what makes autonomous action on live RSP")
    print("infrastructure auditable after the fact rather than merely fast.")


# ---- FUNCTION: _observation_from ----
def _observation_from(actuator, euicc_id):
    """Rebuild a minimal Observation for retrieval (features from live KPIs)."""
    from esim_selfhealing.schemas import Observation
    d = actuator.rsp.device(euicc_id)
    return Observation(ts=0.0, euicc_id=euicc_id, cell_id="CELL-4471",
                       features=[9.5, d.rsrp_dbm, d.drop_rate, d.latency_ms + 40, 0.09])


if __name__ == "__main__":
    main()
