"""USE CASE 3 - PLAN
==========================================================================
Scenario
    The same diagnosis - corrupted ISD-P - arrives twice. First on a
    single field device. Then on a device whose profile is shared by a
    120-device logistics fleet.

    Nothing about the diagnosis changed. The right action did: what is a
    routine re-push on one device becomes a fleet-wide operation on the
    second, and the constraint set must catch that without anyone
    rewriting a rule.

What it demonstrates
    * specialist agents (network / security / SLA-ops) proposing candidates
    * P_success(a) estimated from retrieved precedent, not hard-coded
    * U(a) = w1 P_success - w2 Cost - w3 BlastRadius
    * hard constraint filtering BEFORE ranking, so an infeasible action can
      never win on utility
    * the tie-break rule, and what happens when LEARN raises blast-aversion

Run:  python usecases/uc3_plan_constrained_ranking.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esim_selfhealing.config import DEFAULT
from esim_selfhealing.memory_store import seed_memory
from esim_selfhealing.monitor import Monitor
from esim_selfhealing.plan import Planner
from esim_selfhealing.reason import Reasoner, ToolRegistry
from esim_selfhealing.rsp_api import RSPClient
from esim_selfhealing.telemetry import scenario_isdp_corruption

BAR = "=" * 74


# ---- FUNCTION: section ----
def section(title: str) -> None:
    print(f"\n{BAR}\n{title}\n{BAR}")


# ---- FUNCTION: build_diagnosis ----
def build_diagnosis(memory):
    monitor = Monitor(DEFAULT.monitor)
    rsp = RSPClient(seed=5)
    tools = ToolRegistry(rsp, memory, DEFAULT.reason)
    reasoner = Reasoner(tools, memory, cfg=DEFAULT.reason)
    for obs in scenario_isdp_corruption(seed=42):
        reading = monitor.update(obs)
        if reading.triggered:
            rsp.inject_fault(obs.euicc_id, obs.ground_truth_fault)
            return reasoner.diagnose(reading.incident,
                                     top_features=monitor.top_features(obs)), reading.incident
    raise RuntimeError("no incident opened")


# ---- FUNCTION: show ----
def show(result, header: str) -> None:
    print(f"\n{header}")
    print(f"  weights (w1,w2,w3) = {tuple(round(w,3) for w in result.weights)}")
    print(f"  ranking latency    = {result.latency_ms:.3f} ms  (budget 5 ms)")
    print("\n  FEASIBLE SET (ranked by U(a)):")
    if result.ranked:
        for i, c in enumerate(result.ranked, 1):
            mark = "  <-- a*" if i == 1 else ""
            print(f"    {i}. {c.summary()}  [{c.proposer}]{mark}")
    else:
        print("    (empty - every candidate violated a constraint)")
    if result.infeasible:
        print("\n  FILTERED OUT by the constraint set:")
        for c in result.infeasible:
            print(f"    -  {c.action.value:<22} U={c.utility:+.3f} would have ranked "
                  f"{'FIRST' if c.utility > (result.ranked[0].utility if result.ranked else -9) else 'lower'}")
            for v in c.violated:
                print(f"         violated: {v}")


# ---- FUNCTION: main ----
def main() -> None:
    memory = seed_memory(dim=DEFAULT.reason.embed_dim)
    diagnosis, incident = build_diagnosis(memory)

    section("USE CASE 3 - PLAN: same diagnosis, two blast radii")
    print(f"incident    : {incident.incident_id}")
    print(f"diagnosis   : fault_class={diagnosis.fault_class} conf={diagnosis.confidence:.2f}")
    print(f"A_cand      : {[a.value for a in diagnosis.candidate_actions]}")

    neighbours = memory.retrieve(
        incident.observation, top_n=DEFAULT.reason.top_n,
        min_similarity=DEFAULT.reason.min_similarity, hint=diagnosis.fault_class,
    )
    print(f"\nprecedent N(e_t) used for P_success(a):")
    for rec, sim in neighbours:
        print(f"    {rec.record_id}  sim={sim:.3f}  action={rec.action:<20} "
              f"success={rec.success}")
    if not neighbours:
        print("    (none above the similarity floor - priors will be used)")

    # ------------------------------------------------------------------ A
    section("A. Single field device (fleet_size = 1)")
    planner = Planner(memory, DEFAULT.plan, fleet_size=1)
    single = planner.rank(diagnosis, neighbours)
    show(single, "constraints: c_SLA<=30s, c_sec<=0.50, Risk<=0.30, B_max=1")
    print(f"\n  selected a* = {single.selected.action.value if single.selected else '(none)'}")
    print("  -> routine, single-device, reversible. This is the autonomous path.")

    # ------------------------------------------------------------------ B
    section("B. Shared profile across a 120-device logistics fleet")
    fleet_planner = Planner(memory, DEFAULT.plan, fleet_size=120)
    fleet = fleet_planner.rank(diagnosis, neighbours)
    show(fleet, "same constraints, but SLA-ops now reports blast radius from inventory")
    print(f"\n  selected a* = {fleet.selected.action.value if fleet.selected else '(none)'}")
    print("  -> every candidate would touch 120 devices, so the feasible set is")
    print("     empty and the orchestrator escalates instead of acting. The")
    print("     diagnosis never changed; only the inventory-derived blast radius")
    print("     did, and no rule was rewritten to get this outcome.")

    # ------------------------------------------------------------------ C
    section("C. Weight sensitivity and the tie-break rule")
    print("LEARN maps its Lagrange multipliers back onto (w1,w2,w3). Re-ranking the")
    print("single-device candidate set under a learner that has become much more")
    print("cost- and blast-averse:\n")
    for label, weights in (
        ("baseline            ", (DEFAULT.plan.w1, DEFAULT.plan.w2, DEFAULT.plan.w3)),
        ("lambda_1 binding    ", (DEFAULT.plan.w1, DEFAULT.plan.w2 * 1.4, DEFAULT.plan.w3)),
        ("both lambdas binding", (DEFAULT.plan.w1, DEFAULT.plan.w2 * 1.4, DEFAULT.plan.w3 * 3.0)),
    ):
        pl = Planner(memory, DEFAULT.plan, fleet_size=1)
        pl.set_weights(*weights)
        res = pl.rank(diagnosis, neighbours)
        if not res.ranked:
            print(f"  {label} -> empty feasible set")
            continue
        top = res.ranked[0]
        margin = top.utility - (res.ranked[1].utility if len(res.ranked) > 1 else top.utility)
        print(f"  {label} w={tuple(round(w,2) for w in weights)}  "
              f"a* = {top.action.value:<16} U={top.utility:+.3f}  margin={margin:+.3f}")
    print("\n  The ordering is stable here: re-push leads the next candidate by a")
    print("  wide enough margin to absorb the weight change. Weights decide the")
    print("  outcome only when candidates are close - and when they are close")
    print("  enough to tie, the rule is explicit rather than arbitrary:\n")

    from esim_selfhealing.schemas import ActionType, Candidate
    twins = [
        Candidate(action=ActionType.PROFILE_REPUSH, p_success=0.70, cost=0.20,
                  blast_radius=1, risk=0.12, sla_cost=6.0, sec_cost=0.10,
                  utility=0.60, proposer="security"),
        Candidate(action=ActionType.BEARER_FAILOVER, p_success=0.72, cost=0.24,
                  blast_radius=3, risk=0.18, sla_cost=8.0, sec_cost=0.20,
                  utility=0.60, proposer="network"),
    ]
    twins.sort(key=lambda c: (-c.utility, c.blast_radius, c.cost))
    print(f"    two candidates at U = {twins[0].utility:+.3f} exactly")
    for c in twins:
        print(f"      {c.action.value:<20} blast={c.blast_radius}")
    print(f"    tie-break selects : {twins[0].action.value} (lower blast radius)")

    section("Takeaway")
    print("The constraint set is a filter, not a penalty term. An action that")
    print("violates c_SLA, c_sec or rho_max is removed from consideration before")
    print("utilities are compared - so a high-utility unsafe action cannot win by")
    print("scoring well. Utility only orders what is already admissible in")
    print("principle; the envelope in stage 4 then checks it in fact.")


if __name__ == "__main__":
    main()
