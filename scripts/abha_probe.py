"""Ask the sandbox which M1 endpoints exist and which request shapes it accepts.

`scripts/abha_smoke.py` proves the credentials work. This proves the *paths
and payloads* do, which is the other half of "is M1 actually wired up" and
the half a PDF cannot answer -- ABDM moves endpoints between minor revisions
and the guide is not always ahead of the sandbox.

**It spends nothing and tells nobody.** Every identifier below is
deliberately non-existent: an Aadhaar of all nines, a mobile that no one
holds, an ABHA address nobody registered. That is the whole trick. A
well-formed request for a nonexistent account gets as far as the account
lookup and comes back "User not found" -- which means the scope, login hint,
OTP system, headers, encryption and host were all accepted. A malformed one
fails earlier, with a different message. So:

    "User not found" / "does not match any records"  -> the shape is right
    "Invalid Scope" / "Invalid Login Hint"           -> the shape is wrong
    404                                              -> the path is wrong
    401                                              -> auth is wrong

No OTP is sent to a real phone and no ABHA is created, so this is safe to run
repeatedly against the 100-creation sandbox cap.

    pwsh> python scripts/abha_probe.py             # every endpoint: exists?
    pwsh> python scripts/abha_probe.py --contract  # scope/otpSystem matrices

Reading the output: `EXISTS` means the path resolved and our auth got past
the edge. It does **not** mean the flow works end to end -- only a real OTP
can show that. It means a failure is in the payload, not the plumbing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from abha.client import AbhaClient  # noqa: E402
from abha.config import AbhaConfig, AbhaConfigError  # noqa: E402
from abha.errors import AbhaError  # noqa: E402
from abha.login import _METHODS  # noqa: E402
from abha.phr import _CHANNELS  # noqa: E402
from abha.transport import base_headers  # noqa: E402
from scripts.abha_smoke import load_dotenv  # noqa: E402

# Every path M1 touches. Kept here rather than imported from endpoints.py on
# purpose: this script's job is to check that module against reality, and a
# probe that reads its list from the thing it is testing cannot find a path
# that was never added.
PROBES: list[tuple[str, str]] = [
    ("POST", "/v3/enrollment/request/otp"),
    ("POST", "/v3/enrollment/enrol/byAadhaar"),
    ("POST", "/v3/enrollment/auth/byAbdm"),
    ("GET", "/v3/enrollment/enrol/suggestion"),
    ("POST", "/v3/enrollment/enrol/abha-address"),
    ("POST", "/v3/enrollment/enrol/byDocument"),
    ("POST", "/v3/enrollment/enrol/auth/init"),
    ("POST", "/v3/enrollment/enrol/capturePID"),
    ("POST", "/v3/profile/login/request/otp"),
    ("POST", "/v3/profile/login/verify"),
    ("POST", "/v3/profile/login/verify/user"),
    ("POST", "/v3/profile/account/abha/search"),
    ("GET", "/v3/profile/account"),
    ("GET", "/v3/profile/account/abha-card"),
    ("GET", "/v3/profile/account/qrCode"),
    ("POST", "/v3/profile/account/request/otp"),
    ("POST", "/v3/profile/account/verify"),
    ("GET", "/v3/profile/public/certificate"),
    ("POST", "/v3/phr/web/login/abha/search"),
    ("POST", "/v3/phr/web/login/abha/request/otp"),
    ("POST", "/v3/phr/web/login/abha/verify"),
    ("GET", "/v3/phr/web/login/profile/abha-profile"),
    ("GET", "/v3/phr/web/login/profile/abha/phr-card"),
]

# Non-existent by construction. The Aadhaar is all-nines, which is
# checksum-valid and unassigned, so it reaches UIDAI and is refused there
# rather than being rejected locally as malformed.
FAKE_AADHAAR = "999999999999"
FAKE_MOBILE = "9000000001"
FAKE_ABHA_NUMBER = "11-1111-1111-1111"
FAKE_ABHA_ADDRESS = "nosuchuser1234@sbx"

# Substrings meaning "the request was well-formed; the account just is not
# there". These are the pass condition, not a failure.
_REACHED_LOOKUP = (
    "user not found",
    "does not match",
    "no user found",
    "uidai error",
    "aadhaar number is incorrect",
)
# Substrings meaning the request itself was wrong.
_BAD_SHAPE = ("invalid scope", "invalid login hint", "invalid otp system")


def _summarise(text: str) -> str:
    return " ".join(text.split())[:90]


def probe_endpoints(client: AbhaClient) -> int:
    print("Endpoint reachability\n" + "-" * 78)
    auth = client.session.authorization()
    missing = 0
    for method, path in PROBES:
        url = client.config.abha_url(path)
        headers = base_headers(
            extra={"Authorization": auth, "X-token": "Bearer probe"}
        )
        try:
            response = client.transport._client.request(
                method, url, headers=headers, json={} if method == "POST" else None
            )
        except Exception as exc:  # noqa: BLE001 - a probe reports, never raises
            print(f"  ERROR   {method:5} {path:50} {exc}")
            missing += 1
            continue

        if response.status_code in (404, 405):
            verdict, missing = "MISSING", missing + 1
        elif response.status_code == 401:
            verdict = "AUTH?  "
        else:
            verdict = "EXISTS "
        print(
            f"  {verdict} {response.status_code:>4} {method:5} {path:50} "
            f"{_summarise(response.text)}"
        )
    return missing


def probe_contracts(client: AbhaClient) -> None:
    """Which scope / otpSystem combinations the sandbox actually accepts.

    This is what caught the real bug: ABHA *address* verification is not a
    fourth row in the ABHA-host login table, it is a separate family on the
    PHR host. The table below is the evidence.
    """
    auth = client.session.authorization()

    print("\nABHA-host login table (abha/login.py)\n" + "-" * 78)
    fakes = {
        "aadhaar": FAKE_AADHAAR,
        "mobile": FAKE_MOBILE,
        "abha-number": FAKE_ABHA_NUMBER,
    }
    for method, spec in _METHODS.items():
        body = {
            "scope": list(spec.scope),
            "loginHint": spec.login_hint,
            "loginId": client.encrypt(fakes[method.value]),
            "otpSystem": spec.otp_system,
        }
        response = client.transport._client.request(
            "POST",
            client.config.abha_url("/v3/profile/login/request/otp"),
            headers=base_headers(extra={"Authorization": auth}),
            json=body,
        )
        print(f"  {_verdict(response.text):9} {method.value:14} {_summarise(response.text)}")

    print("\nPHR address table (abha/phr.py)\n" + "-" * 78)
    for channel, spec in _CHANNELS.items():
        body = {
            "scope": list(spec.scope),
            "loginHint": "abha-address",
            "loginId": client.encrypt(FAKE_ABHA_ADDRESS),
            "otpSystem": spec.otp_system,
        }
        response = client.transport._client.request(
            "POST",
            client.config.abha_url("/v3/phr/web/login/abha/request/otp"),
            headers=base_headers(extra={"Authorization": auth}),
            json=body,
        )
        print(f"  {_verdict(response.text):9} {channel.value:14} {_summarise(response.text)}")

    print("\nABHA address on the ABHA host -- expected to be REJECTED\n" + "-" * 78)
    response = client.transport._client.request(
        "POST",
        client.config.abha_url("/v3/profile/login/request/otp"),
        headers=base_headers(extra={"Authorization": auth}),
        json={
            "scope": ["abha-address-login", "mobile-verify"],
            "loginHint": "abha-address",
            "loginId": client.encrypt(FAKE_ABHA_ADDRESS),
            "otpSystem": "abdm",
        },
    )
    verdict = _verdict(response.text)
    note = "correct -- this is why abha/phr.py exists" if verdict == "BAD SHAPE" else "UNEXPECTED"
    print(f"  {verdict:9} {note}: {_summarise(response.text)}")


def _verdict(text: str) -> str:
    lowered = text.lower()
    if any(marker in lowered for marker in _BAD_SHAPE):
        return "BAD SHAPE"
    if any(marker in lowered for marker in _REACHED_LOOKUP):
        return "SHAPE OK"
    return "?"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--contract",
        action="store_true",
        help="also probe the scope/otpSystem matrices",
    )
    args = parser.parse_args()

    load_dotenv()
    try:
        config = AbhaConfig.from_env()
    except AbhaConfigError as exc:
        print(f"[FAIL] {exc}")
        return 2

    try:
        with AbhaClient(config) as client:
            missing = probe_endpoints(client)
            if args.contract:
                probe_contracts(client)
    except AbhaError as exc:
        print(f"[FAIL] {exc}")
        return 1

    print(
        f"\n{len(PROBES) - missing}/{len(PROBES)} endpoints reachable. "
        f"EXISTS means the path and auth are right, not that the flow works "
        f"end to end -- only a real OTP shows that."
    )
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
