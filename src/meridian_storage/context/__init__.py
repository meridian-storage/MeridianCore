# SPDX-License-Identifier: Apache-2.0
"""Immutable operation context propagated through :mod:`contextvars`."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType

from meridian_storage.errors import ErrorCode, ValidationError

_LABEL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_current_context: ContextVar[OperationContext | None] = ContextVar(
    "meridian_operation_context", default=None
)


def _bounded(value: str, *, field_name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        raise ValidationError(
            ErrorCode.CONTEXT_INVALID,
            f"{field_name} must be a non-empty UTF-8 string of at most {maximum} bytes",
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValidationError(
            ErrorCode.CONTEXT_INVALID,
            f"{field_name} must not contain control characters",
        )
    return value


def _labels(values: Mapping[str, str], *, field_name: str) -> Mapping[str, str]:
    if len(values) > 32:
        raise ValidationError(
            ErrorCode.CONTEXT_INVALID,
            f"{field_name} may contain at most 32 labels",
        )
    result: dict[str, str] = {}
    for key, value in values.items():
        if not isinstance(key, str) or _LABEL_RE.fullmatch(key) is None:
            raise ValidationError(
                ErrorCode.CONTEXT_INVALID,
                f"{field_name} contains an invalid label name",
            )
        result[key] = _bounded(value, field_name=f"{field_name}.{key}", maximum=256)
    return MappingProxyType(result)


@dataclass(frozen=True, slots=True)
class OperationContext:
    """Request identity and bounded scope attached to every operation."""

    principal_ref: str
    request_id: str | None = None
    tenant: str | None = None
    scope: Mapping[str, str] = field(default_factory=dict)
    correlation_id: str | None = None
    idempotency_key: str | None = None
    trace_context: Mapping[str, str] = field(default_factory=dict)
    deadline: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "principal_ref",
            _bounded(self.principal_ref, field_name="principal_ref", maximum=512),
        )
        for name, maximum in (
            ("request_id", 128),
            ("tenant", 256),
            ("correlation_id", 128),
            ("idempotency_key", 256),
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _bounded(value, field_name=name, maximum=maximum))
        object.__setattr__(self, "scope", _labels(self.scope, field_name="scope"))
        object.__setattr__(
            self,
            "trace_context",
            _labels(self.trace_context, field_name="trace_context"),
        )
        if self.deadline is not None:
            if self.deadline.tzinfo is None or self.deadline.utcoffset() is None:
                raise ValidationError(
                    ErrorCode.CONTEXT_INVALID,
                    "deadline must be timezone-aware",
                )
            object.__setattr__(self, "deadline", self.deadline.astimezone(UTC))

    def resolve_request_id(self) -> OperationContext:
        """Return a context carrying a generated request identity when absent."""

        if self.request_id is not None:
            return self
        return OperationContext(
            principal_ref=self.principal_ref,
            request_id=str(uuid.uuid4()),
            tenant=self.tenant,
            scope=self.scope,
            correlation_id=self.correlation_id,
            idempotency_key=self.idempotency_key,
            trace_context=self.trace_context,
            deadline=self.deadline,
        )

    def remaining_seconds(self, *, now: datetime | None = None) -> float | None:
        if self.deadline is None:
            return None
        current = now or datetime.now(UTC)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        return max(0.0, (self.deadline - current.astimezone(UTC)).total_seconds())


def current_context(*, required: bool = True) -> OperationContext | None:
    """Return the current operation context, failing closed by default."""

    value = _current_context.get()
    if value is None and required:
        raise ValidationError(
            ErrorCode.CONTEXT_REQUIRED,
            "an OperationContext is required for this Meridian operation",
        )
    return value


@contextmanager
def bind_context(context: OperationContext) -> Iterator[OperationContext]:
    """Install *context* for the current thread/task and restore it reliably."""

    resolved = context.resolve_request_id()
    token: Token[OperationContext | None] = _current_context.set(resolved)
    try:
        yield resolved
    finally:
        _current_context.reset(token)


__all__ = ["OperationContext", "bind_context", "current_context"]
