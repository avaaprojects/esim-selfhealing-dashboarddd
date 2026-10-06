"""Synthetic telemetry source standing in for the RAN + core + eUICC feeds.

Produces the same 5-dimensional x_t the MONITOR stage expects, so the rest of
the pipeline is unchanged when a real Kafka/OTLP consumer replaces this class.

Fault scenarios are injectable so each use case can reproduce a specific
failure mode deterministically (fixed seed).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterator, List, Optional

import numpy as np

from . import fields, real_profiles
from .schemas import FEATURE_NAMES, FEATURE_DIM, Observation

#: Healthy operating point, ordered per schemas.FEATURE_NAMES.
NOMINAL_MEAN = np.array([0.8, -92.0, 0.015, 120.0, 0.010])
NOMINAL_STD = np.array([0.30, 2.50, 0.006, 18.0, 0.005])

#: Per-sample benign drift, as a FRACTION of each channel's own measured
#: standard deviation, with the sign giving the direction things worsen in.
#:
#: Why a fraction rather than the fixed vector below: the legacy vector is
#: absolute, and against the measured profiles it moves AKA failure rate by
#: 26 standard deviations and latency by 16 over a 300-sample run. That is
#: not the diurnal-load-and-firmware-rollout drift it is documented as - it
#: is a device failing slowly - and requiring the detector to stay silent
#: through it is what forced the CUSUM slack so high that a genuine ramped
#: fault became invisible. At this rate the baseline moves about 1.2 standard
#: deviations over a run: clearly visible on the live plot, comfortably
#: absorbed by the EWMA, and leaving the drift detector free to do its job.
DRIFT_RATE_SD = 0.004
DRIFT_DIRECTION = np.array([+1.0, -1.0, +1.0, +1.0, +1.0])

#: The original absolute drift vector, kept for streams with no measured
#: environment so existing fixed-seed scenarios reproduce exactly.
LEGACY_DRIFT = np.array([0.02, -0.03, 0.0002, 0.6, 0.0001])

#: Additive fault signatures. Each maps a fault class to a delta on x_t.
FAULT_SIGNATURES = {
    # Corrupted ISD-P: AKA fails hard, OTA sessions fail, radio is fine.
    "isdp_corruption": np.array([9.0, 0.0, 0.05, 40.0, 0.09]),
    # SM-DP+ session outage: OTA fails and latency spikes, AKA mostly fine.
    "smdp_session_outage": np.array([1.2, 0.0, 0.01, 480.0, 0.30]),
    # Radio degradation: RSRP collapses, drops rise, no OTA signature.
    "radio_degradation": np.array([1.0, -22.0, 0.22, 60.0, 0.005]),
    # Key desync between eUICC and SM-SR: AKA failures only - the profile is
    # intact, so OTA downloads still complete and latency is unaffected.
    "key_desync": np.array([13.0, 0.0, 0.02, 4.0, 0.0]),
}


# ---- CLASS: FaultWindow ----
@dataclass
class FaultWindow:
    """Inject `fault` between sample indices [start, end)."""

    fault: str
    start: int
    end: int
    ramp: bool = False   # linearly ramp the signature in (slow-onset fault)


# ---- CLASS: TelemetryStream ----
class TelemetryStream:
    """Deterministic generator of Observation objects for one eUICC."""

    # ---- METHOD: TelemetryStream.__init__ ----
    def __init__(
        self,
        euicc_id: str = "89330000000048213",
        cell_id: str = "CELL-4471",
        n_samples: int = 400,
        faults: Optional[List[FaultWindow]] = None,
        drift_per_sample: float = 0.0,
        period_s: float = 1.0,
        seed: int = 42,
        t0: Optional[float] = None,
        environment: Optional[str] = None,
        level: Optional[float] = None,
        recovery_samples: int = 12,
    ) -> None:
        self.euicc_id = euicc_id
        self.cell_id = cell_id
        self.n_samples = n_samples
        self.faults = faults or []
        self.drift_per_sample = drift_per_sample
        self.period_s = period_s
        self.rng = np.random.default_rng(seed)
        self.t0 = t0 if t0 is not None else time.time()
        #: With an `environment` the healthy baseline comes from measurements
        #: (see real_profiles.py): a level drawn per run, gentle drift around
        #: it, integer-quantised reporting, and a heavy-tailed latency burst
        #: process. Without one, the original flat independent-noise baseline
        #: is used, so existing scenarios are opted in deliberately rather
        #: than changed underneath.
        self.environment = environment
        self._channel = None
        self._drift_step = LEGACY_DRIFT
        if environment is not None:
            env = real_profiles.profile(environment)
            self._channel = real_profiles.RealisticChannel(env, self.rng, level=level)
            channel_sd = np.sqrt(np.asarray(env.variance_floor(), dtype=float))
            self._drift_step = DRIFT_RATE_SD * channel_sd * DRIFT_DIRECTION
        #: Sample index the loop reached, and the index a successful
        #: remediation landed at. See `mark_remediated`.
        self._cursor = 0
        self._healed_at: Optional[int] = None
        self.recovery_samples = recovery_samples

    @property
    def level(self) -> Optional[float]:
        """This run's drawn RSRP level, when a measured environment is used."""
        return self._channel.level if self._channel is not None else None

    # -- internals ---------------------------------------------------------
    # ---- METHOD: TelemetryStream._active_fault ----
    def _active_fault(self, i: int) -> tuple[Optional[str], float]:
        for w in self.faults:
            if w.start <= i < w.end:
                if w.ramp:
                    span = max(1, w.end - w.start)
                    scale = (i - w.start + 1) / span
                else:
                    scale = 1.0
                if self._healed_at is not None and i >= self._healed_at:
                    # A successful remediation has landed. The fault signature
                    # decays out over `recovery_samples` rather than vanishing
                    # between one sample and the next: a re-pushed profile has
                    # to be re-read, sessions re-established and the radio
                    # re-attached, so the counters fall over seconds, not
                    # instantly. Past the ramp the device is back on its own
                    # measured baseline.
                    span = max(1, self.recovery_samples)
                    decay = max(0.0, 1.0 - (i - self._healed_at + 1) / span)
                    if decay <= 0.0:
                        return None, 0.0
                    scale *= decay
                return w.fault, scale
        return None, 0.0

    # ---- METHOD: TelemetryStream.mark_remediated ----
    def mark_remediated(self, at_index: Optional[int] = None) -> None:
        """Called by the loop when a remediation has actually executed.

        Without this the telemetry is a fixed recording: the fault signature
        stays in the samples for the rest of the run however well the loop
        responded, the orchestrator re-injects it into the RSP client on the
        next tick, and the dashboard ends a successful run still reading
        DEGRADED - remediation with nothing to show for it. Marking the stream
        lets the following samples return to baseline, so a run reads
        degradation, intervention, then recovery.

        Idempotent: only the first successful remediation starts the recovery,
        so a second action on the same incident cannot restart the ramp.
        """
        if self._healed_at is None:
            self._healed_at = self._cursor if at_index is None else at_index

    # ---- METHOD: TelemetryStream._sample ----
    def _sample(self, i: int) -> Observation:
        if self._channel is not None:
            sample = self._channel.step()
            x = np.array([sample[name] for name in FEATURE_NAMES], dtype=float)
        else:
            x = NOMINAL_MEAN + self.rng.normal(0.0, NOMINAL_STD, size=FEATURE_DIM)

        # Slow baseline drift (diurnal load, firmware rollout) the EWMA should
        # absorb WITHOUT opening an incident. Scaled to the channel's own
        # measured variability when a profile is in use - see DRIFT_RATE_SD.
        x = x + self.drift_per_sample * i * self._drift_step

        fault, scale = self._active_fault(i)
        if fault is not None:
            x = x + scale * FAULT_SIGNATURES[fault]

        # Physical clipping, from the one field specification every part of the
        # project reads (esim_selfhealing/fields.py). RSRP in particular was
        # previously unbounded, so noise could produce a positive dBm reading,
        # which cannot exist.
        for j, name in enumerate(FEATURE_NAMES):
            x[j] = fields.TELEMETRY[name].clamp(float(x[j]))

        return Observation(
            ts=self.t0 + i * self.period_s,
            euicc_id=self.euicc_id,
            cell_id=self.cell_id,
            features=x.tolist(),
            ground_truth_fault=fault,
        )

    # -- public API --------------------------------------------------------
    # ---- METHOD: TelemetryStream.__iter__ ----
    def __iter__(self) -> Iterator[Observation]:
        for i in range(self.n_samples):
            # Published before the yield so a consumer that calls
            # `mark_remediated()` while handling this observation records the
            # index it is actually looking at.
            self._cursor = i
            yield self._sample(i)

    # ---- METHOD: TelemetryStream.collect ----
    def collect(self) -> List[Observation]:
        return list(self)


# Every scenario factory below takes an optional `environment`, so one
# definition of a scenario's shape serves both callers: the console use cases
# in usecases/ (which pass a fixed seed and no environment, staying
# byte-reproducible) and the dashboard catalogue in
# backend/services/scenarios.py (which passes a per-run seed and the device's
# measured environment, so each run of a scenario draws its own telemetry).


# ---- FUNCTION: healthy_stream ----
def healthy_stream(
    n: int = 200,
    seed: int = 1,
    environment: Optional[str] = None,
) -> TelemetryStream:
    return TelemetryStream(n_samples=n, seed=seed, environment=environment)


# ---- FUNCTION: scenario_isdp_corruption ----
def scenario_isdp_corruption(
    seed: int = 42,
    environment: Optional[str] = None,
) -> TelemetryStream:
    """Clean baseline, then a sudden ISD-P corruption at sample 250."""
    return TelemetryStream(
        n_samples=300,
        faults=[FaultWindow("isdp_corruption", start=250, end=300)],
        drift_per_sample=1.0,   # slow drift the detector must NOT flag
        seed=seed,
        environment=environment,
    )


# ---- FUNCTION: scenario_slow_radio_drift ----
def scenario_slow_radio_drift(
    seed: int = 43,
    environment: Optional[str] = None,
) -> TelemetryStream:
    """Slow-onset radio degradation - tests ramp sensitivity."""
    return TelemetryStream(
        n_samples=320,
        faults=[FaultWindow("radio_degradation", start=200, end=320, ramp=True)],
        seed=seed,
        environment=environment,
    )


# ---- FUNCTION: scenario_smdp_outage ----
def scenario_smdp_outage(
    seed: int = 44,
    environment: Optional[str] = None,
) -> TelemetryStream:
    return TelemetryStream(
        n_samples=300,
        faults=[FaultWindow("smdp_session_outage", start=240, end=300)],
        seed=seed,
        environment=environment,
    )
