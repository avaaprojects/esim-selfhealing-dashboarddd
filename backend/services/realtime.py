"""REAL-TIME DATA mode: a server-side telemetry generator, running on a
background thread, that feeds one `Observation` at a time through the *same*
`Orchestrator` the Simulation screen uses.

This is not a frontend animation. Every sample produced here is pushed
through `AgentSession.ingest_realtime`, which calls the real
`Monitor.update` / `Orchestrator.handle_incident` - the identical code path
`Orchestrator.run` takes for a scenario stream, just one observation at a
time instead of an in-memory list. Nothing about MONITOR, REASON, PLAN,
SAFETY, ACT, VERIFY or LEARN is duplicated or special-cased for this mode.

Honesty note (see backend/app.py docstring and the dashboard footer): this is
a prototype server/grid telemetry generator running inside this process. It
does not connect to a telecom operator, a live cellular network, or
production eSIM infrastructure.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

from esim_selfhealing.schemas import FEATURE_DIM, Observation
from esim_selfhealing.telemetry import FAULT_SIGNATURES, NOMINAL_MEAN, NOMINAL_STD

#: A small live "grid" of devices the feed cycles through every tick. Fixed,
#: readable IDs so a demo audience can follow one device across samples.
DEFAULT_DEVICES: List[Dict[str, str]] = [
    {"euicc_id": "89330000000100001", "cell_id": "CELL-LIVE-1"},
    {"euicc_id": "89330000000100002", "cell_id": "CELL-LIVE-2"},
    {"euicc_id": "89330000000100003", "cell_id": "CELL-LIVE-3"},
    {"euicc_id": "89330000000100004", "cell_id": "CELL-LIVE-4"},
]

#: How long an owner-injected fault stays active, in samples, unless overridden.
DEFAULT_FAULT_DURATION = 40


@dataclass
class _PendingFault:
    euicc_id: str
    fault: str
    remaining: int


class RealtimeFeed:
    """Owns one background thread that manufactures a continuous telemetry
    stream and pushes each sample into the live agent session.

    Thread-safety: this object only ever calls back into
    `AgentSession.ingest_realtime`, which takes the session's own lock. The
    feed's own state (counters, pending fault) is only touched from the
    background thread except for `start`/`stop`/`inject_fault`, which are
    guarded by `self._admin_lock`.
    """

    def __init__(self, session: "AgentSession", tick_seconds: float = 1.0) -> None:
        self.session = session
        self.tick_seconds = tick_seconds
        self.devices = DEFAULT_DEVICES
        self._admin_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.running = False
        self.started_at: Optional[float] = None
        self.samples_received = 0
        self.last_ts: Optional[float] = None
        self.last_incident_id: Optional[str] = None
        self._rng = np.random.default_rng(2026)
        self._i: Dict[str, int] = {d["euicc_id"]: 0 for d in self.devices}
        self._pending: Optional[_PendingFault] = None

    # -- lifecycle -----------------------------------------------------
    def start(self) -> Dict[str, Any]:
        with self._admin_lock:
            if not self.running:
                self._stop.clear()
                self.running = True
                self.started_at = time.time()
                self._thread = threading.Thread(target=self._loop, daemon=True)
                self._thread.start()
                self.session._log(
                    "realtime", "Real-time data started",
                    f"{len(self.devices)} live device(s), {self.tick_seconds:.1f}s tick",
                )
        return self.status()

    def stop(self) -> Dict[str, Any]:
        with self._admin_lock:
            if self.running:
                self._stop.set()
                self.running = False
                self.session._log("realtime", "Real-time data stopped",
                                  f"{self.samples_received} sample(s) processed this session")
        return self.status()

    def inject_fault(self, fault: str, euicc_id: Optional[str] = None,
                     duration: int = DEFAULT_FAULT_DURATION) -> Dict[str, Any]:
        if fault not in FAULT_SIGNATURES:
            raise KeyError(fault)
        target = euicc_id or self.devices[0]["euicc_id"]
        with self._admin_lock:
            self._pending = _PendingFault(euicc_id=target, fault=fault,
                                          remaining=max(1, int(duration)))
        self.session._log("realtime", "Fault injected (simulation / demo only)",
                          f"{fault} -> eUICC {target[-6:]}")
        return self.status()

    def status(self) -> Dict[str, Any]:
        return {
            "running": self.running,
            "source_label": "Prototype Server / Grid Telemetry",
            "started_at": self.started_at,
            "samples_received": self.samples_received,
            "last_ts": self.last_ts,
            "last_incident_id": self.last_incident_id,
            "devices": [d["euicc_id"] for d in self.devices],
            "tick_seconds": self.tick_seconds,
            "fault_pending": bool(self._pending),
        }

    # -- generation ------------------------------------------------------
    def _sample(self, device: Dict[str, str]) -> Observation:
        euicc_id = device["euicc_id"]
        i = self._i[euicc_id]
        self._i[euicc_id] += 1

        x = NOMINAL_MEAN + self._rng.normal(0.0, NOMINAL_STD, size=FEATURE_DIM)
        fault_name: Optional[str] = None

        pending = self._pending
        if pending is not None and pending.euicc_id == euicc_id and pending.remaining > 0:
            x = x + FAULT_SIGNATURES[pending.fault]
            fault_name = pending.fault
            pending.remaining -= 1
            if pending.remaining <= 0:
                self._pending = None

        x[0] = max(0.0, x[0])
        x[2] = float(np.clip(x[2], 0.0, 1.0))
        x[3] = max(1.0, x[3])
        x[4] = float(np.clip(x[4], 0.0, 1.0))

        return Observation(
            ts=time.time(),
            euicc_id=euicc_id,
            cell_id=device["cell_id"],
            features=x.tolist(),
            ground_truth_fault=fault_name,
        )

    def _loop(self) -> None:
        while not self._stop.is_set():
            for device in self.devices:
                if self._stop.is_set():
                    break
                obs = self._sample(device)
                rec = self.session.ingest_realtime(obs)
                self.samples_received += 1
                self.last_ts = obs.ts
                if rec is not None:
                    self.last_incident_id = rec.incident.incident_id
                elif self.samples_received % 5 == 0:
                    self.session._log(
                        "telemetry", "Telemetry received",
                        f"{self.samples_received} sample(s) so far · "
                        f"eUICC {device['euicc_id'][-6:]}",
                    )
            self._stop.wait(self.tick_seconds)
