"""Export the FastAPI app's OpenAPI schema to a file.

This schema is generated straight from the Pydantic models in
``backend/models.py`` (FastAPI derives it from the route type hints, which
are those models). The frontend's TypeScript types are generated from this
file via ``openapi-typescript`` rather than hand-written, so the two sides
never drift silently.

    pwsh> python scripts/export_openapi.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Same guard the other scripts carry. Without it `python scripts/export_openapi.py`
# from the repo root fails on `import backend`: pytest.ini's `pythonpath = .`
# applies to pytest only, not to a plain script run.
sys.path.insert(0, str(REPO_ROOT))

from backend.main import app  # noqa: E402

DEFAULT_OUT_PATH = REPO_ROOT / "openapi.json"


def main() -> None:
    schema = app.openapi()
    DEFAULT_OUT_PATH.write_text(json.dumps(schema, indent=2), encoding="utf-8")
    print(f"wrote {DEFAULT_OUT_PATH}")


if __name__ == "__main__":
    main()
