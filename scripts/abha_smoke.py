"""Prove the M1 integration against the real ABDM sandbox.

The test suite covers what leaves this machine; it cannot cover whether ABDM
accepts it, because there is no sandbox in CI. This script is the other half,
and it is the thing to run when someone asks whether M1 works.

    pwsh> python scripts/abha_smoke.py                    # connectivity only
    pwsh> python scripts/abha_smoke.py --find me@sbx      # Find ABHA
    pwsh> python scripts/abha_smoke.py --enrol 999999999999 --mobile 9999999999

**The default consumes no quota.** The sandbox allows 100 ABHA creations per
client id, and they are deleted through a web portal, one at a time. So the
no-argument run does only what is free and unlimited: acquire a session
token, fetch the public certificate, and encrypt a value against it. Those
three prove credentials, connectivity, host routing, header assembly and the
encryption scheme -- which is every layer below the flows. Enrolment happens
only when an Aadhaar is passed explicitly.

`--enrol` is interactive: ABDM sends an OTP to a real phone and waits. That
is not automatable and should not be faked.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from abha import enrollment, login  # noqa: E402
from backend.env_file import load_dotenv  # noqa: E402
from abha.client import AbhaClient  # noqa: E402
from abha.config import AbhaConfig, AbhaConfigError  # noqa: E402
from abha.errors import AbhaError  # noqa: E402

OK = "  ok  "
FAIL = " FAIL "


def check_connectivity(client: AbhaClient) -> None:
    """Session, certificate, encryption. Free, unlimited, and enough to prove
    every layer below the flows."""
    token = client.session.access_token()
    print(f"[{OK}] session token acquired ({len(token)} chars)")
    print(f"        expires in {client.session.token.expires_at:.0f} (epoch)")

    ciphertext = client.encrypt("999999999999")
    print(f"[{OK}] public certificate fetched and a value encrypted")
    print(f"        {len(ciphertext)} chars of base64, RSA-OAEP-SHA1")

    print(f"        environment : {client.config.environment.value}")
    print(f"        abha host   : {client.config.abha_url('')}")
    print(f"        phr host    : {client.config.phr_url('')}")


def run_find(client: AbhaClient, abha_address: str) -> None:
    accounts = login.find_abha(client, abha_address=abha_address)
    if not accounts:
        print(f"[{OK}] Find ABHA returned no account for {abha_address}")
        return
    print(f"[{OK}] Find ABHA returned {len(accounts)} account(s)")
    for account in accounts:
        print(f"        {account.abha_number}  {account.name}  {account.abha_address}")


def run_enrolment(client: AbhaClient, aadhaar: str, mobile: str | None) -> None:
    challenge = enrollment.request_aadhaar_otp(client, aadhaar)
    print(f"[{OK}] OTP requested, txnId {challenge.txn_id}")
    if challenge.masked_identifier:
        print(f"        sent to {challenge.masked_identifier}")

    otp = input("        enter the OTP: ").strip()
    result = enrollment.enrol_by_aadhaar(
        client, txn_id=challenge.txn_id, otp=otp, mobile=mobile
    )
    state = "created" if result.is_new else "already existed"
    print(f"[{OK}] ABHA {result.account.abha_number} ({state})")

    # The check worth making loudly. A successful enrolment with a null
    # mobile is the documented failure this whole flow is easiest to get
    # wrong -- see abha/enrollment.py.
    if mobile and not result.mobile_is_linked:
        print(
            f"[{FAIL}] mobile saved as null -- the number given is not the "
            f"Aadhaar-linked one.\n"
            f"        The ABHA is real but has no contact route; OTP login "
            f"will fail on it.\n"
            f"        Repair with enrollment.request_mobile_otp / "
            f"verify_mobile_otp."
        )
    else:
        print(f"[{OK}] mobile linked: {result.account.mobile}")

    if not result.txn_id:
        return
    suggestions = enrollment.suggest_abha_addresses(client, txn_id=result.txn_id)
    print(f"[{OK}] {len(suggestions)} ABHA address suggestion(s): {suggestions[:3]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--find", metavar="ABHA_ADDRESS", help="run Find ABHA")
    parser.add_argument(
        "--enrol",
        metavar="AADHAAR",
        help="create an ABHA. Interactive, and spends one of the sandbox's "
        "100 creations per client id.",
    )
    parser.add_argument("--mobile", help="mobile to attach with --enrol")
    args = parser.parse_args()

    load_dotenv()
    try:
        config = AbhaConfig.from_env()
    except AbhaConfigError as exc:
        print(f"[{FAIL}] {exc}")
        return 2

    try:
        with AbhaClient(config) as client:
            check_connectivity(client)
            if args.find:
                run_find(client, args.find)
            if args.enrol:
                run_enrolment(client, args.enrol, args.mobile)
    except AbhaError as exc:
        print(f"[{FAIL}] {exc}")
        return 1

    if not (args.find or args.enrol):
        print(
            "\nConnectivity only. Pass --find or --enrol to exercise a flow; "
            "--enrol spends sandbox quota."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
