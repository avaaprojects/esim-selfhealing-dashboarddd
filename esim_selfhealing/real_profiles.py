"""Environment profiles calibrated from real network measurements.

Every number here was measured from a published dataset, not chosen.
`tools/calibrate_profiles.py` prints the figures from the source files so a
reviewer can check them rather than take them on trust.

    COMMECT   Radio KPI & latency measurement of cellular and satellite
              networks, rural Denmark, two commercial 5G NSA operators.
              Horizon Europe COMMECT project. doi:10.5281/zenodo.14620779
              -> 40,400 and 44,367 de-duplicated samples of RSRP + latency.

    FOREST    Radio and network KPI measurements for a 5G private network in
              a forest, south-east Norway. COMMECT project.
              doi:10.5281/zenodo.16919567
              -> RSRP against distance, 14 m to 285 m under tree cover.

    LUMOS5G   Commercial mmWave 5G, Minneapolis, Verizon, 4x Galaxy S10 5G
              over six months. Narayanan et al., ACM IMC 2020, CC BY 4.0.
              doi:10.1145/3419394.3423629
              -> 118 runs, driving and walking, 68,118 samples.

Four things the measurements say that a naive generator gets wrong.

1. **The spread is between runs, not inside one.** Measured separately:

       Lumos5G walking   overall sd 17.60 = between-run 17.09, within-run 1.74
       Lumos5G driving   overall sd 19.95 = between-run 17.92, within-run 4.39
       COMMECT op A      overall sd  9.68 = between-window 8.48, within 3.93
       COMMECT op B      overall sd 13.71 = between-window 11.88, within 5.48

   So a device sits at a level set by where it is, and moves only a few dB
   around it. Modelling the whole 10-20 dB spread as within-run noise - which
   is what a single flat distribution does - makes every trace look frantic
   and buries the faults. Here a run draws its own level, then drifts gently.
   That is also what stops every run reporting the same health figure.

2. **A modem reports the same RSRP for most samples, then steps.** Measured
   hold rates are 0.944 to 0.981, with a median step of 1.4-5.5 dB when it
   does move. That is why real traces show a |step| median of 0 dB.

3. **RSRP is integer-quantised.** Whole dBm. Six decimal places in a
   telemetry file is a tell that it was generated.

4. **Latency is not a function of signal strength.** Measured RSRP<->latency
   correlation is -0.017 and -0.068: essentially zero. There is a weak trend
   in the band medians - about 8 ms across the whole RSRP range - and what
   dominates is a heavy tail. Modelled as a lognormal body with an
   autocorrelated burst process, not as a curve against RSRP.

**A note on aggregation, because it changes the numbers.** The COMMECT files
record *per-ping* round-trip time, which is extremely heavy-tailed: median
43.8 ms against a p99 of 1494 ms and a maximum of 42 seconds. A device does
not report raw per-packet RTT in its telemetry; it reports a statistic over a
window, which is exactly why operators aggregate before feeding anomaly
detection. Re-measuring the same files as a median over a 10-ping window:

                        raw per-ping          median over 10 pings
    operator A   p99/median 34.1x  ->  1.7x    sigma 0.3307 -> 0.2447
    operator B   p99/median 29.2x  -> 19.0x    sigma 0.5924 -> 0.4226

The profiles below are calibrated to the **aggregated** figures, because that
is what a telemetry channel carries. This is not smoothing away inconvenient
data: fed the raw per-ping series, a Mahalanobis detector scores benign bursts
*higher* than real faults (measured: healthy p99.9 of 2636 against a worst
fault onset of 241), so no threshold could separate them. The aggregation is
what makes the channel meaningful, and it is what a real deployment does.

No real records are redistributed here: these are fitted parameters.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np

#: 3GPP reports RSRP over -140..-44 dBm, but a device a few metres from a
#: small cell genuinely measures higher: the FOREST set records -35 dBm at
#: 14 m. The ceiling here is the instrument limit, not the reporting range.
RSRP_FLOOR, RSRP_CEILING = -140.0, -20.0

#: How finely each channel is actually reported. RSRP in whole dBm is
#: measured (COMMECT and Lumos5G both report integers); the rest are the
#: resolutions recorded in fields.py. Telemetry that carries more precision
#: than the instrument has is a tell that it was generated.
_RESOLUTION = {"aka_fail_rate": 0.05, "drop_rate": 0.002,
               "latency_ms": 0.1, "ota_fail_rate": 0.002}


def _quantise(value: float, step: float) -> float:
    return round(value / step) * step if step else value


@dataclass(frozen=True)
class EnvironmentProfile:
    """One radio environment, with every figure traced to its measurement."""

    key: str
    label: str
    source: str                   # which dataset, for the provenance line

    #: where this device sits: drawn once per run (between-run spread)
    level_mean: float
    level_sd: float
    #: how far it moves around that level during a run (within-run spread)
    within_sd: float
    #: a modem holds its reported value, then steps
    hold_rate: float
    jump_median_db: float

    #: latency body, lognormal in ms, plus the burst process on top
    latency_log_mu: float
    latency_log_sigma: float
    latency_lag1: float
    spike_rate: float             # fraction of samples above 3x median
    spike_run_p90: int            # 90th-percentile burst length, samples

    #: how much the band median shifts across the full RSRP range (ms).
    #: Measured: about 8-9 ms, i.e. weak. Not a strong coupling.
    latency_rsrp_span_ms: float = 8.0
    #: smoothness of the within-run drift
    within_lag1: float = 0.95
    #: Damping of the reported series relative to the underlying one, caused
    #: by holding a value for most samples. Solved per profile by
    #: tools/calibrate_profiles.py; see `innovation_sd`.
    report_damping: float = 0.61
    #: log-distance path-loss exponent, where the set measured one
    path_loss_exponent: Optional[float] = None
    notes: str = ""

    @property
    def latency_median_ms(self) -> float:
        return math.exp(self.latency_log_mu)

    def variance_floor(self) -> tuple:
        """Per-channel variance floor for the detector, in FEATURE_NAMES order.

        Each channel's *measured* short-term variability. A modem holds its
        reported value for most samples, so an EWMA covariance estimated over
        a 20-sample memory collapses during a hold run - and the next step,
        which is perfectly ordinary, then scores as a large excursion.
        Measured on this project's own generator, healthy runs that open a
        spurious incident, and faults detected, without the floor -> with it:

            rural_static       22/30 -> 2/30     detection 20/24 -> 24/24
            rural_congested    23/30 -> 6/30     detection 24/24 -> 24/24
            forest_obstructed  20/30 -> 2/30     detection 23/24 -> 24/24
            urban_mobile       24/30 -> 10/30    detection 20/24 -> 24/24
            urban_pedestrian   25/30 -> 4/30     detection 20/24 -> 24/24

        Detection improves as well, because the floor also stops the
        covariance inflating to swallow a real fault. The floor is
        information the detector has and the EWMA estimate has temporarily
        lost, not a way of hiding excursions: raising the persistence
        requirement instead was measured at 0-3/30 false positives but only
        1-4/24 faults detected, because the EWMA absorbs a sustained fault
        within two samples - so persistence cannot separate them and
        magnitude cannot either (benign bursts on the raw per-ping series
        score higher than real faults). The variance floor is what works.
        """
        s = self.latency_log_sigma
        latency_var = (math.exp(s * s) - 1.0) * math.exp(2 * self.latency_log_mu + s * s)
        phi = 0.92                                   # the counters' AR(1), below
        counter_sd = {"aka": 0.09, "drop": 0.0016, "ota": 0.0014}
        stat = lambda innov: (innov / math.sqrt(1 - phi * phi)) ** 2   # noqa: E731
        return (stat(counter_sd["aka"]), self.within_sd ** 2, stat(counter_sd["drop"]),
                latency_var, stat(counter_sd["ota"]))

    @property
    def innovation_sd(self) -> float:
        """Within-run AR(1) step that reproduces `within_sd` *after* reporting.

        Holding a value for most samples damps the reported series below the
        underlying one, so driving the AR(1) at `within_sd` lands low and the
        innovation has to be scaled up to compensate.

        How much depends on the profile, which is why this is a per-profile
        field and not one constant. A single shared factor was fitted on the
        rural profiles and left `urban_mobile` - which holds 98% of samples
        rather than 96% - with an underlying process of 7.2 dB standard
        deviation against a measured 4.4. Over-driven like that it wandered
        far enough from the last reported value to trip the forced resync
        below, and emitted 14-16 dB single-sample steps that the real driving
        trace does not contain. A detector is right to flag those, so the
        over-dispersion showed up as false incidents on the roaming device in
        7 of 20 healthy runs: a generator artefact, read as a fault.

        `tools/calibrate_profiles.py` solves these factors against each
        profile's measured target and `tests/test_real_profiles.py` asserts
        the reported spread still lands there, which is the figure that
        matters.
        """
        return ((self.within_sd / self.report_damping)
                * math.sqrt(max(1e-6, 1.0 - self.within_lag1 ** 2)))


#: Measured profiles. tools/calibrate_profiles.py prints these from the sources.
PROFILES: Dict[str, EnvironmentProfile] = {
    "rural_static": EnvironmentProfile(
        key="rural_static", label="Rural, fixed installation",
        source="COMMECT operator A (zenodo 14620779), n=40,400",
        level_mean=-89.7, level_sd=8.5, within_sd=3.9,
        hold_rate=0.957, jump_median_db=2.0,
        latency_log_mu=3.7640, latency_log_sigma=0.2447, latency_lag1=0.688,
        spike_rate=0.0040, spike_run_p90=2,
        within_lag1=0.979, report_damping=0.757,
        notes="Slow drift; occasional long latency bursts (p99 1494 ms, max 42 s)."),
    "rural_congested": EnvironmentProfile(
        key="rural_congested", label="Rural, second operator (busier)",
        source="COMMECT operator B (zenodo 14620779), n=44,367",
        level_mean=-88.4, level_sd=11.9, within_sd=5.5,
        hold_rate=0.944, jump_median_db=2.0,
        latency_log_mu=3.4175, latency_log_sigma=0.4226, latency_lag1=0.487,
        spike_rate=0.0354, spike_run_p90=3,
        within_lag1=0.979, report_damping=0.784,
        notes="Lower median latency than operator A but four times the spike rate."),
    "forest_obstructed": EnvironmentProfile(
        key="forest_obstructed", label="Forest / obstructed terrain",
        source="COMMECT forest 5G private network (zenodo 16919567)",
        # RSRP falls 63 dB between 14 m and 285 m under tree cover. The fitted
        # log-distance exponent is 5.43 against 2.0 for free space (dense
        # foliage is 4-5), with 9.1 dB of residual scatter about the fit - the
        # scatter is what sets the level spread below.
        level_mean=-95.0, level_sd=9.1, within_sd=4.5,
        hold_rate=0.950, jump_median_db=2.0,
        latency_log_mu=2.7081, latency_log_sigma=0.3500, latency_lag1=0.600,
        spike_rate=0.0050, spike_run_p90=2, path_loss_exponent=5.43,
        within_lag1=0.979, report_damping=0.739,
        notes="Heavy attenuation with distance; short range, low latency (12-27 ms measured)."),
    "urban_mobile": EnvironmentProfile(
        key="urban_mobile", label="Urban, vehicle-mounted",
        source="Lumos5G driving (doi 10.1145/3419394.3423629), 46 runs",
        level_mean=-81.4, level_sd=17.9, within_sd=4.4,
        hold_rate=0.980, jump_median_db=5.5,
        latency_log_mu=3.5000, latency_log_sigma=0.3000, latency_lag1=0.500,
        spike_rate=0.0100, spike_run_p90=3,
        within_lag1=0.885, report_damping=0.703,
        notes="Widest level spread of the measured sets: run means span -140..-49 dBm."),
    "urban_pedestrian": EnvironmentProfile(
        key="urban_pedestrian", label="Urban, hand-carried",
        source="Lumos5G walking (doi 10.1145/3419394.3423629), 72 runs",
        level_mean=-84.2, level_sd=17.1, within_sd=1.7,
        hold_rate=0.981, jump_median_db=1.4,
        latency_log_mu=3.5000, latency_log_sigma=0.3000, latency_lag1=0.450,
        spike_rate=0.0100, spike_run_p90=3,
        within_lag1=0.979, report_damping=0.586,
        notes="Steadiest within a run (1.74 dB) despite the widest spread across runs."),
}

DEFAULT_PROFILE = "rural_static"


def profile(key: Optional[str]) -> EnvironmentProfile:
    """The named profile, falling back to the default rather than raising:
    an unknown environment must not take a run down."""
    return PROFILES.get(key or DEFAULT_PROFILE, PROFILES[DEFAULT_PROFILE])


def rsrp_at_distance(env: EnvironmentProfile, distance_m: float,
                     reference_dbm: float = 44.2) -> float:
    """Log-distance path loss, for the profiles that measured an exponent.

    `reference_dbm` is the fitted intercept from the FOREST set:
    RSRP = 44.2 - 10*n*log10(d). Used for *relative* attenuation with
    distance, not as an absolute level.
    """
    n = env.path_loss_exponent or 2.0
    return reference_dbm - 10.0 * n * math.log10(max(1.0, distance_m))


class RealisticChannel:
    """One device's healthy baseline, with the measured dynamics.

    A level drawn once for this run (the between-run spread), a gentle AR(1)
    drift around it, reported in whole dBm with the measured hold rate, and a
    lognormal latency body with an autocorrelated burst process. Faults are
    added by the caller on top of this baseline, so detection behaves as it
    always did - and because the drift is gentle, a sudden fault still stands
    well clear of it.
    """

    def __init__(self, env: EnvironmentProfile, rng: np.random.Generator,
                 level: Optional[float] = None):
        self.env = env
        self.rng = rng
        #: this run's level: the between-run spread is where run-to-run
        #: variation comes from, so two runs never report the same figures
        self.level = float(np.clip(
            level if level is not None else rng.normal(env.level_mean, env.level_sd),
            RSRP_FLOOR + 5.0, RSRP_CEILING))
        self._rsrp = self.level
        self._reported = float(round(self._rsrp))
        self._log_lat = env.latency_log_mu
        self._spike_left = 0
        self._aka, self._drop, self._ota = 0.50, 0.006, 0.005

    def step(self) -> Dict[str, float]:
        env = self.env

        # -- RSRP: gentle AR(1) around this run's level, reported the way a
        #    modem reports it - holding for several samples, then stepping.
        self._rsrp = (self.level
                      + env.within_lag1 * (self._rsrp - self.level)
                      + self.rng.normal(0.0, env.innovation_sd))
        self._rsrp = float(np.clip(self._rsrp, RSRP_FLOOR, RSRP_CEILING))
        # Hold, then report the current reading in whole dBm.
        #
        # A modem reports what it measures now; it does not partially correct
        # toward it. An earlier version drew a separate exponential "jump
        # size" and stepped by that, plus a forced resync when the underlying
        # value had wandered too far. Both were inventions on top of the
        # measurement, and together they produced a heavy tail of 14-16 dB
        # single-sample steps - 9.8% of all steps above 12 dB on the roaming
        # profile - which the change-point detector flagged as faults. They
        # are gone. The step-size distribution is now a CONSEQUENCE of the
        # two things that were actually measured, the hold rate and the
        # within-run spread, rather than a third free parameter: hold longer
        # and the process has wandered further by the time it is reported.
        # `jump_median_db` is therefore no longer an input - it is a
        # prediction, which tools/calibrate_profiles.py checks against the
        # measured value.
        if self.rng.random() >= env.hold_rate:
            self._reported = float(round(self._rsrp))
        rsrp = self._reported

        # -- latency: lognormal body, autocorrelated in log space
        self._log_lat = (env.latency_log_mu
                         + env.latency_lag1 * (self._log_lat - env.latency_log_mu)
                         + self.rng.normal(0.0, env.latency_log_sigma
                                           * math.sqrt(max(1e-6, 1 - env.latency_lag1 ** 2))))
        latency = math.exp(self._log_lat)

        # the weak band coupling the data shows: a few ms across the RSRP
        # range, not a curve. Measured correlation is about -0.05.
        weak = (self.level - rsrp) / max(1.0, 3.0 * max(1.0, env.within_sd))
        latency += env.latency_rsrp_span_ms * float(np.clip(weak, -1.0, 1.0))

        # -- bursts: short runs of much higher latency, as measured. A trigger
        #    produces the trigger sample plus its run, so the trigger
        #    probability is divided by the mean burst length to land on the
        #    measured overall rate rather than roughly double it.
        mean_run = 1.0 + max(1, env.spike_run_p90) / 2.0
        if self._spike_left > 0:
            self._spike_left -= 1
            latency *= float(self.rng.uniform(1.4, 2.5))
        elif self.rng.random() < env.spike_rate / mean_run:
            self._spike_left = int(self.rng.integers(0, max(1, env.spike_run_p90)))
            latency *= float(self.rng.uniform(1.5, 4.0))

        # -- the provisioning channels. No public dataset carries eSIM
        #    provisioning counters, so these take the *dynamics* of the
        #    measured link without claiming measured values: they rise as the
        #    link degrades relative to this run's own level.
        #
        #    They are AR(1) and quantised, like every other channel. Drawing
        #    them as independent noise each sample - which is the obvious
        #    thing to do - makes a counter whose sample-to-sample jitter is
        #    larger than its own level, and a detector correctly reports that
        #    as a stream of anomalies. Real aggregated counters are smooth.
        q = max(0.0, float(np.clip((self.level - rsrp) / max(1.0, 2.5 * max(1.0, env.within_sd)),
                                   -1.0, 1.0)))
        phi = 0.92
        self._aka = phi * self._aka + (1 - phi) * (0.50 + 1.8 * q ** 2) \
            + self.rng.normal(0, 0.09)
        self._drop = phi * self._drop + (1 - phi) * (0.006 + 0.055 * q ** 2) \
            + self.rng.normal(0, 0.0016)
        self._ota = phi * self._ota + (1 - phi) * (0.005 + 0.035 * q ** 2) \
            + self.rng.normal(0, 0.0014)

        res = _RESOLUTION
        return {"aka_fail_rate": _quantise(max(0.0, self._aka), res["aka_fail_rate"]),
                "rsrp_dbm": rsrp,
                "drop_rate": _quantise(float(np.clip(self._drop, 0.0, 1.0)), res["drop_rate"]),
                "latency_ms": _quantise(max(1.0, latency), res["latency_ms"]),
                "ota_fail_rate": _quantise(float(np.clip(self._ota, 0.0, 1.0)), res["ota_fail_rate"])}


def summary_table() -> str:
    """The profiles, as a table. Printed by tools/calibrate_profiles.py."""
    out = [f"{'profile':<20} {'level':>8} {'between':>8} {'within':>7} {'hold':>6} "
           f"{'jump':>5} {'lat med':>8} {'spike%':>7}  source", "-" * 126]
    for p in PROFILES.values():
        out.append(f"{p.key:<20} {p.level_mean:>8.1f} {p.level_sd:>8.1f} {p.within_sd:>7.1f} "
                   f"{p.hold_rate:>6.3f} {p.jump_median_db:>5.1f} {p.latency_median_ms:>8.1f} "
                   f"{100*p.spike_rate:>7.2f}  {p.source}")
    return "\n".join(out)
