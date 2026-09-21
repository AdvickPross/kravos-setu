from __future__ import annotations
from typing import Any
from abha.client import AbhaClient


class AbdmM2Client:
    """
    Client for ABDM M2 Health Information Exchange operations.

    This class handles outbound requests through the existing AbhaClient.
    Callback endpoints should be exposed separately through FastAPI routes.
    """

    def __init__(self, client: AbhaClient):
        self.client = client

    def generate_link_token(
        self,
        *,
        body: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        """
        Generate a token for HIP-initiated care-context linking.
        """
        return self.client.post(
            "/api/hiecm/v3/token/generate-token",
            body=body,
            host="abha",
            extra_headers=extra_headers,
        )

    def send_link_request(
        self,
        *,
        transaction_id: str,
        link: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        """
        Send a link request through the configured ABDM endpoint.

        IMPORTANT:
        Confirm the exact outbound endpoint from the M2 specification
        before calling this method in production.
        """
        payload = {
            "transactionId": transaction_id,
            "link": link,
        }

        return self.client.post(
            "/api/v3/hiu/patient/care-context/link",
            body=payload,
            extra_headers=extra_headers,
        )