from __future__ import annotations

from typing import Any

from abha.client import AbhaClient


class AbdmM2Client:
    """
    Client for ABDM M2 APIs.

    This class is intentionally kept separate from AbhaClient so that
    M1 ABHA functionality remains unchanged while M2-specific API
    operations are added incrementally.
    """

    def __init__(self, client: AbhaClient):
        self.client = client

    def discover_care_contexts(
        self,
        *,
        body: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        return self.client.post(
            "/api/hiecm/user-initiated-linking/v3/patient/care-context/discover",
            body=body,
            host="abha",
            extra_headers=extra_headers,
        )

    def respond_to_care_context_discovery(
        self,
        *,
        body: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        return self.client.post(
            "/api/hiecm/user-initiated-linking/v3/patient/care-context/on-discover",
            body=body,
            host="abha",
            extra_headers=extra_headers,
        )

    def generate_link_token(
        self,
        *,
        body: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        return self.client.post(
            "/api/hiecm/v3/token/generate-token",
            body=body,
            host="abha",
            extra_headers=extra_headers,
        )

    def link_care_context(
        self,
        *,
        body: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        return self.client.post(
            "/api/hiecm/hip/v3/link/carecontext",
            body=body,
            host="abha",
            extra_headers=extra_headers,
        )

    def initialize_care_context_link(
        self,
        *,
        body: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        return self.client.post(
            "/api/hiecm/user-initiated-linking/v3/link/care-context/init",
            body=body,
            host="abha",
            extra_headers=extra_headers,
        )

    def respond_to_care_context_link_init(
        self,
        *,
        body: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        return self.client.post(
            "/api/hiecm/user-initiated-linking/v3/link/care-context/on-init",
            body=body,
            host="abha",
            extra_headers=extra_headers,
        )

    def confirm_care_context_link(
        self,
        *,
        body: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        return self.client.post(
            "/api/hiecm/user-initiated-linking/v3/link/care-context/confirm",
            body=body,
            host="abha",
            extra_headers=extra_headers,
        )
