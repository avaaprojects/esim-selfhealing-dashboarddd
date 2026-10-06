"""Stage 5 - LEARN: constrained policy improvement, off the critical path.

    max_theta  J_R(theta)   s.t.  J_{C_i}(theta) <= d_i,  i = 1..m
    L(theta,lambda) = J_R(theta) - sum_i lambda_i ( J_{C_i}(theta) - d_i )
    L^CLIP(theta)   = E[ min( r_t A_t, clip(r_t, 1-eps, 1+eps) A_t ) ]
    lambda_i        <- max(0, lambda_i + beta( J_hat_{C_i}(theta) - d_i ))
    theta           <- theta + alpha grad_theta L(theta, lambda)

Implementation notes
--------------------
* Pure NumPy. The policy is a linear softmax over the belief vector b_t; the
  point of this module is the *constrained* update rule, not network depth -
  swap in a torch MLP and only `Policy` changes.
* The Lagrangian advantage is A_R - sum_i lambda_i A_{C_i}, rescaled by
  1/(1 + sum lambda_i). Without that rescaling the effective learning rate
  grows with the multipliers and the update destabilises exactly when
  constraints start binding.
* c_i here are the SAME cost terms the envelope enforces at dispatch time
  (safety.SafetyEnvelope.constraint_costs), which is what makes "Learn is
  constrained by the same costs Act enforces" true rather than aspirational.
* Updates run on a batch cadence from a replay buffer, decoupled from the
  ACT-stage latency budget.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .config import ACTION_PROFILE, DEFAULT, LearnConfig
from .schemas import ACTION_SPACE, ActionType, Transition

N_ACTIONS = len(ACTION_SPACE)


# ---------------------------------------------------------------------------
# Policy: linear softmax + value heads
# ---------------------------------------------------------------------------
# ---- CLASS: Policy ----
class Policy:
    """pi_theta(a|b) = softmax(W b + b0). Value heads for reward and each cost."""

    # ---- METHOD: Policy.__init__ ----
    def __init__(self, state_dim: int, n_costs: int, seed: int = 0) -> None:
        rng = np.random.default_rng(seed)
        self.state_dim = state_dim
        self.n_costs = n_costs
        self.W = rng.normal(0, 0.05, size=(state_dim, N_ACTIONS))
        self.b = np.zeros(N_ACTIONS)
        self.Vr = np.zeros(state_dim)                 # reward critic
        self.Vc = np.zeros((n_costs, state_dim))      # one critic per constraint

    # -- forward -----------------------------------------------------------
    # ---- METHOD: Policy.logits ----
    def logits(self, s: np.ndarray) -> np.ndarray:
        return s @ self.W + self.b

    # ---- METHOD: Policy.probs ----
    def probs(self, s: np.ndarray) -> np.ndarray:
        z = self.logits(s)
        z = z - z.max(axis=-1, keepdims=True)
        e = np.exp(z)
        return e / e.sum(axis=-1, keepdims=True)

    # ---- METHOD: Policy.log_prob ----
    def log_prob(self, s: np.ndarray, a: np.ndarray) -> np.ndarray:
        p = self.probs(s)
        idx = np.arange(len(a))
        return np.log(np.clip(p[idx, a], 1e-12, None))

    # ---- METHOD: Policy.entropy ----
    def entropy(self, s: np.ndarray) -> np.ndarray:
        p = self.probs(s)
        return -(p * np.log(np.clip(p, 1e-12, None))).sum(axis=-1)

    # ---- METHOD: Policy.sample ----
    def sample(self, s: np.ndarray, rng: np.random.Generator) -> Tuple[int, float]:
        p = self.probs(s)
        a = int(rng.choice(N_ACTIONS, p=p))
        return a, float(np.log(max(p[a], 1e-12)))

    # ---- METHOD: Policy.greedy ----
    def greedy(self, s: np.ndarray) -> int:
        return int(np.argmax(self.probs(s)))

    # ---- METHOD: Policy.value ----
    def value(self, s: np.ndarray) -> np.ndarray:
        return s @ self.Vr

    # ---- METHOD: Policy.cost_value ----
    def cost_value(self, s: np.ndarray, i: int) -> np.ndarray:
        return s @ self.Vc[i]


# ---------------------------------------------------------------------------
# Replay buffer
# ---------------------------------------------------------------------------
# ---- CLASS: ReplayBuffer ----
class ReplayBuffer:
    """(b_t, a*, r_t, c_t, o_{t+1}) written at every loop iteration."""

    # ---- METHOD: ReplayBuffer.__init__ ----
    def __init__(self, n_costs: int, capacity: int = 100_000) -> None:
        self.n_costs = n_costs
        self.capacity = capacity
        self.items: List[Transition] = []

    # ---- METHOD: ReplayBuffer.add ----
    def add(self, t: Transition) -> None:
        self.items.append(t)
        if len(self.items) > self.capacity:
            self.items.pop(0)

    # ---- METHOD: ReplayBuffer.ready ----
    def ready(self, batch_size: int) -> bool:
        return len(self.items) >= batch_size

    # ---- METHOD: ReplayBuffer.drain ----
    def drain(self) -> List[Transition]:
        out, self.items = self.items, []
        return out

    # ---- METHOD: ReplayBuffer.__len__ ----
    def __len__(self) -> int:
        return len(self.items)


# ---------------------------------------------------------------------------
# PPO-Lagrangian trainer
# ---------------------------------------------------------------------------
# ---- CLASS: UpdateStats ----
@dataclass
class UpdateStats:
    iteration: int
    mean_reward: float
    cost_estimates: Tuple[float, ...]      # J_hat_{C_i}
    lambdas: Tuple[float, ...]
    policy_loss: float
    entropy: float
    violation_rate: float

    # ---- METHOD: UpdateStats.line ----
    def line(self) -> str:
        costs = ", ".join(f"{c:.4f}" for c in self.cost_estimates)
        lams = ", ".join(f"{l:.3f}" for l in self.lambdas)
        return (
            f"iter {self.iteration:>3}  J_R={self.mean_reward:+.3f}  "
            f"J_C=[{costs}]  lambda=[{lams}]  H={self.entropy:.3f}  "
            f"viol={self.violation_rate:.3f}"
        )


# ---- CLASS: PPOLagrangian ----
class PPOLagrangian:
    """Constrained policy improvement with dual ascent on the multipliers."""

    # ---- METHOD: PPOLagrangian.__init__ ----
    def __init__(self, state_dim: int, cfg: Optional[LearnConfig] = None) -> None:
        self.cfg = cfg or DEFAULT.learn
        self.n_costs = len(self.cfg.d_limits)
        self.policy = Policy(state_dim, self.n_costs, seed=self.cfg.seed)
        self.lambdas = np.zeros(self.n_costs)
        self.buffer = ReplayBuffer(self.n_costs)
        self.rng = np.random.default_rng(self.cfg.seed)
        self.history: List[UpdateStats] = []
        self.peak_lambdas = np.zeros(self.n_costs)
        self._iter = 0

    # -- advantage estimation ---------------------------------------------
    # ---- METHOD: PPOLagrangian._advantages ----
    @staticmethod
    def _advantages(rewards: np.ndarray, values: np.ndarray) -> np.ndarray:
        """Single-step GAE; each remediation is treated as a terminal decision."""
        adv = rewards - values
        std = adv.std()
        return adv / (std + 1e-8) if std > 1e-8 else adv

    # -- one constrained update -------------------------------------------
    # ---- METHOD: PPOLagrangian.update ----
    def update(self, transitions: Optional[Sequence[Transition]] = None) -> UpdateStats:
        batch = list(transitions) if transitions is not None else self.buffer.drain()
        if not batch:
            raise ValueError("no transitions to learn from")

        S = np.array([t.belief_vector for t in batch], dtype=float)
        A = np.array([t.action_index for t in batch], dtype=int)
        R = np.array([t.reward for t in batch], dtype=float)
        C = np.array([t.costs for t in batch], dtype=float)          # (n, m)
        old_logp = np.array([t.log_prob for t in batch], dtype=float)

        # Critics (least-squares fit, the linear analogue of a value-loss step).
        v_r = self.policy.value(S)
        adv_r = self._advantages(R, v_r)
        adv_c = np.zeros_like(C)
        for i in range(self.n_costs):
            adv_c[:, i] = self._advantages(C[:, i], self.policy.cost_value(S, i))

        # Lagrangian advantage, rescaled so |lambda| does not inflate the step.
        lag_adv = adv_r - adv_c @ self.lambdas
        lag_adv = lag_adv / (1.0 + self.lambdas.sum())

        eps = self.cfg.clip_eps
        policy_loss = 0.0
        for _ in range(self.cfg.epochs_per_batch):
            P = self.policy.probs(S)
            logp = np.log(np.clip(P[np.arange(len(A)), A], 1e-12, None))
            ratio = np.exp(logp - old_logp)

            unclipped = ratio * lag_adv
            clipped = np.clip(ratio, 1 - eps, 1 + eps) * lag_adv
            obj = np.minimum(unclipped, clipped)
            policy_loss = -float(obj.mean())

            # Gradient of the clipped surrogate w.r.t. the logits.
            # d/dlogits log pi(a|s) = onehot(a) - P
            active = (unclipped <= clipped).astype(float)   # where the min bites
            in_range = ((ratio > 1 - eps) & (ratio < 1 + eps)).astype(float)
            coeff = lag_adv * ratio * np.where(active > 0, 1.0, in_range)

            onehot = np.zeros_like(P)
            onehot[np.arange(len(A)), A] = 1.0
            grad_logits = (onehot - P) * coeff[:, None]

            # Entropy bonus keeps the policy from collapsing early.
            ent_grad = -(P * (np.log(np.clip(P, 1e-12, None)) + 1.0))
            grad_logits = grad_logits + self.cfg.entropy_coef * ent_grad

            self.policy.W += self.cfg.lr_policy * (S.T @ grad_logits) / len(A)
            self.policy.b += self.cfg.lr_policy * grad_logits.mean(axis=0)

        # Critic regression steps.
        self.policy.Vr += self.cfg.lr_value * (S.T @ (R - self.policy.value(S))) / len(A)
        for i in range(self.n_costs):
            resid = C[:, i] - self.policy.cost_value(S, i)
            self.policy.Vc[i] += self.cfg.lr_value * (S.T @ resid) / len(A)

        # Dual ascent:  lambda_i <- max(0, lambda_i + beta( J_hat_Ci - d_i ))
        j_c = C.mean(axis=0)
        d = np.array(self.cfg.d_limits, dtype=float)
        self.lambdas = np.maximum(0.0, self.lambdas + self.cfg.lr_dual * (j_c - d))
        self.peak_lambdas = np.maximum(self.peak_lambdas, self.lambdas)

        self._iter += 1
        stats = UpdateStats(
            iteration=self._iter,
            mean_reward=float(R.mean()),
            cost_estimates=tuple(float(x) for x in j_c),
            lambdas=tuple(float(x) for x in self.lambdas),
            policy_loss=policy_loss,
            entropy=float(self.policy.entropy(S).mean()),
            violation_rate=float((C[:, 0] > 0).mean()),
        )
        self.history.append(stats)
        return stats

    # -- utility weights fed back to PLAN ----------------------------------
    # ---- METHOD: PPOLagrangian.plan_weights ----
    def plan_weights(self, base: Tuple[float, float, float],
                     use_peak: bool = True) -> Tuple[float, float, float]:
        """Map the multipliers back onto (w1, w2, w3) of the PLAN stage.

        A binding blast-radius constraint should make PLAN more blast-averse; a
        binding violation constraint raises cost aversion too.

        ``use_peak`` matters. Dual ascent drives lambda back toward zero once the
        policy stops violating, so the *current* multiplier of a converged safe
        policy is ~0 and carries no information. The peak is what records how
        expensive the constraint was to satisfy, which is the signal PLAN wants:
        forget it and PLAN drifts back to the weights that produced the
        violations in the first place.
        """
        w1, w2, w3 = base
        lam = self.peak_lambdas if use_peak else self.lambdas
        return (
            w1,
            w2 * (1.0 + 0.5 * float(lam[0])),
            w3 * (1.0 + 1.0 * float(lam[min(1, self.n_costs - 1)])),
        )


# ---------------------------------------------------------------------------
# A small constrained environment so LEARN is runnable on its own
# ---------------------------------------------------------------------------
# ---- CLASS: RemediationEnv ----
class RemediationEnv:
    """Contextual bandit over fault beliefs - the off-line training surrogate.

    State  : belief vector b_t over fault classes (plus a bias term)
    Action : an index into ACTION_SPACE
    Reward : +1 resolved, minus latency and cost penalties
    Costs  : c_1 envelope-violation indicator, c_2 normalised excess blast radius

    Built directly from the same efficacy and action-profile tables the live
    simulator uses, so a policy trained here is not learning a different
    problem from the one it will be deployed on.
    """

    # ---- METHOD: RemediationEnv.__init__ ----
    def __init__(self, b_max: int = 1, rho_max: float = 0.30, seed: int = 3) -> None:
        from .monitor import FAULT_CLASSES
        from .rsp_api import REMEDIATION_EFFICACY

        self.fault_classes = FAULT_CLASSES
        self.efficacy = REMEDIATION_EFFICACY
        self.b_max = b_max
        self.rho_max = rho_max
        self.rng = np.random.default_rng(seed)
        self.state_dim = len(FAULT_CLASSES) + 1     # + bias

    # ---- METHOD: RemediationEnv.sample_state ----
    def sample_state(self, rng: Optional[np.random.Generator] = None) -> Tuple[np.ndarray, str]:
        """Draw a fault, then a noisy belief vector concentrated on it.

        # === BUGFIX (labelled) ============================================
        # Previously this always drew from `self.rng`, ignoring any caller-
        # supplied generator. That silently broke reproducibility in every
        # caller that *looked* seeded:
        #   * rollout(..., rng)   only the ACTION draw (policy.sample) used
        #     the passed-in rng; the STATE draw still consumed self.rng, a
        #     single generator shared and advanced across every call.
        #   * evaluate() / action_mix() each created their own local seeded
        #     `rng` (e.g. default_rng(99) / default_rng(7)) and then never
        #     used it anywhere - it was dead code - so results silently
        #     depended on how much self.rng had already been consumed by
        #     unrelated prior training calls.
        #   * uc5's train() explicitly resets `rng = default_rng(cfg.seed)`
        #     before each of the two training runs, and its own docstring
        #     says "same rollouts, same seeds - the only difference is the
        #     constraint" - a claim this bug quietly violated, since the
        #     fault/belief sequence seen by the constrained vs unconstrained
        #     run differed depending on unrelated call history.
        # Fix: accept an explicit `rng` and use it when given, falling back
        # to `self.rng` only when the caller does not care. See the callers
        # below (rollout / evaluate / action_mix), all updated to pass it.
        # ===================================================================
        """
        gen = rng if rng is not None else self.rng
        # index 0 is 'nominal'; train on genuine faults.
        fault_idx = int(gen.integers(1, len(self.fault_classes)))
        b = gen.dirichlet(np.full(len(self.fault_classes), 0.4))
        b[fault_idx] += 1.8
        b = b / b.sum()
        s = np.concatenate([b, [1.0]])              # bias feature
        return s, self.fault_classes[fault_idx]

    # ---- METHOD: RemediationEnv.step ----
    def step(self, action_index: int, fault: str,
             rng: Optional[np.random.Generator] = None) -> Tuple[float, np.ndarray]:
        # BUGFIX (same family as sample_state above): the resolved/not-resolved
        # draw is the other stochastic source in a rollout step. It must use
        # the SAME caller-supplied `rng` as sample_state()/policy.sample(),
        # or the reproducibility contract documented on RemediationEnv (and
        # relied on by uc5's "same rollouts, same seeds" comparison) still
        # silently breaks even after sample_state() is fixed.
        gen = rng if rng is not None else self.rng
        action: ActionType = ACTION_SPACE[action_index]
        prof = ACTION_PROFILE[action]
        p = self.efficacy.get(fault, {}).get(action, 0.05)
        resolved = gen.random() < p

        reward = (
            (1.0 if resolved else -0.3)
            - 0.30 * float(prof["cost"])
            - 0.004 * float(prof["sla"])
        )
        blast = int(prof["blast"])
        risk = float(prof["risk"])
        c1 = 1.0 if (blast > self.b_max or risk > self.rho_max) else 0.0
        c2 = max(0.0, blast - self.b_max) / max(1, self.b_max)
        return reward, np.array([c1, c2])

    # ---- METHOD: RemediationEnv.rollout ----
    def rollout(self, policy: Policy, n: int, rng: np.random.Generator) -> List[Transition]:
        out: List[Transition] = []
        for _ in range(n):
            # BUGFIX: pass `rng` through so the state draw is governed by the
            # same seeded generator as the action draw below (see sample_state).
            s, fault = self.sample_state(rng)
            a, logp = policy.sample(s, rng)
            r, c = self.step(a, fault, rng)
            out.append(Transition(
                belief_vector=s, action_index=a, reward=r, costs=c,
                next_belief_vector=s, done=True, log_prob=logp,
                value=float(policy.value(s)),
            ))
        return out

    # ---- METHOD: RemediationEnv.evaluate ----
    def evaluate(self, policy: Policy, n: int = 2000) -> Dict[str, float]:
        """Greedy-policy report: mean reward, resolution rate, violation rate."""
        rng = np.random.default_rng(99)
        rewards, viols, resolved, blasts = [], [], [], []
        for _ in range(n):
            # BUGFIX: `rng` was created above but never used, so evaluate()
            # silently drew states from self.rng instead - see sample_state.
            s, fault = self.sample_state(rng)
            a = policy.greedy(s)
            r, c = self.step(a, fault, rng)
            rewards.append(r)
            viols.append(c[0])
            blasts.append(ACTION_PROFILE[ACTION_SPACE[a]]["blast"])
            resolved.append(1.0 if r > 0.5 else 0.0)
        return {
            "mean_reward": float(np.mean(rewards)),
            "violation_rate": float(np.mean(viols)),
            "resolution_rate": float(np.mean(resolved)),
            "mean_blast_radius": float(np.mean(blasts)),
        }

    # ---- METHOD: RemediationEnv.action_mix ----
    def action_mix(self, policy: Policy, n: int = 2000) -> Dict[str, float]:
        rng = np.random.default_rng(7)
        counts = np.zeros(N_ACTIONS)
        for _ in range(n):
            # BUGFIX: same dead-`rng` issue as evaluate() above.
            s, _ = self.sample_state(rng)
            counts[policy.greedy(s)] += 1
        return {ACTION_SPACE[i].value: float(counts[i] / n) for i in range(N_ACTIONS)}
