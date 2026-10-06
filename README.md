# Self-Healing eSIM with Agentic AI — reference implementation

A runnable ReAct–MAPE-K self-healing loop for eUICC / SM-DP+ infrastructure.
One module per stage of the deck, one use case per stage, and an orchestrator
that runs Algorithm 1 end to end.

Everything runs offline with NumPy only. Optional backends activate when
available (liboqs for real ML-DSA signing, the Anthropic API for the reasoning
policy) — the loop's behaviour and safety properties do not depend on either.

```
pip install numpy
python usecases/uc6_end_to_end_night_shift.py
python tests/test_smoke.py
```

## Layout

```
esim_selfhealing/
  schemas.py        domain types: Observation, Incident, Diagnosis, Candidate,
                    Command, AdmissibilityReport, Transition
  config.py         every tunable constant: thresholds, weights, envelope
                    parameters, latency budgets
  telemetry.py      synthetic RAN + core + eUICC telemetry with injectable faults

  monitor.py        STAGE 1  EWMA baseline, Mahalanobis change-point detection,
                             chi-square trigger, CUSUM cross-check, Bayes filter
  reason.py         STAGE 2  bounded ReAct loop, read-only tool registry,
                             RAG grounding, pluggable LLM backend
  plan.py           STAGE 3  specialist proposers + constrained utility ranking
  act.py            STAGE 4  command building, PQC signing, envelope gating,
                             dispatch, rollback, hash-chained audit log
  learn.py          STAGE 5  PPO-Lagrangian, dual ascent, replay buffer,
                             offline training environment

  safety.py         Sigma    the safety envelope and the routing rule
  crypto_pqc.py              ML-DSA signing (liboqs) or an HMAC-SHA3 stub
  memory_store.py   D        incident memory, encoder, cosine top-N retrieval
  rsp_api.py                 simulated SM-DP+/SM-SR endpoints and eUICC state
  orchestrator.py            Algorithm 1, wired end to end, plus KPI reporting

usecases/           one runnable scenario per component
tests/test_smoke.py invariants that must survive a refactor
```

## How the code maps to the deck

| Deck | Module | What implements it |
|---|---|---|
| Slide 5 — constrained POMDP | `schemas.py`, `monitor.py` | action space `ActionType`, observation vector `FEATURE_NAMES`, belief update `Monitor._bayes_update` |
| Slide 7 — MONITOR | `monitor.py` | `Monitor.update`: EWMA, Mahalanobis, `chi2_{d,1-alpha}`, CUSUM |
| Slide 8 — REASON | `reason.py` | `Reasoner.diagnose`, `ToolRegistry`, `IncidentMemory.retrieve` |
| Slide 9 — PLAN | `plan.py` | `Planner.rank`, `NetworkAgent` / `SecurityAgent` / `SLAOpsAgent` |
| Slide 10 — ACT | `act.py` | `Actuator.execute`, `AuditLog` |
| Slide 11 — LEARN | `learn.py` | `PPOLagrangian.update`, dual ascent, `plan_weights` |
| Slide 12 — envelope Σ | `safety.py` | `SafetyEnvelope.admissible`, `.route`, `.constraint_costs` |
| Slide 13 — Algorithm 1 | `orchestrator.py` | `Orchestrator.run`, `.handle_incident` |
| Slide 15 — complexity | `config.LatencyBudget` | measured per stage in use case 6 |
| Slide 16 — KPIs | `orchestrator.RunReport` | auto-remediation rate, violation rate, rollback rate, MTTR |

## Use cases

Each is standalone and prints a narrated run. No arguments.

| Script | Component | The question it answers |
|---|---|---|
| `uc1_monitor_drift_vs_fault.py` | MONITOR | Does the detector absorb a firmware-rollout drift while still catching a real ISD-P corruption, at the false-alarm rate we configured? |
| `uc2_reason_react_diagnosis.py` | REASON | Two incidents with identical alert text and different root causes — can the ReAct loop separate them, and what happens on cold start? |
| `uc3_plan_constrained_ranking.py` | PLAN | Same diagnosis on one handset and on a 120-device shared profile — does the constraint set catch the difference without a rule rewrite? |
| `uc4_act_safety_gated_dispatch.py` | ACT | Four remediations at the gate: one dispatches, three are refused for three different reasons, and the audit log detects an edit. |
| `uc5_learn_ppo_lagrangian.py` | LEARN | The highest-reward policy is the unsafe one. Does the Lagrangian constraint actually stop the learner finding it? |
| `uc6_end_to_end_night_shift.py` | all five | An unattended shift across five devices — what does the operator find in the morning? |

## Design decisions worth knowing about

**Score before you update.** `Monitor.update` computes `g_t` against the
baseline as it stood *before* the sample arrived. Folding `x_t` into `mu` and
`Sigma` first lets a large jump inflate its own reference covariance and mask
itself — the detector goes quiet exactly when it should fire.

**The persistence rule is doing real work.** A single chi-square exceedance
tracks `alpha` correctly but still fires a few times per thousand samples.
Requiring two consecutive exceedances cuts false alarms by more than an order
of magnitude and costs one sample of detection delay. `min_consecutive=2` is the
default for that reason; use case 1 prints the tradeoff.

**Retrieval embeds the fault signature, not the operating point.** The encoder
standardises telemetry against a fixed nominal operating point and normalises,
so what survives is the *direction* of the deviation. Encoding raw values makes
every record look alike (RSRP near −92 dBm dominates whether the device is
healthy or not); squashing z-scores with `tanh` or clipping is just as bad,
because it saturates the large deviations that carry the signal.

**REASON cannot act.** Every tool in the registry is read-only. All state
change happens in ACT, behind the envelope. This is enforced by construction and
pinned by `test_reason_is_read_only_and_bounded`.

**Constraints filter, then utility orders.** An action violating `c_SLA`,
`c_sec`, `Risk` or `B_max` is removed from the candidate set before utilities
are compared, so a high-utility unsafe action cannot win by scoring well. PLAN
duplicates the envelope's `B_max` deliberately: it can then hand ACT an empty
feasible set and let the orchestrator escalate, rather than proposing something
the gate will refuse.

**The inverse is built before the command is signed.** `Actuator.execute`
constructs `a^-1` first; an action with no constructible inverse is inadmissible
by definition. That is what makes "rollback is checked before dispatch, not
improvised after" true in code rather than in prose.

**Each envelope clause is reported separately.** `AdmissibilityReport` keeps the
four verdicts apart so a rejection always names the guarantee it failed.
"Denied" is not an acceptable audit record on telecom-critical infrastructure.

**Peak lambda, not final lambda, feeds back to PLAN.** Dual ascent drives the
multipliers back toward zero once the policy stops violating, so a converged
safe policy has a near-zero multiplier that carries no information. The peak
records how expensive the constraint was to satisfy — forget it and PLAN drifts
back to the weights that produced the violations.

## What this implementation does not do

* `StubSigner` is HMAC-SHA3-256, not post-quantum. It exists so the code runs
  anywhere. `signer.is_post_quantum` reports which backend is live and the ACT
  use case prints it, so a demo cannot be mistaken for a hardened deployment.
  Install `oqs` for real FIPS 204 ML-DSA-65.
* `rsp_api.py` is a simulator. Efficacy rates, latencies and eUICC state
  transitions are plausible engineering defaults, not measured field data.
* The `RemediationEnv` used by LEARN is a contextual bandit, not a full POMDP
  rollout. It shares the efficacy and action-profile tables with the simulator,
  so the policy is not learning a different problem — but sim-to-real transfer
  against live RSP infrastructure remains the open question the deck names.
* `HeuristicReActLLM` is a deterministic rule-based controller, not a language
  model. It produces a genuine ReAct trace (each tool call depends on prior
  observations) and keeps the reference runs reproducible. Swap in
  `AnthropicReActLLM` for the real thing.
* The Monitor's observation model and the Reason stage's evidence weights are
  hand-specified. In deployment both are fitted from labelled incident history.

## Configuration

Everything is in `config.py`. The values most worth revisiting for a pilot:

```python
MonitorConfig.alpha            # false-alarm budget -> chi-square threshold
MonitorConfig.min_consecutive  # persistence rule; trades delay for false alarms
ReasonConfig.k_max             # bounds the ReAct loop, and so the latency tail
PlanConfig.w1, w2, w3          # utility weights; LEARN revises these
EnvelopeConfig.b_max           # devices an autonomous action may touch
EnvelopeConfig.rho_max         # residual risk bound
EnvelopeConfig.rho_human       # above this, escalate rather than queue
LearnConfig.d_limits           # d_i, the constraint budgets
```
