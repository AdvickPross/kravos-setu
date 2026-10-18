from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CareContext(BaseModel):
    reference_number: str = Field(
        ...,
        alias="referenceNumber",
        min_length=1,
    )
    display: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class DiscoveredPatient(BaseModel):
    reference_number: str = Field(
        ...,
        alias="referenceNumber",
        min_length=1,
    )
    display: str | None = None

    care_contexts: list[CareContext] = Field(
        ...,
        alias="careContexts",
    )

    hi_type: str = Field(
        ...,
        alias="hiType",
        min_length=1,
    )

    count: int = Field(
        ...,
        ge=0,
    )

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="after")
    def validate_count(self) -> "DiscoveredPatient":
        if self.count != len(self.care_contexts):
            raise ValueError(
                "count must match the number of careContexts"
            )
        return self


class DiscoveryError(BaseModel):
    code: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)


class DiscoveryResponseInfo(BaseModel):
    request_id: str = Field(
        ...,
        alias="requestId",
        min_length=1,
    )

    model_config = ConfigDict(populate_by_name=True)


class HmisLmisDiscoveryResponseRequest(BaseModel):
    transaction_id: str = Field(
        ...,
        alias="transactionId",
        min_length=1,
    )

    patient: list[DiscoveredPatient] | None = None

    matched_by: list[str] | None = Field(
        default=None,
        alias="matchedBy",
    )

    response: DiscoveryResponseInfo

    error: DiscoveryError | None = None

    model_config = ConfigDict(populate_by_name=True)
class TokenRequest(BaseModel):
    abha_address: str | None = Field(
        default=None,
        alias="abhaAddress",
    )

    abha_number: str | None = Field(
        default=None,
        alias="abhaNumber",
    )

    name: str = Field(
        ...,
        min_length=1,
    )

    gender: str = Field(
        ...,
        min_length=1,
    )

    year_of_birth: int = Field(
        ...,
        alias="yearOfBirth",
        ge=1900,
        le=2200,
    )

    # Ours, not ABDM's: ties the transaction to a patient. Never sent.
    patient_id: str | None = Field(
        default=None,
        alias="patientId",
        exclude=True,
    )

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="after")
    def validate_abha_identifier(self) -> "TokenRequest":
        if not self.abha_address and not self.abha_number:
            raise ValueError(
                "Either abhaAddress or abhaNumber must be provided"
            )

        return self


class LinkCareContextRequest(BaseModel):
    abha_address: str = Field(
        ...,
        alias="abhaAddress",
        min_length=1,
    )

    abha_number: str | None = Field(
        default=None,
        alias="abhaNumber",
    )

    patient: list[DiscoveredPatient] = Field(
        ...,
        min_length=1,
    )

    # Ours, not ABDM's. The link token is looked up server-side from what
    # on-generate-token stored and is sent as X-LINK-TOKEN.
    patient_id: str | None = Field(
        default=None,
        alias="patientId",
        exclude=True,
    )

    model_config = ConfigDict(populate_by_name=True)


class TokenResponse(BaseModel):
    abha_number: str | None = Field(
        default=None,
        alias="abhaNumber",
    )

    abha_address: str | None = Field(
        default=None,
        alias="abhaAddress",
    )

    link_token: str | None = Field(
        default=None,
        alias="linkToken",
    )

    response: dict[str, Any] | None = None

    error: dict[str, Any] | None = None

    model_config = ConfigDict(populate_by_name=True)

class DiscoveryIdentifier(BaseModel):
    type: str = Field(
        ...,
        min_length=1,
    )

    value: str = Field(
        ...,
        min_length=1,
    )

class VerifiedIdentifier(BaseModel):
    type: str = Field(
        ...,
        min_length=1,
    )

    value: str = Field(
        ...,
        min_length=1,
    )


class HipDiscoveryPatient(BaseModel):
    id: str = Field(
        ...,
        min_length=1,
    )

    verified_identifiers: list[VerifiedIdentifier] = Field(
        default_factory=list,
        alias="verifiedIdentifiers",
    )

    unverified_identifiers: list[DiscoveryIdentifier] = Field(
        default_factory=list,
        alias="unverifiedIdentifiers",
    )

    name: str = Field(
        ...,
        min_length=1,
    )

    gender: str = Field(
        ...,
        min_length=1,
    )

    year_of_birth: int = Field(
        ...,
        alias="yearOfBirth",
        ge=1900,
        le=2200,
    )

    model_config = ConfigDict(populate_by_name=True)


class HipDiscoveryCallbackRequest(BaseModel):
    transaction_id: str = Field(
        ...,
        alias="transactionId",
        min_length=1,
    )

    patient: HipDiscoveryPatient

    model_config = ConfigDict(populate_by_name=True)


class LinkInitPatient(BaseModel):
    """One hiType group in a link/init request. The gateway echoes back what
    we offered in on-discover; display, hiType and count are not relied on."""

    reference_number: str = Field(..., alias="referenceNumber", min_length=1)
    care_contexts: list[CareContext] = Field(..., alias="careContexts", min_length=1)
    hi_type: str | None = Field(default=None, alias="hiType")
    count: int | None = None

    model_config = ConfigDict(populate_by_name=True)


class LinkInitRequest(BaseModel):
    transaction_id: str = Field(
        ...,
        alias="transactionId",
        min_length=1,
    )

    abha_address: str | None = Field(
        default=None,
        alias="abhaAddress",
    )

    patient: list[LinkInitPatient] = Field(
        ...,
        min_length=1,
    )

    model_config = ConfigDict(
        populate_by_name=True,
    )


class LinkConfirmation(BaseModel):
    link_ref_number: str = Field(..., alias="linkRefNumber", min_length=1)
    token: str = Field(..., min_length=1)

    model_config = ConfigDict(populate_by_name=True)


class LinkConfirmRequest(BaseModel):
    """Body of the gateway's /hip/link/care-context/confirm: the OTP the
    patient typed into their PHR app, against our link reference."""

    confirmation: LinkConfirmation

    model_config = ConfigDict(populate_by_name=True)
