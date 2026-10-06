"""USE CASE 1 - MONITOR
==========================================================================
Scenario
    A firmware rollout is slowly shifting the baseline of an eUICC fleet:
    latency creeps up, RSRP drifts down, all night. At 04:10 one eUICC
    suffers a genuine ISD-P corruption.

    Static thresholds page someone for both. The question this use case
    answers: does the change-point detector absorb the drift while still
    catching the real fault, and is its false-alarm rate actually the
    alpha we configured?

What it demonstrates
    * EWMA baseline tracking a moving operating point
    * Mahalanobis scoring across correlated features, evaluated against the
      baseline as it stood BEFORE the sample arrived
    * chi-square trigger with an analytic false-positive budget, and the
      m-of-m persistence rule that trades detection delay for false alarms
    * the Bayes belief b_t handed downstream to REASON
    * O(d^2) per-sample latency against the <10 ms budget

Run:  python usecases/uc1_monitor_drift_vs_fault.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from esim_selfhealing.config import MonitorConfig
from esim_selfhealing.monitor import Monitor
from esim_selfhealing.schemas import FEATURE_NAMES
from esim_selfhealing.telemetry import healthy_stream, scenario_isdp_corruption

BAR = "=" * 74


# ---- FUNCTION: section ----
def section(title: str) -> None:
    print(f"\n{BAR}\n{title}\n{BAR}")


# ---- FUNCTION: false_alarm_rate ----
def false_alarm_rate(cfg: MonitorConfig, runs: int = 8) -> tuple[int, int]:
    fp = eligible = 0
    for seed in range(runs):
        mon = Monitor(cfg)
        readings = mon.run(healthy_stream(n=400, seed=100 + seed))
        post_warmup = [r for r in readings if not r.warming_up]
        fp += sum(1 for r in post_warmup if r.triggered)
        eligible += len(post_warmup)
    return fp, eligible


# ---- FUNCTION: main ----
def main() -> None:
    section("USE CASE 1 - MONITOR: firmware drift vs. a real ISD-P corruption")
    base = MonitorConfig()
    print(f"detector   : EWMA(lambda={base.lam}) + Mahalanobis, d={len(FEATURE_NAMES)}")
    print(f"threshold  : chi2_(d={len(FEATURE_NAMES)}, 1-alpha={1-base.alpha:.2f}) = "
          f"{Monitor(base).threshold:.2f}")

    # ---------------------------------------------------------------- part A
    section("A. Calibration on a purely healthy fleet - the persistence tradeoff")
    print(f"  {'rule':<28} {'false-alarm rate':>18} {'detection delay':>17}")
    print(f"  {'-'*28} {'-'*18} {'-'*17}")
    for m in (1, 2, 3):
        cfg = MonitorConfig(min_consecutive=m)
        fp, eligible = false_alarm_rate(cfg)
        mon = Monitor(cfg)
        readings = mon.run(scenario_isdp_corruption(seed=42))
        post = [i for i, r in enumerate(readings) if r.triggered and i >= 250]
        delay = f"{post[0]-250} sample(s)" if post else "MISSED"
        label = f"{m} consecutive exceedance" + ("s" if m > 1 else "")
        print(f"  {label:<28} {fp/eligible:>17.4f} {delay:>17}")
    print(f"\n  configured budget alpha = {base.alpha}")
    print("  A single exceedance already tracks alpha; two consecutive ones cut")
    print("  false alarms by more than an order of magnitude for one extra sample")
    print(f"  of delay. Default is min_consecutive={base.min_consecutive} for that reason.")

    # ---------------------------------------------------------------- part B
    section("B. Detection - slow drift present, real fault injected at sample 250")
    cfg = MonitorConfig()
    mon = Monitor(cfg)
    readings = mon.run(scenario_isdp_corruption(seed=42))

    pre = [r for r in readings[:250] if not r.warming_up]
    print(f"  post-warmup samples before the fault : {len(pre)}")
    print(f"  incidents opened during drift only   : {sum(1 for r in pre if r.triggered)}")
    print("  -> the EWMA absorbs the firmware-rollout drift; a static threshold on")
    print("     latency or RSRP would have paged on most of these samples\n")

    post = [(i, r) for i, r in enumerate(readings) if r.triggered and i >= 250]
    idx, first = post[0]
    print(f"  first incident at sample {idx} ({idx-250} sample(s) after onset)")
    print(f"    incident_id : {first.incident.incident_id}")
    print(f"    g_t         : {first.score:.2f}   threshold {first.threshold:.2f}")
    print(f"    CUSUM max   : {first.cusum_max:.2f} (univariate cross-check)")
    print(f"    detect lat  : {first.latency_ms:.3f} ms   (budget 10 ms)")

    print("\n  dominant deviations handed to REASON (z-scores vs prior baseline):")
    for name, z in mon.top_features(first.observation, k=3):
        print(f"    {name:<16} z = {z:+.2f}")

    print("\n  belief b_t over latent fault classes:")
    for cls, p in sorted(first.belief.items(), key=lambda kv: -kv[1]):
        print(f"    {cls:<22} {p:0.3f} {'#' * int(round(p * 40))}")
    print(f"\n  ground truth (simulator only) : {first.observation.ground_truth_fault}")
    print("  b_t is a prior, not a verdict - REASON confirms or overturns it with")
    print("  live tool calls in the next stage.")

    # ---------------------------------------------------------------- part C
    section("C. Score trajectory around the change point")
    scores = np.array([r.score for r in readings])
    for lo, hi, tag in ((240, 250, "pre-fault "), (250, 260, "post-fault")):
        w = scores[lo:hi]
        print(f"  {tag} samples {lo}-{hi-1}: g_t mean {w.mean():8.2f}  max {w.max():9.2f}")
    ratio = scores[250:260].mean() / max(scores[240:250].mean(), 1e-9)
    print(f"\n  post/pre mean score ratio : {ratio:.1f}x")
    print(f"  peak score                : {scores[250:300].max():.0f} "
          f"(threshold {Monitor(cfg).threshold:.1f})")

    # ---------------------------------------------------------------- part D
    section("D. Throughput")
    lats = np.array([r.latency_ms for r in readings])
    print(f"  mean per-sample latency : {lats.mean():.3f} ms")
    print(f"  p99 per-sample latency  : {np.percentile(lats, 99):.3f} ms")
    print(f"  budget                  : 10 ms")
    print(f"  implied per-core rate   : ~{1000/max(lats.mean(),1e-6):,.0f} samples/s")

    section("Takeaway")
    print("One statistic separates 'the fleet is drifting' from 'this device")
    print("broke', at a false-alarm rate you set analytically rather than tune by")
    print("hand. Everything downstream - the ReAct loop, the ranking, the safety")
    print("envelope - only ever runs on incidents that cleared this bar, which is")
    print("what keeps the expensive REASON stage off the critical path almost all")
    print("of the time.")


if __name__ == "__main__":
    main()
