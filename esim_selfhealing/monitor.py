"""Stage 1 - MONITOR: streaming state estimation.

Implements exactly the deck's slide-7 maths:

    mu_t    = (1-lam) mu_{t-1} + lam x_t
    Sigma_t = (1-lam) Sigma_{t-1} + lam (x_t-mu_t)(x_t-mu_t)^T
    g_t     = (x_t-mu_t)^T Sigma_t^{-1} (x_t-mu_t)
    OpenIncident  <=>  g_t > chi2_{d,1-alpha}

plus the univariate CUSUM cross-check and the slide-5 Bayes filter

    b_t(s') ∝ Omega(o_t|s',a_{t-1}) sum_s T(s'|s,a_{t-1}) b_{t-1}(s)

Design notes
------------
* O(d^2) per sample: the covariance is updated rank-1 and inverted via a
  Cholesky solve on a d x d matrix with d = 5, so the whole update is a few
  microseconds - comfortably inside the <10 ms budget of slide 15.
* The threshold is a chi-square quantile, so the false-positive rate is
  ``alpha`` by construction rather than a hand-tuned magic number.
* mu and Sigma keep updating during an incident; a refractory period stops one
  fault from opening hundreds of incidents.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from .config import DEFAULT, MonitorConfig
from .schemas import FEATURE_DIM, FEATURE_NAMES, Incident, Observation

# ---------------------------------------------------------------------------
# Latent fault classes for the belief state b_t.
# ---------------------------------------------------------------------------
FAULT_CLASSES: tuple[str, ...] = (
    "nominal",
    "isdp_corruption",
    "smdp_session_outage",
    "radio_degradation",
    "key_desync",
)

#: T(s'|s,a) with a=no_op - a sticky transition kernel: faults persist.
_TRANSITION_STICKY = 0.90


# ---- FUNCTION: _chi2_quantile ----
def _chi2_quantile(dof: int, p: float) -> float:
    """chi2_{dof, p} via Wilson-Hilferty, then Newton-refined on the CDF.

    Avoids a SciPy dependency while staying accurate to ~1e-6 in the tail we
    care about (p in [0.90, 0.9999]).
    """
    z = _normal_quantile(p)
    # Wilson-Hilferty cube-root starting point.
    x = dof * (1.0 - 2.0 / (9.0 * dof) + z * math.sqrt(2.0 / (9.0 * dof))) ** 3
    x = max(x, 1e-6)
    for _ in range(60):
        f = _chi2_cdf(x, dof) - p
        pdf = _chi2_pdf(x, dof)
        if pdf < 1e-300:
            break
        step = f / pdf
        x_new = x - step
        if x_new <= 0:
            x_new = x / 2.0
        if abs(x_new - x) < 1e-10:
            x = x_new
            break
        x = x_new
    return x


# ---- FUNCTION: _normal_quantile ----
def _normal_quantile(p: float) -> float:
    """Acklam's inverse normal CDF approximation."""
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5]) * q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


# ---- FUNCTION: _lower_gamma_reg ----
def _lower_gamma_reg(s: float, x: float) -> float:
    """Regularised lower incomplete gamma P(s,x) - series / continued fraction."""
    if x <= 0:
        return 0.0
    if x < s + 1.0:
        term = 1.0 / s
        total = term
        n = 1
        while n < 500:
            term *= x / (s + n)
            total += term
            if abs(term) < abs(total) * 1e-15:
                break
            n += 1
        return total * math.exp(-x + s * math.log(x) - math.lgamma(s))
    # Continued fraction for Q(s,x); P = 1 - Q.
    tiny = 1e-300
    b = x + 1.0 - s
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for i in range(1, 500):
        an = -i * (i - s)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    q = math.exp(-x + s * math.log(x) - math.lgamma(s)) * h
    return 1.0 - q


# ---- FUNCTION: _chi2_cdf ----
def _chi2_cdf(x: float, dof: int) -> float:
    return _lower_gamma_reg(dof / 2.0, x / 2.0)


# ---- FUNCTION: _chi2_pdf ----
def _chi2_pdf(x: float, dof: int) -> float:
    k = dof / 2.0
    return math.exp((k - 1) * math.log(x) - x / 2.0 - k * math.log(2.0) - math.lgamma(k))


# ---- CLASS: MonitorState ----
@dataclass
class MonitorState:
    """Per-eUICC detector state."""

    mu: np.ndarray
    sigma: np.ndarray
    belief: np.ndarray
    mu_prev: np.ndarray = field(default_factory=lambda: np.zeros(FEATURE_DIM))
    sigma_prev: np.ndarray = field(default_factory=lambda: np.eye(FEATURE_DIM))
    n_seen: int = 0
    #: Two-sided CUSUM accumulators, one pair per channel. Separate arms are
    #: what make the statistic drift-sensitive rather than noise-sensitive:
    #: on a stationary channel the signed increments cancel and both arms sit
    #: at zero, while a sustained shift in one direction accumulates in one.
    cusum: np.ndarray = field(default_factory=lambda: np.zeros(FEATURE_DIM))
    cusum_neg: np.ndarray = field(default_factory=lambda: np.zeros(FEATURE_DIM))
    #: Slow-timescale baseline. mu tracks a ramp and so cannot see it; this
    #: deliberately lags, and the gap between the two is the drift signal.
    mu_slow: np.ndarray = field(default_factory=lambda: np.zeros(FEATURE_DIM))
    #: Recent absolute deviations from the slow baseline, as a ring buffer -
    #: the raw material for "what does this device routinely do", which is
    #: read off as a high quantile rather than a mean. See Monitor.update.
    dev_hist: np.ndarray = field(default_factory=lambda: np.zeros((0, FEATURE_DIM)))
    dev_n: int = 0
    #: Floor learned from that history, cached between refreshes.
    var_slow: np.ndarray = field(default_factory=lambda: np.zeros(FEATURE_DIM))
    last_incident_ts: float = -math.inf
    consecutive: int = 0
    #: Samples still to run before the drift test re-arms after an incident.
    drift_hold: int = 0


# ---- CLASS: MonitorReading ----
@dataclass
class MonitorReading:
    """What the detector returns for a single observation."""

    observation: Observation
    score: float                 # g_t
    threshold: float             # chi2_{d,1-alpha}
    triggered: bool
    warming_up: bool
    belief: Dict[str, float]     # b_t
    cusum_max: float
    #: Two-timescale drift statistic: the gap between the fast and slow
    #: baselines, in floored standard deviations. This is what opens an
    #: incident on a ramped fault the chi-square test cannot see.
    drift_score: float
    #: How far the device sits outside its own normal range, in standard
    #: deviations of the worst channel, against the slow baseline. This is
    #: the magnitude of the excursion; `score` is only its abruptness.
    deviation_sigma: float
    #: The EWMA baseline mu this sample was scored against, per channel, and
    #: the floored standard deviation that scaled it. These are the
    #: detector's own working state, not a redrawing of the data: plotted as
    #: a band around each channel they show exactly what "normal" meant at
    #: that moment and why a sample did or did not score.
    baseline: Dict[str, float] = field(default_factory=dict)
    band: Dict[str, float] = field(default_factory=dict)
    latency_ms: float = 0.0
    z_scores: Dict[str, float] = field(default_factory=dict)
    incident: Optional[Incident] = None


# ---- CLASS: Monitor ----
class Monitor:
    """Online multivariate change-point detector + Bayes belief filter."""

    # ---- METHOD: Monitor.__init__ ----
    def __init__(self, cfg: MonitorConfig | None = None) -> None:
        self.cfg = cfg or DEFAULT.monitor
        self.d = FEATURE_DIM
        self.threshold = _chi2_quantile(self.d, 1.0 - self.cfg.alpha)
        self._states: Dict[str, MonitorState] = {}
        self._z_cache: Dict[tuple, Dict[str, float]] = {}
        self.readings: List[MonitorReading] = []
        #: Yardstick for the drift test: the measured per-channel resolution,
        #: which is fixed, so a developing fault cannot widen it. Falls back
        #: to unit scaling when no floor is configured.
        if self.cfg.variance_floor is not None:
            floor = np.asarray(self.cfg.variance_floor, dtype=float)[:self.d]
            self._configured_floor = np.clip(floor, 1e-12, None)
            self._drift_scale = np.sqrt(self._configured_floor)
        else:
            self._configured_floor = np.zeros(self.d)
            self._drift_scale = np.ones(self.d)

    # -- state -------------------------------------------------------------
    # ---- METHOD: Monitor._state ----
    def _state(self, key: str, x: np.ndarray) -> MonitorState:
        if key not in self._states:
            prior = np.full(len(FAULT_CLASSES), 1.0 / len(FAULT_CLASSES))
            prior[0] = 0.90                       # start believing "nominal"
            prior[1:] = 0.10 / (len(FAULT_CLASSES) - 1)
            self._states[key] = MonitorState(
                mu=x.copy(),
                sigma=np.eye(self.d) * 1.0,
                belief=prior,
                mu_slow=x.copy(),
                var_slow=self._configured_floor.copy(),
                dev_hist=np.zeros((self.cfg.floor_window, self.d)),
            )
        return self._states[key]

    # -- Bayes filter (deck slide 5) --------------------------------------
    # ---- METHOD: Monitor._observation_likelihood ----
    @staticmethod
    def _observation_likelihood(z: np.ndarray) -> np.ndarray:
        """Omega(o|s',a) - sensor model over fault classes.

        Operates on the standardised residual z = (x - mu)/sigma so the
        coefficients are unit-free and comparable across features. Each fault
        # ---- CLASS: is ----
        class is a signed direction in z-space; the likelihood is a softmax of
        how strongly the residual points along it.

        These directions are written by hand here to keep the reference
        implementation dependency-free. In deployment they are fitted from
        labelled incident history (multinomial logistic regression over z is
        enough), which is also what lets them be recalibrated per operator.
        """
        aka, rsrp, drop, lat, ota = z
        scores = np.array([
            -0.35 * float(np.dot(z, z)),               # nominal: small residual
            0.90 * aka + 1.30 * ota + 0.25 * lat - 0.40 * abs(rsrp),   # isdp_corruption
            1.30 * lat + 1.40 * ota - 0.40 * aka,      # smdp_session_outage
            -1.30 * rsrp + 1.30 * drop - 0.30 * ota,   # radio_degradation
            1.40 * aka - 1.00 * ota - 0.40 * lat,      # key_desync
        ])
        scores = scores - scores.max()
        w = np.exp(scores)
        return w / w.sum()

    # ---- METHOD: Monitor._bayes_update ----
    def _bayes_update(self, st: MonitorState, z: np.ndarray) -> np.ndarray:
        n = len(FAULT_CLASSES)
        # T(s'|s, a_{t-1}=no_op): sticky diagonal.
        trans = np.full((n, n), (1.0 - _TRANSITION_STICKY) / (n - 1))
        np.fill_diagonal(trans, _TRANSITION_STICKY)
        predicted = trans.T @ st.belief
        likelihood = self._observation_likelihood(z)
        posterior = likelihood * predicted
        s = posterior.sum()
        st.belief = posterior / s if s > 0 else predicted
        return st.belief

    # -- main entry point --------------------------------------------------
    # ---- METHOD: Monitor.update ----
    def update(self, obs: Observation) -> MonitorReading:
        import time as _time
        t_start = _time.perf_counter()

        x = np.asarray(obs.features, dtype=float)
        st = self._state(obs.euicc_id, x)
        lam = self.cfg.lam

        # Score FIRST, against the baseline as it stood before this sample.
        # Folding x_t into mu and Sigma before scoring would let a large jump
        # inflate its own reference covariance and mask itself.
        st.mu_prev = st.mu.copy()
        st.sigma_prev = st.sigma.copy()
        dev = (x - st.mu).reshape(-1, 1)
        reg = st.sigma + np.eye(self.d) * (self.cfg.ridge + 1e-9 * np.trace(st.sigma))

        # Hold the diagonal at this device's own normal variability, taken as
        # it stood BEFORE this sample so a jump cannot inflate the yardstick
        # it is about to be measured against.
        #
        # Two things set that floor. The configured one is the channel's
        # measurement resolution: a quantised channel that has been reporting
        # the same value for a hundred samples is not actually known to that
        # precision, and without a floor the next 1-2 dB step scores as a
        # six-sigma event. The learned one is this device's long-memory
        # fluctuation about its own slow baseline, and it is what makes the
        # detector fair across environments. The configured floor is measured
        # on a fixed depot tracker; a vehicle roaming between cells, or a
        # gateway under tree cover on broken terrain, genuinely swings wider,
        # and judging it against the quiet device's variability flags its
        # ordinary behaviour as a fault. Measured: the roaming device raised
        # a false incident in 7 of 20 healthy runs under the global floor.
        # What is normal depends on where the device is, so the detector
        # learns it per device instead of assuming it.
        floor = np.maximum(self._configured_floor, st.var_slow)
        idx = np.arange(self.d)
        reg[idx, idx] = np.maximum(reg[idx, idx], floor)
        try:
            L = np.linalg.cholesky(reg)
            z = np.linalg.solve(L, dev)
            g_t = float(np.sum(z * z))
        except np.linalg.LinAlgError:
            g_t = float(dev.T @ np.linalg.pinv(reg) @ dev)

        # Per-feature z-scores against the SAME prior baseline used for g_t.
        # Computed here, before the update, so a large jump is not measured
        # against a covariance it has already inflated.
        diag_prior = np.maximum(np.diag(st.sigma), floor)
        std_prior = np.sqrt(np.clip(diag_prior, 1e-9, None))
        z_prior = dev.ravel() / std_prior

        # Now fold the sample into the EWMA baseline (rank-1, O(d^2)).
        # Finch's incremental form: using the PRE-update deviation keeps the
        # covariance estimate unbiased, which is what makes the chi-square
        # threshold mean what it says.
        st.mu = st.mu + lam * dev.ravel()
        st.sigma = (1.0 - lam) * (st.sigma + lam * (dev @ dev.T))
        st.n_seen += 1

        # Univariate CUSUM cross-check (slide 7 footnote).
        #
        # This is the detector's answer to a slow ramp. The EWMA baseline
        # tracks a gradual shift by design - that is what lets it absorb
        # diurnal load and firmware rollout without opening incidents - so a
        # fault that arrives over minutes rather than in one step never
        # produces a large instantaneous Mahalanobis distance. What it does
        # produce is a residual with a persistent SIGN, because mu is forever
        # chasing the drift from behind, and that is exactly what a two-sided
        # CUSUM accumulates.
        #
        # Signed, not absolute. Accumulating |z| means even a perfectly
        # stationary channel climbs at E|z| - k per sample and crosses any
        # finite boundary eventually, which would make the statistic a clock
        # rather than a detector. With signed arms, stationary noise cancels.
        #
        # Scored against the same floored prior standard deviation as z_prior,
        # so a quantised channel cannot manufacture a large z out of its own
        # collapsed variance (see MonitorConfig.variance_floor).
        # Winsorised increment. Latency is lognormal with a burst process on
        # top (real_profiles.py), so a single congestion spike can carry a z
        # of 10 or more. Left unclipped it would push the accumulator past any
        # boundary on its own, turning the drift detector into a second, worse
        # spike detector - and spikes are already the chi-square test's job.
        # Clipping bounds each sample's contribution so only a PERSISTENT
        # shift can accumulate, which is what this statistic is here to find.
        k = self.cfg.cusum_k
        zc = np.clip(z_prior, -self.cfg.cusum_clip, self.cfg.cusum_clip)
        st.cusum = np.maximum(0.0, st.cusum + zc - k)
        st.cusum_neg = np.maximum(0.0, st.cusum_neg - zc - k)
        cusum_max = float(max(st.cusum.max(), st.cusum_neg.max()))

        # Two-timescale drift test (see MonitorConfig.drift_h). Measured
        # against the floored resolution rather than the running sigma, so a
        # developing fault cannot inflate its own yardstick and hide in it.
        if st.drift_hold > 0:
            # Settling after an acknowledged change: pin the slow baseline to
            # the fast one so no gap accumulates while mu is still moving.
            st.drift_hold -= 1
            st.mu_slow = st.mu.copy()
            drift_score = 0.0
        else:
            st.mu_slow = st.mu_slow + self.cfg.drift_lam_slow * (x - st.mu_slow)
            drift_score = float(np.abs((st.mu - st.mu_slow) / self._drift_scale).max())

        # Learn this device's normal variability, as a high quantile of its
        # recent deviation from its own slow baseline.
        #
        # A quantile, not a mean square. Several of these channels hold a
        # value for most samples and then step - a roaming device reports the
        # same dBm for fifty samples and then drops 6 dB as it passes behind
        # a building. The mean squared deviation of that sits far below the
        # steps themselves, so a mean-based floor leaves exactly the routine
        # behaviour unprotected and the detector flags it: measured, the
        # roaming device still raised false incidents in 6 of 20 healthy runs
        # on a mean-square floor. A high quantile says instead "tolerate what
        # this device is actually seen to do", which is the honest statement
        # of what normal means for it.
        idx_hist = st.dev_n % self.cfg.floor_window
        st.dev_hist[idx_hist] = np.abs(x - st.mu_slow)
        st.dev_n += 1
        if st.dev_n >= self.cfg.floor_window and st.dev_n % self.cfg.floor_refresh == 0:
            q = np.quantile(st.dev_hist, self.cfg.floor_quantile, axis=0)
            st.var_slow = q * q

        # How far outside its own normal range this device now sits. Measured
        # against the slow baseline, which has not absorbed the fault, and in
        # units of the same floored standard deviation the drift test uses.
        deviation_sigma = float(np.abs((x - st.mu_slow) / self._drift_scale).max())

        belief = self._bayes_update(st, z_prior)
        warming = st.n_seen < self.cfg.warmup_samples
        refractory = (obs.ts - st.last_incident_ts) < self.cfg.refractory_s

        exceeded = bool(g_t > self.threshold)
        st.consecutive = st.consecutive + 1 if exceeded else 0
        persistent = st.consecutive >= self.cfg.min_consecutive

        # Two independent routes to an incident, answering two different
        # failure shapes: the chi-square test catches a step change within a
        # sample or two, the CUSUM catches a ramp the EWMA has been quietly
        # absorbing. A fault that is too gradual for the first is precisely
        # the one the second exists for.
        drifting = ((self.cfg.drift_h > 0.0 and drift_score > self.cfg.drift_h)
                    or (self.cfg.cusum_h > 0.0 and cusum_max > self.cfg.cusum_h))
        triggered = ((exceeded and persistent) or drifting) \
            and not warming and not refractory

        belief_map = {c: float(p) for c, p in zip(FAULT_CLASSES, belief)}
        incident = None
        if triggered:
            st.last_incident_ts = obs.ts
            st.cusum[:] = 0.0
            st.cusum_neg[:] = 0.0
            # Resynchronise the slow baseline onto the fast one. The change
            # has been acknowledged and acted on; leaving the slow baseline
            # behind would keep the gap open for ~1/drift_lam_slow samples
            # and re-fire on the device's own recovery.
            st.mu_slow = st.mu.copy()
            st.drift_hold = self.cfg.drift_settle
            st.consecutive = 0
            # Record the statistic that actually fired, expressed on the
            # chi-square scale. A ramped fault is caught by the drift test
            # while its instantaneous Mahalanobis distance is still near
            # zero - the EWMA has absorbed it, which is the whole reason the
            # drift test exists - so carrying g_t through would hand
            # downstream severity a figure saying "nothing is wrong" about a
            # device whose RSRP has collapsed 22 dB. Normalising each route's
            # exceedance by its own threshold puts them on one comparable
            # scale without changing what the chi-square route reports.
            effective = g_t
            if self.cfg.drift_h > 0.0:
                effective = max(effective, self.threshold * drift_score / self.cfg.drift_h)
            incident = Incident.new(obs, effective, self.threshold, belief_map,
                                    deviation_sigma=deviation_sigma)

        reading = MonitorReading(
            observation=obs,
            score=g_t,
            threshold=self.threshold,
            triggered=triggered,
            warming_up=warming,
            belief=belief_map,
            cusum_max=cusum_max,
            drift_score=drift_score,
            deviation_sigma=deviation_sigma,
            baseline={n: float(v) for n, v in zip(FEATURE_NAMES, st.mu_prev)},
            band={n: float(v) for n, v in zip(FEATURE_NAMES, std_prior)},
            latency_ms=(_time.perf_counter() - t_start) * 1000.0,
            z_scores={n: float(v) for n, v in zip(FEATURE_NAMES, z_prior)},
            incident=incident,
        )
        self._z_cache[(obs.euicc_id, obs.ts)] = reading.z_scores
        self.readings.append(reading)
        return reading

    # ---- METHOD: Monitor.reset_device ----
    def reset_device(self, euicc_id: str) -> None:
        """Forget one device's baseline, belief, CUSUM and refractory window.

        Needed to process a stored recording of the same device twice: the
        baseline has already absorbed the first pass, and the recording's
        timestamps precede the last incident's, so without this the second
        pass would be silently suppressed. `readings` (the history) is kept.
        """
        self._states.pop(euicc_id, None)
        for key in [k for k in self._z_cache if k[0] == euicc_id]:
            del self._z_cache[key]

    # -- convenience -------------------------------------------------------
    # ---- METHOD: Monitor.run ----
    def run(self, stream) -> List[MonitorReading]:
        return [self.update(o) for o in stream]

    # ---- METHOD: Monitor.incidents ----
    def incidents(self) -> List[Incident]:
        return [r.incident for r in self.readings if r.incident is not None]

    # ---- METHOD: Monitor.top_features ----
    def top_features(self, obs: Observation, k: int = 3) -> List[tuple[str, float]]:
        """Per-feature z-scores - the 'why' handed to the REASON stage.

        Returned from the cache recorded at scoring time, so the values are the
        ones the detector actually saw rather than a recomputation against a
        baseline that has since absorbed the anomaly.
        """
        cached = self._z_cache.get((obs.euicc_id, obs.ts))
        if cached is None:
            st = self._states[obs.euicc_id]
            x = np.asarray(obs.features, dtype=float)
            std = np.sqrt(np.clip(np.diag(st.sigma_prev), 1e-9, None))
            cached = {n: float(v) for n, v in zip(FEATURE_NAMES, (x - st.mu_prev) / std)}
        return sorted(cached.items(), key=lambda kv: -abs(kv[1]))[:k]
