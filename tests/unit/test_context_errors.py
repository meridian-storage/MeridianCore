# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from meridian_storage import (
    CatalogNotFound,
    ConfigurationError,
    ErrorCategory,
    ErrorCode,
    MeridianError,
    OperationContext,
    SafeCause,
    TransientError,
    ValidationError,
    bind_context,
    current_context,
)


def test_context_generates_request_identity_when_bound_and_restores() -> None:
    context = OperationContext(
        principal_ref="principal:test",
        tenant="tenant",
        scope={"workspace": "one"},
    )
    assert context.request_id is None
    with bind_context(context) as resolved:
        assert resolved.request_id is not None
        assert current_context() is resolved
        assert resolved.resolve_request_id() is resolved
    assert current_context(required=False) is None
    with pytest.raises(ValidationError) as failure:
        current_context()
    assert failure.value.code == ErrorCode.CONTEXT_REQUIRED


def test_context_normalizes_deadline_and_remaining_time() -> None:
    deadline = datetime.now(UTC) + timedelta(seconds=10)
    context = OperationContext("principal", deadline=deadline)
    assert 0 < (context.remaining_seconds(now=datetime.now(UTC)) or 0) <= 10
    assert (
        OperationContext("principal", deadline=datetime.now(UTC) - timedelta(1)).remaining_seconds()
        == 0
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        context.remaining_seconds(now=datetime.now())
    with pytest.raises(ValidationError, match="timezone-aware"):
        OperationContext("principal", deadline=datetime.now())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"principal_ref": ""},
        {"principal_ref": "principal", "request_id": "x\n"},
        {"principal_ref": "principal", "scope": {"bad key": "value"}},
        {"principal_ref": "principal", "scope": {f"k{i}": "v" for i in range(33)}},
        {"principal_ref": "principal", "trace_context": {"trace": "x" * 257}},
    ],
)
def test_context_rejects_unbounded_or_invalid_metadata(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError) as failure:
        OperationContext(**kwargs)  # type: ignore[arg-type]
    assert failure.value.code == ErrorCode.CONTEXT_INVALID


def test_error_envelope_is_stable_and_redacted() -> None:
    error = MeridianError(
        ErrorCode.ADAPTER_FAILURE,
        "safe diagnostic",
        category=ErrorCategory.INTERNAL,
        operation_contract="meridian.structured.get",
        resource_ref="structured:investigation.cases",
        adapter_provenance={"adapterId": "adapter", "capabilityFingerprint": "sha256:safe"},
        request_id="request",
        execution_id="execution",
        cause=SafeCause("VendorException", "SAFE_CODE"),
    )
    payload = error.to_dict()
    assert payload == {
        "code": "MERIDIAN_ADAPTER_FAILURE",
        "category": "INTERNAL",
        "message": "safe diagnostic",
        "retryable": False,
        "operationContract": "meridian.structured.get",
        "resourceRef": "structured:investigation.cases",
        "requestId": "request",
        "executionId": "execution",
        "adapterProvenance": {
            "adapterId": "adapter",
            "capabilityFingerprint": "sha256:safe",
        },
        "cause": {"type": "VendorException", "code": "SAFE_CODE"},
    }
    assert "traceback" not in str(payload).lower()


def test_safe_cause_does_not_capture_exception_message() -> None:
    secret = "do-not-leak"
    cause = SafeCause.from_exception(RuntimeError(secret))
    assert cause.type == "RuntimeError"
    assert secret not in str(cause)
    error = ConfigurationError(
        ErrorCode.CONFIG_INVALID,
        "safe",
        cause=cause,
    )
    assert secret not in str(error.to_dict())


def test_typed_error_defaults() -> None:
    transient = TransientError(ErrorCode.ADAPTER_FAILURE, "retry")
    assert transient.retryable
    assert transient.category is ErrorCategory.TRANSIENT
    missing = CatalogNotFound("query")
    assert missing.code == ErrorCode.CATALOG_NOT_FOUND
    assert missing.resource_ref == "query"
