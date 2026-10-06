"""What each field means, and whether it may be randomised.

Generating a value for every column with one random function produces data that
*looks* like telemetry and is not. Two separate reasons:

1. **Structural fields are identities, not numbers.** An eUICC id carries a
   Luhn check digit and an issuer prefix; an ICCID identifies a real SIM; a PLMN
   maps to an actual operator; a cell id must exist in the radio plan. A random
   value in these fields is not "a different device", it is an invalid record.
   Anything downstream that joins on them silently stops matching.

2. **Measured channels are not independent.** MONITOR scores a sample with the
   Mahalanobis distance, which is measured against an *estimated covariance*
   matrix - it asks "how unusual is this combination, given how these channels
   normally move together". Draw the five channels independently and that
   covariance describes a process that does not exist: the score still computes,
   and it no longer means anything. A real fault moves several channels at once
   in a fixed pattern (FAULT_SIGNATURES in telemetry.py); independent noise does
   not, so the detector cannot separate the two.

So each field below carries:

    randomisable   may a generator vary it at all
    low / high     physical limits - outside these the value is impossible,
                   not merely unusual (so a file carrying one is rejected)
    typical        the range a healthy device sits in, for sanity checks
    unit           stated explicitly, because "rate" has meant both a
                   fraction and a per-100 count in telecom specs

`aka_fail_rate` is the one to read carefully: it is failures **per 100 attach
attempts**, i.e. a percentage, not a 0..1 fraction. A nominal 0.8 is 0.8%, and
the key-desync signature (+13.0) is 13.8%. Both are realistic; the name is what
misleads, which is why the unit is spelled out here and shown in the UI.

Nothing in this module invents data. It records what the fields are, so the
generator, the CSV validator and a reviewer all work from one definition.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class Field:
    """One column: what it is, and what a generator may do with it."""

    name: str
    unit: str
    description: str
    randomisable: bool
    #: physically impossible outside this range (None = unbounded in that direction)
    low: Optional[float] = None
    high: Optional[float] = None
    #: where a healthy device normally sits, for plausibility checks
    typical: Optional[Tuple[float, float]] = None
    #: why it may not be randomised, when it may not be
    reason: str = ""
    #: how finely the channel is actually reported. Measured: a modem reports
    #: RSRP in whole dBm, and the COMMECT and Lumos5G files confirm it. Used
    #: as a variance floor by the detector, because a channel that has held
    #: the same value for many samples is not known to infinite precision.
    resolution: Optional[float] = None

    def clamp(self, value: float) -> float:
        """The value held inside its physical limits."""
        if self.low is not None:
            value = max(self.low, value)
        if self.high is not None:
            value = min(self.high, value)
        return value

    def violates(self, value: float) -> Optional[str]:
        """A message if the value is physically impossible, else None."""
        if self.low is not None and value < self.low:
            return f"{self.name} = {value:g} {self.unit} is below the physical minimum {self.low:g}"
        if self.high is not None and value > self.high:
            return f"{self.name} = {value:g} {self.unit} is above the physical maximum {self.high:g}"
        return None

    def implausible(self, value: float) -> Optional[str]:
        """A message if the value is possible but outside the healthy range."""
        if self.typical is None or self.violates(value):
            return None
        lo, hi = self.typical
        if not (lo <= value <= hi):
            return (f"{self.name} = {value:g} {self.unit} is outside the typical healthy range "
                    f"{lo:g}..{hi:g} (fine during a fault; suspicious across a whole file)")
        return None


# ---------------------------------------------------------------------------
# the five measured channels (x_t, in FEATURE_NAMES order)
# ---------------------------------------------------------------------------
TELEMETRY: Dict[str, Field] = {
    "aka_fail_rate": Field(
        name="aka_fail_rate", unit="failures per 100 attach attempts",
        description="Authentication (AKA) failures. A percentage, NOT a 0..1 fraction.",
        randomisable=True, low=0.0, high=100.0, typical=(0.0, 5.0), resolution=0.05),
    "rsrp_dbm": Field(
        name="rsrp_dbm", unit="dBm",
        description=(
            "Reference Signal Received Power. Always negative. 3GPP TS 36.133 defines the "
            "reporting range as -140..-44 dBm, but real equipment reports above it at close "
            "range: the forest 5G measurements used to calibrate the profiles reach -35 dBm "
            "at 14 m. The ceiling here is the physical limit, not the reporting range, "
            "because a spec that rejects measured data is the wrong spec."),
        randomisable=True, low=-140.0, high=-20.0, typical=(-110.0, -80.0), resolution=1.0),
    "drop_rate": Field(
        name="drop_rate", unit="fraction 0..1",
        description="Session drop rate.",
        randomisable=True, low=0.0, high=1.0, typical=(0.0, 0.10), resolution=0.002),
    "latency_ms": Field(
        name="latency_ms", unit="milliseconds",
        description="OTA round-trip latency. Cannot be zero or negative; beyond ~30 s the session has timed out.",
        randomisable=True, low=1.0, high=30000.0, typical=(40.0, 400.0), resolution=1.0),
    "ota_fail_rate": Field(
        name="ota_fail_rate", unit="fraction 0..1",
        description="SM-DP+ / SM-SR session failure rate.",
        randomisable=True, low=0.0, high=1.0, typical=(0.0, 0.10), resolution=0.002),
}

# ---------------------------------------------------------------------------
# identity and structure: a random value here is an invalid record, not a
# different device
# ---------------------------------------------------------------------------
STRUCTURAL: Dict[str, Field] = {
    "euicc_id": Field(
        name="euicc_id", unit="19-digit EID / 17-digit demo id", description="The eUICC's identity.",
        randomisable=False,
        reason="Carries an issuer prefix and a check digit, and every incident, report, "
               "telemetry row and registry entry joins on it. A random id matches nothing."),
    "eid": Field(
        name="eid", unit="32-digit EID", description="GSMA eUICC identifier.", randomisable=False,
        reason="Structured per SGP.02: issuer and version digits plus a check digit."),
    "iccid": Field(
        name="iccid", unit="19-20 digit ICCID", description="The installed profile's SIM identifier.",
        randomisable=False,
        reason="Begins with the 89 telecom major-industry identifier and a country/issuer code, "
               "and ends in a Luhn check digit."),
    "cell_id": Field(
        name="cell_id", unit="identifier", description="Serving radio cell.", randomisable=False,
        reason="Must exist in the radio plan; a random cell cannot be correlated with RSRP."),
    "client_id": Field(
        name="client_id", unit="identifier", description="Owning company.", randomisable=False,
        reason="Decides who may see the record. A random value would show one company's data to another."),
    "group_id": Field(
        name="group_id", unit="identifier", description="Device group within a company.", randomisable=False,
        reason="Must belong to the device's own client_id."),
    "network_id": Field(
        name="network_id", unit="identifier", description="Network context (PLMN, auth method).",
        randomisable=False, reason="Maps to a real PLMN and authentication method."),
    "plmn": Field(
        name="plmn", unit="MCC-MNC", description="Public Land Mobile Network code.", randomisable=False,
        reason="Identifies an actual operator. 001-01 is the reserved test code, which is what this demo uses."),
    "rsp_env_id": Field(
        name="rsp_env_id", unit="identifier", description="SM-DP+ / SM-SR environment.", randomisable=False,
        reason="Names the provisioning environment the device is enrolled against."),
    "profile_name": Field(
        name="profile_name", unit="text", description="Installed profile.", randomisable=False,
        reason="Belongs to the operator and the device's enrolment, not to chance."),
    "fleet_size": Field(
        name="fleet_size", unit="devices", description="Devices sharing this profile.",
        randomisable=False, low=1.0,
        reason="Drives the safety envelope's blast-radius limit: randomising it changes whether an "
               "action is allowed, which would make the gate's decisions meaningless."),
    "sim_fault_label": Field(
        name="sim_fault_label", unit="fault class", description="Which fault the simulator should inject.",
        randomisable=False,
        reason="The answer key. It is never shown to the agent; it tells the simulated RSP server what is "
               "wrong so a fix can be verified. A random label would make recovery results nonsense."),
    "ts_epoch": Field(
        name="ts_epoch", unit="seconds since 1970", description="When the reading was taken.",
        randomisable=False,
        reason="The detector is a time series: EWMA, CUSUM and the persistence rule all assume ordered, "
               "evenly spaced samples. Shuffled or random timestamps change the score itself."),
    "provenance": Field(
        name="provenance", unit="label", description="Where the data came from.", randomisable=False,
        reason="States whether the row is synthetic, an operator upload or real. Randomising it would "
               "let simulated data present itself as real."),
}

ALL: Dict[str, Field] = {**TELEMETRY, **STRUCTURAL}


def randomisable(name: str) -> bool:
    """May a generator vary this field? Unknown fields are refused by default."""
    field = ALL.get(name)
    return bool(field and field.randomisable)


def bounds(name: str) -> Tuple[Optional[float], Optional[float]]:
    field = ALL.get(name)
    return (field.low, field.high) if field else (None, None)


def check_row(values: Dict[str, float]) -> list[str]:
    """Every physical violation in one row of telemetry."""
    problems = []
    for key, value in values.items():
        field = TELEMETRY.get(key)
        if field is None:
            continue
        bad = field.violates(float(value))
        if bad:
            problems.append(bad)
    return problems


def summary_table() -> str:
    """The audit, as a table. Used by tools/check_fields.py."""
    rows = ["field                unit                                 random?  range",
            "-" * 100]
    for field in list(TELEMETRY.values()) + list(STRUCTURAL.values()):
        span = ("unbounded" if field.low is None and field.high is None
                else f"{'' if field.low is None else f'{field.low:g}'}"
                     f" .. {'' if field.high is None else f'{field.high:g}'}")
        rows.append(f"{field.name:<20} {field.unit:<36} "
                    f"{'YES' if field.randomisable else 'NO ':<8} {span}")
        if not field.randomisable and field.reason:
            rows.append(f"{'':<20} why not: {field.reason}")
    return "\n".join(rows)


def variance_floors() -> tuple:
    """Per-channel variance floor for the detector, in each channel's own
    units squared, from the measured reporting resolution.

    A modem reports RSRP in whole dBm and holds the same value for most
    samples, so an EWMA variance estimate collapses during a hold run and the
    next 1 dB step scores as a large excursion. Flooring each channel's
    variance at its resolution stops that. See MonitorConfig.variance_floor.
    """
    from esim_selfhealing.schemas import FEATURE_NAMES
    return tuple((TELEMETRY[n].resolution or 0.0) ** 2 for n in FEATURE_NAMES)
