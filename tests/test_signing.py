from __future__ import annotations

import pytest

from pipe1_license_server.signing import (
    EntitlementSigner,
    SignatureVerificationError,
    generate_private_key_b64,
    verify_entitlement_envelope,
)


def test_entitlement_signature_roundtrip() -> None:
    signer = EntitlementSigner(generate_private_key_b64(), "server-test-key")
    payload = {"license_id": "lic_test", "device_id": "pipe1-device"}
    envelope = signer.sign(payload)

    verified = verify_entitlement_envelope(
        envelope, {signer.key_id: signer.public_key_b64}
    )

    assert verified == payload


def test_entitlement_signature_rejects_tampering() -> None:
    signer = EntitlementSigner(generate_private_key_b64(), "server-test-key")
    envelope = signer.sign({"license_id": "lic_test", "plan": "standard"})
    envelope["payload"] = {**envelope["payload"], "plan": "enterprise"}

    with pytest.raises(SignatureVerificationError, match="invalid signature"):
        verify_entitlement_envelope(envelope, {signer.key_id: signer.public_key_b64})
