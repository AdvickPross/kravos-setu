"""Load `.env` into the process environment.

`.env.example` has said "copy to .env and fill in" since the first commit,
and until now nothing read the result. Every module took its configuration
from `os.environ` and the only thing that populated it was the shell, so
following the documented setup produced a `.env` that did nothing: LLM polish
stayed off with a key present, Pinecone stayed unconfigured, and `/api/abha/*`
answered 503 while the credentials sat in the file it named. That is a
documentation bug and a configuration bug at the same time, and this is the
fix for both.

Deliberately not `python-dotenv`. The whole behaviour is twenty lines, the
dependency list in `requirements.txt` is short on purpose, and a parser this
small is easier to reason about than a package whose interpolation and
override rules would have to be looked up.

**Existing variables always win.** A shell that exported
`ABDM_ENVIRONMENT=production` must not lose to a stale sandbox value in a
file -- that direction of precedence is the one that prevents a deployment
from quietly pointing at the wrong ecosystem.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = REPO_ROOT / ".env"


def load_dotenv(path: Path = DEFAULT_ENV_FILE) -> dict[str, str]:
    """Read `KEY=value` lines into `os.environ`. Returns what it set.

    A missing file is not an error: the app is required to run with no `.env`
    at all, and every optional feature degrades on its own.
    """
    if not path.exists():
        return {}

    applied: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        # `export FOO=bar` is a common shape for a file people also source
        # from a shell; accept it rather than producing a variable named
        # "export FOO".
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        value = value.strip()
        # Strip one layer of matching quotes -- `FOO="bar"` means bar, and a
        # naive parser would set the quotes as part of the secret, producing
        # a 401 that looks nothing like a quoting problem.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not key or key in os.environ:
            continue
        os.environ[key] = value
        applied[key] = value
    return applied
