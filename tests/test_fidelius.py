"""abha.fidelius against the reference implementation's published vectors.

Every constant below is copied from the Fidelius CLI README
(github.com/mgrmtech/fidelius-cli): two `gkm` key pairs, then an `e`
(encrypt) run and its output. Matching them byte for byte is the only proof
that an HIU running the reference code can decrypt what we send.
"""

from __future__ import annotations

import base64

import pytest

from abha import fidelius as f

REQUESTER = {
    "privateKey": "DMxHPri8d7IT23KgLk281zZenMfVHSdeamq0RhwlIBk=",
    "publicKey": "BAheD5rUqTy4V5xR4/6HWmYpopu5CO+KO8BECS0udNqUTSNo91TIqIIy1A4Vh+F94c+n9vAcwXU2bGcfsI5f69Y=",
    "x509PublicKey": "MIIBMTCB6gYHKoZIzj0CATCB3gIBATArBgcqhkjOPQEBAiB/////////////////////////////////////////7TBEBCAqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqYSRShRAQge0Je0Je0Je0Je0Je0Je0Je0Je0Je0Je0JgtenHcQyGQEQQQqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq0kWiCuGaG4oIa04B7dLHdI0UySPU1+bXxhsinpxaJ+ztPZAiAQAAAAAAAAAAAAAAAAAAAAFN753qL3nNZYEmMaXPXT7QIBCANCAAQIXg+a1Kk8uFecUeP+h1pmKaKbuQjvijvARAktLnTalE0jaPdUyKiCMtQOFYfhfeHPp/bwHMF1NmxnH7COX+vW",
    "nonce": "6uj1RdDUbcpI3lVMZvijkMC8Te20O4Bcyz0SyivX8Eg=",
}
SENDER = {
    "privateKey": "AYhVZpbVeX4KS5Qm/W0+9Ye2q3rnVVGmqRICmseWni4=",
    "publicKey": "BABVt+mpRLMXiQpIfEq6bj8hlXsdtXIxLsspmMgLNI1SR5mHgDVbjHO2A+U4QlMddGzqyEidzm1AkhtSxSO2Ahg=",
    "x509PublicKey": "MIIBMTCB6gYHKoZIzj0CATCB3gIBATArBgcqhkjOPQEBAiB/////////////////////////////////////////7TBEBCAqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqYSRShRAQge0Je0Je0Je0Je0Je0Je0Je0Je0Je0Je0JgtenHcQyGQEQQQqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq0kWiCuGaG4oIa04B7dLHdI0UySPU1+bXxhsinpxaJ+ztPZAiAQAAAAAAAAAAAAAAAAAAAAFN753qL3nNZYEmMaXPXT7QIBCANCAAQAVbfpqUSzF4kKSHxKum4/IZV7HbVyMS7LKZjICzSNUkeZh4A1W4xztgPlOEJTHXRs6shInc5tQJIbUsUjtgIY",
    "nonce": "lmXgblZwotx+DfBgKJF0lZXtAXgBEYr5khh79Zytr2Y=",
}
PLAINTEXT = b"Wormtail should never have been Potter cottage's secret keeper."
CIPHERTEXT = "pzMvVZNNVtJzqPkkxcCbBUWgDEBy/mBXIeT2dJWI16ZAQnnXUb9lI+S4k8XK6mgZSKKSRIHkcNvJpllnBg548wUgavBa0vCRRwdL6kY6Yw=="


def test_derived_curve_parameters_are_consistent():
    assert f._on_curve(f.G)
    assert f._mul(f.N, f.G) is None  # G has order N


@pytest.mark.parametrize("party", [REQUESTER, SENDER], ids=["requester", "sender"])
def test_public_keys_derive_from_private_keys_like_bouncycastle(party):
    point = f.public_point_for(f.decode_private_key(party["privateKey"]))
    assert f.encode_public_key(point, x509=False) == party["publicKey"]
    assert f.encode_public_key(point, x509=True) == party["x509PublicKey"]


def test_encrypt_matches_the_reference_cli_byte_for_byte():
    encrypted = f.encrypt(
        PLAINTEXT,
        sender_private_key=f.decode_private_key(SENDER["privateKey"]),
        sender_nonce=base64.b64decode(SENDER["nonce"]),
        requester_public_key=REQUESTER["publicKey"],
        requester_nonce=base64.b64decode(REQUESTER["nonce"]),
    )
    assert encrypted == CIPHERTEXT


def test_x509_requester_key_gives_the_same_ciphertext():
    encrypted = f.encrypt(
        PLAINTEXT,
        sender_private_key=f.decode_private_key(SENDER["privateKey"]),
        sender_nonce=base64.b64decode(SENDER["nonce"]),
        requester_public_key=REQUESTER["x509PublicKey"],
        requester_nonce=base64.b64decode(REQUESTER["nonce"]),
    )
    assert encrypted == CIPHERTEXT


def test_decrypt_the_reference_ciphertext():
    assert (
        f.decrypt(
            CIPHERTEXT,
            requester_private_key=f.decode_private_key(REQUESTER["privateKey"]),
            requester_nonce=base64.b64decode(REQUESTER["nonce"]),
            sender_public_key=SENDER["publicKey"],
            sender_nonce=base64.b64decode(SENDER["nonce"]),
        )
        == PLAINTEXT
    )


def test_fresh_key_material_round_trips():
    hip, hiu = f.generate_key_material(), f.generate_key_material()
    encrypted = f.encrypt(
        b'{"resourceType": "Bundle"}',
        sender_private_key=hip.private_key,
        sender_nonce=hip.nonce,
        requester_public_key=hiu.public_key(x509=True),
        requester_nonce=hiu.nonce,
    )
    assert (
        f.decrypt(
            encrypted,
            requester_private_key=hiu.private_key,
            requester_nonce=hiu.nonce,
            sender_public_key=hip.public_key(x509=False),
            sender_nonce=hip.nonce,
        )
        == b'{"resourceType": "Bundle"}'
    )


def test_private_key_encoding_round_trips():
    for _ in range(20):
        d = f.generate_key_material().private_key
        assert f.decode_private_key(f.encode_private_key(d)) == d


def test_off_curve_public_key_is_refused():
    point = bytearray(base64.b64decode(REQUESTER["publicKey"]))
    point[-1] ^= 1
    with pytest.raises(f.FideliusError, match="not on curve"):
        f.decode_public_key(base64.b64encode(bytes(point)).decode())


@pytest.mark.parametrize("value", ["not base64!!", base64.b64encode(b"\x04" + b"\x00" * 10).decode()])
def test_malformed_public_key_is_refused(value):
    with pytest.raises(f.FideliusError):
        f.decode_public_key(value)
