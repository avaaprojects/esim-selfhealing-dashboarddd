"""Post-quantum command signing:  sigma_a = Sign_{SK_orch}(cmd_a)  under ML-DSA.

The deck specifies ML-DSA (NIST FIPS 204). Two backends implement one
interface:

* ``MLDSASigner``   - real FIPS 204 via ``oqs`` (liboqs-python) when installed.
* ``StubSigner``    - HMAC-SHA3-256 over the canonical command encoding. Same
  API, same failure modes, no external dependency.

The stub is NOT post-quantum and is not a security control - it exists so the
reference implementation runs anywhere. `is_post_quantum` tells callers which
backend is live, and the ACT stage prints it, so a demo can never be mistaken
for a hardened deployment.

Canonicalisation matters as much as the primitive: both backends sign the same
deterministic JSON encoding of (action, endpoint, payload, issued_at), so a
signature cannot be replayed against a different payload with equal semantics.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Optional, Protocol

from .schemas import Command


# ---- FUNCTION: canonical_bytes ----
def canonical_bytes(cmd: Command) -> bytes:
    """Deterministic wire encoding of cmd_a - the exact bytes that get signed."""
    body = {
        "command_id": cmd.command_id,
        "action": cmd.action.value,
        "endpoint": cmd.endpoint,
        "payload": cmd.payload,
        "issued_at": round(cmd.issued_at, 3),
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


# ---- CLASS: Signer ----
class Signer(Protocol):
    algorithm: str
    is_post_quantum: bool

    # ---- METHOD: Signer.sign ----
    def sign(self, cmd: Command) -> bytes: ...


# ---- CLASS: SignatureVerifier ----
class SignatureVerifier(Protocol):
    algorithm: str
    is_post_quantum: bool

    # ---- METHOD: SignatureVerifier.verify ----
    def verify(self, cmd: Command) -> bool: ...


# ---- CLASS: StubSigner ----
class StubSigner:
    """HMAC-SHA3-256 stand-in. Deterministic, dependency-free, NOT quantum-safe."""

    algorithm = "HMAC-SHA3-256 (ML-DSA stub)"
    is_post_quantum = False

    # ---- METHOD: StubSigner.__init__ ----
    def __init__(self, secret: Optional[bytes] = None) -> None:
        self._secret = secret or os.environ.get("ESIM_ORCH_SECRET", "orchestrator-dev-key").encode()

    # ---- METHOD: StubSigner.sign ----
    def sign(self, cmd: Command) -> bytes:
        return hmac.new(self._secret, canonical_bytes(cmd), hashlib.sha3_256).digest()

    # ---- METHOD: StubSigner.verify ----
    def verify(self, cmd: Command) -> bool:
        if cmd.signature is None:
            return False
        return hmac.compare_digest(cmd.signature, self.sign(_unsigned_view(cmd)))


# ---- CLASS: MLDSASigner ----
class MLDSASigner:
    """Real ML-DSA-65 (FIPS 204) via liboqs. Requires `pip install oqs`."""

    algorithm = "ML-DSA-65 (FIPS 204)"
    is_post_quantum = True

    # ---- METHOD: MLDSASigner.__init__ ----
    def __init__(self, variant: str = "ML-DSA-65") -> None:
        try:
            import oqs  # type: ignore
        except ImportError as exc:                       # pragma: no cover
            raise RuntimeError(
                "liboqs-python not installed; use StubSigner or `pip install oqs`"
            ) from exc
        self._oqs = oqs
        self._variant = variant
        self._signer = oqs.Signature(variant)
        self.public_key = self._signer.generate_keypair()

    # ---- METHOD: MLDSASigner.sign ----
    def sign(self, cmd: Command) -> bytes:                # pragma: no cover
        return self._signer.sign(canonical_bytes(cmd))

    # ---- METHOD: MLDSASigner.verify ----
    def verify(self, cmd: Command) -> bool:               # pragma: no cover
        if cmd.signature is None:
            return False
        with self._oqs.Signature(self._variant) as v:
            return bool(v.verify(canonical_bytes(_unsigned_view(cmd)), cmd.signature, self.public_key))


# ---- FUNCTION: _unsigned_view ----
def _unsigned_view(cmd: Command) -> Command:
    """A copy with the signature stripped, so verify() re-derives the same bytes."""
    return Command(
        command_id=cmd.command_id,
        action=cmd.action,
        endpoint=cmd.endpoint,
        payload=cmd.payload,
        signature=None,
        inverse=None,
        issued_at=cmd.issued_at,
    )


# ---- FUNCTION: build_signer ----
def build_signer(prefer_pq: bool = True) -> Signer:
    """Return the strongest available backend, falling back to the stub."""
    if prefer_pq:
        try:
            return MLDSASigner()
        except RuntimeError:
            pass
    return StubSigner()
