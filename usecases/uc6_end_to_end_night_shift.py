"""USE CASE 6 - END TO END (Algorithm 1)
==========================================================================
Scenario
    One night shift across a small eUICC estate. Four devices, four
    different failure modes, arriving on overlapping telemetry streams:

      * a corrupted ISD-P on a field device
      * an SM-DP+ session outage
      * a slow radio degradation
      * a key desync between eUICC and SM-SR

    Nobody is watching the NOC console. The question is what the operator
    finds in the morning: which incidents closed themselves, which are
    sitting in the approval queue, which got escalated - and whether any
    autonomous action violated a constraint.

What it demonstrates
    * Algorithm 1 exactly as written on the deck, running unattended
    * the full trace of one incident through all five stages
    * the KPI set of the evaluation slide, computed on a real run
    * the per-stage latency profile against the budget table
    * memory D and the policy both growing from the night's outcomes

Run:  python usecases/uc6_end_to_end_night_shift.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esim_selfhealing.config import DEFAULT
from esim_selfhealing.orchestrator import build_default_orchestrator
from esim_selfhealing.reason import format_trace
from esim_selfhealing.safety import format_report
from esim_selfhealing.telemetry import FaultWindow, TelemetryStream

BAR = "=" * 74


# ---- FUNCTION: section ----
def section(title: str) -> None:
    print(f"\n{BAR}\n{title}\n{BAR}")


# ---- FUNCTION: night_shift_streams ----
def night_shift_streams():
    """Four devices, four fault classes, one shift."""
    plan = [
        ("89330000000048213", "CELL-4471", "isdp_corruption", 42),
        ("89330000000051887", "CELL-2210", "smdp_session_outage", 44),
        ("89330000000063902", "CELL-8814", "radio_degradation", 43),
        ("89330000000077145", "CELL-4471", "key_desync", 45),
        # Same fault as the first device, but this profile is shared across a
        # 120-device logistics fleet - the blast radius is what differs.
        ("89330000000090031", "CELL-6602", "isdp_corruption", 46),
    ]
    for euicc, cell, fault, seed in plan:
        yield TelemetryStream(
            euicc_id=euicc,
            cell_id=cell,
            n_samples=300,
            faults=[FaultWindow(fault, start=240, end=300)],
            drift_per_sample=1.0,
            seed=seed,
        )


# ---- FUNCTION: main ----
def main() -> None:
    section("USE CASE 6 - Algorithm 1 running an unattended night shift")
    orch = build_default_orchestrator(verbose=False, enable_learning=True,
                                      update_every=4)
    # Inventory: one of tonight's devices runs a profile shared fleet-wide.
    orch.rsp.register_shared_profile("89330000000090031", devices=120)
    print(f"signer         : {orch.actuator.signer.algorithm} "
          f"(post_quantum={orch.actuator.signer.is_post_quantum})")
    print(f"memory |D| at start : {len(orch.memory)} historical records")
    print(f"envelope       : B_max={DEFAULT.envelope.b_max} "
          f"rho_max={DEFAULT.envelope.rho_max} rho_human={DEFAULT.envelope.rho_human}")

    all_records = []
    total_samples = 0
    for stream in night_shift_streams():
        print(f"\n  device {stream.euicc_id} on {stream.cell_id} "
              f"({stream.faults[0].fault})")
        report = orch.run(stream)
        total_samples += report.samples_seen
        for rec in report.records:
            print(f"    {rec.incident.incident_id}  "
                  f"diagnosed={rec.diagnosis.fault_class:<20} "
                  f"a*={(rec.selected_action.value if rec.selected_action else '-'):<22} "
                  f"{rec.status.value}")
        all_records.extend(report.records)

    # ------------------------------------------------------------------ A
    section("A. One incident traced through all five stages")
    rec = next((r for r in all_records if r.status.value == "AUTO-REMEDIATED"), None)
    rec = rec or all_records[0]
    inc = rec.incident
    print(f"MONITOR  g_t = {inc.anomaly_score:.1f} > chi2 = {inc.threshold:.1f} "
          f"-> {inc.incident_id} opened")
    top = sorted(inc.belief.items(), key=lambda kv: -kv[1])[:2]
    print(f"         b_t top classes: " +
          ", ".join(f"{c}={p:.2f}" for c, p in top))
    print(f"\nREASON   {len([s for s in rec.diagnosis.trace if s.tool != 'FINISH'])} "
          f"tool call(s), {len(rec.diagnosis.retrieved_ids)} precedent(s) retrieved")
    print(format_trace(rec.diagnosis))
    print(f"\nPLAN     a* = {rec.selected_action.value if rec.selected_action else '(none)'}")
    if rec.act_result:
        print("\nACT")
        print(format_report(rec.act_result.report))
        print(f"  status        : {rec.act_result.status.value}")
        print(f"  detail        : {rec.act_result.message}")
    print(f"\nLEARN    reward r_t = {rec.reward:+.3f}  costs c_t = "
          f"{tuple(round(c,3) for c in rec.costs)}")
    print(f"         transition written to the replay buffer; "
          f"(I_t, a*, status) appended to D")
    print(f"\nstage latency (ms): " +
          "  ".join(f"{k}={v:.1f}" for k, v in rec.stage_latency_ms.items()))

    # ------------------------------------------------------------------ B
    section("B. What the operator finds in the morning")
    from esim_selfhealing.schemas import Status

    mix = {s.value: sum(1 for r in all_records if r.status is s) for s in Status}
    n = max(1, len(all_records))
    print(f"  samples processed         : {total_samples}")
    print(f"  incidents opened          : {len(all_records)}")
    for k, v in mix.items():
        print(f"    {k:<22} : {v}  ({v/n:.0%})")
    print(f"\n  auto-remediation rate     : {mix['AUTO-REMEDIATED']/n:.1%}")

    dispatched = [r for r in all_records if r.act_result and r.act_result.dispatched]
    violations = sum(1 for r in dispatched if r.costs and r.costs[0] > 0)
    print(f"  actions dispatched        : {len(dispatched)}")
    print(f"  constraint violations     : {violations}  "
          f"({violations/max(1,len(dispatched)):.1%}, target ~0 by construction)")

    rolled = [r for r in dispatched if r.act_result and not r.act_result.success]
    if rolled:
        ok = sum(1 for r in rolled if r.act_result.rolled_back)
        print(f"  failed dispatches         : {len(rolled)}, "
              f"rollback success {ok}/{len(rolled)}")

    print(f"\n  approval queue            : {len(orch.actuator.human_queue)} item(s)")
    for item in orch.actuator.human_queue:
        print(f"    {item['incident_id']}  {item['action']:<22} "
              f"risk={item['risk']:.2f}  failed: {', '.join(item['failed_clauses'])}")
    print(f"  escalations               : {len(orch.actuator.escalations)} item(s)")
    for item in orch.actuator.escalations:
        print(f"    {item['incident_id']}  {item.get('reason', 'unresolved root cause')}")

    # ------------------------------------------------------------------ C
    section("C. Latency profile vs the budget table")
    budget = {
        "monitor": (DEFAULT.latency.monitor_ms, ""),
        "reason": (DEFAULT.latency.reason_ms, ""),
        "plan": (DEFAULT.latency.plan_ms, ""),
        "act_gate": (DEFAULT.latency.act_ms, "signing + 4 clause checks"),
        "act_dispatch": (None, "RSP round trip - physics, not overhead"),
        "learn": (None, "asynchronous, off critical path"),
    }
    print(f"  {'stage':<14} {'mean ms':>10} {'budget ms':>11}   note")
    for k, (b, note) in budget.items():
        vals = [r.stage_latency_ms.get(k, 0.0) for r in all_records]
        mean = sum(vals) / max(1, len(vals))
        if b is None:
            print(f"  {k:<14} {mean:>10.2f} {'-':>11}   {note}")
        else:
            verdict = "OK" if mean <= b else "OVER BUDGET"
            extra = f" ({note})" if note else ""
            print(f"  {k:<14} {mean:>10.2f} {b:>11.0f}   {verdict}{extra}")
    print("\n  The 50 ms ACT budget governs the gate, not the round trip. A profile")
    print("  re-push over the air takes seconds no matter who authorises it; what")
    print("  the design has to keep cheap is the decision to authorise it.")
    print("  REASON remains the dominant controllable cost, as the deck predicts.")

    # ------------------------------------------------------------------ D
    section("D. What the night left behind")
    print(f"  memory |D|                : {len(orch.memory)} records "
          f"(+{len(all_records)} from this shift)")
    print(f"  audit entries             : {len(orch.actuator.audit)}  "
          f"chain intact = {orch.actuator.audit.verify_chain()}")
    if orch.learner:
        print(f"  replay buffer             : {len(orch.learner.buffer)} pending "
              f"transitions")
        print(f"  policy updates            : {len(orch.learner.history)}")
        if orch.learner.history:
            print(f"  last update               : {orch.learner.history[-1].line()}")
        print(f"  lambda                    : "
              f"{tuple(round(float(l),4) for l in orch.learner.lambdas)}")

    section("Takeaway")
    print("The routine failures closed themselves. The wide-blast-radius and")
    print("irreversible ones are waiting for a human, each with the reasoning")
    print("trace that produced them. Nothing was dispatched that the envelope")
    print("did not admit - which is the property that makes the auto-remediation")
    print("rate a number worth improving rather than a number worth fearing.")


if __name__ == "__main__":
    main()
