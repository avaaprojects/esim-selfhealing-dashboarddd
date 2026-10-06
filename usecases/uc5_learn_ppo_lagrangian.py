"""USE CASE 5 - LEARN
==========================================================================
Scenario
    Six months of remediation outcomes have accumulated. The highest-reward
    action on several fault classes is also the widest-blast-radius one:
    delete-and-reprovision fixes almost everything. An unconstrained
    learner will discover that and converge on it.

    That is precisely the failure mode a telecom operator cannot ship. The
    constrained learner has to find the best policy *inside* the same
    envelope the ACT stage enforces at dispatch time.

What it demonstrates
    * PPO with a clipped surrogate on the Lagrangian advantage
    * dual ascent driving lambda_i up while the constraint is violated,
      and back toward zero once it is satisfied
    * the constrained policy trading a little reward for ~zero violations
    * the learned multipliers feeding back into PLAN's weights w1,w2,w3
    * the whole update running off the ACT critical path, on a batch cadence

Run:  python usecases/uc5_learn_ppo_lagrangian.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from esim_selfhealing.config import DEFAULT, LearnConfig
from esim_selfhealing.learn import PPOLagrangian, RemediationEnv
from esim_selfhealing.monitor import FAULT_CLASSES

BAR = "=" * 74


# ---- FUNCTION: section ----
def section(title: str) -> None:
    print(f"\n{BAR}\n{title}\n{BAR}")


# ---- FUNCTION: train ----
def train(env: RemediationEnv, cfg: LearnConfig, iterations: int,
          constrained: bool, log_every: int = 10) -> PPOLagrangian:
    trainer = PPOLagrangian(state_dim=env.state_dim, cfg=cfg)
    rng = np.random.default_rng(cfg.seed)
    for i in range(1, iterations + 1):
        batch = env.rollout(trainer.policy, cfg.batch_size, rng)
        stats = trainer.update(batch)
        if not constrained:
            trainer.lambdas[:] = 0.0          # ablation: ignore the constraints
        if i % log_every == 0 or i == 1:
            print(f"  {stats.line()}")
    return trainer


# ---- FUNCTION: report ----
def report(env: RemediationEnv, trainer: PPOLagrangian, label: str) -> dict:
    m = env.evaluate(trainer.policy)
    print(f"\n  {label}")
    print(f"    mean reward        : {m['mean_reward']:+.3f}")
    print(f"    resolution rate    : {m['resolution_rate']:.2%}")
    print(f"    violation rate     : {m['violation_rate']:.2%}   "
          f"(d_1 = {DEFAULT.learn.d_limits[0]})")
    print(f"    mean blast radius  : {m['mean_blast_radius']:.2f}   "
          f"(B_max = {DEFAULT.envelope.b_max})")
    return m


# ---- FUNCTION: main ----
def main() -> None:
    section("USE CASE 5 - LEARN: constrained vs unconstrained policy improvement")
    env = RemediationEnv(b_max=DEFAULT.envelope.b_max, rho_max=DEFAULT.envelope.rho_max)
    print(f"state          : belief over {len(FAULT_CLASSES)} fault classes + bias "
          f"(dim {env.state_dim})")
    print(f"actions        : {len(env.efficacy['isdp_corruption'])} remediations")
    print(f"constraints    : c_1 envelope violation <= {DEFAULT.learn.d_limits[0]}, "
          f"c_2 excess blast <= {DEFAULT.learn.d_limits[1]}")
    print("costs c_i are the SAME terms safety.SafetyEnvelope enforces at dispatch")

    iterations = 400
    cfg = DEFAULT.learn

    # ------------------------------------------------------------------ A
    section("A. Unconstrained PPO (ablation: lambda pinned to 0)")
    print("  the learner is free to chase reward wherever it lives\n")
    unconstrained = train(env, cfg, iterations, constrained=False, log_every=100)
    m_unc = report(env, unconstrained, "unconstrained policy, greedy evaluation")
    mix_unc = env.action_mix(unconstrained.policy)

    # ------------------------------------------------------------------ B
    section("B. PPO-Lagrangian (dual ascent on the multipliers)")
    print("  same rollouts, same seeds - the only difference is the constraint\n")
    constrained = train(env, cfg, iterations, constrained=True, log_every=100)
    m_con = report(env, constrained, "constrained policy, greedy evaluation")
    mix_con = env.action_mix(constrained.policy)

    # ------------------------------------------------------------------ C
    section("C. Side by side")
    rows = [
        ("mean reward", f"{m_unc['mean_reward']:+.3f}", f"{m_con['mean_reward']:+.3f}"),
        ("resolution rate", f"{m_unc['resolution_rate']:.1%}", f"{m_con['resolution_rate']:.1%}"),
        ("violation rate", f"{m_unc['violation_rate']:.1%}", f"{m_con['violation_rate']:.1%}"),
        ("mean blast radius", f"{m_unc['mean_blast_radius']:.2f}", f"{m_con['mean_blast_radius']:.2f}"),
    ]
    print(f"  {'metric':<20} {'unconstrained':>15} {'PPO-Lagrangian':>16}")
    print(f"  {'-'*20} {'-'*15} {'-'*16}")
    for name, a, b in rows:
        print(f"  {name:<20} {a:>15} {b:>16}")

    print("\n  action mix under the greedy policy:")
    print(f"  {'action':<26} {'unconstrained':>15} {'constrained':>13}")
    for action in sorted(set(mix_unc) | set(mix_con)):
        u, c = mix_unc.get(action, 0.0), mix_con.get(action, 0.0)
        if u < 0.005 and c < 0.005:
            continue
        print(f"  {action:<26} {u:>14.1%} {c:>13.1%}")

    # ------------------------------------------------------------------ D
    section("D. Multiplier trajectory")
    hist = constrained.history
    print(f"  {'iter':>5} {'J_C1':>8} {'lambda_1':>10} {'J_C2':>8} {'lambda_2':>10}")
    for st in hist[:: max(1, len(hist) // 10)]:
        print(f"  {st.iteration:>5} {st.cost_estimates[0]:>8.4f} {st.lambdas[0]:>10.4f} "
              f"{st.cost_estimates[1]:>8.4f} {st.lambdas[1]:>10.4f}")
    print(f"\n  peak lambda  : {tuple(round(float(l),4) for l in constrained.peak_lambdas)}")
    print(f"  final lambda : {tuple(round(float(l),4) for l in constrained.lambdas)}")
    print("\n  lambda climbs while the constraint is violated, which is what pushes")
    print("  the policy off key_rotation - then decays back toward zero once the")
    print("  policy no longer needs the pressure. A converged safe policy has a")
    print("  near-zero multiplier; the PEAK is what records the price that was")
    print("  paid, and that is the value fed back to PLAN below.")

    # ------------------------------------------------------------------ E
    section("E. Feedback into PLAN")
    base = (DEFAULT.plan.w1, DEFAULT.plan.w2, DEFAULT.plan.w3)
    tuned = constrained.plan_weights(base)
    print(f"  baseline weights (w1,w2,w3) : {tuple(round(w,3) for w in base)}")
    print(f"  after learning              : {tuple(round(w,3) for w in tuned)}")
    print(f"  peak lambda                 : "
          f"{tuple(round(float(l),4) for l in constrained.peak_lambdas)}")
    print("\n  A binding blast-radius constraint makes PLAN structurally more")
    print("  blast-averse on the next incident - the two stages share one notion")
    print("  of cost instead of drifting apart.")

    section("Takeaway")
    print("The unconstrained learner finds the higher-reward policy. It is also")
    print("the one that would touch profiles it has no business touching. Safe RL")
    print("here is not a wrapper around the policy - the same c_i the envelope")
    print("checks at dispatch time appear inside the objective, so the policy")
    print("cannot learn its way around the gate.")


if __name__ == "__main__":
    main()
