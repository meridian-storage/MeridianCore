# SPDX-License-Identifier: Apache-2.0
"""Adapter lifecycle, probe, physical verification, and execution SPI."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from meridian_storage._canonical import deep_freeze
from meridian_storage._types import JsonValue
from meridian_storage.context import OperationContext
from meridian_storage.registry.resources import ResourceRef
from meridian_storage.runtime.config import BindingConfig, SecretReference
from meridian_storage.runtime.operations import Operation

from .capabilities import CapabilityManifest

_FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_SECRET_BYTES = 1024 * 1024


def _safe_string(value: str, field_name: str, maximum: int = 512) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{field_name} must be a bounded non-empty string")
    return value


def _fingerprint(value: str, field_name: str) -> str:
    if not isinstance(value, str) or _FINGERPRINT_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a sha256 fingerprint")
    return value


def _safe_mapping(
    values: Mapping[str, str],
    field_name: str,
    *,
    maximum_entries: int,
    maximum_value_bytes: int,
) -> Mapping[str, str]:
    if not isinstance(values, Mapping) or len(values) > maximum_entries:
        raise ValueError(f"{field_name} exceeds its entry bound")
    normalized = {
        _safe_string(key, f"{field_name} key", 512): _safe_string(
            value, f"{field_name} value", maximum_value_bytes
        )
        for key, value in values.items()
    }
    return MappingProxyType(dict(sorted(normalized.items())))


class SecretValue:
    """Secret bytes whose string representations are always redacted."""

    __slots__ = ("__value",)

    def __init__(self, value: bytes | bytearray | memoryview) -> None:
        raw = bytes(value)
        if not raw or len(raw) > _MAX_SECRET_BYTES:
            raise ValueError("secret bytes must be non-empty and no larger than 1 MiB")
        self.__value = raw

    def reveal(self) -> bytes:
        """Return secret bytes to the Adapter factory only."""

        return self.__value

    def __repr__(self) -> str:
        return "SecretValue(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"


@runtime_checkable
class SecretResolver(Protocol):
    def resolve(self, reference: SecretReference) -> SecretValue:
        """Resolve one opaque reference without exposing the resulting bytes."""


@dataclass(frozen=True, slots=True)
class AdapterCreateContext:
    binding: BindingConfig
    identity: SecretValue
    credential: SecretValue
    tls_ca: SecretValue | None = None
    tls_client_certificate: SecretValue | None = None


@dataclass(frozen=True, slots=True)
class AdapterProbe:
    manifest: CapabilityManifest
    evidence: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, CapabilityManifest):
            raise ValueError("Adapter probe manifest must be a CapabilityManifest")
        object.__setattr__(
            self,
            "evidence",
            _safe_mapping(
                self.evidence,
                "probe evidence",
                maximum_entries=64,
                maximum_value_bytes=512,
            ),
        )


@dataclass(frozen=True, slots=True)
class PhysicalResource:
    resource_ref: ResourceRef
    resource_fingerprint: str
    schema_fingerprint: str | None
    profile: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "resource_ref", ResourceRef.parse(self.resource_ref))
        object.__setattr__(
            self,
            "resource_fingerprint",
            _fingerprint(self.resource_fingerprint, "Resource fingerprint"),
        )
        if self.schema_fingerprint is not None:
            object.__setattr__(
                self,
                "schema_fingerprint",
                _fingerprint(self.schema_fingerprint, "Schema fingerprint"),
            )
        object.__setattr__(self, "profile", _safe_string(self.profile, "Resource profile", 256))


@dataclass(frozen=True, slots=True)
class PhysicalVerification:
    fingerprint: str
    mappings: Mapping[str, str] = field(default_factory=dict)
    evidence: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "fingerprint",
            _fingerprint(self.fingerprint, "physical fingerprint"),
        )
        object.__setattr__(
            self,
            "mappings",
            _safe_mapping(
                self.mappings,
                "physical mappings",
                maximum_entries=100_000,
                maximum_value_bytes=2048,
            ),
        )
        object.__setattr__(
            self,
            "evidence",
            _safe_mapping(
                self.evidence,
                "physical evidence",
                maximum_entries=64,
                maximum_value_bytes=512,
            ),
        )


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    operation: Operation
    context: OperationContext
    request_id: str
    execution_id: str
    binding_id: str
    registry_revision: int
    registry_fingerprint: str
    attempt: int

    def __post_init__(self) -> None:
        if not isinstance(self.operation, Operation):
            raise ValueError("ExecutionRequest operation must be an Operation")
        if not isinstance(self.context, OperationContext):
            raise ValueError("ExecutionRequest context must be an OperationContext")
        for field_name in ("request_id", "execution_id", "binding_id"):
            object.__setattr__(
                self,
                field_name,
                _safe_string(getattr(self, field_name), field_name, 256),
            )
        object.__setattr__(
            self,
            "registry_fingerprint",
            _fingerprint(self.registry_fingerprint, "registry fingerprint"),
        )
        if (
            isinstance(self.registry_revision, bool)
            or not isinstance(self.registry_revision, int)
            or self.registry_revision < 1
        ):
            raise ValueError("registry_revision must be a positive integer")
        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int) or self.attempt < 1:
            raise ValueError("attempt must be a positive integer")


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    data: JsonValue
    result_bytes: int = 0
    provenance: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.result_bytes, bool) or not isinstance(self.result_bytes, int):
            raise ValueError("result_bytes must be a non-negative integer")
        if self.result_bytes < 0:
            raise ValueError("result_bytes must be a non-negative integer")
        object.__setattr__(self, "data", deep_freeze(self.data))
        object.__setattr__(
            self,
            "provenance",
            _safe_mapping(
                self.provenance,
                "result provenance",
                maximum_entries=64,
                maximum_value_bytes=512,
            ),
        )


@runtime_checkable
class AdapterSession(Protocol):
    def begin(self) -> None: ...

    def execute(self, request: ExecutionRequest) -> ExecutionResult: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


@runtime_checkable
class AdapterRuntime(Protocol):
    def open(self) -> None: ...

    def probe(self) -> AdapterProbe: ...

    def verify_physical(self, resources: tuple[PhysicalResource, ...]) -> PhysicalVerification: ...

    def open_session(self, *, transactional: bool) -> AdapterSession: ...

    def close(self) -> None: ...


@runtime_checkable
class AdapterFactory(Protocol):
    @property
    def adapter_id(self) -> str: ...

    def create(self, context: AdapterCreateContext) -> AdapterRuntime: ...


__all__ = [
    "AdapterCreateContext",
    "AdapterFactory",
    "AdapterProbe",
    "AdapterRuntime",
    "AdapterSession",
    "ExecutionRequest",
    "ExecutionResult",
    "PhysicalResource",
    "PhysicalVerification",
    "SecretResolver",
    "SecretValue",
]
