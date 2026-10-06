"""Smoke tests - one per component, plus the invariants that matter.

    python tests/test_smoke.py          # no pytest required
    pytest tests/test_smoke.py -q       # or under pytest

These are not exhaustive unit tests. They pin the properties that the design
claims are true, so a refactor that quietly breaks one of them fails loudly:

    * MONITOR    fires on a real fault, stays quiet on a healthy stream
    * REASON     never mutates state, respects K_max
    * PLAN       never selects an infeasible candidate
    * ACT        never dispatches an inadmissible action; audit chain detects edits
    * Sigma      irreversible actions are always inadmissible
    * LEARN      the constrained learner ends below the unconstrained one on cost
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from esim_selfhealing.act import Actuator
from esim_selfhealing.config import DEFAULT, LearnConfig
from esim_selfhealing.crypto_pqc import StubSigner, build_signer, canonical_bytes
from esim_selfhealing.learn import PPOLagrangian, RemediationEnv
from esim_selfhealing.memory_store import IncidentMemory, seed_memory
from esim_selfhealing.monitor import Monitor
from esim_selfhealing.orchestrator import build_default_orchestrator
from esim_selfhealing.plan import Planner
from esim_selfhealing.reason import Reasoner, ToolRegistry
from esim_selfhealing.rsp_api import RSPClient
from esim_selfhealing.safety import SafetyEnvelope
from esim_selfhealing.schemas import ActionType, Candidate, Status
from esim_selfhealing.telemetry import (
    FaultWindow,
    TelemetryStream,
    healthy_stream,
    scenario_isdp_corruption,
)


# ---------------------------------------------------------------------------
# ---- FUNCTION: _incident ----
def _incident(stream=None, seed: int = 42):
    mon = Monitor(DEFAULT.monitor)
    for obs in (stream or scenario_isdp_corruption(seed=seed)):
        r = mon.update(obs)
        if r.triggered:
            return mon, r
    raise AssertionError("no incident opened")


# ---- FUNCTION: _context ----
def _context(seed: int = 5):
    memory = seed_memory(dim=DEFAULT.reason.embed_dim)
    signer = build_signer(prefer_pq=True)
    rsp = RSPClient(verifier=signer, seed=seed)
    tools = ToolRegistry(rsp, memory, DEFAULT.reason)
    reasoner = Reasoner(tools, memory, cfg=DEFAULT.reason)
    envelope = SafetyEnvelope(verifier=signer, cfg=DEFAULT.envelope)
    actuator = Actuator(rsp, envelope, signer)
    return memory, signer, rsp, tools, reasoner, envelope, actuator


# ---------------------------------------------------------------------------
# ---- FUNCTION: test_monitor_detects_fault ----
def test_monitor_detects_fault():
    mon, reading = _incident()
    assert reading.incident is not None
    assert reading.score > reading.threshold
    assert reading.observation.ground_truth_fault is not None


# ---- FUNCTION: test_monitor_quiet_on_healthy_stream ----
def test_monitor_quiet_on_healthy_stream():
    mon = Monitor(DEFAULT.monitor)
    readings = mon.run(healthy_stream(n=400, seed=101))
    eligible = [r for r in readings if not r.warming_up]
    rate = sum(1 for r in eligible if r.triggered) / len(eligible)
    assert rate < 0.01, f"false-alarm rate {rate:.4f} exceeds the alpha budget"


# ---- FUNCTION: test_monitor_belief_is_a_distribution ----
def test_monitor_belief_is_a_distribution():
    _, reading = _incident()
    total = sum(reading.belief.values())
    assert abs(total - 1.0) < 1e-9
    assert all(0.0 <= p <= 1.0 for p in reading.belief.values())


# ---- FUNCTION: test_monitor_latency_within_budget ----
def test_monitor_latency_within_budget():
    mon = Monitor(DEFAULT.monitor)
    readings = mon.run(scenario_isdp_corruption(seed=42))
    p99 = float(np.percentile([r.latency_ms for r in readings], 99))
    assert p99 < DEFAULT.latency.monitor_ms


# ---------------------------------------------------------------------------
# ---- FUNCTION: test_reason_is_read_only_and_bounded ----
def test_reason_is_read_only_and_bounded():
    memory, _, rsp, tools, reasoner, _, _ = _context()
    mon, reading = _incident()
    obs = reading.observation
    rsp.inject_fault(obs.euicc_id, obs.ground_truth_fault)
    before = rsp.query_euicc_state(obs.euicc_id)

    diag = reasoner.diagnose(reading.incident, top_features=mon.top_features(obs))

    assert rsp.query_euicc_state(obs.euicc_id) == before, "REASON mutated eUICC state"
    assert len(diag.trace) <= DEFAULT.reason.k_max
    assert diag.candidate_actions, "REASON produced an empty candidate set"


# ---- FUNCTION: test_reason_identifies_each_fault_class ----
def test_reason_identifies_each_fault_class():
    memory = seed_memory(dim=DEFAULT.reason.embed_dim)
    cases = [("isdp_corruption", 42), ("smdp_session_outage", 44),
             ("radio_degradation", 43), ("key_desync", 45)]
    for fault, seed in cases:
        _, _, rsp, tools, reasoner, _, _ = _context(seed=seed)
        reasoner.memory = memory
        tools.memory = memory
        stream = TelemetryStream(n_samples=300, seed=seed,
                                 faults=[FaultWindow(fault, 240, 300)],
                                 drift_per_sample=1.0)
        mon, reading = _incident(stream)
        rsp.inject_fault(reading.observation.euicc_id, fault)
        diag = reasoner.diagnose(reading.incident,
                                 top_features=mon.top_features(reading.observation))
        assert diag.fault_class == fault, f"{fault} misdiagnosed as {diag.fault_class}"


# ---------------------------------------------------------------------------
# ---- FUNCTION: test_plan_never_selects_infeasible ----
def test_plan_never_selects_infeasible():
    memory, _, rsp, tools, reasoner, _, _ = _context()
    mon, reading = _incident()
    rsp.inject_fault(reading.observation.euicc_id, reading.observation.ground_truth_fault)
    diag = reasoner.diagnose(reading.incident,
                             top_features=mon.top_features(reading.observation))
    neighbours = memory.retrieve(reading.observation, top_n=3, min_similarity=0.0)

    for fleet in (1, 5, 120):
        result = Planner(memory, DEFAULT.plan).rank(diag, neighbours, fleet_size=fleet)
        if result.selected is not None:
            assert result.selected.feasible
            assert not result.selected.violated
        assert all(c.feasible for c in result.ranked)


# ---- FUNCTION: test_plan_ranking_is_ordered ----
def test_plan_ranking_is_ordered():
    memory, _, rsp, tools, reasoner, _, _ = _context()
    mon, reading = _incident()
    rsp.inject_fault(reading.observation.euicc_id, reading.observation.ground_truth_fault)
    diag = reasoner.diagnose(reading.incident,
                             top_features=mon.top_features(reading.observation))
    neighbours = memory.retrieve(reading.observation, top_n=3, min_similarity=0.0)
    ranked = Planner(memory, DEFAULT.plan).rank(diag, neighbours).ranked
    utilities = [c.utility for c in ranked]
    assert utilities == sorted(utilities, reverse=True)


# ---------------------------------------------------------------------------
# ---- FUNCTION: test_irreversible_action_is_never_admissible ----
def test_irreversible_action_is_never_admissible():
    _, signer, _, _, _, envelope, actuator = _context()
    cand = Candidate(action=ActionType.ISDP_DELETE_REPROVISION, p_success=0.99,
                     cost=0.85, blast_radius=1, risk=0.05, sla_cost=1.0, sec_cost=0.1)
    cmd = actuator.build_command(cand.action, "89330000000048213")
    cmd.inverse = actuator.build_inverse(cmd)
    cmd.signature = signer.sign(cmd)
    report = envelope.admissible(cand, cmd)
    assert cmd.inverse is None
    assert not report.rollback_ok
    assert not report.admissible


# ---- FUNCTION: test_inadmissible_action_is_never_dispatched ----
def test_inadmissible_action_is_never_dispatched():
    memory, _, rsp, tools, reasoner, envelope, actuator = _context()
    mon, reading = _incident()
    rsp.inject_fault(reading.observation.euicc_id, reading.observation.ground_truth_fault)
    diag = reasoner.diagnose(reading.incident,
                             top_features=mon.top_features(reading.observation))
    wide = Candidate(action=ActionType.PROFILE_REPUSH, p_success=0.9, cost=0.2,
                     blast_radius=99, risk=0.12, sla_cost=6.0, sec_cost=0.1)
    result = actuator.execute(wide, diag, reading.observation.euicc_id)
    assert not result.report.admissible
    assert not result.dispatched
    assert result.status is not Status.AUTO_REMEDIATED


# ---- FUNCTION: test_signature_rejects_tampered_payload ----
def test_signature_rejects_tampered_payload():
    signer = StubSigner()
    _, _, _, _, _, _, actuator = _context()
    cmd = actuator.build_command(ActionType.PROFILE_REPUSH, "89330000000048213")
    cmd.signature = signer.sign(cmd)
    assert signer.verify(cmd)
    cmd.payload["euicc_id"] = "89330000000099999"
    assert not signer.verify(cmd)


# ---- FUNCTION: test_canonical_encoding_is_stable ----
def test_canonical_encoding_is_stable():
    _, _, _, _, _, _, actuator = _context()
    cmd = actuator.build_command(ActionType.ISDP_SWITCH, "89330000000048213")
    assert canonical_bytes(cmd) == canonical_bytes(cmd)


# ---- FUNCTION: test_audit_chain_detects_edits ----
def test_audit_chain_detects_edits():
    memory, _, rsp, tools, reasoner, envelope, actuator = _context()
    mon, reading = _incident()
    rsp.inject_fault(reading.observation.euicc_id, reading.observation.ground_truth_fault)
    diag = reasoner.diagnose(reading.incident,
                             top_features=mon.top_features(reading.observation))
    cand = Candidate(action=ActionType.PROFILE_REPUSH, p_success=0.9, cost=0.2,
                     blast_radius=1, risk=0.12, sla_cost=6.0, sec_cost=0.1)
    actuator.execute(cand, diag, reading.observation.euicc_id)
    assert actuator.audit.verify_chain()
    actuator.audit.entries[0].status = "TAMPERED"
    assert not actuator.audit.verify_chain()


# ---------------------------------------------------------------------------
# ---- FUNCTION: test_constrained_learner_beats_unconstrained_on_cost ----
def test_constrained_learner_beats_unconstrained_on_cost():
    env = RemediationEnv()
    cfg = LearnConfig()

    # ---- FUNCTION: run ----
    def run(constrained: bool) -> float:
        t = PPOLagrangian(env.state_dim, cfg)
        rng = np.random.default_rng(cfg.seed)
        for _ in range(250):
            t.update(env.rollout(t.policy, cfg.batch_size, rng))
            if not constrained:
                t.lambdas[:] = 0.0
        return env.evaluate(t.policy, n=1500)["violation_rate"]

    assert run(True) <= run(False)


# ---- FUNCTION: test_learner_policy_is_a_distribution ----
def test_learner_policy_is_a_distribution():
    env = RemediationEnv()
    t = PPOLagrangian(env.state_dim, LearnConfig())
    s, _ = env.sample_state()
    p = t.policy.probs(s)
    assert abs(p.sum() - 1.0) < 1e-9 and (p >= 0).all()


# ---------------------------------------------------------------------------
# ---- FUNCTION: test_end_to_end_produces_no_violations ----
def test_end_to_end_produces_no_violations():
    orch = build_default_orchestrator(enable_learning=True, update_every=4)
    report = orch.run(scenario_isdp_corruption(seed=42))
    assert report.records, "the loop opened no incidents"
    assert report.constraint_violation_rate() == 0.0
    assert orch.actuator.audit.verify_chain()


# ---- FUNCTION: test_memory_grows_from_outcomes ----
def test_memory_grows_from_outcomes():
    orch = build_default_orchestrator(enable_learning=False)
    before = len(orch.memory)
    report = orch.run(scenario_isdp_corruption(seed=42))
    assert len(orch.memory) == before + len(report.records)


# ---------------------------------------------------------------------------
# ---- FUNCTION: main ----
def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {exc}")
        except Exception as exc:                      # noqa: BLE001
            failures += 1
            print(f"  ERROR {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests)-failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
