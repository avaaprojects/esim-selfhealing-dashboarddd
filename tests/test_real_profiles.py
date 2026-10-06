"""The generated telemetry still matches the datasets it was calibrated from.

    python tests/test_real_profiles.py

`esim_selfhealing/real_profiles.py` carries statistics measured from three
public datasets. The generator does not consume most of them directly - it
drives an AR(1) process and reports it the way a modem does - so the only
thing that establishes the model is still faithful is generating from it and
measuring the result back.

That is not pedantry. Every realism bug found in this project showed up here
first and nowhere else:

  * the roaming profile was over-driven by 60% and emitted 14-16 dB steps in
    a single second, which the change-point detector correctly flagged as
    faults - a generator artefact arriving on screen as an incident;
  * a single shared damping factor fitted the rural profiles and broke that
    one, which is why the factor is now per profile;
  * the step-size distribution was a third free parameter rather than a
    consequence of the two measured ones.

The step median is the interesting assertion. It is NOT fitted against the
generator - it falls out of the hold rate and the within-run spread - so it
is a prediction, and checking it is how we know the model is right rather
than merely tuned.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                                  # noqa: E402

from esim_selfhealing import fields                                 # noqa: E402
from esim_selfhealing.real_profiles import (                        # noqa: E402
    PROFILES, RSRP_CEILING, RSRP_FLOOR, RealisticChannel, profile,
)
from esim_selfhealing.schemas import FEATURE_NAMES                  # noqa: E402

# 120 runs, not 40. The within-run spread estimated from a 40-run block
# carries 5-8% of sampling noise on its own (measured across six independent
# blocks), which is the same size as the tolerance being asserted - so a
# 40-run check fails or passes on which seeds it happened to draw rather
# than on whether the profile is right.
RUNS, SAMPLES = 120, 300
PASSED, FAILED = [], []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS ' if ok else 'FAIL '} {name}" + (f": {detail}" if not ok and detail else ""))


def generate(env, runs=RUNS, samples=SAMPLES):
    within, levels, holds, steps, lat_med, big = [], [], [], [], [], 0
    nsteps = 0
    for r in range(runs):
        ch = RealisticChannel(env, np.random.default_rng(20_000 + r))
        rows = [ch.step() for _ in range(samples)]
        rs = np.array([x["rsrp_dbm"] for x in rows])
        la = np.array([x["latency_ms"] for x in rows])
        within.append(rs.std())
        levels.append(rs.mean())
        d = np.diff(rs)
        holds.append(float((d == 0).mean()))
        moved = np.abs(d[d != 0])
        if moved.size:
            steps.append(float(np.median(moved)))
            nsteps += moved.size
            big += int((moved > 12.0).sum())
        lat_med.append(float(np.median(la)))
    return {
        "within_sd": float(np.mean(within)),
        "level_sd": float(np.std(levels)),
        "hold_rate": float(np.mean(holds)),
        "step_median": float(np.mean(steps)) if steps else 0.0,
        "latency_median": float(np.mean(lat_med)),
        "big_step_share": big / max(1, nsteps),
    }


def test_profiles_reproduce_their_measurements():
    for key, env in PROFILES.items():
        m = generate(env)
        check(f"{key}: within-run spread matches ({m['within_sd']:.2f} vs {env.within_sd})",
              abs(m["within_sd"] - env.within_sd) / env.within_sd <= 0.12)
        check(f"{key}: between-run spread matches ({m['level_sd']:.1f} vs {env.level_sd})",
              abs(m["level_sd"] - env.level_sd) / env.level_sd <= 0.20)
        check(f"{key}: hold rate matches ({m['hold_rate']:.3f} vs {env.hold_rate})",
              abs(m["hold_rate"] - env.hold_rate) <= 0.02)
        check(f"{key}: latency median matches ({m['latency_median']:.1f} vs {env.latency_median_ms:.1f})",
              abs(m["latency_median"] - env.latency_median_ms) / env.latency_median_ms <= 0.12)


def test_step_size_is_predicted_not_fitted():
    """The step median is a consequence of hold rate and spread, not an input.

    Asserted within a factor of two, and that is a real limitation rather
    than a loose test. For the rural profiles the measured hold rate and
    within-run spread are not jointly consistent with a 2.0 dB step under
    any mean-reverting process: reproducing steps that small needs an
    autocorrelation so close to 1 that each run becomes a random walk and
    drifts off its own drawn level, which breaks the between-run spread -
    a measured quantity - to fix a derived one. The calibration caps the
    autocorrelation at 0.98 and accepts the residual, so the rural profiles
    predict steps roughly 50-80% larger than measured.

    What the model does get right is the ordering and the scale separation,
    which is what the view actually depends on: a vehicle steps several
    times further than a hand-carried unit, and both are checked below.
    """
    medians = {}
    for key, env in PROFILES.items():
        m = generate(env)
        medians[key] = m["step_median"]
        off = abs(m["step_median"] - env.jump_median_db) / env.jump_median_db
        check(f"{key}: predicted step median within a factor of 2 of measured "
              f"({m['step_median']:.2f} vs {env.jump_median_db})", off <= 1.00,
              f"off by {off:.0%}")
    check("the vehicle steps further than the hand-carried unit, as measured",
          medians["urban_mobile"] > 1.8 * medians["urban_pedestrian"],
          f"{medians['urban_mobile']:.2f} vs {medians['urban_pedestrian']:.2f}")


def test_no_implausible_single_sample_jumps():
    """A 1 Hz trace does not step 12 dB between consecutive samples often.

    The fixed-installation profiles should essentially never do it. A vehicle
    genuinely can - it drives behind buildings - so it is allowed a tail, but
    a bounded one.
    """
    # The vehicle's allowance is wide because its measured step median is
    # 5.5 dB; a tail above 12 dB follows from that and is not an artefact.
    limits = {"urban_mobile": 0.18}
    for key, env in PROFILES.items():
        share = generate(env)["big_step_share"]
        limit = limits.get(key, 0.06)
        check(f"{key}: >12 dB single-sample steps stay rare ({share:.1%} <= {limit:.0%})",
              share <= limit)


def test_generated_values_respect_the_field_specification():
    for key, env in PROFILES.items():
        bad = None
        for r in range(8):
            ch = RealisticChannel(env, np.random.default_rng(30_000 + r))
            for _ in range(SAMPLES):
                row = ch.step()
                for name in FEATURE_NAMES:
                    why = fields.TELEMETRY[name].violates(float(row[name]))
                    if why:
                        bad = f"{name}: {why}"
                        break
                if bad:
                    break
            if bad:
                break
        check(f"{key}: every generated value is inside the field specification",
              bad is None, bad or "")


def test_rsrp_stays_inside_the_physical_envelope():
    for key, env in PROFILES.items():
        lo = hi = None
        for r in range(8):
            ch = RealisticChannel(env, np.random.default_rng(40_000 + r))
            vals = [ch.step()["rsrp_dbm"] for _ in range(SAMPLES)]
            lo = min(vals) if lo is None else min(lo, min(vals))
            hi = max(vals) if hi is None else max(hi, max(vals))
        check(f"{key}: RSRP within [{RSRP_FLOOR:.0f}, {RSRP_CEILING:.0f}] dBm "
              f"(saw {lo:.0f}..{hi:.0f})", RSRP_FLOOR <= lo and hi <= RSRP_CEILING)


def test_runs_differ_from_each_other():
    """Run-to-run variation is where the dashboard's live variety comes from."""
    env = profile("rural_static")
    means = []
    for r in range(12):
        ch = RealisticChannel(env, np.random.default_rng(50_000 + r))
        means.append(float(np.mean([ch.step()["rsrp_dbm"] for _ in range(SAMPLES)])))
    check(f"two runs never produce the same level (spread {np.std(means):.1f} dB)",
          len(set(round(m, 3) for m in means)) == len(means) and np.std(means) > 1.0)


def main() -> int:
    for fn in (test_profiles_reproduce_their_measurements,
               test_step_size_is_predicted_not_fitted,
               test_no_implausible_single_sample_jumps,
               test_generated_values_respect_the_field_specification,
               test_rsrp_stays_inside_the_physical_envelope,
               test_runs_differ_from_each_other):
        print(f"{fn.__name__}:")
        fn()
        print()
    print(f"{len(PASSED)}/{len(PASSED) + len(FAILED)} checks passed")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
