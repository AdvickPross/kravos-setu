"""The wadh digest.

Six characters, one hash, and the single most reliably-wrong computation in
an ABDM integration. These tests exist because the failure mode is remote,
delayed and misattributed: a wrong flag produces `K-547` several calls later,
with an error message that mentions neither flags nor digests.
"""

from __future__ import annotations

import base64
import hashlib

import pytest

from abha.biometrics import BiometricModality, build_wadh, pid_options, wadh_source


def test_lr_flag_is_Y():
    """The flag that is almost always wrong. Most sample code sets it to
    'N' -- correct for other UIDAI auth products, wrong here, and the
    difference is exactly K-547."""
    assert wadh_source(BiometricModality.FINGERPRINT)[4] == "Y"


def test_source_string_is_exactly_the_documented_concatenation():
    """'2.5' + ra + rc + lr + de + pfr, in that order. Any reordering hashes
    to something the RD service will not match."""
    assert wadh_source(BiometricModality.FINGERPRINT) == "2.5FYYNN"
    assert wadh_source(BiometricModality.IRIS) == "2.5IYYNN"
    assert wadh_source(BiometricModality.FACE) == "2.5PYYNN"


def test_digest_is_base64_of_raw_bytes_not_of_the_hex_string():
    """Hashing correctly and then encoding the hex digest produces a
    44-character value that looks right and is not."""
    expected = base64.b64encode(hashlib.sha256(b"2.5FYYNN").digest()).decode()
    assert build_wadh(BiometricModality.FINGERPRINT) == expected

    hex_variant = base64.b64encode(
        hashlib.sha256(b"2.5FYYNN").hexdigest().encode()
    ).decode()
    assert build_wadh(BiometricModality.FINGERPRINT) != hex_variant


def test_digest_is_sha256_not_sha1():
    """Different subsystem from abha/crypto.py, which is SHA-1. The mismatch
    between the two is a real source of confusion."""
    assert len(base64.b64decode(build_wadh())) == 32


def test_modality_is_the_only_thing_that_changes_the_digest():
    digests = {build_wadh(m) for m in BiometricModality}
    assert len(digests) == len(BiometricModality)


def test_pid_options_carry_the_matching_wadh():
    """The options and the digest must describe the same request; computing
    them independently is how they drift."""
    options = pid_options(BiometricModality.IRIS)
    assert options["ra"] == "I"
    assert options["wadh"] == build_wadh(BiometricModality.IRIS)


def test_pid_options_pin_the_five_fixed_flags():
    options = pid_options()
    assert options["ver"] == "2.5"
    assert (options["rc"], options["lr"], options["de"], options["pfr"]) == (
        "Y",
        "Y",
        "N",
        "N",
    )


@pytest.mark.parametrize("modality", ["F", BiometricModality.FINGERPRINT])
def test_a_raw_modality_string_is_accepted(modality):
    """Callers reading a device's own config hold a string, not an enum."""
    assert build_wadh(modality) == build_wadh(BiometricModality.FINGERPRINT)


def test_source_is_exposed_for_debugging():
    """Comparing base64 digests against an RD service tells you only that
    they differ; comparing the source strings tells you which character."""
    assert wadh_source("F") == "2.5FYYNN"
