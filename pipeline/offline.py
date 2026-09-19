"""Offline processing queue and edge-to-cloud synchronization engine."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline.schemas.canonical import PipelineResult

log = logging.getLogger(__name__)

DEFAULT_QUEUE_DB = Path(__file__).resolve().parent.parent / "data" / "edge_queue.db"


class EdgeOfflineQueue:
    """Local SQLite-backed queue that persists processed document results while edge is offline."""

    def __init__(self, db_path: Path = DEFAULT_QUEUE_DB) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS edge_sync_queue (
                    doc_id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    status TEXT NOT NULL, -- 'PENDING', 'SYNCED', 'FAILED'
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    synced_at TEXT
                )
                """
            )
            conn.commit()

    def enqueue(self, result: PipelineResult, patient_id: str) -> None:
        """Persist processed document result locally on edge disk."""
        payload_dict = {
            "document_id": result.document_id,
            "source_file": result.source_file,
            "pipeline_version": result.pipeline_version,
            "mean_confidence": result.mean_confidence,
            "sections_seen": result.sections_seen,
            "entities": [e.as_fact_row() for e in result.entities],
        }
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO edge_sync_queue (doc_id, patient_id, status, payload, created_at)
                VALUES (?, ?, 'PENDING', ?, ?)
                ON CONFLICT(doc_id) DO UPDATE SET
                    payload = excluded.payload,
                    status = 'PENDING'
                """,
                (
                    result.document_id,
                    patient_id,
                    json.dumps(payload_dict),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        log.info("Enqueued document %s for offline edge sync", result.document_id)

    def sync_pending(self, sync_fn: Any) -> int:
        """Sync pending local records when cloud connectivity is established.

        Idempotent: prevents duplicate inserts.
        """
        synced_count = 0
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT doc_id, payload FROM edge_sync_queue WHERE status = 'PENDING'"
            )
            rows = cursor.fetchall()
            for doc_id, payload_str in rows:
                payload = json.loads(payload_str)
                try:
                    sync_fn(payload)
                    cursor.execute(
                        "UPDATE edge_sync_queue SET status = 'SYNCED', synced_at = ? WHERE doc_id = ?",
                        (datetime.now(timezone.utc).isoformat(), doc_id),
                    )
                    synced_count += 1
                except Exception as exc:
                    log.warning("Cloud sync failed for %s: %s", doc_id, exc)
            conn.commit()
        return synced_count
