"""RSA-OAEP-SHA1 and the certificate cache.

The padding test is the important one. SHA-256 OAEP produces ciphertext of
exactly the same length that ABDM cannot decrypt, so the mistake has no local
symptom at all -- it surfaces only as a remote 400 that blames the Aadhaar
number.
"""

from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from abha.crypto import (
    AbhaCryptoError,
    PublicKeyCache,
    encrypt,
    load_public_key,
    normalize_public_key,
)


@pytest.fixture(scope="module")
def keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = (
        private.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    return private, pem


def test_ciphertext_decrypts_under_sha1_oaep_and_not_sha256(keypair):
    """Pins the exact padding ABDM's decryption side uses.

    `RSA/ECB/OAEPWithSHA-1AndMGF1Padding` names the MGF1 digest and leaves
    the OAEP digest implicit at SHA-1. Reading it as SHA-256 + MGF1(SHA-1) is
    the natural misreading and is undetectable without this assertion.
    """
    private, pem = keypair
    ciphertext = base64.b64decode(encrypt("999999999999", load_public_key(pem)))

    sha1 = padding.OAEP(
        mgf=padding.MGF1(hashes.SHA1()), algorithm=hashes.SHA1(), label=None
    )
    assert private.decrypt(ciphertext, sha1) == b"999999999999"

    sha256 = padding.OAEP(
        mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None
    )
    with pytest.raises(ValueError):
        private.decrypt(ciphertext, sha256)


def test_output_is_base64_not_hex(keypair):
    _, pem = keypair
    value = encrypt("123456", load_public_key(pem))
    assert base64.b64decode(value)
    assert len(value) < 400  # hex of a 256-byte block would be 512 chars


def test_encryption_is_randomised(keypair):
    """OAEP is probabilistic. Identical ciphertext across calls would mean
    the padding was skipped, which ABDM would reject and which would also
    make the values correlatable across requests."""
    _, pem = keypair
    key = load_public_key(pem)
    assert encrypt("9999999999", key) != encrypt("9999999999", key)


def test_bare_base64_key_is_accepted(keypair):
    """ABDM's certificate endpoint has been seen returning the key with no
    PEM header lines. Rejecting that would make this package work only
    against whichever variant was live the day it was written."""
    _, pem = keypair
    bare = "".join(pem.strip().splitlines()[1:-1])
    assert load_public_key(bare)


def test_escaped_newlines_are_accepted(keypair):
    """The same key arrives with literal backslash-n when it comes through a
    JSON string field."""
    _, pem = keypair
    assert load_public_key(pem.replace("\n", "\\n"))


def test_empty_key_is_rejected_with_a_clear_error():
    with pytest.raises(AbhaCryptoError):
        normalize_public_key("")


def test_garbage_key_does_not_raise_a_cryptography_internal():
    """The caller should see one exception type from this package, not
    whichever of four types `cryptography` happened to raise."""
    with pytest.raises(AbhaCryptoError):
        load_public_key("not a key at all")


def test_empty_value_is_refused_rather_than_encrypted(keypair):
    """An empty Aadhaar encrypts fine and means the caller lost the value
    upstream; ABDM's rejection would point at the wrong layer."""
    _, pem = keypair
    with pytest.raises(AbhaCryptoError):
        encrypt("", load_public_key(pem))


def test_oversized_value_names_the_real_problem(keypair):
    _, pem = keypair
    with pytest.raises(AbhaCryptoError) as exc:
        encrypt("x" * 500, load_public_key(pem))
    assert "too long" in str(exc.value)


def test_certificate_is_fetched_once_then_cached(keypair):
    _, pem = keypair
    calls: list[int] = []

    def fetch() -> str:
        calls.append(1)
        return pem

    cache = PublicKeyCache()
    for _ in range(5):
        cache.get(fetch)
    assert len(calls) == 1


def test_invalidate_forces_a_refetch(keypair):
    """The recovery path for a key rotation that outran the TTL."""
    _, pem = keypair
    calls: list[int] = []

    def fetch() -> str:
        calls.append(1)
        return pem

    cache = PublicKeyCache()
    cache.get(fetch)
    cache.invalidate()
    cache.get(fetch)
    assert len(calls) == 2


def test_cache_expires(keypair):
    """A process that outlives a rotation must heal without a restart."""
    _, pem = keypair
    calls: list[int] = []

    def fetch() -> str:
        calls.append(1)
        return pem

    cache = PublicKeyCache(ttl_seconds=0)
    cache.get(fetch)
    cache.get(fetch)
    assert len(calls) == 2
