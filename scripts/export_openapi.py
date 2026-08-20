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
from pathlib import Path

from backend.main import app

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_PATH = REPO_ROOT / "openapi.json"


def main() -> None:
    schema = app.openapi()
    DEFAULT_OUT_PATH.write_text(json.dumps(schema, indent=2), encoding="utf-8")
    print(f"wrote {DEFAULT_OUT_PATH}")


if __name__ == "__main__":
    main()
