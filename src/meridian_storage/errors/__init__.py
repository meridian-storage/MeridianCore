# SPDX-License-Identifier: Apache-2.0
"""Stable, typed and redacted Meridian public errors."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class ErrorCategory(StrEnum):
    CONFIGURATION = "CONFIGURATION"
    COMPATIBILITY = "COMPATIBILITY"
    VALIDATION = "VALIDATION"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    CONSTRAINT = "CONSTRAINT"
    AUTHENTICATION = "AUTHENTICATION"
    AUTHORIZATION = "AUTHORIZATION"
    TIMEOUT = "TIMEOUT"
    RATE_LIMIT = "RATE_LIMIT"
    TRANSIENT = "TRANSIENT"
    UNAVAILABLE = "UNAVAILABLE"
    TRANSACTION = "TRANSACTION"
    CORRUPTION = "CORRUPTION"
    INTERNAL = "INTERNAL"


class ErrorCode(StrEnum):
    CONFIG_INVALID = "MERIDIAN_CONFIG_INVALID"
    CONFIG_NOT_FOUND = "MERIDIAN_CONFIG_NOT_FOUND"
    CONFIG_DUPLICATE_ID = "MERIDIAN_CONFIG_DUPLICATE_ID"
    CONFIG_UNKNOWN_FIELD = "MERIDIAN_CONFIG_UNKNOWN_FIELD"
    CONFIG_SECRET_REFERENCE = "MERIDIAN_CONFIG_SECRET_REFERENCE"
    DISCOVERY_DUPLICATE = "MERIDIAN_DISCOVERY_DUPLICATE"
    DISCOVERY_FAILED = "MERIDIAN_DISCOVERY_FAILED"
    CATALOG_NOT_FOUND = "MERIDIAN_CATALOG_NOT_FOUND"
    CATALOG_UNAVAILABLE = "MERIDIAN_CATALOG_UNAVAILABLE"
    CATALOG_CONTRACT = "MERIDIAN_CATALOG_CONTRACT"
    ADAPTER_NOT_FOUND = "MERIDIAN_ADAPTER_NOT_FOUND"
    ADAPTER_CONTRACT = "MERIDIAN_ADAPTER_CONTRACT"
    CAPABILITY_FINGERPRINT = "MERIDIAN_CAPABILITY_FINGERPRINT"
    CAPABILITY_UNSUPPORTED = "MERIDIAN_CAPABILITY_UNSUPPORTED"
    PHYSICAL_FINGERPRINT = "MERIDIAN_PHYSICAL_FINGERPRINT"
    PLACEMENT_UNRESOLVED = "MERIDIAN_PLACEMENT_UNRESOLVED"
    PLACEMENT_AMBIGUOUS = "MERIDIAN_PLACEMENT_AMBIGUOUS"
    REGISTRY_DUPLICATE = "MERIDIAN_REGISTRY_DUPLICATE"
    REGISTRY_REFERENCE = "MERIDIAN_REGISTRY_REFERENCE"
    REGISTRY_CYCLE = "MERIDIAN_REGISTRY_CYCLE"
    REGISTRY_REFRESH = "MERIDIAN_REGISTRY_REFRESH"
    RUNTIME_STATE = "MERIDIAN_RUNTIME_STATE"
    RUNTIME_STARTUP = "MERIDIAN_RUNTIME_STARTUP"
    RUNTIME_CLOSED = "MERIDIAN_RUNTIME_CLOSED"
    CONTEXT_REQUIRED = "MERIDIAN_CONTEXT_REQUIRED"
    CONTEXT_INVALID = "MERIDIAN_CONTEXT_INVALID"
    RESOURCE_NOT_FOUND = "MERIDIAN_RESOURCE_NOT_FOUND"
    OPERATION_SCOPE = "MERIDIAN_OPERATION_SCOPE"
    OPERATION_INVALID = "MERIDIAN_OPERATION_INVALID"
    OPERATION_RESULT_LIMIT = "MERIDIAN_OPERATION_RESULT_LIMIT"
    DEADLINE_EXCEEDED = "MERIDIAN_DEADLINE_EXCEEDED"
    TRANSACTION_SCOPE = "MERIDIAN_TRANSACTION_SCOPE"
    TRANSACTION_STATE = "MERIDIAN_TRANSACTION_STATE"
    IDEMPOTENCY_CONFLICT = "MERIDIAN_IDEMPOTENCY_CONFLICT"
    ADAPTER_FAILURE = "MERIDIAN_ADAPTER_FAILURE"
    PLUGIN_NOT_FOUND = "MERIDIAN_PLUGIN_NOT_FOUND"
    PLUGIN_CONTRACT = "MERIDIAN_PLUGIN_CONTRACT"
    INTERNAL = "MERIDIAN_INTERNAL"


@dataclass(frozen=True, slots=True)
class SafeCause:
    """A deliberately redacted cause safe to return across public boundaries."""

    type: str
    code: str | None = None

    @classmethod
    def from_exception(cls, exc: BaseException) -> SafeCause:
        return cls(type=type(exc).__name__)


class MeridianError(Exception):
    """Base class for every public Meridian failure."""

    def __init__(
        self,
        code: ErrorCode | str,
        message: str,
        *,
        category: ErrorCategory,
        retryable: bool = False,
        operation_contract: str | None = None,
        resource_ref: str | None = None,
        adapter_provenance: Mapping[str, str] | None = None,
        request_id: str | None = None,
        execution_id: str | None = None,
        cause: SafeCause | None = None,
    ) -> None:
        self.code = str(code)
        self.message = message
        self.category = category
        self.retryable = retryable
        self.operation_contract = operation_contract
        self.resource_ref = resource_ref
        self.adapter_provenance = MappingProxyType(dict(adapter_provenance or {}))
        self.request_id = request_id
        self.execution_id = execution_id
        self.cause = cause
        super().__init__(f"{self.code}: {self.message}")

    def to_dict(self) -> dict[str, Any]:
        """Return the stable, credential-free public error envelope."""

        payload: dict[str, Any] = {
            "code": self.code,
            "category": self.category.value,
            "message": self.message,
            "retryable": self.retryable,
        }
        optional = {
            "operationContract": self.operation_contract,
            "resourceRef": self.resource_ref,
            "requestId": self.request_id,
            "executionId": self.execution_id,
        }
        payload.update({key: value for key, value in optional.items() if value is not None})
        if self.adapter_provenance:
            payload["adapterProvenance"] = dict(self.adapter_provenance)
        if self.cause is not None:
            payload["cause"] = {
                key: value
                for key, value in {"type": self.cause.type, "code": self.cause.code}.items()
                if value is not None
            }
        return payload


class ConfigurationError(MeridianError):
    def __init__(self, code: ErrorCode | str, message: str, **details: Any) -> None:
        super().__init__(code, message, category=ErrorCategory.CONFIGURATION, **details)


class CompatibilityError(MeridianError):
    def __init__(self, code: ErrorCode | str, message: str, **details: Any) -> None:
        super().__init__(code, message, category=ErrorCategory.COMPATIBILITY, **details)


class ValidationError(MeridianError):
    def __init__(self, code: ErrorCode | str, message: str, **details: Any) -> None:
        super().__init__(code, message, category=ErrorCategory.VALIDATION, **details)


class NotFoundError(MeridianError):
    def __init__(self, code: ErrorCode | str, message: str, **details: Any) -> None:
        super().__init__(code, message, category=ErrorCategory.NOT_FOUND, **details)


class CatalogNotFound(NotFoundError):
    """An unknown or unregistered Catalog name was requested."""

    def __init__(self, catalog_name: str, **details: Any) -> None:
        super().__init__(
            ErrorCode.CATALOG_NOT_FOUND,
            f"Catalog {catalog_name!r} is not registered by Meridian V1",
            resource_ref=catalog_name,
            **details,
        )


class ConflictError(MeridianError):
    def __init__(self, code: ErrorCode | str, message: str, **details: Any) -> None:
        super().__init__(code, message, category=ErrorCategory.CONFLICT, **details)


class ConstraintError(MeridianError):
    def __init__(self, code: ErrorCode | str, message: str, **details: Any) -> None:
        super().__init__(code, message, category=ErrorCategory.CONSTRAINT, **details)


class AuthenticationError(MeridianError):
    def __init__(self, code: ErrorCode | str, message: str, **details: Any) -> None:
        super().__init__(code, message, category=ErrorCategory.AUTHENTICATION, **details)


class AuthorizationError(MeridianError):
    def __init__(self, code: ErrorCode | str, message: str, **details: Any) -> None:
        super().__init__(code, message, category=ErrorCategory.AUTHORIZATION, **details)


class MeridianTimeoutError(MeridianError):
    def __init__(self, code: ErrorCode | str, message: str, **details: Any) -> None:
        super().__init__(code, message, category=ErrorCategory.TIMEOUT, **details)


class RateLimitError(MeridianError):
    def __init__(self, code: ErrorCode | str, message: str, **details: Any) -> None:
        super().__init__(code, message, category=ErrorCategory.RATE_LIMIT, **details)


class TransientError(MeridianError):
    def __init__(self, code: ErrorCode | str, message: str, **details: Any) -> None:
        details.setdefault("retryable", True)
        super().__init__(code, message, category=ErrorCategory.TRANSIENT, **details)


class UnavailableError(MeridianError):
    def __init__(self, code: ErrorCode | str, message: str, **details: Any) -> None:
        super().__init__(code, message, category=ErrorCategory.UNAVAILABLE, **details)


class TransactionError(MeridianError):
    def __init__(self, code: ErrorCode | str, message: str, **details: Any) -> None:
        super().__init__(code, message, category=ErrorCategory.TRANSACTION, **details)


class CommitState(StrEnum):
    KNOWN_COMMITTED = "known-committed"
    KNOWN_NOT_COMMITTED = "known-not-committed"
    UNKNOWN_COMMIT = "unknown-commit"


class CommitOutcomeError(MeridianTimeoutError):
    """No effect retry: reconcile the domain operation under fresh authorization."""

    def __init__(self, state: CommitState, message: str) -> None:
        self.commit_state = state
        super().__init__(ErrorCode.TRANSACTION_STATE, message, retryable=False)

    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), "commitState": self.commit_state.value}


class CorruptionError(MeridianError):
    def __init__(self, code: ErrorCode | str, message: str, **details: Any) -> None:
        super().__init__(code, message, category=ErrorCategory.CORRUPTION, **details)


class InternalError(MeridianError):
    def __init__(self, code: ErrorCode | str, message: str, **details: Any) -> None:
        super().__init__(code, message, category=ErrorCategory.INTERNAL, **details)


class LifecycleError(MeridianError):
    def __init__(self, code: ErrorCode | str, message: str, **details: Any) -> None:
        super().__init__(code, message, category=ErrorCategory.UNAVAILABLE, **details)


__all__ = [
    "AuthenticationError",
    "AuthorizationError",
    "CatalogNotFound",
    "CommitOutcomeError",
    "CommitState",
    "CompatibilityError",
    "ConfigurationError",
    "ConflictError",
    "ConstraintError",
    "CorruptionError",
    "ErrorCategory",
    "ErrorCode",
    "InternalError",
    "LifecycleError",
    "MeridianError",
    "MeridianTimeoutError",
    "NotFoundError",
    "RateLimitError",
    "SafeCause",
    "TransactionError",
    "TransientError",
    "UnavailableError",
    "ValidationError",
]
