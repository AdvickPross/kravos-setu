"""The `wadh` digest for biometric auth. Section 6 of the v1.4 guide.

Small module, one job, and it is here as a separate file because this single
computation is the most reliably-wrong part of an ABDM integration.

`wadh` is a digest over six concatenated flag characters that tell UIDAI what
kind of authentication is being requested. The RD service on the capture
device computes the same digest independently; if the two disagree by one
character, the device signs a PID block UIDAI will not accept, and the
failure surfaces several calls later as `K-547` -- an error whose text says
nothing about flags, digests, or which of the six was wrong.

The flag that is almost always wrong is `lr`. It means "local request" and
must be **`'Y'`**. Most sample code on the internet sets it to `'N'`, which
is the correct value for other UIDAI auth products and the wrong one here.
`abha/errors.py` appends a note to any K-547 pointing back at this module,
because the error alone will not lead anyone here.

The remaining five are fixed by the guide for this flow: `ra` is the modality
(the only one that varies), `rc='Y'`, `de='N'`, `pfr='N'`, and the version
prefix is the literal `'2.5'`.

Note SHA-256 here, in contrast to the SHA-1 in `abha/crypto.py`. Different
subsystem, different specification -- and the mismatch is a real source of
confusion, so it is stated rather than left to be rediscovered.

This module computes; it does not capture. The fingerprint or iris image
never touches this service -- the RD-service device produces the signed PID
block and the client sends it on. That is a boundary worth keeping.
"""

from __future__ import annotations

import base64
import hashlib
from enum import Enum


class BiometricModality(str, Enum):
    """The `ra` flag: which biometric the device will capture."""

    FINGERPRINT = "F"
    IRIS = "I"
    FACE = "P"


# The five fixed flags, in the order they are concatenated. Changing any of
# these changes the digest and breaks auth -- they are the guide's values,
# not tunables.
_VERSION = "2.5"
_RC = "Y"
_LR = "Y"  # must be 'Y'. See the module docstring; 'N' yields K-547.
_DE = "N"
_PFR = "N"


def wadh_source(modality: BiometricModality | str) -> str:
    """The exact string that gets hashed.

    Exposed separately from `build_wadh` so a failing integration can print
    it and compare character-by-character against what the RD service used.
    Comparing base64 digests tells you only that they differ.
    """
    ra = modality.value if isinstance(modality, BiometricModality) else str(modality)
    return f"{_VERSION}{ra}{_RC}{_LR}{_DE}{_PFR}"


def build_wadh(modality: BiometricModality | str = BiometricModality.FINGERPRINT) -> str:
    """Base64 of the SHA-256 digest of `wadh_source`.

    Base64 of the raw digest bytes -- not of its hex representation. Hashing
    correctly and then encoding the hex string is a quiet way to produce a
    44-character value that looks right and is not.
    """
    digest = hashlib.sha256(wadh_source(modality).encode("utf-8")).digest()
    return base64.b64encode(digest).decode("ascii")


def pid_options(
    modality: BiometricModality | str = BiometricModality.FINGERPRINT,
    *,
    format_code: str = "0",
) -> dict[str, str]:
    """The option attributes handed to the RD service before capture.

    Returned as a plain mapping for the caller to serialise into the device's
    XML. This service does not build the XML itself: the RD service owns that
    format, it varies by vendor, and a half-correct XML builder here would be
    a liability rather than a convenience.
    """
    ra = modality.value if isinstance(modality, BiometricModality) else str(modality)
    return {
        "ver": _VERSION,
        "env": "P",
        "ra": ra,
        "rc": _RC,
        "lr": _LR,
        "de": _DE,
        "pfr": _PFR,
        "fCount": "1",
        "format": format_code,
        "wadh": build_wadh(ra),
    }
