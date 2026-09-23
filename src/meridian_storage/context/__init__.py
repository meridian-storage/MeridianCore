# SPDX-License-Identifier: Apache-2.0
"""Immutable operation context propagated through :mod:`contextvars`."""

from __future__ import annotations

import math
import re
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Event
from types import MappingProxyType

from meridian_storage.errors import ErrorCode, MeridianTimeoutError, ValidationError

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
    monotonic_deadline: float | None = None
    _deadline_monotonic: float | None = field(default=None, init=False, repr=False, compare=False)
    cancellation: Event | None = field(default=None, compare=False, repr=False)

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
            converted = time.monotonic() + max(
                0.0, (self.deadline - datetime.now(UTC)).total_seconds()
            )
            bound = (
                converted
                if self.monotonic_deadline is None
                else min(converted, self.monotonic_deadline)
            )
            object.__setattr__(self, "_deadline_monotonic", bound)
        if self.monotonic_deadline is not None and not math.isfinite(self.monotonic_deadline):
            raise ValidationError(ErrorCode.CONTEXT_INVALID, "monotonic_deadline must be finite")

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
            monotonic_deadline=self._deadline_monotonic
            if self._deadline_monotonic is not None
            else self.monotonic_deadline,
            cancellation=self.cancellation,
        )

    def remaining_seconds(self, *, now: datetime | None = None) -> float | None:
        if self.cancellation is not None and self.cancellation.is_set():
            return 0.0
        deadline = (
            self._deadline_monotonic
            if self._deadline_monotonic is not None
            else self.monotonic_deadline
        )
        remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
        # Explicit `now` remains supported for deterministic wall-clock callers.
        if now is not None:
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError("now must be timezone-aware")
            if self.deadline is not None:
                wall = max(0.0, (self.deadline - now.astimezone(UTC)).total_seconds())
                remaining = wall if remaining is None else min(remaining, wall)
        return remaining

    def check_budget(self) -> None:
        remaining = self.remaining_seconds()
        if remaining is not None and remaining <= 0:
            raise MeridianTimeoutError(
                ErrorCode.DEADLINE_EXCEEDED,
                "operation deadline expired or cancelled",
                request_id=self.request_id,
            )


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
