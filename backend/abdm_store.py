from __future__ import annotations
from typing import Any
from backend.data_layer import connection

def create_transaction(
    *,
    patient_id: str | None,
    transaction_id: str | None,
    request_id: str,
    operation: str,
    status: str = "INITIATED",
) -> int:
    """
    Create a record for an ABDM M2 operation.

    Returns:
        Database ID of the created transaction.
    """
    conn = connection()

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO abdm_transactions (
                patient_id,
                transaction_id,
                request_id,
                operation,
                status
            )
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                patient_id,
                transaction_id,
                request_id,
                operation,
                status,
            ),
        )

        row = cur.fetchone()

    if row is None:
        raise RuntimeError("Failed to create ABDM transaction")

    return int(row[0])



def update_transaction(
    *,
    request_id: str,
    status: str,
    transaction_id: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> int:
    """
    Update the status of an ABDM M2 transaction.

    Returns:
        Number of updated transaction records.
    """
    conn = connection()

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE abdm_transactions
            SET
                transaction_id = COALESCE(%s, transaction_id),
                status = %s,
                error_code = %s,
                error_message = %s,
                updated_at = now()
            WHERE request_id = %s
            """,
            (
                transaction_id,
                status,
                error_code,
                error_message,
                request_id,
            ),
        )

        return cur.rowcount
   
def save_care_context(
    *,
    patient_id: str | None,
    transaction_id: str | None,
    reference_number: str,
    display: str | None = None,
    hi_type: str | None = None,
    hip_id: str | None = None,
    status: str = "DISCOVERED",
) -> int:
    """
    Save a discovered ABDM care context.

    If the same care context already exists for the same transaction,
    return the existing database ID instead of inserting a duplicate.
    """
    conn = connection()

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id
            FROM abdm_care_contexts
            WHERE reference_number = %s
              AND (
                    transaction_id = %s
                    OR (
                        transaction_id IS NULL
                        AND %s IS NULL
                    )
                  )
            ORDER BY id
            LIMIT 1
            """,
            (
                reference_number,
                transaction_id,
                transaction_id,
            ),
        )

        existing_row = cur.fetchone()

        if existing_row is not None:
            return int(existing_row[0])

        cur.execute(
            """
            INSERT INTO abdm_care_contexts (
                patient_id,
                transaction_id,
                reference_number,
                display,
                hi_type,
                hip_id,
                status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                patient_id,
                transaction_id,
                reference_number,
                display,
                hi_type,
                hip_id,
                status,
            ),
        )

        row = cur.fetchone()

    if row is None:
        raise RuntimeError("Failed to save ABDM care context")

    return int(row[0])


def mark_care_context_linked(
    *,
    reference_number: str,
    transaction_id: str | None = None,
) -> int:
    """
    Mark a care context as linked.

    Returns:
        Number of updated records.
    """
    conn = connection()

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE abdm_care_contexts
            SET
                status = 'LINKED',
                linked_at = COALESCE(linked_at, now())
            WHERE reference_number = %s
              AND (
                    transaction_id = %s
                    OR (
                        transaction_id IS NULL
                        AND %s IS NULL
                    )
                  )
            """,
            (
                reference_number,
                transaction_id,
                transaction_id,
            ),
        )

        return cur.rowcount


def save_link_token(
    *,
    abha_address: str,
    hip_id: str,
    link_token: str,
) -> None:
    """Store (or replace) the link token for an ABHA address at this HIP."""
    conn = connection()

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO abdm_link_tokens (abha_address, hip_id, link_token, expires_at)
            VALUES (%s, %s, %s, now() + interval '6 months')
            ON CONFLICT (abha_address, hip_id) DO UPDATE SET
                link_token = EXCLUDED.link_token,
                created_at = now(),
                expires_at = EXCLUDED.expires_at
            """,
            (abha_address, hip_id, link_token),
        )


def get_link_token(
    *,
    abha_address: str,
    hip_id: str,
) -> str | None:
    """The unexpired link token for an ABHA address, or None."""
    conn = connection()

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT link_token
            FROM abdm_link_tokens
            WHERE abha_address = %s
              AND hip_id = %s
              AND expires_at > now()
            """,
            (abha_address, hip_id),
        )
        row = cur.fetchone()

    return row[0] if row else None


def list_care_contexts(
    *,
    patient_id: str | None = None,
    transaction_id: str | None = None,
) -> list[dict[str, Any]]:
    """
    Retrieve saved care contexts using optional filters.
    """
    conn = connection()

    query = """
        SELECT
            id,
            patient_id,
            transaction_id,
            reference_number,
            display,
            hi_type,
            hip_id,
            status,
            discovered_at,
            linked_at
        FROM abdm_care_contexts
        WHERE (%s IS NULL OR patient_id = %s)
          AND (%s IS NULL OR transaction_id = %s)
        ORDER BY discovered_at DESC
    """

    with conn.cursor() as cur:
        cur.execute(
            query,
            (
                patient_id,
                patient_id,
                transaction_id,
                transaction_id,
            ),
        )

        rows = cur.fetchall()
        columns = [description.name for description in cur.description]

    return [dict(zip(columns, row)) for row in rows]