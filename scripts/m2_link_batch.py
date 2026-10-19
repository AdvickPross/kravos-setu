"""Run the M2 linking batch once (NHA HIP_INTI_LINK_505, HIP_INIT_NOTIFY_HIECM).

For every patient tagged to an ABHA address: request a link token if none is
stored, otherwise link every care context not yet linked. For every patient
with a mobile on file and no ABHA address: ask ABDM to text them a deep link,
once per new set of records.

    pwsh> python scripts/m2_link_batch.py

Schedule it (Task Scheduler / cron) to make linking happen from the backend
with no UI involved. Results arrive later on the bridge callbacks, so the
backend must be running and reachable at the registered bridge URL. A token
request made here is followed up automatically: when on-generate-token
arrives, the backend links that patient straight away.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.env_file import load_dotenv  # noqa: E402


def main() -> int:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from abha.client import AbhaClient
    from abha.config import AbhaConfig, AbhaConfigError
    from abha.m2_client import AbdmM2Client
    from backend.m2_hip_linking import run_linking_batch

    try:
        client = AbdmM2Client(AbhaClient(AbhaConfig.from_env()))
        client.hip_id  # fail fast, before touching any patient
    except AbhaConfigError as exc:
        print(f"not configured: {exc}", file=sys.stderr)
        return 2

    outcomes = run_linking_batch(client)
    print(json.dumps(outcomes, indent=2, sort_keys=True))
    return 1 if outcomes.get("ERROR") else 0


if __name__ == "__main__":
    raise SystemExit(main())
