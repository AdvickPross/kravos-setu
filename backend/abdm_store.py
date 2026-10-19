from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

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
                        AND %s::text IS NULL
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
                        AND %s::text IS NULL
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
        WHERE (%s::text IS NULL OR patient_id = %s)
          AND (%s::text IS NULL OR transaction_id = %s)
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


# -- user-initiated linking ------------------------------------------------------


def save_discovery(
    *,
    transaction_id: str,
    patient_id: str,
    abha_address: str | None,
    mobile: str | None,
    offered: list[str],
) -> None:
    """Record which patient a discovery matched and which care contexts
    (encounter ids) were offered."""
    conn = connection()

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO abdm_discoveries
                (transaction_id, patient_id, abha_address, mobile, offered)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (transaction_id) DO UPDATE SET
                patient_id = EXCLUDED.patient_id,
                abha_address = EXCLUDED.abha_address,
                mobile = EXCLUDED.mobile,
                offered = EXCLUDED.offered
            """,
            (transaction_id, patient_id, abha_address, mobile, Jsonb(offered)),
        )


def get_discovery(transaction_id: str) -> dict[str, Any] | None:
    conn = connection()

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT transaction_id, patient_id, abha_address, mobile, offered
            FROM abdm_discoveries
            WHERE transaction_id = %s
            """,
            (transaction_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        columns = [d.name for d in cur.description]

    return dict(zip(columns, row))


def create_link_request(
    *,
    link_ref_number: str,
    transaction_id: str,
    patient_id: str,
    abha_address: str | None,
    care_contexts: list[dict[str, Any]],
    otp_hash: str,
    ttl_seconds: int,
) -> None:
    conn = connection()

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO abdm_link_requests (
                link_ref_number, transaction_id, patient_id, abha_address,
                care_contexts, otp_hash, expires_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, now() + make_interval(secs => %s))
            """,
            (
                link_ref_number,
                transaction_id,
                patient_id,
                abha_address,
                Jsonb(care_contexts),
                otp_hash,
                ttl_seconds,
            ),
        )


def claim_link_attempt(link_ref_number: str, *, max_attempts: int) -> dict[str, Any] | None:
    """Count one OTP attempt and return the request, atomically.

    Returns None for an unknown reference. The returned ``attempts`` already
    includes this one, and ``expired`` is computed by the database clock, so
    two concurrent confirms can't both get the last attempt.
    """
    conn = connection()

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE abdm_link_requests
            SET attempts = attempts + 1
            WHERE link_ref_number = %s
              AND status = 'PENDING'
              AND attempts < %s
            RETURNING link_ref_number, transaction_id, patient_id, abha_address,
                      care_contexts, otp_hash, attempts,
                      expires_at <= now() AS expired
            """,
            (link_ref_number, max_attempts),
        )
        row = cur.fetchone()
        if row is not None:
            columns = [d.name for d in cur.description]
            return dict(zip(columns, row))

        cur.execute(
            "SELECT status, attempts FROM abdm_link_requests WHERE link_ref_number = %s",
            (link_ref_number,),
        )
        existing = cur.fetchone()

    if existing is None:
        return None
    # Known, but already confirmed or out of attempts.
    return {"link_ref_number": link_ref_number, "status": existing[0], "exhausted": True}


def set_link_request_status(link_ref_number: str, status: str) -> None:
    conn = connection()

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE abdm_link_requests SET status = %s WHERE link_ref_number = %s",
            (status, link_ref_number),
        )


def save_linked_care_context(
    *,
    patient_id: str,
    hip_id: str,
    transaction_id: str,
    reference_number: str,
    display: str | None,
    hi_type: str | None,
) -> None:
    """Record a care context the patient linked to their ABHA."""
    conn = connection()

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO abdm_care_contexts (
                patient_id, transaction_id, reference_number, display,
                hi_type, hip_id, status, linked_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, 'LINKED', now())
            """,
            (patient_id, transaction_id, reference_number, display, hi_type, hip_id),
        )


def linked_care_context_refs(*, patient_id: str, hip_id: str) -> set[str]:
    """Care contexts already linked, so discovery does not offer them again."""
    conn = connection()

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT reference_number
            FROM abdm_care_contexts
            WHERE patient_id = %s AND hip_id = %s AND status = 'LINKED'
            """,
            (patient_id, hip_id),
        )
        return {row[0] for row in cur.fetchall()}


# -- patient mobiles -------------------------------------------------------------


def set_patient_mobile(*, patient_id: str, mobile: str) -> None:
    conn = connection()

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO patient_mobiles (patient_id, mobile)
            VALUES (%s, %s)
            ON CONFLICT (patient_id) DO UPDATE SET
                mobile = EXCLUDED.mobile,
                updated_at = now()
            """,
            (patient_id, mobile),
        )


def get_patient_mobile(patient_id: str) -> str | None:
    conn = connection()

    with conn.cursor() as cur:
        cur.execute(
            "SELECT mobile FROM patient_mobiles WHERE patient_id = %s",
            (patient_id,),
        )
        row = cur.fetchone()

    return row[0] if row else None


def patients_with_mobile(mobile: str) -> list[dict[str, Any]]:
    """Demographics of every patient registered with this mobile number."""
    conn = connection()

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.patient_id, p.name, p.gender, p.birth_date
            FROM patient_mobiles m
            JOIN patients p ON p.patient_id = m.patient_id
            WHERE m.mobile = %s
            """,
            (mobile,),
        )
        columns = [d.name for d in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


# -- HIP-initiated linking ------------------------------------------------------


def get_transaction(request_id: str) -> dict[str, Any] | None:
    conn = connection()

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT request_id, patient_id, transaction_id, operation, status,
                   error_code, error_message, created_at, updated_at
            FROM abdm_transactions
            WHERE request_id = %s
            ORDER BY id DESC
            LIMIT 1
            """,
            (request_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        columns = [d.name for d in cur.description]

    return dict(zip(columns, row))


def token_request_in_flight(patient_id: str, *, within_seconds: int) -> bool:
    """A generate-token call for this patient still awaiting its callback.

    ABDM deduplicates identical token requests (ABDM-1092) for at least ~70s,
    so a second request in that window can only fail, and it would hide the
    first refusal's real cause.
    """
    conn = connection()

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM abdm_transactions
            WHERE patient_id = %s
              AND operation = 'LINK_TOKEN_GENERATION'
              AND status = 'INITIATED'
              AND created_at > now() - make_interval(secs => %s)
            LIMIT 1
            """,
            (patient_id, within_seconds),
        )
        return cur.fetchone() is not None


def care_context_refs(
    *, patient_id: str, hip_id: str, statuses: tuple[str, ...]
) -> set[str]:
    conn = connection()

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT reference_number
            FROM abdm_care_contexts
            WHERE patient_id = %s AND hip_id = %s AND status = ANY(%s)
            """,
            (patient_id, hip_id, list(statuses)),
        )
        return {row[0] for row in cur.fetchall()}


def mark_care_contexts_requested(
    *,
    patient_id: str,
    hip_id: str,
    request_id: str,
    contexts: list[dict[str, Any]],
) -> None:
    """Record care contexts sent in one link/carecontext call, pending its
    on_carecontext callback. Replaces any earlier failed attempt's row."""
    conn = connection()

    with conn.cursor() as cur:
        for context in contexts:
            cur.execute(
                """
                DELETE FROM abdm_care_contexts
                WHERE patient_id = %s AND hip_id = %s AND reference_number = %s
                  AND status = 'LINK_FAILED'
                """,
                (patient_id, hip_id, context["referenceNumber"]),
            )
            cur.execute(
                """
                INSERT INTO abdm_care_contexts (
                    patient_id, transaction_id, reference_number, display,
                    hi_type, hip_id, status, request_id
                )
                VALUES (%s, NULL, %s, %s, %s, %s, 'LINK_REQUESTED', %s)
                """,
                (
                    patient_id,
                    context["referenceNumber"],
                    context.get("display"),
                    context.get("hiType"),
                    hip_id,
                    request_id,
                ),
            )


def settle_care_context_request(request_id: str, *, linked: bool) -> int:
    """Apply an on_carecontext outcome to the rows that call requested."""
    conn = connection()

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE abdm_care_contexts
            SET status = %s,
                linked_at = CASE WHEN %s THEN COALESCE(linked_at, now()) ELSE linked_at END
            WHERE request_id = %s AND status = 'LINK_REQUESTED'
            """,
            ("LINKED" if linked else "LINK_FAILED", linked, request_id),
        )
        return cur.rowcount



# -- deep-link SMS ------------------------------------------------------------------


def patients_with_mobiles() -> list[dict[str, Any]]:
    conn = connection()

    with conn.cursor() as cur:
        cur.execute("SELECT patient_id, mobile FROM patient_mobiles ORDER BY patient_id")
        return [{"patient_id": r[0], "mobile": r[1]} for r in cur.fetchall()]


def get_sms_notification(patient_id: str) -> dict[str, Any] | None:
    conn = connection()

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT patient_id, mobile, notified, request_id, status, notified_at
            FROM abdm_sms_notifications
            WHERE patient_id = %s
            """,
            (patient_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        columns = [d.name for d in cur.description]

    return dict(zip(columns, row))


def save_sms_notification(
    *, patient_id: str, mobile: str, notified: list[str], request_id: str
) -> None:
    conn = connection()

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO abdm_sms_notifications (patient_id, mobile, notified, request_id)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (patient_id) DO UPDATE SET
                mobile = EXCLUDED.mobile,
                notified = EXCLUDED.notified,
                request_id = EXCLUDED.request_id,
                status = 'SENT',
                notified_at = now()
            """,
            (patient_id, mobile, Jsonb(notified), request_id),
        )


def set_sms_notification_status(request_id: str, status: str) -> int:
    conn = connection()

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE abdm_sms_notifications SET status = %s WHERE request_id = %s",
            (status, request_id),
        )
        return cur.rowcount
