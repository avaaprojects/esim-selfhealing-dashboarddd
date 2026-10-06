"""USE CASE 2 - REASON
==========================================================================
Scenario
    Two incidents open within a minute of each other. Both look like "AKA
    failures with OTA errors" in the alert text. One is a corrupted ISD-P
    on a single device; the other is an SM-DP+ session outage that will
    resolve itself and must NOT trigger a profile re-push.

    Static rules cannot separate them from telemetry alone - the
    difference only appears once you query live eUICC state. That is
    exactly what the ReAct loop does: think, call a read-only tool, look
    at what came back, decide the next call.

What it demonstrates
    * a real interleaved Thought -> Action -> Observation trace H_k
    * RAG grounding: diagnosis conditioned on retrieved precedent N(e_t)
    * tool calls that are strictly read-only (no state change in REASON)
    * K_max bounding the loop, and the cold-start case where D is empty
    * Parse(H_k) -> (d_t, A_cand) handed to PLAN

Run:  python usecases/uc2_reason_react_diagnosis.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esim_selfhealing.config import DEFAULT
from esim_selfhealing.memory_store import IncidentMemory, seed_memory
from esim_selfhealing.monitor import Monitor
from esim_selfhealing.reason import Reasoner, ToolRegistry, format_trace
from esim_selfhealing.rsp_api import RSPClient
from esim_selfhealing.telemetry import scenario_isdp_corruption, scenario_smdp_outage

BAR = "=" * 74


# ---- FUNCTION: section ----
def section(title: str) -> None:
    print(f"\n{BAR}\n{title}\n{BAR}")


# ---- FUNCTION: first_incident ----
def first_incident(stream, monitor: Monitor):
    for obs in stream:
        reading = monitor.update(obs)
        if reading.triggered:
            return reading
    raise RuntimeError("no incident opened in this stream")


# ---- FUNCTION: diagnose_scenario ----
def diagnose_scenario(label: str, stream, memory: IncidentMemory, seed: int):
    section(label)
    monitor = Monitor(DEFAULT.monitor)
    rsp = RSPClient(seed=seed)
    tools = ToolRegistry(rsp, memory, DEFAULT.reason)
    reasoner = Reasoner(tools, memory, cfg=DEFAULT.reason)

    reading = first_incident(stream, monitor)
    incident = reading.incident
    obs = incident.observation

    # Put the simulated RSP into the state the injected fault implies, so the
    # read-only tools return the same reality the telemetry is showing.
    rsp.inject_fault(obs.euicc_id, obs.ground_truth_fault)

    print(f"incident      : {incident.incident_id} on eUICC {obs.euicc_id}")
    print(f"g_t           : {incident.anomaly_score:.1f} (threshold {incident.threshold:.1f})")
    print("alert text    : AKA failures with OTA errors  <- identical for both incidents")
    print(f"memory |D|    : {len(memory)} records\n")

    diag = reasoner.diagnose(incident, top_features=monitor.top_features(obs))

    print(format_trace(diag))
    print(f"\nParse(H_k):")
    print(f"  d_t            : {diag.text}")
    print(f"  fault_class    : {diag.fault_class}   (ground truth: {obs.ground_truth_fault})")
    print(f"  confidence     : {diag.confidence:.2f}")
    print(f"  A_cand         : {[a.value for a in diag.candidate_actions]}")
    print(f"  precedents     : {diag.retrieved_ids or '(none)'}")
    print(f"  policy calls   : {diag.llm_calls} (K_max = {DEFAULT.reason.k_max})")
    print(f"  tools invoked  : {[n for n, _ in tools.call_log]}")
    print("  state mutated  : NO - every tool in this stage is read-only")
    return diag


# ---- FUNCTION: main ----
def main() -> None:
    memory = seed_memory(dim=DEFAULT.reason.embed_dim)

    warm = diagnose_scenario(
        "A. Incident #1 - looks like a profile problem, and is one",
        scenario_isdp_corruption(seed=42), memory, seed=5,
    )
    diagnose_scenario(
        "B. Incident #2 - same alert text, different root cause",
        scenario_smdp_outage(seed=44), memory, seed=6,
    )

    # ------------------------------------------------------------------ C
    section("C. Cold start - a novel failure class with an empty memory D")
    empty = IncidentMemory(dim=DEFAULT.reason.embed_dim)
    monitor = Monitor(DEFAULT.monitor)
    rsp = RSPClient(seed=7)
    tools = ToolRegistry(rsp, empty, DEFAULT.reason)
    reasoner = Reasoner(tools, empty, cfg=DEFAULT.reason)

    reading = first_incident(scenario_isdp_corruption(seed=51), monitor)
    rsp.inject_fault(reading.observation.euicc_id, reading.observation.ground_truth_fault)
    diag = reasoner.diagnose(reading.incident, top_features=monitor.top_features(reading.observation))

    retrieval_obs = next(s.observation for s in diag.trace
                         if s.tool == "retrieve_similar_incidents")
    print(f"retrieval returned : {retrieval_obs}")
    print(f"fault_class        : {diag.fault_class}")
    print(f"confidence         : {diag.confidence:.2f}   "
          f"vs {warm.confidence:.2f} for the same fault class with |D| = {len(memory)}")
    print("\nWith no precedent the loop still reaches a diagnosis from live tool")
    print("evidence, but at lower confidence - which PLAN converts into higher")
    print("residual risk, which the envelope then converts into human review.")
    print("That chain is the intended behaviour on novel failure classes, not a")
    print("failure of the loop.")

    section("Takeaway")
    print("Same alert text, two root causes, two different candidate sets - and")
    print("the reasoning that produced each is a literal transcript, not a")
    print("post-hoc rationalisation. H_k is what gets hashed into the audit log")
    print("in stage 4.")


if __name__ == "__main__":
    main()
