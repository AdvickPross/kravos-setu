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

-- One row of patients.jsonl. Mirrors backend.models.PatientSummary exactly.
CREATE TABLE IF NOT EXISTS patients (
    patient_id  TEXT PRIMARY KEY,
    name        TEXT,
    birth_date  DATE,
    gender      TEXT,
    deceased    TIMESTAMPTZ,
    n_facts     INTEGER NOT NULL DEFAULT 0,
    source_file TEXT
);

-- Conversational RAG (backend/conversation.py). No clinician identity
-- exists in this system -- ABHA sign-in identifies the patient, not the
-- doctor asking questions -- so a conversation is keyed by patient_id and
-- its own generated id, not by clinician.
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    patient_id      TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_turn_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS conversations_patient_last_turn_idx
    ON conversations (patient_id, last_turn_at DESC);

CREATE TABLE IF NOT EXISTS conversation_turns (
    turn_id         TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations (conversation_id),
    turn_index      INTEGER NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    text            TEXT NOT NULL,
    -- Which facts (backend.models.Fact.fact_id) and narrative chunks this
    -- assistant turn's answer actually cited -- provenance is not optional
    -- anywhere else in this system and stays that way here.
    cited_fact_ids  JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS conversation_turns_conv_idx
    ON conversation_turns (conversation_id, turn_index);

-- ABDM M2 transaction tracking.
CREATE TABLE IF NOT EXISTS abdm_transactions (
    id              BIGSERIAL PRIMARY KEY,
    patient_id      TEXT,
    transaction_id  TEXT,
    request_id      TEXT NOT NULL,
    operation       TEXT NOT NULL,
    status          TEXT NOT NULL,
    error_code      TEXT,
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS abdm_transactions_patient_idx
    ON abdm_transactions (patient_id);

CREATE INDEX IF NOT EXISTS abdm_transactions_transaction_idx
    ON abdm_transactions (transaction_id);

CREATE INDEX IF NOT EXISTS abdm_transactions_request_idx
    ON abdm_transactions (request_id);

-- ABDM M2 care-context records.
CREATE TABLE IF NOT EXISTS abdm_care_contexts (
    id                BIGSERIAL PRIMARY KEY,
    patient_id        TEXT,
    transaction_id    TEXT,
    reference_number  TEXT NOT NULL,
    display           TEXT,
    hi_type           TEXT,
    hip_id            TEXT,
    status            TEXT NOT NULL DEFAULT 'DISCOVERED',
    discovered_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    linked_at         TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS abdm_care_contexts_patient_idx
    ON abdm_care_contexts (patient_id);

CREATE INDEX IF NOT EXISTS abdm_care_contexts_transaction_idx
    ON abdm_care_contexts (transaction_id);

CREATE INDEX IF NOT EXISTS abdm_care_contexts_reference_idx
    ON abdm_care_contexts (reference_number);

-- ABDM M2 link tokens, from /hip/token/on-generate-token. One live token per
-- ABHA address per HIP; ABDM documents them as valid for 6 months.
CREATE TABLE IF NOT EXISTS abdm_link_tokens (
    abha_address  TEXT NOT NULL,
    hip_id        TEXT NOT NULL,
    link_token    TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (abha_address, hip_id)
);

-- User-initiated linking. A discovery we answered: which patient the
-- gateway's transaction resolved to and which care contexts we offered, so
-- link/init can refuse anything we did not offer.
CREATE TABLE IF NOT EXISTS abdm_discoveries (
    transaction_id  TEXT PRIMARY KEY,
    patient_id      TEXT NOT NULL,
    abha_address    TEXT,
    mobile          TEXT,
    offered         JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One OTP challenge per link/init. Only a hash of the OTP is kept.
CREATE TABLE IF NOT EXISTS abdm_link_requests (
    link_ref_number TEXT PRIMARY KEY,
    transaction_id  TEXT NOT NULL,
    patient_id      TEXT NOT NULL,
    abha_address    TEXT,
    care_contexts   JSONB NOT NULL,
    otp_hash        TEXT NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'PENDING',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Mobile numbers captured at registration. Synthea has none; this is what
-- USER_INIT_LINK_603's mobile fallback match searches.
CREATE TABLE IF NOT EXISTS patient_mobiles (
    patient_id  TEXT PRIMARY KEY,
    mobile      TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS patient_mobiles_mobile_idx
    ON patient_mobiles (mobile);

-- HIP-initiated linking: the REQUEST-ID of the link/carecontext call that
-- asked to link this row, so on_carecontext can settle it.
ALTER TABLE abdm_care_contexts ADD COLUMN IF NOT EXISTS request_id TEXT;
CREATE INDEX IF NOT EXISTS abdm_care_contexts_request_idx
    ON abdm_care_contexts (request_id);

-- Deep-link SMS (HIP_INIT_NOTIFY_HIECM): which care contexts a patient
-- without an ABHA address has already been told about, so a batch run
-- does not text them again for the same records.
-- Consent artefacts granted to this HIP (/api/v3/consent/request/hip/notify).
-- A revoked, expired or denied consent is deleted, as NHA's
-- HIP_INIT_REVOKE_CONSENT / HIP_INIT_EXPIRE_CONSENT require; the
-- transaction log keeps the fact that it existed.
CREATE TABLE IF NOT EXISTS abdm_consents (
    consent_id     TEXT PRIMARY KEY,
    abha_address   TEXT,
    hip_id         TEXT NOT NULL,
    hiu_id         TEXT,
    hi_types       JSONB NOT NULL,
    care_contexts  JSONB NOT NULL,
    date_from      TIMESTAMPTZ NOT NULL,
    date_to        TIMESTAMPTZ NOT NULL,
    data_erase_at  TIMESTAMPTZ,
    artefact       JSONB NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One health-information request and what we did with it.
CREATE TABLE IF NOT EXISTS abdm_data_transfers (
    transaction_id  TEXT PRIMARY KEY,
    consent_id      TEXT NOT NULL,
    request_id      TEXT NOT NULL,
    status          TEXT NOT NULL,
    data_push_url   TEXT,
    statuses        JSONB,
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS abdm_sms_notifications (
    patient_id   TEXT PRIMARY KEY,
    mobile       TEXT NOT NULL,
    notified     JSONB NOT NULL,
    request_id   TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'SENT',
    notified_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
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

PATIENT_COLUMNS = (
    "patient_id",
    "name",
    "birth_date",
    "gender",
    "deceased",
    "n_facts",
    "source_file",
)

# tests/conftest.py points at this database instead of DbSettings.load()'s
# default -- never the one scripts/load_facts.py populates with the real
# 1,083,231-row corpus, so a test run can never truncate real data.
TEST_DBNAME = "kravos_test"


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
