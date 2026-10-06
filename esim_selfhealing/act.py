"""Stage 4 - ACT: safety-gated tool-calling on live RSP APIs.

    Dispatch(a*) <=> Admissible(a*, Sigma) = 1

Order of operations is the whole point of this stage, and it is deliberate:

    1. build cmd_{a*}
    2. build the inverse a^-1 FIRST  (no inverse -> inadmissible, never dispatched)
    3. sign:  sigma_{a*} = Sign_{SK_orch}(cmd_{a*})   under ML-DSA
    4. evaluate Admissible(a*, Sigma)  - all four clauses, independently
    5. dispatch only on a full pass; otherwise route to human-in-loop / escalate
    6. append an immutable log entry linking cmd, sigma and the ReAct trace H_k

Step 2 before step 5 is what makes "rollback is checked before dispatch, not
improvised after" true in code rather than in prose.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .crypto_pqc import Signer, canonical_bytes
from .rsp_api import ENDPOINTS, INVERSE_ENDPOINTS, RSPClient
from .safety import SafetyEnvelope
from .schemas import (
    ActResult,
    ActionType,
    Candidate,
    Command,
    Diagnosis,
    Status,
)


# ---- CLASS: AuditEntry ----
@dataclass
class AuditEntry:
    """Immutable log record linking cmd_{a*}, sigma_{a*} and the trace H_k."""

    entry_id: str
    prev_hash: str
    incident_id: str
    command_id: str
    action: str
    endpoint: str
    signature_hex: str
    trace_digest: str          # hash of the ReAct trace that produced the action
    status: str
    admissible: bool
    failed_clauses: List[str]
    ts: float
    entry_hash: str = ""

    # ---- METHOD: AuditEntry.compute_hash ----
    def compute_hash(self) -> str:
        body = {k: v for k, v in self.__dict__.items() if k != "entry_hash"}
        return hashlib.sha3_256(
            json.dumps(body, sort_keys=True, default=str).encode()
        ).hexdigest()


# ---- CLASS: AuditLog ----
class AuditLog:
    """Hash-chained append-only log. Any edit breaks `verify_chain()`."""

    # ---- METHOD: AuditLog.__init__ ----
    def __init__(self) -> None:
        self.entries: List[AuditEntry] = []

    # ---- METHOD: AuditLog.append ----
    def append(self, entry: AuditEntry) -> AuditEntry:
        entry.prev_hash = self.entries[-1].entry_hash if self.entries else "0" * 64
        entry.entry_hash = entry.compute_hash()
        self.entries.append(entry)
        return entry

    # ---- METHOD: AuditLog.verify_chain ----
    def verify_chain(self) -> bool:
        prev = "0" * 64
        for e in self.entries:
            if e.prev_hash != prev or e.entry_hash != e.compute_hash():
                return False
            prev = e.entry_hash
        return True

    # ---- METHOD: AuditLog.__len__ ----
    def __len__(self) -> int:
        return len(self.entries)


# ---- CLASS: Actuator ----
class Actuator:
    """Builds, signs, gates and dispatches the winning action a*."""

    # ---- METHOD: Actuator.__init__ ----
    def __init__(self, rsp: RSPClient, envelope: SafetyEnvelope, signer: Signer) -> None:
        self.rsp = rsp
        self.envelope = envelope
        self.signer = signer
        self.audit = AuditLog()
        self.human_queue: List[Dict[str, Any]] = []
        self.escalations: List[Dict[str, Any]] = []

    # -- command construction ---------------------------------------------
    # ---- METHOD: Actuator.build_command ----
    def build_command(self, action: ActionType, euicc_id: str,
                      extra: Optional[Dict[str, Any]] = None) -> Command:
        payload: Dict[str, Any] = {"euicc_id": euicc_id}
        payload.update(extra or {})
        return Command(
            command_id=f"CMD-{uuid.uuid4().hex[:10].upper()}",
            action=action,
            endpoint=ENDPOINTS[action],
            payload=payload,
        )

    # ---- METHOD: Actuator.build_inverse ----
    def build_inverse(self, cmd: Command) -> Optional[Command]:
        """a^-1, pre-computed BEFORE dispatch. None => inadmissible."""
        endpoint = INVERSE_ENDPOINTS.get(cmd.action)
        if endpoint is None:
            return None
        inv = Command(
            command_id=f"INV-{cmd.command_id[4:]}",
            action=cmd.action,
            endpoint=endpoint,
            payload={**cmd.payload, "restore": True},
        )
        inv.signature = self.signer.sign(inv)
        return inv

    # -- the gated dispatch ------------------------------------------------
    # ---- METHOD: Actuator.execute ----
    def execute(self, candidate: Candidate, diagnosis: Diagnosis,
                euicc_id: str) -> ActResult:
        t0 = time.perf_counter()

        cmd = self.build_command(candidate.action, euicc_id,
                                 extra={"fault_class": diagnosis.fault_class})
        cmd.inverse = self.build_inverse(cmd)              # step 2, before signing
        cmd.signature = self.signer.sign(cmd)              # sigma_{a*}

        report = self.envelope.admissible(candidate, cmd)
        status = self.envelope.route(candidate, report)

        dispatched = success = rolled_back = False
        message = ""
        exec_latency = 0.0

        if status is Status.AUTO_REMEDIATED:
            result = self.rsp.execute(cmd)
            dispatched = True
            success = result.ok
            exec_latency = result.latency_ms
            message = result.detail
            if not result.ok and cmd.inverse is not None:
                rb = self.rsp.rollback(cmd.inverse)
                rolled_back = rb.ok
                message += f"; {rb.detail}"
            if not result.ok:
                # Dispatched, admissible, rolled back - but the fault is still
                # there. It leaves the loop for a human rather than silently
                # retrying, which is what would turn one bad action into ten.
                status = Status.HUMAN_IN_LOOP
                self.human_queue.append({
                    "incident_id": diagnosis.incident_id,
                    "action": candidate.action.value,
                    "risk": candidate.risk,
                    "failed_clauses": ["remediation dispatched but did not resolve"],
                    "command_id": cmd.command_id,
                })
        elif status is Status.HUMAN_IN_LOOP:
            self.human_queue.append({
                "incident_id": diagnosis.incident_id,
                "action": candidate.action.value,
                "risk": candidate.risk,
                "failed_clauses": report.failed_clauses(),
                "command_id": cmd.command_id,
            })
            message = "queued for operator approval: " + ", ".join(report.failed_clauses())
        else:
            self.escalations.append({
                "incident_id": diagnosis.incident_id,
                "diagnosis": diagnosis.text,
                "trace_steps": len(diagnosis.trace),
                "failed_clauses": report.failed_clauses(),
            })
            message = "escalated to NOC with full reasoning trace"

        self.audit.append(AuditEntry(
            entry_id=f"AUD-{uuid.uuid4().hex[:8].upper()}",
            prev_hash="",
            incident_id=diagnosis.incident_id,
            command_id=cmd.command_id,
            action=candidate.action.value,
            endpoint=cmd.endpoint,
            signature_hex=(cmd.signature or b"").hex()[:32] + "...",
            trace_digest=trace_digest(diagnosis),
            status=status.value,
            admissible=report.admissible,
            failed_clauses=report.failed_clauses(),
            ts=time.time(),
        ))

        gate_latency = (time.perf_counter() - t0) * 1000.0
        return ActResult(
            incident_id=diagnosis.incident_id,
            action=candidate.action,
            status=status,
            report=report,
            dispatched=dispatched,
            success=success,
            latency_ms=gate_latency + exec_latency,
            gate_latency_ms=gate_latency,
            rolled_back=rolled_back,
            message=message,
        )


# ---- FUNCTION: trace_digest ----
def trace_digest(diagnosis: Diagnosis) -> str:
    """Stable hash of H_k, so the audit entry pins the reasoning that caused a*."""
    body = [
        {"k": s.k, "thought": s.thought, "tool": s.tool, "obs": s.observation}
        for s in diagnosis.trace
    ]
    return hashlib.sha3_256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]


# ---- FUNCTION: command_fingerprint ----
def command_fingerprint(cmd: Command) -> str:
    return hashlib.sha3_256(canonical_bytes(cmd)).hexdigest()[:16]
