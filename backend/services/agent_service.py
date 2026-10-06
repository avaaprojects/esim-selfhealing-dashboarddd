"""The one object that owns a live Orchestrator and answers every API call.

Deliberately free of FastAPI imports so it can be exercised directly from a
plain Python REPL or a test, which is also how it was verified.

State model: a single long-lived `AgentSession`. Memory D, the audit chain and
the PPO-Lagrangian policy all accumulate across runs within a session, which is
the point - the Memory and Learning screens are only meaningful if the agent is
allowed to keep what it learned. `reset()` starts a fresh session.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

import numpy as np

from esim_selfhealing import fields
from esim_selfhealing.config import DEFAULT
from esim_selfhealing.learn import RemediationEnv
from esim_selfhealing.monitor import FAULT_CLASSES, Monitor
from esim_selfhealing.orchestrator import LoopRecord, build_default_orchestrator
from esim_selfhealing.schemas import ACTION_SPACE, Observation, Status
from esim_selfhealing.telemetry import healthy_stream

from . import scenarios
from .instrumentation import instrument
from .realtime import RealtimeFeed
from .serialization import (
    ACTION_LABELS,
    FAULT_LABELS,
    FEATURE_META,
    _f,
    action_space_dict,
    audit_dict,
    belief_list,
    config_dict,
    diagnosis_dict,
    incident_dict,
    loop_record_dict,
    memory_record_dict,
    reading_dict,
    run_report_dict,
)

#: Ordered stages of the loop, as the UI pipeline renders them. OBSERVE and
#: VERIFY are presentation names for MONITOR and for the post-dispatch outcome
#: check inside ACT; the other five map 1:1 onto the core modules.
PIPELINE_STAGES: List[Dict[str, str]] = [
    {"key": "observe", "label": "OBSERVE", "module": "monitor.py",
     "latency_key": "monitor"},
    {"key": "reason", "label": "REASON", "module": "reason.py",
     "latency_key": "reason"},
    {"key": "plan", "label": "PLAN", "module": "plan.py", "latency_key": "plan"},
    {"key": "safety", "label": "SAFETY", "module": "safety.py",
     "latency_key": "act_gate"},
    {"key": "act", "label": "ACT", "module": "act.py", "latency_key": "act_dispatch"},
    {"key": "verify", "label": "VERIFY", "module": "act.py + rsp_api.py",
     "latency_key": "act_dispatch"},
    {"key": "learn", "label": "LEARN", "module": "learn.py", "latency_key": "learn"},
]


class AgentSession:
    """Thread-safe façade over one wired-up Orchestrator."""

    def __init__(self, enable_learning: bool = True) -> None:
        self._lock = threading.Lock()
        self.reset(enable_learning=enable_learning)

    # -- lifecycle ---------------------------------------------------------
    def reset(self, enable_learning: bool = True) -> None:
        # A reset that happens while REAL-TIME DATA is streaming must stop the
        # feed first - otherwise its background thread would keep pushing
        # observations into an orchestrator this method is about to replace.
        existing_feed = getattr(self, "realtime", None)
        if existing_feed is not None and existing_feed.running:
            existing_feed.stop()

        self.orch = build_default_orchestrator(
            enable_learning=enable_learning, update_every=4, verbose=False
        )
        self.capture = instrument(self.orch)
        self.enable_learning = enable_learning
        self.started_at = time.time()
        self.records: List[LoopRecord] = []
        #: incident_id -> "simulation" | "realtime", so screens can label where
        #: an incident came from without changing what LoopRecord carries.
        self.record_source: Dict[str, str] = {}
        self.run_index = 0
        self.last_report: Optional[Dict[str, Any]] = None
        self.last_scenario: Optional[str] = None
        self.last_run_at: Optional[float] = None
        #: Processed operator inputs (Input screen -> Output screen) AND scenario
        #: runs (the Run self-healing button), by run id. Both open the Output.
        self.input_runs: Dict[str, Dict[str, Any]] = {}
        self.run_seq = 0                      # numbers the scenario runs: RUN-0001
        self.latest_run_id: Optional[str] = None
        self.activity: List[Dict[str, Any]] = []
        self.memory_seeded = len(self.orch.memory)
        self._baseline_readings = self._warm_baseline()
        self.realtime = RealtimeFeed(self)
        self._log("session", "Agent session initialised",
                  f"|D| = {self.memory_seeded} historical records")

    def _warm_baseline(self) -> List[Dict[str, Any]]:
        """A short healthy window so LIVE MONITOR has real data before any run.

        This is a genuine `Monitor` fed a genuine `healthy_stream()` on a
        throwaway detector - not fabricated numbers - so the chart shows the
        detector sitting quietly under threshold, which is the honest resting
        state of the system.
        """
        scratch = Monitor(DEFAULT.monitor)
        readings = [scratch.update(o) for o in healthy_stream(n=120, seed=1)]
        return [reading_dict(r) for r in readings]

    def _log(self, kind: str, title: str, detail: str = "",
             incident_id: Optional[str] = None) -> None:
        self.activity.insert(0, {
            "ts": time.time(), "kind": kind, "title": title,
            "detail": detail, "incident_id": incident_id,
        })
        del self.activity[200:]

    # -- RUN SELF-HEALING --------------------------------------------------
    def run(self, scenario_key: str = scenarios.DEFAULT_SCENARIO,
            enable_learning: Optional[bool] = None) -> Dict[str, Any]:
        scenario = scenarios.get(scenario_key)
        if scenario is None:
            raise KeyError(scenario_key)

        with self._lock:
            if enable_learning is not None and enable_learning != self.enable_learning:
                # Toggling the learner requires a rebuilt orchestrator, because
                # PPOLagrangian is constructed in the factory.
                self.reset(enable_learning=enable_learning)

            # Each run targets a distinct set of eUICCs. This is not cosmetic:
            # `Monitor` keys its EWMA baseline and refractory window per eUICC,
            # so replaying the same device would mean the detector has already
            # absorbed that fault into mu and Sigma and the second run would
            # legitimately open no incident at all. Fresh devices keep every run
            # comparable while memory D, the audit chain and the policy continue
            # to accumulate across runs - which is the whole point of a session.
            self.run_index += 1
            streams = []
            #: per-run device id -> the registry device it was derived from.
            #: Runs after the first target fresh ids so the detector is not
            #: scoring a device whose baseline it has already adapted to, and
            #: those ids are deliberately not in the registry. Without this
            #: map the Output screen could not name the client for any run
            #: but the first.
            device_base: Dict[str, str] = {}
            for stream in scenario.streams():
                base = stream.euicc_id
                stream.euicc_id = self._device_id(base, self.run_index)
                device_base[stream.euicc_id] = base
                streams.append(stream)

            # Inventory next: PLAN reads fleet size per incident, so a shared
            # profile has to be registered before the stream is consumed.
            for euicc_id, devices in scenario.fleet.items():
                self.orch.rsp.register_shared_profile(
                    self._device_id(euicc_id, self.run_index), devices=devices
                )

            t0 = time.perf_counter()
            before = len(self.records)
            r0 = len(self.orch.monitor.readings)
            aggregate_samples = 0
            aggregate_fp = 0
            new_records: List[LoopRecord] = []

            for stream in streams:
                report = self.orch.run(stream)
                aggregate_samples += report.samples_seen
                aggregate_fp += report.false_positives
                new_records.extend(report.records)

            self.records.extend(new_records)
            for rec in new_records:
                self.record_source[rec.incident.incident_id] = "simulation"
            wall_ms = (time.perf_counter() - t0) * 1000.0
            devices = [s.euicc_id for s in streams]
            r1 = len(self.orch.monitor.readings)

        # Rebuild a RunReport-shaped payload over the whole scenario, since a
        # multi-device scenario runs several streams.
        from esim_selfhealing.orchestrator import RunReport
        merged = RunReport(records=new_records, samples_seen=aggregate_samples,
                           false_positives=aggregate_fp)

        # A scenario run is a run like any other: recorded in `input_runs` with
        # its own id, so "Run self-healing" can open the same Output screen that
        # "Process data" opens. `mode` is "scenario", and the commit below stands
        # in for the input selection a processed dataset would have had.
        finished = time.time()
        self.run_seq += 1
        run_id = f"RUN-{self.run_seq:04d}"
        run = {
            "id": run_id, "mode": "scenario", "status": "complete",
            "dataset_id": scenario_key, "dataset_title": scenario.name,
            "dataset_path": None, "device_ids": devices,
            "device_base": device_base,
            "started_at": finished - wall_ms / 1000.0, "finished_at": finished,
            "wall_ms": _f(wall_ms), "samples": aggregate_samples,
            "false_positives": aggregate_fp,
            "incident_ids": [r.incident.incident_id for r in new_records],
            "reading_range": [r0, r1],
            "scenario": self._scenario_dict(scenario),
        }
        self.input_runs[run_id] = run
        self.latest_run_id = run_id

        self.last_scenario = scenario_key
        self.last_run_at = time.time()
        self.last_report = {
            "scenario": self._scenario_dict(scenario),
            "wall_ms": _f(wall_ms),
            "devices": devices,
            "run_index": self.run_index,
            "report": run_report_dict(merged),
            "records": [self._record_payload(r) for r in new_records],
            "learning": self.learning(),
            "audit_verified": self.orch.actuator.audit.verify_chain(),
            # Only meaningful once more than one device is in play (shared
            # fleet / night shift): a single-device run is not "the estate".
            "estate": self._estate_summary(scenario, devices, new_records,
                                           aggregate_samples) if len(devices) > 1 else None,
            "run_id": run_id,
        }

        self._log("run", f"Ran scenario: {scenario.name}",
                  f"{aggregate_samples} samples, {len(new_records)} incident(s)")
        for rec in new_records:
            self._log(
                "incident",
                f"{rec.incident.incident_id} -> {rec.status.value}",
                f"{FAULT_LABELS.get(rec.diagnosis.fault_class, rec.diagnosis.fault_class)}"
                f" / {ACTION_LABELS.get(rec.selected_action.value, '-') if rec.selected_action else 'no feasible action'}",
                incident_id=rec.incident.incident_id,
            )
        assert before + len(new_records) == len(self.records)
        return self.last_report

    @staticmethod
    def _device_id(base: str, run_index: int) -> str:
        """Stable per-run device id derived from the scenario's base eUICC id.

        Kept numeric and the same width as the original so it still reads as an
        eUICC identifier rather than a debug label.
        """
        if run_index <= 1:
            return base
        try:
            return str(int(base) + run_index).zfill(len(base))
        except ValueError:
            return f"{base}{run_index}"

    def _record_payload(self, rec: LoopRecord) -> Dict[str, Any]:
        return loop_record_dict(rec, self.capture.get(rec.incident.incident_id))

    def _scenario_dict(self, s: scenarios.Scenario) -> Dict[str, Any]:
        return {
            "key": s.key, "name": s.name, "description": s.description,
            "expectation": s.expectation, "fault_class": s.fault_class,
            "fault_label": FAULT_LABELS.get(s.fault_class, s.fault_class),
            "use_case": s.use_case,
            "fleet": s.fleet,
            "category": s.category,
            "category_label": scenarios.category_label(s.category),
        }

    def _estate_summary(self, scenario: scenarios.Scenario, devices: List[str],
                        new_records: List[LoopRecord], samples: int) -> Dict[str, Any]:
        """Whole-estate view for a multi-device run (shared_fleet, night_shift).

        Every figure here is read off the records this run actually produced,
        or off the fleet inventory PLAN itself consulted - nothing is a
        per-device animation standing in for an aggregate the backend never
        computed.
        """
        open_incidents = [r for r in new_records if r.status is not Status.AUTO_REMEDIATED]
        affected = {r.incident.observation.euicc_id for r in new_records}
        # Read the *registered* fleet size back off the RSP client (keyed by
        # the run's actual, possibly re-numbered, device ids) rather than the
        # scenario's static `fleet` dict, which is keyed by the original ids.
        blast_radius = max([self.orch.rsp.fleet_size(d) for d in devices] + [1])
        return {
            "devices_monitored": len(devices),
            "telemetry_samples": samples,
            "incidents_total": len(new_records),
            "active_incidents": len(open_incidents),
            "affected_profiles": len(affected),
            "blast_radius": blast_radius,
            "overall_state": "DEGRADED" if new_records else "HEALTHY",
        }

    # -- REAL-TIME DATA ------------------------------------------------
    def ingest_realtime(self, obs: Observation) -> Optional[LoopRecord]:
        """One live sample -> the real MONITOR/REASON/PLAN/SAFETY/ACT/LEARN
        pipeline. Called by `RealtimeFeed` off its background thread.

        This is the same effect `Orchestrator.run` has per sample inside a
        scenario stream - `monitor.update` then, only if it triggers,
        `handle_incident` - just invoked one observation at a time so a
        continuously-running feed can drive it instead of a finite list.
        """
        with self._lock:
            reading = self.orch.monitor.update(obs)
            if self.orch.rsp.active_fault.get(obs.euicc_id) != obs.ground_truth_fault:
                self.orch.rsp.inject_fault(obs.euicc_id, obs.ground_truth_fault)
            if not reading.triggered:
                return None
            rec = self.orch.handle_incident(reading.incident)
            rec.stage_latency_ms["monitor"] = reading.latency_ms
            self.records.append(rec)
            self.record_source[rec.incident.incident_id] = "realtime"

        self._log(
            "incident",
            f"{rec.incident.incident_id} -> {rec.status.value} (real-time)",
            f"{FAULT_LABELS.get(rec.diagnosis.fault_class, rec.diagnosis.fault_class)}"
            f" / {ACTION_LABELS.get(rec.selected_action.value, '-') if rec.selected_action else 'no feasible action'}",
            incident_id=rec.incident.incident_id,
        )
        return rec

    def start_realtime(self) -> Dict[str, Any]:
        return self.realtime.start()

    def stop_realtime(self) -> Dict[str, Any]:
        return self.realtime.stop()

    def realtime_status(self) -> Dict[str, Any]:
        return self.realtime.status()

    def inject_realtime_fault(self, fault_class: str, euicc_id: Optional[str] = None,
                              duration: int = 40) -> Dict[str, Any]:
        return self.realtime.inject_fault(fault_class, euicc_id=euicc_id, duration=duration)

    # -- read models -------------------------------------------------------
    def health(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "mode": "DEMO / SIMULATION",
            "started_at": self.started_at,
            "learning_enabled": self.enable_learning,
        }

    def status(self) -> Dict[str, Any]:
        """The left-hand column of the Overview screen.

        Every panel is read off live objects: the RSP client's device table,
        the signer, the envelope config, and the most recent monitor reading.
        """
        latest = self.orch.monitor.readings[-1] if self.orch.monitor.readings else None
        latest_dict = reading_dict(latest) if latest else (
            self._baseline_readings[-1] if self._baseline_readings else None
        )

        open_incidents = [r for r in self.records
                          if r.status is not Status.AUTO_REMEDIATED]
        active_faults = {k: v for k, v in self.orch.rsp.active_fault.items() if v}
        signer = self.orch.actuator.signer
        env = self.orch.actuator.envelope.cfg

        score = (latest_dict or {}).get("score") or 0.0
        threshold = (latest_dict or {}).get("threshold") or 1.0
        healthy = score <= threshold and not active_faults

        def panel(key, label, state, value, detail, ranges=None):
            out = {"key": key, "label": label, "state": state,
                   "value": value, "detail": detail}
            if ranges:
                out["ranges"] = ranges
            return out

        _feat = (latest_dict or {}).get("features", {}) or {}
        _base = (latest_dict or {}).get("baseline", {}) or {}
        _band = (latest_dict or {}).get("band", {}) or {}

        def channel(name: str, label: str, precision: int = 3, sigma: float = 3.0):
            """One channel's live value against its own nominal range.

            The range is the detector's EWMA baseline plus or minus `sigma`
            of its floored standard deviation - the same envelope the charts
            shade and the same one the score is computed from, re-derived
            every sample. So these bounds move as the device's own normal
            moves, which is what makes them meaningful on a gateway under
            tree cover or a unit roaming between cells: a fixed cut-off
            would call that device faulty for behaving the way it always
            behaves there.
            """
            v, mu, sd = _feat.get(name), _base.get(name), _band.get(name)
            if v is None or mu is None or sd is None:
                return None
            lo, hi = mu - sigma * sd, mu + sigma * sd
            # Held inside the channel's physical limits, from the one field
            # specification the whole project reads. Without this the band
            # arithmetic prints bounds that cannot exist - a nominal AKA
            # failure rate starting at -6.6%, a nominal latency starting at
            # -42 ms - whenever the covariance is wide, which is exactly
            # when someone is looking at the screen.
            spec = fields.TELEMETRY.get(name)
            if spec is not None:
                lo, hi = spec.clamp(lo), spec.clamp(hi)
            return {"key": name, "label": label, "value": _f(v),
                    "low": _f(lo), "high": _f(hi), "sigma": sigma,
                    "precision": precision, "in_range": bool(lo <= v <= hi)}

        def layer(state_if_out: str, *channels):
            """Panel state from whether every channel sits in its own range."""
            rs = [c for c in channels if c]
            if not rs:
                return "NOMINAL", []
            return ("NOMINAL" if all(c["in_range"] for c in rs) else state_if_out), rs

        euicc_state, euicc_ranges = layer(
            "DEGRADED",
            channel("aka_fail_rate", "AKA failure rate", 2),
            channel("ota_fail_rate", "OTA failure rate", 4),
        )
        if active_faults:
            euicc_state = "FAULT"
        net_state, net_ranges = layer(
            "DEGRADED",
            channel("rsrp_dbm", "RSRP", 1),
            channel("drop_rate", "Session drop rate", 4),
            channel("latency_ms", "OTA latency", 1),
        )
        auth_state, auth_ranges = layer("DEGRADED", channel("aka_fail_rate", "AKA failure rate", 2))
        rsp_state, rsp_ranges = layer("DEGRADED", channel("ota_fail_rate", "OTA failure rate", 4))

        return {
            "mode": "DEMO / SIMULATION",
            "system_health": {
                "state": "HEALTHY" if healthy else "DEGRADED",
                # A bounded 0..1 headroom figure: how far the latest anomaly
                # score sits below the chi-square threshold.
                "headroom": _f(max(0.0, min(1.0, 1.0 - score / threshold))) if threshold else None,
                "score": _f(score),
                "threshold": _f(threshold),
            },
            "panels": [
                panel("euicc", "eUICC state", euicc_state,
                      f"{len(self.orch.rsp.devices)} device(s)",
                      ", ".join(f"{k[-6:]}: {FAULT_LABELS.get(v, v)}"
                                for k, v in active_faults.items())
                      or "provisioning counters within their nominal range",
                      euicc_ranges),
                panel("network", "Network", net_state,
                      f"RSRP {_feat.get('rsrp_dbm', 0):.1f} dBm" if latest_dict else "n/a",
                      f"drop rate {_feat.get('drop_rate', 0):.3f}" if latest_dict else "",
                      net_ranges),
                panel("auth", "Authentication", auth_state,
                      f"AKA fail {_feat.get('aka_fail_rate', 0):.2f}" if latest_dict else "n/a",
                      "per 100 attach attempts", auth_ranges),
                panel("rsp", "RSP / eSIM", rsp_state,
                      f"OTA fail {_feat.get('ota_fail_rate', 0):.3f}" if latest_dict else "n/a",
                      f"{len(self.orch.actuator.audit)} audit entr(ies)", rsp_ranges),
                panel("security", "Security",
                      "POST-QUANTUM" if signer.is_post_quantum else "STUB SIGNER",
                      signer.algorithm,
                      f"audit chain {'intact' if self.orch.actuator.audit.verify_chain() else 'BROKEN'}"
                      f" | B_max={env.b_max} rho_max={env.rho_max}"),
            ],
            "counters": {
                "incidents_total": len(self.records),
                "open_incidents": len(open_incidents),
                "auto_remediated": sum(1 for r in self.records
                                       if r.status is Status.AUTO_REMEDIATED),
                "human_in_loop": len(self.orch.actuator.human_queue),
                "escalations": len(self.orch.actuator.escalations),
                "memory_records": len(self.orch.memory),
                "audit_entries": len(self.orch.actuator.audit),
                "policy_updates": len(self.orch.learner.history) if self.orch.learner else 0,
            },
            "last_scenario": self.last_scenario,
            "last_run_at": self.last_run_at,
            "latest_reading": latest_dict,
            "data_source": {
                "mode": "realtime" if self.realtime.running else "simulation",
                "realtime": self.realtime.status(),
            },
        }

    def telemetry(self, limit: int = 120, euicc_id: Optional[str] = None,
                  incident_id: Optional[str] = None,
                  run_id: Optional[str] = None) -> Dict[str, Any]:
        """Telemetry series for the whole session, or for one device.

        With no filter this is exactly what it always was. `euicc_id` narrows it
        to that device's own readings; `incident_id` additionally centres the
        window on the reading that opened the incident and reports where it sits
        (`marker_index`), so a report can show "the telemetry around the fault".
        A filtered request never falls back to the healthy baseline - that
        baseline is not this device's data.
        """
        features = [{"key": k, **FEATURE_META[k]} for k in FEATURE_META]
        readings = self.orch.monitor.readings

        if euicc_id is None and incident_id is None and run_id is None:
            series = ([reading_dict(r) for r in readings[-limit:]] if readings
                      else self._baseline_readings[-limit:])
            return {
                "features": features,
                "threshold": series[-1]["threshold"] if series else None,
                "source": "monitor.readings" if readings else "healthy_stream baseline",
                "samples": series,
            }

        pool = list(readings)
        run = self.input_runs.get(run_id) if run_id else None
        if run is not None:
            # Only what this processed input produced, not earlier passes over
            # the same device.
            lo, hi = run["reading_range"]
            pool = pool[lo:hi]
            if euicc_id is None and incident_id is None:
                euicc_id = run["device_ids"][0]
        if incident_id is not None and euicc_id is None:
            euicc_id = self._incident_device(incident_id)
        device_readings = [r for r in pool if r.observation.euicc_id == euicc_id]

        marker: Optional[int] = None
        window = device_readings[-limit:]
        if incident_id is not None:
            idx = next((i for i, r in enumerate(device_readings)
                        if r.incident is not None
                        and r.incident.incident_id == incident_id), None)
            if idx is not None:
                # Two thirds of the window before the trigger, the rest after.
                after = min(limit // 3, len(device_readings) - idx - 1)
                start = max(0, idx - (limit - after - 1))
                window = device_readings[start: idx + after + 1]
                marker = idx - start

        series = [reading_dict(r) for r in window]
        return {
            "features": features,
            "threshold": series[-1]["threshold"] if series else None,
            "source": (f"monitor.readings for eUICC {euicc_id}" if series
                       else "no telemetry recorded for this device yet"),
            "samples": series,
            "euicc_id": euicc_id,
            "incident_id": incident_id,
            "marker_index": marker,
        }

    def _incident_device(self, incident_id: str) -> Optional[str]:
        for rec in self.records:
            if rec.incident.incident_id == incident_id:
                return rec.incident.observation.euicc_id
        return None

    def incident_device(self, incident_id: str) -> Optional[str]:
        """The eUICC an incident was opened on, or None if it is not in the session."""
        return self._incident_device(incident_id)

    def has_incident(self, incident_id: str) -> bool:
        return self._incident_device(incident_id) is not None


    # -- PROCESSING AN OPERATOR INPUT ---------------------------------------
    def process_dataset(self, run_id: str, observations: List[Observation],
                        fleet_sizes: Dict[str, int], meta: Dict[str, Any]) -> Dict[str, Any]:
        """Run a stored dataset through the real loop (MONITOR -> ... -> LEARN).

        Same code path as `Orchestrator.run` for a scenario. Each device's
        detector baseline and simulated eUICC are reset first, so processing
        the same recording twice gives the same answer instead of being
        suppressed by the first pass; memory D, the audit chain and the policy
        keep accumulating. Fleet sizes come from the client registry, so a
        shared profile really does raise PLAN's blast radius.
        """
        devices = sorted({o.euicc_id for o in observations})
        with self._lock:
            for eid in devices:
                self.orch.monitor.reset_device(eid)
                self.orch.rsp.inject_fault(eid, None)
                self.orch.rsp.register_shared_profile(eid, devices=int(fleet_sizes.get(eid, 1)))
            r0 = len(self.orch.monitor.readings)
            t0 = time.perf_counter()
            report = self.orch.run(observations)
            wall_ms = (time.perf_counter() - t0) * 1000.0
            self.records.extend(report.records)
            for rec in report.records:
                self.record_source[rec.incident.incident_id] = "simulation"
            r1 = len(self.orch.monitor.readings)

        finished = time.time()
        run = {
            "id": run_id, "mode": "synthetic", **meta,
            "device_ids": devices, "status": "complete",
            "started_at": finished - wall_ms / 1000.0, "finished_at": finished,
            "wall_ms": _f(wall_ms), "samples": report.samples_seen,
            "false_positives": report.false_positives,
            "incident_ids": [r.incident.incident_id for r in report.records],
            "reading_range": [r0, r1],
        }
        self.input_runs[run_id] = run
        self.latest_run_id = run_id
        self.last_scenario, self.last_run_at = f"{run_id} · {meta.get('dataset_id')}", finished
        self._log("run", f"Processed input {run_id}",
                  f"{report.samples_seen} samples, {len(report.records)} incident(s)")
        for rec in report.records:
            self._log("incident", f"{rec.incident.incident_id} -> {rec.status.value}",
                      f"{FAULT_LABELS.get(rec.diagnosis.fault_class, rec.diagnosis.fault_class)}",
                      incident_id=rec.incident.incident_id)
        return run

    def process_realtime(self, run_id: str, device_id: str, meta: Dict[str, Any]) -> Dict[str, Any]:
        """Attach an operator input to the live feed for one device.

        The feed keeps its own baseline, so nothing is reset. Starting it here
        is part of "process": a real-time input with the feed off would have
        nothing to process.
        """
        started_feed = not self.realtime.running
        if started_feed:
            self.realtime.start()
        run = {
            "id": run_id, "mode": "realtime", **meta, "device_ids": [device_id],
            "status": "monitoring", "started_at": time.time(), "finished_at": None,
            "wall_ms": None, "samples": 0, "false_positives": 0, "incident_ids": [],
            "reading_range": [len(self.orch.monitor.readings), None],
            "record_start": len(self.records), "started_feed": started_feed,
        }
        self.input_runs[run_id] = run
        self.latest_run_id = run_id
        self._log("run", f"Processing input {run_id} from the real-time feed", f"device {device_id[-6:]}")
        return run

    def run_state(self, run_id: str) -> Optional[Dict[str, Any]]:
        """A run with its live figures filled in (real-time runs keep moving)."""
        run = self.input_runs.get(run_id)
        if run is None:
            return None
        run = dict(run)
        if run["mode"] == "realtime":
            lo, _ = run["reading_range"]
            eids = set(run["device_ids"])
            run["samples"] = sum(1 for r in list(self.orch.monitor.readings)[lo:]
                                 if r.observation.euicc_id in eids)
            run["incident_ids"] = [rec.incident.incident_id
                                   for rec in list(self.records)[run["record_start"]:]
                                   if rec.incident.observation.euicc_id in eids]
            run["feed_running"] = bool(self.realtime.running)
        return run

    def latest_run(self) -> Optional[Dict[str, Any]]:
        return self.run_state(self.latest_run_id) if self.latest_run_id else None

    def incident_capture(self, incident_id: str):
        """Plan / command / eUICC before-after captured for one incident."""
        return self.capture.get(incident_id)

    def audit_for(self, incident_id: str) -> List[Dict[str, Any]]:
        return [audit_dict(e) for e in self.orch.actuator.audit.entries if e.incident_id == incident_id]

    def in_human_queue(self, incident_id: str) -> bool:
        return any(q.get("incident_id") == incident_id for q in self.orch.actuator.human_queue)

    def signer_algorithm(self) -> str:
        return self.orch.actuator.signer.algorithm

    # -- DEVICES -----------------------------------------------------------
    def devices(self) -> List[Dict[str, Any]]:
        """Every device the dashboard knows about, from the objects that
        already exist: the real-time grid, every eUICC that produced telemetry,
        every eUICC the RSP client holds state for, and every device an
        incident was opened against. Nothing here is a separate device list.
        """
        rsp = self.orch.rsp
        live = {d["euicc_id"]: d["cell_id"] for d in self.realtime.devices}
        table: Dict[str, Dict[str, Any]] = {}

        def slot(eid: str) -> Dict[str, Any]:
            return table.setdefault(eid, {
                "euicc_id": eid, "cell_id": None, "samples": 0, "last_ts": None,
                "last_score": None, "threshold": None, "incident_count": 0,
                "open_incidents": 0, "latest_incident_id": None,
            })

        for eid, cell in live.items():
            slot(eid)["cell_id"] = cell
        for r in list(self.orch.monitor.readings):
            o = r.observation
            s = slot(o.euicc_id)
            s["cell_id"] = o.cell_id
            s["samples"] += 1
            s["last_ts"] = _f(o.ts)
            s["last_score"] = _f(r.score)
            s["threshold"] = _f(r.threshold)
        for eid in list(rsp.devices):
            slot(eid)
        for rec in list(self.records):          # chronological
            s = slot(rec.incident.observation.euicc_id)
            s["incident_count"] += 1
            if rec.status is not Status.AUTO_REMEDIATED:
                s["open_incidents"] += 1
            s["latest_incident_id"] = rec.incident.incident_id

        out = []
        for eid, s in table.items():
            dev = rsp.devices.get(eid)
            fault = rsp.active_fault.get(eid)
            healthy = dev.healthy() if dev is not None else True
            # FAULT: the eUICC is unhealthy right now. RECOVERED: a fault was
            # injected earlier but the agent's action left the eUICC healthy.
            state = "FAULT" if not healthy else ("RECOVERED" if fault else "NOMINAL")
            out.append({
                **s,
                "state": state,
                "active_fault": fault,
                "active_fault_label": FAULT_LABELS.get(fault, fault) if fault else None,
                "fleet_size": rsp.fleet_size(eid),
                "source": "realtime" if eid in live else "simulation",
            })
        out.sort(key=lambda d: (-d["open_incidents"], d["state"] == "NOMINAL", d["euicc_id"]))
        return out

    def has_device(self, euicc_id: str) -> bool:
        return any(d["euicc_id"] == euicc_id for d in self.devices())

    def device(self, euicc_id: str) -> Optional[Dict[str, Any]]:
        """One device: summary, live eUICC state, latest reading, its incidents."""
        summary = next((d for d in self.devices() if d["euicc_id"] == euicc_id), None)
        if summary is None:
            return None
        dev = self.orch.rsp.devices.get(euicc_id)
        euicc = None
        if dev is not None:
            euicc = {
                "active_isdp": dev.active_isdp,
                "installed_isdp": list(dev.installed_isdp),
                "checksum_ok": bool(dev.checksum_ok),
                "key_epoch": dev.key_epoch,
                "smsr_key_epoch": dev.smsr_key_epoch,
                "smdp_reachable": bool(dev.smdp_reachable),
                "apn": dev.apn,
                "radio_alarm": bool(dev.radio_alarm),
                "last_ota_results": list(dev.last_ota_results),
                "rsrp_dbm": _f(dev.rsrp_dbm),
                "drop_rate": _f(dev.drop_rate),
                "latency_ms": _f(dev.latency_ms),
            }
        latest = next((reading_dict(r) for r in reversed(list(self.orch.monitor.readings))
                       if r.observation.euicc_id == euicc_id), None)
        return {
            **summary,
            "euicc": euicc,
            "latest_reading": latest,
            "incidents": [i for i in self.incidents()
                          if i["observation"]["euicc_id"] == euicc_id],
        }

    def incidents(self) -> List[Dict[str, Any]]:
        out = []
        for rec in reversed(self.records):
            cap = self.capture.get(rec.incident.incident_id)
            out.append({
                **incident_dict(rec.incident),
                "status": rec.status.value,
                "fault_class": rec.diagnosis.fault_class,
                "fault_label": FAULT_LABELS.get(rec.diagnosis.fault_class,
                                                rec.diagnosis.fault_class),
                "confidence": _f(rec.diagnosis.confidence),
                "selected_action": rec.selected_action.value if rec.selected_action else None,
                "selected_label": (ACTION_LABELS.get(rec.selected_action.value)
                                   if rec.selected_action else None),
                "dispatched": bool(rec.act_result and rec.act_result.dispatched),
                "success": bool(rec.act_result and rec.act_result.success),
                "admissible": bool(rec.act_result and rec.act_result.report.admissible),
                "total_latency_ms": _f(rec.total_latency_ms()),
                "fleet_size": cap.fleet_size if cap else 1,
                "source": self.record_source.get(rec.incident.incident_id, "simulation"),
            })
        return out

    def incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        for rec in self.records:
            if rec.incident.incident_id == incident_id:
                payload = self._record_payload(rec)
                payload["stages"] = self._stage_view(rec, payload)
                payload["source"] = self.record_source.get(incident_id, "simulation")
                return payload
        return None

    def _stage_view(self, rec: LoopRecord, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """The OBSERVE -> ... -> LEARN trace, one entry per stage."""
        inc = rec.incident
        act = payload.get("act") or {}
        plan = payload.get("plan") or {}
        top_belief = belief_list(inc.belief)[0]
        lat = rec.stage_latency_ms

        stages = [
            {"key": "observe", "label": "OBSERVE",
             "headline": f"g_t = {inc.anomaly_score:.2f} > chi2 = {inc.threshold:.2f}",
             "detail": f"most likely class {top_belief['label']} at {top_belief['p']:.0%}",
             "latency_ms": _f(lat.get("monitor")), "ok": True},
            {"key": "reason", "label": "REASON",
             "headline": payload["diagnosis"]["fault_label"],
             "detail": f"{payload['diagnosis']['tool_calls']} tool call(s), "
                       f"{len(payload['diagnosis']['retrieved_ids'])} precedent(s), "
                       f"confidence {payload['diagnosis']['confidence']:.0%}",
             "latency_ms": _f(lat.get("reason")), "ok": True},
            {"key": "plan", "label": "PLAN",
             "headline": (plan.get("selected") or {}).get("label", "no feasible action"),
             "detail": f"{len(plan.get('ranked', []))} feasible, "
                       f"{len(plan.get('infeasible', []))} filtered out",
             "latency_ms": _f(lat.get("plan")),
             "ok": bool(plan.get("selected"))},
            {"key": "safety", "label": "SAFETY",
             "headline": "ACTION APPROVED" if (act.get("admissibility") or {}).get("admissible")
                         else "ACTION BLOCKED",
             "detail": ", ".join((act.get("admissibility") or {}).get("failed_clauses", []))
                       or "all four clauses of Sigma passed",
             "latency_ms": _f(lat.get("act_gate")),
             "ok": bool((act.get("admissibility") or {}).get("admissible"))},
            {"key": "act", "label": "ACT",
             "headline": (payload.get("command") or {}).get("endpoint", "not dispatched"),
             "detail": act.get("message", "no command dispatched"),
             "latency_ms": _f(lat.get("act_dispatch")),
             "ok": bool(act.get("dispatched"))},
            {"key": "verify", "label": "VERIFY",
             "headline": "RESOLVED" if act.get("success")
                         else ("ROLLED BACK" if act.get("rolled_back") else "NOT RESOLVED"),
             "detail": f"status {rec.status.value}",
             "latency_ms": None,
             "ok": bool(act.get("success"))},
            {"key": "learn", "label": "LEARN",
             "headline": f"reward {rec.reward:+.3f}",
             "detail": f"costs c = ({', '.join(f'{c:.3f}' for c in rec.costs)}); "
                       f"(I_t, a*, status) appended to D",
             "latency_ms": _f(lat.get("learn")), "ok": True},
        ]
        return stages

    def trace(self, incident_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if incident_id:
            return self.incident(incident_id)
        if not self.records:
            return None
        return self.incident(self.records[-1].incident.incident_id)

    def memory(self, limit: int = 60, query_incident: Optional[str] = None) -> Dict[str, Any]:
        records = self.orch.memory.records
        retrieved: List[Dict[str, Any]] = []
        if query_incident:
            cap = self.capture.get(query_incident)
            if cap:
                from .serialization import neighbours_dict
                retrieved = neighbours_dict(cap.neighbours)

        by_action: Dict[str, Dict[str, int]] = {}
        for r in records:
            slot = by_action.setdefault(r.action, {"total": 0, "success": 0})
            slot["total"] += 1
            slot["success"] += int(r.success)

        return {
            "total": len(records),
            "seeded": self.memory_seeded,
            "learned_this_session": max(0, len(records) - self.memory_seeded),
            "encoder": {
                "type": type(self.orch.memory.encoder).__name__,
                "dim": self.orch.memory.encoder.dim,
                "min_similarity": _f(DEFAULT.reason.min_similarity),
                "top_n": DEFAULT.reason.top_n,
            },
            "outcome_by_action": [
                {"action": a, "label": ACTION_LABELS.get(a, a),
                 "total": v["total"], "success": v["success"],
                 "success_rate": _f(v["success"] / v["total"]) if v["total"] else None}
                for a, v in sorted(by_action.items())
            ],
            "retrieved": retrieved,
            # Newest first: the session's own incidents sit at the top.
            "records": [memory_record_dict(r) for r in reversed(records[-limit:])],
        }

    def learning(self) -> Dict[str, Any]:
        learner = self.orch.learner
        if learner is None:
            return {"enabled": False, "history": [], "message": "learning disabled"}

        history = [
            {
                "iteration": s.iteration,
                "mean_reward": _f(s.mean_reward),
                "cost_estimates": [_f(c) for c in s.cost_estimates],
                "lambdas": [_f(l) for l in s.lambdas],
                "policy_loss": _f(s.policy_loss),
                "entropy": _f(s.entropy),
                "violation_rate": _f(s.violation_rate),
            }
            for s in learner.history
        ]

        # Greedy action distribution of the CURRENT policy over the belief
        # simplex, evaluated on the same surrogate environment learn.py ships.
        env = RemediationEnv(b_max=DEFAULT.envelope.b_max,
                             rho_max=DEFAULT.envelope.rho_max)
        mix = env.action_mix(learner.policy, n=600)
        evaluation = env.evaluate(learner.policy, n=600)

        latest = history[-1] if history else None
        return {
            "enabled": True,
            "updates": len(history),
            "buffer_size": len(learner.buffer),
            "update_every": self.orch.update_every,
            "d_limits": [_f(d) for d in learner.cfg.d_limits],
            "lambdas": [_f(x) for x in learner.lambdas],
            "peak_lambdas": [_f(x) for x in learner.peak_lambdas],
            "cost_names": ["envelope violation c_1", "excess blast radius c_2"],
            "latest": latest,
            "history": history,
            "action_distribution": [
                {"action": a, "label": ACTION_LABELS.get(a, a), "p": _f(p)}
                for a, p in mix.items()
            ],
            "evaluation": {k: _f(v) for k, v in evaluation.items()},
            "plan_weights": {
                "base": list(map(_f, (DEFAULT.plan.w1, DEFAULT.plan.w2, DEFAULT.plan.w3))),
                "current": list(map(_f, self.orch.planner.weights)),
            },
        }

    def safety(self) -> Dict[str, Any]:
        """Envelope configuration plus every gate decision taken this session."""
        env = self.orch.actuator.envelope.cfg
        signer = self.orch.actuator.signer
        gates = []
        for rec in reversed(self.records):
            if rec.act_result is None:
                continue
            payload = self._record_payload(rec)
            gates.append({
                "incident_id": rec.incident.incident_id,
                "action": rec.selected_action.value if rec.selected_action else None,
                "label": (ACTION_LABELS.get(rec.selected_action.value)
                          if rec.selected_action else None),
                "status": rec.status.value,
                "gate_latency_ms": _f(rec.act_result.gate_latency_ms),
                **(payload["act"]["admissibility"]),
            })
        return {
            "envelope": {
                "b_max": env.b_max,
                "tau_rollback_s": _f(env.tau_rollback_s),
                "rho_max": _f(env.rho_max),
                "rho_human": _f(env.rho_human),
                "require_signature": bool(env.require_signature),
            },
            "signer": {
                "algorithm": signer.algorithm,
                "post_quantum": bool(signer.is_post_quantum),
            },
            "audit_chain_valid": self.orch.actuator.audit.verify_chain(),
            "audit": [audit_dict(e) for e in reversed(self.orch.actuator.audit.entries)],
            "human_queue": self.orch.actuator.human_queue[::-1],
            "escalations": self.orch.actuator.escalations[::-1],
            "gates": gates,
            "latency_budget_ms": _f(DEFAULT.latency.act_ms),
        }

    def actions(self) -> Dict[str, Any]:
        """Action space plus, where available, this session's observed outcomes."""
        observed: Dict[str, Dict[str, int]] = {}
        for rec in self.records:
            if rec.selected_action is None or rec.act_result is None:
                continue
            slot = observed.setdefault(rec.selected_action.value,
                                       {"dispatched": 0, "succeeded": 0})
            slot["dispatched"] += int(rec.act_result.dispatched)
            slot["succeeded"] += int(rec.act_result.success)
        space = action_space_dict()
        for entry in space:
            entry["observed"] = observed.get(entry["action"],
                                             {"dispatched": 0, "succeeded": 0})
        return {"actions": space, "endpoints": self._endpoints()}

    @staticmethod
    def _endpoints() -> List[Dict[str, Any]]:
        from esim_selfhealing.rsp_api import ENDPOINTS, INVERSE_ENDPOINTS
        return [
            {"action": a.value, "label": ACTION_LABELS.get(a.value, a.value),
             "endpoint": ep, "inverse": INVERSE_ENDPOINTS.get(a)}
            for a, ep in ENDPOINTS.items()
        ]

    def simulation(self) -> Dict[str, Any]:
        return {
            "scenarios": [self._scenario_dict(s) for s in scenarios.CATALOGUE.values()],
            "categories": scenarios.CATEGORY_ORDER,
            "default": scenarios.DEFAULT_SCENARIO,
            "last_scenario": self.last_scenario,
            "last_result": self.last_report,
        }

    def pipeline(self) -> Dict[str, Any]:
        """Static stage metadata + the latency profile of the most recent run."""
        latest = self.records[-1] if self.records else None
        profile = latest.stage_latency_ms if latest else {}
        return {
            "stages": [
                {**s, "latency_ms": _f(profile.get(s["latency_key"]))}
                for s in PIPELINE_STAGES
            ],
            "fault_classes": [
                {"key": c, "label": FAULT_LABELS.get(c, c)} for c in FAULT_CLASSES
            ],
            "action_space": [a.value for a in ACTION_SPACE],
            "config": config_dict(DEFAULT),
        }


#: Module-level singleton used by the API layer.
SESSION = AgentSession()
