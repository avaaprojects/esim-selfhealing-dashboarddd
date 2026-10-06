# Where the numbers come from, and what the detector can and cannot see

This note covers the realism work: the measured environment profiles, how
each run differs from the last, and four defects it exposed in the detector
and the severity model. It is written to be read alongside the code, and
every claim in it is reproducible with the commands given.

## 1. Why the system health figure used to be frozen

Every run of a scenario was built from one hardcoded seed, so a scenario
produced byte-identical telemetry every time it was clicked and reported the
same anomaly score. Nothing was broken; there was simply nothing to vary.

Each run now draws its own seed, and each device draws its own signal level
from the measured between-run spread of its environment. The figure the
dashboard shows is the detection statistic of the latest sample, so it now
moves whenever the telemetry does.

```
python tests/test_acceptance.py --runs 20
```

`SCENARIO_SEED=fixed` restores byte-identical runs, which is what the test
suite uses so a failure stays reproducible.

## 2. The measured profiles

`esim_selfhealing/real_profiles.py` carries five environment profiles
calibrated from three public datasets:

| profile | source | level (dBm) | within-run sd | notes |
|---|---|---|---|---|
| `rural_static` | COMMECT operator A, zenodo 14620779, n=40,400 | -89.7 | 3.9 | slow drift, long latency bursts |
| `rural_congested` | COMMECT operator B, zenodo 14620779, n=44,367 | -88.4 | 5.5 | 4x the spike rate of operator A |
| `forest_obstructed` | COMMECT forest 5G private network, zenodo 16919567 | -95.0 | 4.5 | path-loss exponent 5.43 |
| `urban_mobile` | Lumos5G driving, doi 10.1145/3419394.3423629 | -81.4 | 4.4 | widest level spread, -140..-49 |
| `urban_pedestrian` | Lumos5G walking, same source | -84.2 | 1.7 | steadiest within a run |

Each demo device is assigned the environment it physically sits in
(`backend/services/scenarios.py`, `DEVICE_ENVIRONMENT`). This is the terrain
requirement: the forest gateway sits roughly 10-15 dB below the depot
tracker because the forest measurements say it should, and the roaming unit
swings widest.

Four findings from the data contradicted the obvious way to generate it:

* **RSRP and latency are not correlated.** Measured -0.017 and -0.068. An
  earlier model coupled them at 0.96, which looked convincing and was wrong.
* **RSRP is integer-quantised and mostly held.** A modem reports whole dBm
  and repeats its last value for 95-98% of samples.
* **Latency is lognormal with a burst process on top**, not Gaussian.
* **Most of the variation is between runs, not within them.** The level a
  device sits at varies far more run to run than it does during a run.

Reproduce the fit, including a solve of the per-profile parameters:

```
python tools/calibrate_profiles.py            # report the fit
python tools/calibrate_profiles.py --solve    # re-solve and print values
python tests/test_real_profiles.py            # assert it still holds
```

### A generator bug the detector found

Chasing false alarms on the roaming device turned up a defect in the
generator, not the detector: `urban_mobile` was over-driven by 60% and
emitted 14-16 dB steps between consecutive one-second samples, which the
real driving trace does not contain. The change-point detector was right to
flag them.

Two causes, both fixed. A single damping constant was fitted on the rural
profiles and did not transfer, so it is now solved per profile. And the step
size was a third free parameter - an exponential draw on top of a forced
resync - layered on the two quantities that were actually measured. Both
inventions are gone: a modem now simply reports its current reading, and the
step-size distribution falls out of the hold rate and the spread.

That makes the step median a **prediction** rather than a fitted value,
which is a check on the model. For the vehicle it predicts 5.7 dB against
5.5 dB measured. For the rural profiles it over-predicts by 50-80%, and that
residual is real: their measured hold rate and spread are not jointly
consistent with a 2.0 dB step under any mean-reverting process, and closing
the gap would require an autocorrelation so near 1 that each run drifts off
its own level and breaks the between-run spread - trading a measured
quantity for a derived one. The calibration caps the autocorrelation and
accepts the residual.

## 3. Four defects this exposed

### Remediation that changed nothing

The fault signature was baked into the recording for the whole fault window,
so a successful repair kept emitting it and the dashboard ended a successful
run still reading DEGRADED. The loop now closes back onto the telemetry: when
the RSP confirms a dispatched command succeeded, the signature decays out
over about twelve samples and the device returns to its own baseline. A run
reads degradation, intervention, then recovery. It stays DEGRADED only when
the action genuinely did not land.

### A ramped fault the detector could not see

The EWMA baseline has a time constant of `1/lam` = 20 samples and tracks
anything slower than that by design - which is what lets it absorb diurnal
load without paging anyone. A fault arriving more gradually therefore leaves
no residual to score. Measured, detection of a ramped radio fault fell from
16/16 at a 10-sample onset to 0/16 by 30 samples.

A per-sample CUSUM is the textbook answer and does not work here: the
detector residual is autocorrelated at lag-1 around 0.9 because the channels
are AR(1), which violates its independent-increments assumption. Swept
across 25 settings, the slack needed to stop it firing on correlated noise
also made it blind to the ramp.

What works is a second, slower baseline. The fast one tracks the ramp, the
slow one does not, and the gap between them is the drift - already smoothed
on both sides. Measured separation: healthy peaks at 6.7 floored standard
deviations, the ramped fault bottoms out at 21.7.

Result: **100/100 detections across all four fault classes and all five
environments, with 6 false alarms in 30,000 samples** - inside the declared
1% false-alarm budget.

The CUSUM is still computed and shown, now as a correct two-sided statistic.
The previous one accumulated `|z|`, which climbs on stationary noise and
would cross any boundary eventually.

### A variance floor that was fair to only one device

The floor stops the covariance collapsing during a hold run. It was a single
global tuple measured on a fixed depot tracker, so a vehicle roaming between
cells was judged against a stationary device's variability and its ordinary
behaviour read as a fault - 7 false incidents in 20 healthy runs.

Each device now learns its own floor, as a high quantile of its recent
deviation from its slow baseline. A quantile and not a mean: these channels
hold a value and then step, and the mean squared deviation sits far below
the steps themselves, leaving exactly the routine behaviour unprotected.

### Severity that could not tell a catastrophe from a blip

Severity was `anomaly score / threshold`. But a threshold-crossing detector
fires on the first sample to cross, so the score it reports is by
construction just above the threshold whatever the fault. Measured across
the four fault classes the ratio spans 1.0 to 1.7 - it records how abruptly
a fault arrived, not how serious it is, and an ISD-P corruption affecting
120 devices landed in the same band as a marginal excursion.

Severity is now based on how far the device sits outside its *own* normal
range at detection, against the slow baseline so a ramp counts too:

| | deviation at detection |
|---|---|
| healthy peak | 17 sigma |
| ramped radio degradation | 19 |
| ISD-P corruption | 38 |
| key desync | 55 |
| SM-DP+ session outage | 82 |

Bands are deployment settings in `config.json`. The anomaly ratio is still
reported as context.

## 4. What the live view shows

The telemetry window is not fixed. A long window is right while nothing is
happening and the wrong view the moment an excursion starts, so it contracts
geometrically from 240 samples toward 60 as the worst channel moves away
from its baseline, and relaxes back as it settles. The contraction is
exponential in the deviation, so the plot moves continuously rather than
snapping between widths, and it is driven by the same quantity the severity
verdict uses.

All five channels animate, and each is drawn with the detector's own EWMA
baseline and its band around it. Those come from the detector's state at
each sample - `MonitorReading.baseline` and `.band` - not from a line
refitted in the browser, so what the chart shades is literally what the
sample was judged against.

The status panels show each channel's live value inside its own nominal
range, recomputed every sample and clamped to the channel's physical limits
from `esim_selfhealing/fields.py`. The clamp matters: without it the band
arithmetic prints bounds that cannot exist, such as a nominal AKA failure
rate starting at -6.6%.

The panels previously used fixed cut-offs - `aka > 3`, `ota > 0.05` - which
is the thing this whole exercise is against: a number with no derivation,
applied identically to a depot tracker and a vehicle.

## 5. Known limits

* **The rural step-size residual**, described in section 2. Documented
  rather than tuned away, because removing it would break a measured
  quantity to fix a derived one.
* **Provisioning counters are modelled, not measured.** No public dataset
  carries eSIM AKA or OTA failure counters. They take the *dynamics* of the
  measured link - AR(1), quantised, rising as the link degrades - without
  claiming measured values, and are labelled accordingly.
* **`urban_mobile` has a real tail.** About 14% of its steps exceed 12 dB.
  That follows from its measured 5.5 dB step median and is left in; the
  detector handles it through the per-device floor rather than the generator
  smoothing it away.
* **Severity bands are calibrated on these five environments.** A pilot on
  other terrain should re-derive them with `tools/calibrate_profiles.py` and
  the deviation figures in section 3.
