"""A simulated GSMA SGP.22 RSP back end: SM-DP+ / SM-SR and the eUICC itself.

It exists so the whole loop is runnable end-to-end without touching live
infrastructure. Two surfaces:

* read-only queries used by the REASON stage tools;
* ``execute(command)`` used by the ACT stage, which mutates eUICC state,
  verifies the PQC signature, and can fail probabilistically per action.

Replace this class with a real ES2+/ES9+/ES10x client and nothing upstream
changes - `Orchestrator` only depends on these method names.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .crypto_pqc import SignatureVerifier
from .schemas import ActionType, Command


# ---- CLASS: EuiccState ----
@dataclass
class EuiccState:
    """The subset of eUICC state the agent can observe and change."""

    euicc_id: str
    active_isdp: str = "ISD-P#1"
    installed_isdp: List[str] = field(default_factory=lambda: ["ISD-P#1", "ISD-P#2-bootstrap"])
    checksum_ok: bool = True
    key_epoch: int = 7
    smsr_key_epoch: int = 7
    smdp_reachable: bool = True
    apn: str = "primary"
    radio_alarm: bool = False
    last_ota_results: List[str] = field(default_factory=lambda: ["OK", "OK", "OK"])
    rsrp_dbm: float = -92.0
    drop_rate: float = 0.015
    latency_ms: float = 120.0

    # ---- METHOD: EuiccState.healthy ----
    def healthy(self) -> bool:
        return (
            self.checksum_ok
            and self.smdp_reachable
            and self.key_epoch == self.smsr_key_epoch
            and not self.radio_alarm
        )


#: How each fault class corrupts the simulated eUICC.
FAULT_EFFECTS = {
    "isdp_corruption": dict(checksum_ok=False, last_ota_results=["FAIL", "FAIL", "OK"]),
    "smdp_session_outage": dict(smdp_reachable=False, last_ota_results=["TIMEOUT", "TIMEOUT", "FAIL"]),
    "radio_degradation": dict(radio_alarm=True, rsrp_dbm=-114.0, drop_rate=0.24),
    "key_desync": dict(key_epoch=6, last_ota_results=["AUTH_FAIL", "AUTH_FAIL", "OK"]),
}

#: P(action actually fixes the fault) - the simulator's ground truth. PLAN
#: never sees these; it must estimate them from retrieved precedent.
REMEDIATION_EFFICACY: Dict[str, Dict[ActionType, float]] = {
    "isdp_corruption": {
        ActionType.PROFILE_REPUSH: 0.88,
        ActionType.ISDP_SWITCH: 0.40,
        ActionType.ISDP_DELETE_REPROVISION: 0.97,
        ActionType.BEARER_FAILOVER: 0.05,
        ActionType.KEY_ROTATION: 0.10,
        ActionType.NO_OP: 0.02,
    },
    "smdp_session_outage": {
        ActionType.BEARER_FAILOVER: 0.80,
        ActionType.PROFILE_REPUSH: 0.30,
        ActionType.ISDP_SWITCH: 0.15,
        ActionType.KEY_ROTATION: 0.05,
        ActionType.ISDP_DELETE_REPROVISION: 0.35,
        ActionType.NO_OP: 0.20,
    },
    "radio_degradation": {
        ActionType.BEARER_FAILOVER: 0.72,
        ActionType.NO_OP: 0.50,
        ActionType.PROFILE_REPUSH: 0.08,
        ActionType.ISDP_SWITCH: 0.10,
        ActionType.KEY_ROTATION: 0.02,
        ActionType.ISDP_DELETE_REPROVISION: 0.10,
    },
    "key_desync": {
        ActionType.KEY_ROTATION: 0.90,
        ActionType.PROFILE_REPUSH: 0.28,
        ActionType.ISDP_DELETE_REPROVISION: 0.85,
        ActionType.ISDP_SWITCH: 0.12,
        ActionType.BEARER_FAILOVER: 0.05,
        ActionType.NO_OP: 0.03,
    },
}

#: Endpoint each action is dispatched to.
ENDPOINTS: Dict[ActionType, str] = {
    ActionType.PROFILE_REPUSH: "ES9+/ES10x:DownloadProfile+EnableProfile",
    ActionType.ISDP_SWITCH: "ES10c:EnableProfile",
    ActionType.BEARER_FAILOVER: "ES6:UpdateConnectivityParameters",
    ActionType.KEY_ROTATION: "ES8+:EstablishSecureChannel(rotate)",
    ActionType.ISDP_DELETE_REPROVISION: "ES10c:DeleteProfile + ES9+:DownloadProfile",
    ActionType.NO_OP: "local:NoOp",
}

#: Inverse command for each reversible action.
INVERSE_ENDPOINTS: Dict[ActionType, str] = {
    ActionType.PROFILE_REPUSH: "ES10c:EnableProfile(previous)",
    ActionType.ISDP_SWITCH: "ES10c:EnableProfile(previous)",
    ActionType.BEARER_FAILOVER: "ES6:UpdateConnectivityParameters(primary)",
    ActionType.KEY_ROTATION: "ES8+:RestoreKeyEpoch",
    ActionType.NO_OP: "local:NoOp",
    # ISDP_DELETE_REPROVISION intentionally absent - irreversible.
}


# ---- CLASS: ExecutionResult ----
@dataclass
class ExecutionResult:
    ok: bool
    latency_ms: float
    detail: str


# ---- CLASS: RSPClient ----
class RSPClient:
    """Simulated RSP infrastructure for one or more eUICCs."""

    # ---- METHOD: RSPClient.__init__ ----
    def __init__(self, verifier: Optional[SignatureVerifier] = None, seed: int = 5) -> None:
        self.verifier = verifier
        self.rng = random.Random(seed)
        self.devices: Dict[str, EuiccState] = {}
        self.active_fault: Dict[str, Optional[str]] = {}
        self._shared: Dict[str, int] = {}
        self.audit: List[str] = []

    # -- device lifecycle --------------------------------------------------
    # ---- METHOD: RSPClient.device ----
    def device(self, euicc_id: str) -> EuiccState:
        if euicc_id not in self.devices:
            self.devices[euicc_id] = EuiccState(euicc_id=euicc_id)
            self.active_fault[euicc_id] = None
        return self.devices[euicc_id]

    # ---- METHOD: RSPClient.inject_fault ----
    def inject_fault(self, euicc_id: str, fault: Optional[str]) -> None:
        """Put the simulated device into the state implied by a fault class."""
        dev = self.device(euicc_id)
        # reset to healthy first
        healthy = EuiccState(euicc_id=euicc_id)
        for f in ("active_isdp", "checksum_ok", "key_epoch", "smsr_key_epoch",
                  "smdp_reachable", "apn", "radio_alarm", "last_ota_results",
                  "rsrp_dbm", "drop_rate", "latency_ms"):
            setattr(dev, f, getattr(healthy, f))
        self.active_fault[euicc_id] = fault
        if fault and fault in FAULT_EFFECTS:
            for k, v in FAULT_EFFECTS[fault].items():
                setattr(dev, k, list(v) if isinstance(v, list) else v)
        self.audit.append(f"[sim] fault={fault} injected on {euicc_id}")

    # -- read-only tool surface (REASON) -----------------------------------
    # ---- METHOD: RSPClient.query_euicc_state ----
    def query_euicc_state(self, euicc_id: str) -> str:
        d = self.device(euicc_id)
        return (
            f"active_isdp={d.active_isdp} installed={len(d.installed_isdp)} "
            f"checksum_mismatch={not d.checksum_ok} "
            f"key_epoch_mismatch={d.key_epoch != d.smsr_key_epoch} "
            f"euicc_key_epoch={d.key_epoch} smsr_key_epoch={d.smsr_key_epoch}"
        )

    # ---- METHOD: RSPClient.query_ota_sessions ----
    def query_ota_sessions(self, euicc_id: str) -> str:
        d = self.device(euicc_id)
        return (
            f"last_sessions={d.last_ota_results} smdp_reachable={d.smdp_reachable} "
            f"apn={d.apn}"
        )

    # ---- METHOD: RSPClient.query_audit_trail ----
    def query_audit_trail(self, euicc_id: str) -> str:
        entries = [a for a in self.audit if euicc_id in a][-5:]
        return f"audit_entries={entries or ['(none)']}"

    # ---- METHOD: RSPClient.query_device_kpi ----
    def query_device_kpi(self, euicc_id: str) -> str:
        d = self.device(euicc_id)
        return (
            f"rsrp_dbm={d.rsrp_dbm:.1f} drop_rate={d.drop_rate:.3f} "
            f"latency_ms={d.latency_ms:.0f} radio_alarm={d.radio_alarm}"
        )

    # -- write surface (ACT) ----------------------------------------------
    # ---- METHOD: RSPClient.execute ----
    def execute(self, cmd: Command) -> ExecutionResult:
        """Dispatch a signed command. Signature is verified at the endpoint."""
        t0 = time.perf_counter()
        euicc_id = cmd.payload.get("euicc_id", "")
        dev = self.device(euicc_id)

        if self.verifier is not None:
            if cmd.signature is None or not self.verifier.verify(cmd):
                self.audit.append(f"[rsp] REJECTED unsigned/invalid command on {euicc_id}")
                return ExecutionResult(False, (time.perf_counter() - t0) * 1000,
                                       "signature verification failed at RSP endpoint")

        fault = self.active_fault.get(euicc_id)
        efficacy = REMEDIATION_EFFICACY.get(fault or "", {}).get(cmd.action, 0.5 if fault is None else 0.05)
        ok = self.rng.random() < efficacy

        # Apply the state change the command represents.
        self._apply(dev, cmd.action, ok)
        if ok and fault is not None:
            self.active_fault[euicc_id] = None

        self.audit.append(
            f"[rsp] {cmd.command_id} {cmd.action.value} -> {ENDPOINTS[cmd.action]} "
            f"on {euicc_id} result={'OK' if ok else 'FAIL'}"
        )
        # Simulated network + RSP processing time.
        latency = {
            ActionType.NO_OP: 1.0,
            ActionType.ISDP_SWITCH: 900.0,
            ActionType.PROFILE_REPUSH: 4000.0,
            ActionType.BEARER_FAILOVER: 1500.0,
            ActionType.KEY_ROTATION: 2500.0,
            ActionType.ISDP_DELETE_REPROVISION: 9000.0,
        }[cmd.action] * self.rng.uniform(0.85, 1.15)
        return ExecutionResult(ok, latency, f"{ENDPOINTS[cmd.action]} {'succeeded' if ok else 'failed'}")

    # ---- METHOD: RSPClient._apply ----
    def _apply(self, dev: EuiccState, action: ActionType, ok: bool) -> None:
        if action == ActionType.PROFILE_REPUSH and ok:
            dev.checksum_ok = True
            dev.last_ota_results = ["OK"] + dev.last_ota_results[:2]
        elif action == ActionType.ISDP_SWITCH:
            others = [p for p in dev.installed_isdp if p != dev.active_isdp]
            if others:
                dev.active_isdp = others[0]
        elif action == ActionType.BEARER_FAILOVER:
            dev.apn = "bootstrap"
            if ok:
                dev.smdp_reachable = True
                dev.radio_alarm = False
        elif action == ActionType.KEY_ROTATION and ok:
            dev.key_epoch += 1
            dev.smsr_key_epoch = dev.key_epoch
        elif action == ActionType.ISDP_DELETE_REPROVISION and ok:
            dev.checksum_ok = True
            dev.active_isdp = "ISD-P#1"
            dev.key_epoch = dev.smsr_key_epoch

    # ---- METHOD: RSPClient.rollback ----
    def rollback(self, inverse: Command) -> ExecutionResult:
        t0 = time.perf_counter()
        self.audit.append(f"[rsp] ROLLBACK {inverse.command_id} {inverse.endpoint}")
        return ExecutionResult(True, (time.perf_counter() - t0) * 1000 + 800.0,
                               f"rollback via {inverse.endpoint} completed")

    # -- fleet helper ------------------------------------------------------
    # ---- METHOD: RSPClient.register_shared_profile ----
    def register_shared_profile(self, euicc_id: str, devices: int) -> None:
        """Record that this eUICC's profile is shared across `devices` devices."""
        self._shared[euicc_id] = int(devices)

    # ---- METHOD: RSPClient.fleet_size ----
    def fleet_size(self, euicc_id: str) -> int:
        """Devices sharing the profile/session an action would touch.

        In deployment this is an inventory lookup; PLAN calls it per incident,
        which is what lets an identical diagnosis produce a different outcome on
        a single handset and on a shared fleet profile.
        """
        return self._shared.get(euicc_id, 1)
