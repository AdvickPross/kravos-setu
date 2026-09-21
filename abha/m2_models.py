from __future__ import annotations
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class CareContext(BaseModel):
    reference_number: str = Field(
        ...,
        alias="referenceNumber",
        min_length=1,
    )
    display: str | None = None

    model_config = ConfigDict(
        populate_by_name=True,
    )


class PatientReference(BaseModel):
    reference_number: str = Field(
        ...,
        alias="referenceNumber",
        min_length=1,
    )
    display: str | None = None

    model_config = ConfigDict(
        populate_by_name=True,
    )


class OnDiscoverRequest(BaseModel):
    transaction_id: str = Field(
        ...,
        alias="transactionId",
        min_length=1,
    )
    patient: dict[str, Any] | None = None
    reference_number: str | None = Field(
        default=None,
        alias="referenceNumber",
    )
    care_contexts: list[CareContext] = Field(
        default_factory=list,
        alias="careContexts",
    )
    hi_type: str | None = Field(
        default=None,
        alias="hiType",
    )

    model_config = ConfigDict(
        populate_by_name=True,
    )


class LinkRequest(BaseModel):
    transaction_id: str = Field(
        ...,
        alias="transactionId",
        min_length=1,
    )
    link: dict[str, Any] = Field(
        ...,
        min_length=1,
    )

    model_config = ConfigDict(
        populate_by_name=True,
    )


class LinkResponse(BaseModel):
    transaction_id: str | None = Field(
        default=None,
        alias="transactionId",
    )
    link: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    request_id: str | None = Field(
        default=None,
        alias="requestId",
    )
    meta: dict[str, Any] | None = None

    model_config = ConfigDict(
        populate_by_name=True,
    )


class TokenRequest(BaseModel):
    patient_id: str | None = Field(
        default=None,
        alias="patientId",
    )
    care_contexts: list[PatientReference] = Field(
        default_factory=list,
        alias="careContexts",
    )

    model_config = ConfigDict(
        populate_by_name=True,
    )