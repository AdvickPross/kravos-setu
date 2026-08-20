"""Postgres connection settings and the facts schema.

Settings come from ``data/db.json``, overridable per-field by environment
variables (``KRAVOS_PORT``, ``KRAVOS_PASSWORD``, ...). Nothing hardcodes the
port -- 5434 here because 5433 is already taken on this machine.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_CONFIG_PATH = _REPO_ROOT / "data" / "db.json"

FACTS_TABLE = "facts"

# The two access patterns the router generates, plus date-range filtering.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS facts (
    fact_id              TEXT PRIMARY KEY,
    patient_id           TEXT NOT NULL,
    category             TEXT,
    safety_class         TEXT,
    text                 TEXT,
    code_system          TEXT,
    code                 TEXT,
    display              TEXT,
    value                TEXT,
    unit                 TEXT,
    date                 TIMESTAMPTZ,
    status               TEXT,
    source_resource_type TEXT,
    source_resource_id   TEXT,
    encounter_id         TEXT,
    also_in              JSONB,
    -- Consent lineage. Nullable because Synthea has no consent artefact,
    -- but present from the first load: an unscoped query path that exists
    -- is a path someone uses later.
    consent_artefact_id  TEXT,
    hi_type              TEXT,
    hip_id               TEXT
);

CREATE INDEX IF NOT EXISTS facts_patient_category_idx
    ON facts (patient_id, category);
CREATE INDEX IF NOT EXISTS facts_patient_code_idx
    ON facts (patient_id, code_system, code);
CREATE INDEX IF NOT EXISTS facts_date_idx
    ON facts (date);
CREATE INDEX IF NOT EXISTS facts_patient_date_idx
    ON facts (patient_id, date DESC);
"""

COLUMNS = (
    "fact_id",
    "patient_id",
    "category",
    "safety_class",
    "text",
    "code_system",
    "code",
    "display",
    "value",
    "unit",
    "date",
    "status",
    "source_resource_type",
    "source_resource_id",
    "encounter_id",
    "also_in",
    "consent_artefact_id",
    "hi_type",
    "hip_id",
)


@dataclass(frozen=True)
class DbSettings:
    host: str = "localhost"
    port: int = 5434
    dbname: str = "kravos"
    user: str = "kravos"
    password: str = "kravos"
    connect_timeout: int = 10

    @classmethod
    def load(cls, path: str | Path = DEFAULT_DB_CONFIG_PATH) -> DbSettings:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        values = {k: v for k, v in raw.items() if not k.startswith("_")}
        for field in ("host", "port", "dbname", "user", "password", "connect_timeout"):
            override = os.environ.get(f"KRAVOS_{field.upper()}")
            if override:
                values[field] = override
        values["port"] = int(values.get("port", 5434))
        values["connect_timeout"] = int(values.get("connect_timeout", 10))
        return cls(**values)

    @property
    def conninfo(self) -> str:
        return (
            f"host={self.host} port={self.port} dbname={self.dbname} "
            f"user={self.user} password={self.password} "
            f"connect_timeout={self.connect_timeout}"
        )

    def connect(self):
        """Open a psycopg connection. Imported lazily so the rules layers
        (intent, router) stay importable without a database."""
        import psycopg

        return psycopg.connect(self.conninfo)
