"""Historical incident memory D and the retrieval used by REASON.

    e_t   = f_enc(o_t)                        in R^p
    N(e_t)= top-N argmax_{i in D} cos(e_t, f_enc(i))

The encoder here is a deterministic hashed-feature projection: numeric
telemetry is z-scored into fixed slots and the diagnosis text is hashed into
the remaining slots. It is dependency-free and reproducible, which is what a
reference implementation needs. Swap `HashingEncoder` for a sentence-embedding
model (or the eUICC-domain encoder you fine-tune) without touching callers -
only `encode()` is used elsewhere.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from .schemas import FEATURE_NAMES, ActionType, Observation, Status


# ---- CLASS: MemoryRecord ----
@dataclass
class MemoryRecord:
    """One closed incident (I_t, a*, status) written back to D."""

    record_id: str
    features: Sequence[float]
    fault_class: str
    diagnosis: str
    action: str
    status: str
    success: bool
    resolution_s: float
    tags: List[str] = field(default_factory=list)


# ---- CLASS: HashingEncoder ----
class HashingEncoder:
    """f_enc - deterministic o_t -> R^p, no model download required.

    Two blocks, each L2-normalised before being mixed:

      * numeric block - the telemetry standardised against a FIXED nominal
        operating point, then L2-normalised, so what survives is the *direction*
        of the fault signature: which metrics moved and in what proportion.
        Encoding raw values instead makes every record look alike, because RSRP
        near -92 dBm dominates the vector whether the device is healthy or not;
        squashing the z-scores (tanh, clipping) is just as bad, because it
        saturates the large deviations that carry the discriminating signal.
      * text block  - hashed tokens of the fault class and diagnosis text.

    Mixing normalised blocks means a query with no usable text still retrieves
    on telemetry similarity alone, instead of collapsing to noise.
    """

    NUMERIC_WEIGHT = 0.75
    TEXT_WEIGHT = 0.25

    # ---- METHOD: HashingEncoder.__init__ ----
    def __init__(self, dim: int = 16, numeric_slots: int = 5) -> None:
        self.dim = dim
        self.numeric_slots = min(numeric_slots, dim)
        # Fixed reference operating point (healthy fleet), used to standardise.
        from .telemetry import NOMINAL_MEAN, NOMINAL_STD
        self._ref_mean = np.asarray(NOMINAL_MEAN, dtype=float)[: self.numeric_slots]
        self._ref_std = np.asarray(NOMINAL_STD, dtype=float)[: self.numeric_slots]

    # ---- METHOD: HashingEncoder.encode ----
    def encode(self, features: Sequence[float], text: str = "") -> np.ndarray:
        v = np.zeros(self.dim, dtype=float)

        x = np.asarray(features, dtype=float)[: self.numeric_slots]
        z = (x - self._ref_mean) / np.clip(self._ref_std, 1e-9, None)
        num = z                                      # direction, not magnitude
        n = np.linalg.norm(num)
        if n > 0:
            v[: self.numeric_slots] = self.NUMERIC_WEIGHT * num / n

        if text and self.dim > self.numeric_slots:
            txt = np.zeros(self.dim - self.numeric_slots)
            for token in _tokenise(text):
                h = int(hashlib.blake2b(token.encode(), digest_size=8).hexdigest(), 16)
                txt[h % len(txt)] += 1.0 if (h >> 17) & 1 else -1.0
            n = np.linalg.norm(txt)
            if n > 0:
                v[self.numeric_slots:] = self.TEXT_WEIGHT * txt / n

        n = np.linalg.norm(v)
        return v / n if n > 0 else v


# ---- FUNCTION: _tokenise ----
def _tokenise(text: str) -> List[str]:
    return [t for t in "".join(c.lower() if c.isalnum() else " " for c in text).split() if len(t) > 2]


# ---- CLASS: IncidentMemory ----
class IncidentMemory:
    """D - append-only incident store with cosine top-N retrieval."""

    # ---- METHOD: IncidentMemory.__init__ ----
    def __init__(self, encoder: Optional[HashingEncoder] = None, dim: int = 16) -> None:
        self.encoder = encoder or HashingEncoder(dim=dim)
        self.records: List[MemoryRecord] = []
        self._matrix: Optional[np.ndarray] = None

    # -- write path --------------------------------------------------------
    # ---- METHOD: IncidentMemory.add ----
    def add(self, record: MemoryRecord) -> None:
        self.records.append(record)
        self._matrix = None      # invalidate the cached embedding matrix

    # ---- METHOD: IncidentMemory.add_many ----
    def add_many(self, records: Iterable[MemoryRecord]) -> None:
        for r in records:
            self.add(r)

    # ---- METHOD: IncidentMemory._embeddings ----
    def _embeddings(self) -> np.ndarray:
        if self._matrix is None:
            if not self.records:
                self._matrix = np.zeros((0, self.encoder.dim))
            else:
                self._matrix = np.vstack([
                    self.encoder.encode(r.features, f"{r.fault_class} {r.diagnosis}")
                    for r in self.records
                ])
        return self._matrix

    # -- read path ---------------------------------------------------------
    # ---- METHOD: IncidentMemory.retrieve ----
    def retrieve(
        self,
        observation: Observation,
        top_n: int = 3,
        min_similarity: float = 0.0,
        hint: str = "",
    ) -> List[Tuple[MemoryRecord, float]]:
        """N(e_t) - the top-N precedents above the similarity floor."""
        if not self.records:
            return []
        e_t = self.encoder.encode(observation.features, hint)
        sims = self._embeddings() @ e_t
        order = np.argsort(-sims)[: max(top_n * 3, top_n)]
        out: List[Tuple[MemoryRecord, float]] = []
        for i in order:
            s = float(sims[i])
            if s >= min_similarity:
                out.append((self.records[i], s))
            if len(out) == top_n:
                break
        return out

    # ---- METHOD: IncidentMemory.success_rate ----
    def success_rate(self, action: ActionType, neighbours: Sequence[Tuple[MemoryRecord, float]],
                     laplace: float = 1.0, prior: float = 0.5) -> float:
        """P_success(a) ~= (1/|N|) sum_i 1[outcome_i(a) = success], Laplace-smoothed."""
        hits = [r for r, _ in neighbours if r.action == action.value]
        if not hits:
            return prior
        wins = sum(1 for r in hits if r.success)
        return (wins + laplace * prior) / (len(hits) + laplace)

    # -- persistence -------------------------------------------------------
    # ---- METHOD: IncidentMemory.save ----
    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps([asdict(r) for r in self.records], indent=2))

    # ---- METHOD: IncidentMemory.load ----
    @classmethod
    def load(cls, path: str | Path, dim: int = 16) -> "IncidentMemory":
        mem = cls(dim=dim)
        raw = json.loads(Path(path).read_text())
        mem.add_many(MemoryRecord(**r) for r in raw)
        return mem

    # ---- METHOD: IncidentMemory.__len__ ----
    def __len__(self) -> int:
        return len(self.records)


# ---------------------------------------------------------------------------
# A small seeded corpus so the loop has precedent to reason over out of the box.
# ---------------------------------------------------------------------------
# ---- FUNCTION: seed_memory ----
def seed_memory(n_per_class: int = 12, seed: int = 11, dim: int = 16) -> IncidentMemory:
    """Build a synthetic D with realistic action/outcome statistics.

    The per-class action outcome rates below are the numbers PLAN will later
    recover as P_success(a) - they are the only 'prior knowledge' the agent has
    before its own incidents start accumulating.
    """
    from .telemetry import FAULT_SIGNATURES, NOMINAL_MEAN, NOMINAL_STD

    rng = np.random.default_rng(seed)
    mem = IncidentMemory(dim=dim)

    playbook: Dict[str, List[Tuple[str, float, str]]] = {
        # fault class -> [(action, empirical success rate, diagnosis text)]
        "isdp_corruption": [
            (ActionType.PROFILE_REPUSH.value, 0.86, "ISD-P checksum mismatch, profile re-push restored AKA"),
            (ActionType.ISDP_SWITCH.value, 0.41, "switched to secondary ISD-P, partial recovery"),
            (ActionType.ISDP_DELETE_REPROVISION.value, 0.95, "deleted and reprovisioned ISD-P after re-push failed"),
        ],
        "smdp_session_outage": [
            (ActionType.BEARER_FAILOVER.value, 0.78, "SM-DP+ unreachable over primary APN, failover to bootstrap"),
            (ActionType.PROFILE_REPUSH.value, 0.33, "re-push attempted while SM-DP+ still degraded"),
        ],
        "radio_degradation": [
            (ActionType.BEARER_FAILOVER.value, 0.71, "RSRP collapse on serving cell, moved bearer"),
            (ActionType.NO_OP.value, 0.52, "transient radio dip, self-recovered"),
        ],
        "key_desync": [
            (ActionType.KEY_ROTATION.value, 0.88, "eUICC and SM-SR key desync, session keys rotated"),
            (ActionType.PROFILE_REPUSH.value, 0.29, "re-push did not clear the AKA failures"),
        ],
    }

    rid = 0
    for fault, options in playbook.items():
        sig = FAULT_SIGNATURES[fault]
        for action, rate, text in options:
            for _ in range(n_per_class):
                x = NOMINAL_MEAN + rng.normal(0, NOMINAL_STD) + sig * rng.uniform(0.75, 1.25)
                ok = bool(rng.random() < rate)
                rid += 1
                mem.add(MemoryRecord(
                    record_id=f"HIST-{rid:04d}",
                    features=x.tolist(),
                    fault_class=fault,
                    diagnosis=text,
                    action=action,
                    status=Status.AUTO_REMEDIATED.value if ok else Status.HUMAN_IN_LOOP.value,
                    success=ok,
                    resolution_s=float(rng.uniform(3.0, 45.0)),
                    tags=[fault, action],
                ))
    return mem


__all__ = [
    "FEATURE_NAMES",
    "HashingEncoder",
    "IncidentMemory",
    "MemoryRecord",
    "seed_memory",
]
