from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PrivateFormat,
    PublicFormat,
    NoEncryption,
)


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def generate_private_key_b64() -> str:
    private_key = Ed25519PrivateKey.generate()
    raw = private_key.private_bytes(
        encoding=Encoding.Raw,
        format=PrivateFormat.Raw,
        encryption_algorithm=NoEncryption(),
    )
    return _b64url_encode(raw)


@dataclass(frozen=True)
class EntitlementSigner:
    private_key_b64: str
    key_id: str

    @property
    def _private_key(self) -> Ed25519PrivateKey:
        return Ed25519PrivateKey.from_private_bytes(_b64url_decode(self.private_key_b64))

    @property
    def public_key_b64(self) -> str:
        public_key = self._private_key.public_key()
        raw = public_key.public_bytes(encoding=Encoding.Raw, format=PublicFormat.Raw)
        return _b64url_encode(raw)

    def sign(self, payload: dict[str, Any]) -> dict[str, Any]:
        signature = self._private_key.sign(canonical_json_bytes(payload))
        return {
            "payload": payload,
            "signature": _b64url_encode(signature),
            "alg": "EdDSA",
            "kid": self.key_id,
        }


class SignatureVerificationError(ValueError):
    pass


def verify_entitlement_envelope(
    envelope: dict[str, Any],
    public_keys: dict[str, str],
) -> dict[str, Any]:
    if envelope.get("alg") != "EdDSA":
        raise SignatureVerificationError("unsupported alg")
    kid = envelope.get("kid")
    if not isinstance(kid, str) or kid not in public_keys:
        raise SignatureVerificationError("unknown kid")
    payload = envelope.get("payload")
    signature = envelope.get("signature")
    if not isinstance(payload, dict) or not isinstance(signature, str):
        raise SignatureVerificationError("malformed entitlement")

    public_key = Ed25519PublicKey.from_public_bytes(_b64url_decode(public_keys[kid]))
    try:
        public_key.verify(_b64url_decode(signature), canonical_json_bytes(payload))
    except Exception as exc:
        raise SignatureVerificationError("invalid signature") from exc
    return payload

