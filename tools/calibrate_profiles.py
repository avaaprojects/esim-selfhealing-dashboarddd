#!/usr/bin/env python3
"""Solve each environment profile's reporting-damping factor, and report fit.

Why this exists
---------------
`EnvironmentProfile` carries statistics measured from the source datasets:
the within-run spread of RSRP, how often a modem holds its last reported
value, how large a step is when it does move. Those are measurements and are
not negotiable. But the generator does not consume `within_sd` directly - it
drives an AR(1) process and then *reports* it the way a modem does, holding
for most samples and stepping occasionally, and that reporting damps the
spread below the underlying process.

So the innovation has to be scaled up to compensate, and how much depends on
the profile's own hold rate. Using one shared factor across all five left
`urban_mobile` over-driven by 60%, wandering far enough to trip the forced
resync and emit 14-16 dB single-sample steps the real driving trace does not
contain - which the change-point detector then correctly flagged as faults.

This script solves the factor per profile by bisection, so the generated
reported spread lands on the measured one. Run it after changing any
measured statistic, and paste the printed `report_damping` values back into
`esim_selfhealing/real_profiles.py`.

Usage
-----
    python tools/calibrate_profiles.py              # report fit only
    python tools/calibrate_profiles.py --solve      # solve and print values
"""
from __future__ import annotations

import argparse
import dataclasses
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from esim_selfhealing import real_profiles as rp   # noqa: E402

RUNS = 60
SAMPLES = 300


def measure(env: rp.EnvironmentProfile, runs: int = RUNS, samples: int = SAMPLES) -> dict:
    """Generate `runs` independent runs and report what comes out."""
    within, levels, holds, steps, lat_med, spikes = [], [], [], [], [], []
    big_steps = 0
    total_steps = 0
    for r in range(runs):
        rng = np.random.default_rng(10_000 + r)
        ch = rp.RealisticChannel(env, rng)
        rows = [ch.step() for _ in range(samples)]
        rs = np.array([row["rsrp_dbm"] for row in rows])
        la = np.array([row["latency_ms"] for row in rows])
        within.append(rs.std())
        levels.append(rs.mean())
        d = np.diff(rs)
        holds.append(float((d == 0).mean()))
        moved = np.abs(d[d != 0])
        if moved.size:
            steps.append(float(np.median(moved)))
            total_steps += moved.size
            big_steps += int((moved > 12.0).sum())
        m = float(np.median(la))
        lat_med.append(m)
        spikes.append(100.0 * float((la > 3 * m).mean()))
    return {
        "within_sd": float(np.mean(within)),
        "level_sd": float(np.std(levels)),
        "hold_rate": float(np.mean(holds)),
        "step_median": float(np.mean(steps)) if steps else 0.0,
        "latency_median": float(np.mean(lat_med)),
        "spike_pct": float(np.mean(spikes)),
        "big_step_pct": 100.0 * big_steps / max(1, total_steps),
    }


def solve_damping(env: rp.EnvironmentProfile, tol: float = 0.01,
                  runs: int = 30) -> float:
    """Bisect the damping factor so the reported spread hits `within_sd`.

    Monotone in the right direction: a larger damping divisor means a
    smaller innovation, so a smaller reported spread.
    """
    lo, hi = 0.20, 2.50
    best = env.report_damping
    for _ in range(22):
        mid = 0.5 * (lo + hi)
        got = measure(dataclasses.replace(env, report_damping=mid), runs=runs)["within_sd"]
        best = mid
        if abs(got - env.within_sd) / env.within_sd < tol:
            break
        if got > env.within_sd:
            lo = mid          # too wide -> damp harder
        else:
            hi = mid
    return best


def solve_profile(env: rp.EnvironmentProfile) -> tuple[float, float]:
    """Solve (within_lag1, report_damping) against both measured targets.

    Two measured quantities constrain the RSRP model and they constrain
    different things, so fitting one parameter to one of them leaves the
    other free to come out wrong:

    * `within_sd` is the spread over a whole run. The damping factor sets it.
    * `jump_median_db` is how far the value moves between one report and the
      next. That is governed by how far the underlying process wanders during
      a hold, which is the autocorrelation `within_lag1` - a process that
      wanders quickly covers more ground between reports even when its total
      spread is unchanged.

    Fitting damping alone reproduced the run-level spread and over-predicted
    the step size on the rural profiles by a factor of two, which says their
    real within-run variation is mostly slow wander with small steps on top,
    not fast noise. So: an outer bisection on `within_lag1` against the step
    median, with the damping re-solved inside it to hold the spread on target.
    """
    # Capped below 1: at 0.99+ the process is effectively a random walk
    # within the run and drifts off the level drawn for it, which inflates
    # the between-run spread - trading a measured quantity for a derived
    # one. The step median is reported as a cross-check, not forced.
    lo, hi = 0.60, 0.980
    lag, damping = env.within_lag1, env.report_damping
    for _ in range(9):
        lag = 0.5 * (lo + hi)
        trial = dataclasses.replace(env, within_lag1=lag)
        damping = solve_damping(trial, runs=24)
        got = measure(dataclasses.replace(trial, report_damping=damping),
                      runs=24)["step_median"]
        if abs(got - env.jump_median_db) / env.jump_median_db < 0.08:
            break
        if got > env.jump_median_db:
            lo = lag          # steps too large -> wander more slowly
        else:
            hi = lag
    return lag, damping


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--solve", action="store_true",
                    help="solve report_damping per profile and print the values")
    args = ap.parse_args()

    if args.solve:
        print("solving (within_lag1, report_damping) against the measured")
        print("within-run spread and step median\n")
        solved = {}
        for key, env in rp.PROFILES.items():
            lag, damp = solve_profile(env)
            solved[key] = (lag, damp)
            print(f"   {key:<20} within_lag1={lag:.3f} (was {env.within_lag1:.3f})   "
                  f"report_damping={damp:.3f} (was {env.report_damping:.3f})")
        print("\npaste into esim_selfhealing/real_profiles.py:\n")
        for key, (lag, damp) in solved.items():
            print(f"    {key}: within_lag1={lag:.3f}, report_damping={damp:.3f},")
        print()
        rp.PROFILES = {k: dataclasses.replace(v, within_lag1=solved[k][0],
                                              report_damping=solved[k][1])
                       for k, v in rp.PROFILES.items()}

    print(f"{'profile':<20} {'within sd':>18} {'between sd':>18} {'hold':>16} "
          f"{'step med':>9} {'lat med':>17} {'spike %':>15} {'>12dB':>7}")
    ok = True
    for key, env in rp.PROFILES.items():
        m = measure(env)
        within_off = abs(m["within_sd"] - env.within_sd) / env.within_sd
        level_off = abs(m["level_sd"] - env.level_sd) / env.level_sd
        if within_off > 0.12 or level_off > 0.15:
            ok = False
        print(f"{key:<20} "
              f"{m['within_sd']:7.2f} (t {env.within_sd:4.1f}) "
              f"{m['level_sd']:8.2f} (t {env.level_sd:4.1f}) "
              f"{m['hold_rate']:7.3f} (t {env.hold_rate:.3f}) "
              f"{m['step_median']:9.2f} "
              f"{m['latency_median']:8.1f} (t {env.latency_median_ms:5.1f}) "
              f"{m['spike_pct']:7.2f} (t {100*env.spike_rate:4.2f}) "
              f"{m['big_step_pct']:6.2f}%")

    print("\n'>12dB' is the share of reported steps larger than 12 dB in one sample.")
    print("A real 1 Hz trace almost never does that; a generator that does will be")
    print("flagged by any honest change-point detector, and the incident is an")
    print("artefact rather than a fault.")
    print("\nFIT OK" if ok else "\nFIT OUT OF TOLERANCE - rerun with --solve")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
