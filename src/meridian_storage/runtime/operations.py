# SPDX-License-Identifier: Apache-2.0
"""Versioned Catalog Expression, Operation, and result contracts."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from importlib import resources as package_resources
from pathlib import Path
from types import MappingProxyType
from typing import cast

from meridian_storage._canonical import deep_freeze, deep_thaw, sha256_fingerprint
from meridian_storage._types import JsonValue
from meridian_storage.registry.resources import (
    REGISTERED_CATALOGS,
    CapabilityRequirement,
    ResourceRef,
)

EXPRESSION_FORMAT_VERSION = "meridian-expression.v1"
OPERATION_FORMAT_VERSION = "meridian-operation.v1"
CATALOG_MANIFEST_FORMAT_VERSION = "meridian-catalog-manifest.v1"
_FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
REGISTERED_CATALOG_METHODS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "structured": (
            "aggregate",
            "create_resource",
            "delete",
            "get",
            "patch",
            "publish_schema",
            "put",
            "query",
            "search",
            "traverse",
        ),
        "object": (
            "create_resource",
            "delete",
            "get",
            "list",
            "publish_schema",
            "put",
            "read_range",
            "stat",
        ),
        "cache": (
            "compare_and_set",
            "create_resource",
            "delete",
            "get",
            "invalidate",
            "put",
            "put_if_absent",
        ),
        "evidence": ("append", "create_resource", "publish_schema", "query"),
        "streaming": (
            "acknowledge",
            "create_resource",
            "negative_acknowledge",
            "poll",
            "publish",
            "publish_batch",
            "publish_schema",
            "read_range",
            "subscribe",
        ),
    }
)


def _bounded(value: str, field_name: str, maximum: int = 256) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{field_name} must be a bounded non-empty string")
    return value


def _string_tuple(values: Sequence[object], field_name: str) -> tuple[str, ...]:
    result = tuple(sorted(_bounded(cast(str, item), field_name) for item in values))
    if len(set(result)) != len(result):
        raise ValueError(f"{field_name} entries must be unique")
    return result


def _fingerprint(value: str, field_name: str) -> str:
    if not isinstance(value, str) or _FINGERPRINT_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a sha256 fingerprint")
    return value


def _safe_metadata(values: Mapping[str, str]) -> Mapping[str, str]:
    if not isinstance(values, Mapping) or len(values) > 64:
        raise ValueError("provenance must be an object with at most 64 entries")
    normalized = {
        _bounded(key, "provenance key", 128): _bounded(value, "provenance value", 512)
        for key, value in values.items()
    }
    return MappingProxyType(dict(sorted(normalized.items())))


@dataclass(frozen=True, slots=True)
class Expression:
    """Mapping-first consumer intent created by an installed Catalog package."""

    catalog: str
    method: str
    arguments: Mapping[str, JsonValue]
    format_version: str = EXPRESSION_FORMAT_VERSION

    def __post_init__(self) -> None:
        if self.format_version != EXPRESSION_FORMAT_VERSION:
            raise ValueError(f"format_version must be {EXPRESSION_FORMAT_VERSION!r}")
        if self.catalog not in REGISTERED_CATALOGS:
            raise ValueError(f"unsupported Meridian Catalog name: {self.catalog!r}")
        object.__setattr__(self, "method", _bounded(self.method, "Expression method", 128))
        object.__setattr__(
            self,
            "arguments",
            cast(Mapping[str, JsonValue], deep_freeze(cast(JsonValue, self.arguments))),
        )

    @property
    def fingerprint(self) -> str:
        return sha256_fingerprint(cast(JsonValue, self.to_dict()))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "formatVersion": self.format_version,
            "catalog": self.catalog,
            "method": self.method,
            "arguments": deep_thaw(cast(JsonValue, self.arguments)),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> Expression:
        required = {"formatVersion", "catalog", "method", "arguments"}
        if set(value) != required:
            raise ValueError("Expression must contain only the four V1 envelope fields")
        arguments = value["arguments"]
        if not isinstance(arguments, Mapping):
            raise TypeError("Expression arguments must be an object")
        return cls(
            format_version=cast(str, value["formatVersion"]),
            catalog=cast(str, value["catalog"]),
            method=cast(str, value["method"]),
            arguments=cast(Mapping[str, JsonValue], arguments),
        )


@dataclass(frozen=True, slots=True)
class OperationContract:
    """One method-to-Operation contract published by a Catalog provider."""

    method: str
    operation_contract: str
    operation_version: str
    read_only: bool
    idempotency: str
    guarantees: tuple[str, ...] = ()
    minimum_limits: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "method", _bounded(self.method, "Catalog method", 128))
        object.__setattr__(
            self,
            "operation_contract",
            _bounded(self.operation_contract, "Operation contract", 256),
        )
        object.__setattr__(
            self,
            "operation_version",
            _bounded(self.operation_version, "Operation version", 64),
        )
        if not isinstance(self.read_only, bool):
            raise ValueError("Operation read_only must be a boolean")
        if self.idempotency not in {"always", "never", "conditional"}:
            raise ValueError("Operation idempotency must be always, never, or conditional")
        object.__setattr__(
            self,
            "guarantees",
            _string_tuple(self.guarantees, "Operation guarantee"),
        )
        limits: dict[str, int] = {}
        for key, item in self.minimum_limits.items():
            if isinstance(item, bool) or not isinstance(item, int) or item < 0:
                raise ValueError("Operation minimum limits must be non-negative integers")
            limits[_bounded(key, "Operation limit name", 128)] = item
        object.__setattr__(self, "minimum_limits", MappingProxyType(dict(sorted(limits.items()))))

    @property
    def requirement(self) -> CapabilityRequirement:
        return CapabilityRequirement(
            operation_contract=self.operation_contract,
            operation_version=self.operation_version,
            guarantees=self.guarantees,
            minimum_limits=self.minimum_limits,
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "method": self.method,
            "operationContract": self.operation_contract,
            "operationVersion": self.operation_version,
            "readOnly": self.read_only,
            "idempotency": self.idempotency,
            "guarantees": list(self.guarantees),
            "minimumLimits": dict(self.minimum_limits),
        }


@dataclass(frozen=True, slots=True)
class CatalogManifest:
    """Installed package identity and exhaustive Catalog Operation surface."""

    catalog_name: str
    package_name: str
    package_version: str
    catalog_contract_version: str
    operations: tuple[OperationContract, ...]
    extensions: Mapping[str, JsonValue] = field(default_factory=dict)
    format_version: str = CATALOG_MANIFEST_FORMAT_VERSION

    def __post_init__(self) -> None:
        if self.format_version != CATALOG_MANIFEST_FORMAT_VERSION:
            raise ValueError(f"format_version must be {CATALOG_MANIFEST_FORMAT_VERSION!r}")
        if self.catalog_name not in REGISTERED_CATALOGS:
            raise ValueError(f"unsupported Meridian Catalog name: {self.catalog_name!r}")
        for field_name in ("package_name", "package_version", "catalog_contract_version"):
            object.__setattr__(self, field_name, _bounded(getattr(self, field_name), field_name))
        operations = tuple(sorted(self.operations, key=lambda item: item.method))
        if not operations:
            raise ValueError("a Catalog manifest must publish at least one Operation contract")
        if len({item.method for item in operations}) != len(operations):
            raise ValueError("Catalog method names must be unique")
        if len({item.operation_contract for item in operations}) != len(operations):
            raise ValueError("Catalog Operation contracts must be unique")
        expected_methods = set(REGISTERED_CATALOG_METHODS[self.catalog_name])
        actual_methods = {item.method for item in operations}
        if actual_methods != expected_methods:
            raise ValueError(
                f"Catalog {self.catalog_name!r} method registry does not match Meridian V1"
            )
        object.__setattr__(self, "operations", operations)
        object.__setattr__(
            self,
            "extensions",
            cast(Mapping[str, JsonValue], deep_freeze(cast(JsonValue, self.extensions))),
        )

    @property
    def fingerprint(self) -> str:
        return sha256_fingerprint(cast(JsonValue, self.to_dict()))

    def operation_for(self, method: str) -> OperationContract:
        for operation in self.operations:
            if operation.method == method:
                return operation
        raise KeyError(method)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "formatVersion": self.format_version,
            "catalogName": self.catalog_name,
            "packageName": self.package_name,
            "packageVersion": self.package_version,
            "catalogContractVersion": self.catalog_contract_version,
            "operations": [item.to_dict() for item in self.operations],
            "extensions": deep_thaw(cast(JsonValue, self.extensions)),
        }


@dataclass(frozen=True, slots=True)
class Operation:
    """One serialized engine-neutral request normalized from an Expression."""

    catalog: str
    operation_contract: str
    operation_version: str
    resources: tuple[ResourceRef, ...]
    input: Mapping[str, JsonValue] = field(default_factory=dict)
    requirements: tuple[CapabilityRequirement, ...] = ()
    read_only: bool = False
    idempotent: bool = False
    format_version: str = OPERATION_FORMAT_VERSION

    def __post_init__(self) -> None:
        if self.format_version != OPERATION_FORMAT_VERSION:
            raise ValueError(f"format_version must be {OPERATION_FORMAT_VERSION!r}")
        if self.catalog not in REGISTERED_CATALOGS:
            raise ValueError(f"unsupported Meridian Catalog name: {self.catalog!r}")
        if not isinstance(self.read_only, bool) or not isinstance(self.idempotent, bool):
            raise ValueError("Operation read_only and idempotent must be booleans")
        object.__setattr__(
            self,
            "operation_contract",
            _bounded(self.operation_contract, "Operation contract"),
        )
        object.__setattr__(
            self,
            "operation_version",
            _bounded(self.operation_version, "Operation version", 64),
        )
        resources = tuple(ResourceRef.parse(item) for item in self.resources)
        if not resources or len(set(resources)) != len(resources):
            raise ValueError("an Operation must target a non-empty unique Resource set")
        if any(item.catalog != self.catalog for item in resources):
            raise ValueError("every Operation Resource must belong to its Catalog")
        object.__setattr__(self, "resources", resources)
        object.__setattr__(
            self,
            "input",
            cast(Mapping[str, JsonValue], deep_freeze(cast(JsonValue, self.input))),
        )
        requirements = tuple(
            sorted(
                self.requirements,
                key=lambda item: (item.operation_contract, item.operation_version),
            )
        )
        if len({(item.operation_contract, item.operation_version) for item in requirements}) != len(
            requirements
        ):
            raise ValueError("Operation requirements must be unique")
        object.__setattr__(self, "requirements", requirements)

    @property
    def request_fingerprint(self) -> str:
        return sha256_fingerprint(cast(JsonValue, self.to_dict()))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "formatVersion": self.format_version,
            "catalog": self.catalog,
            "operationContract": self.operation_contract,
            "operationVersion": self.operation_version,
            "resources": [item.to_dict() for item in self.resources],
            "input": deep_thaw(cast(JsonValue, self.input)),
            "requirements": [item.to_dict() for item in self.requirements],
            "readOnly": self.read_only,
            "idempotent": self.idempotent,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> Operation:
        required = {
            "formatVersion",
            "catalog",
            "operationContract",
            "operationVersion",
            "resources",
            "input",
            "requirements",
            "readOnly",
            "idempotent",
        }
        if set(value) != required:
            raise ValueError("Operation must contain only the V1 envelope fields")
        resources = value["resources"]
        input_value = value["input"]
        requirements = value["requirements"]
        if not isinstance(resources, Sequence) or isinstance(resources, (str, bytes)):
            raise TypeError("Operation resources must be an array")
        if not isinstance(input_value, Mapping):
            raise TypeError("Operation input must be an object")
        if not isinstance(requirements, Sequence) or isinstance(requirements, (str, bytes)):
            raise TypeError("Operation requirements must be an array")
        return cls(
            format_version=cast(str, value["formatVersion"]),
            catalog=cast(str, value["catalog"]),
            operation_contract=cast(str, value["operationContract"]),
            operation_version=cast(str, value["operationVersion"]),
            resources=tuple(
                ResourceRef.parse(cast(Mapping[str, object], item)) for item in resources
            ),
            input=cast(Mapping[str, JsonValue], input_value),
            requirements=tuple(
                CapabilityRequirement.from_mapping(cast(Mapping[str, object], item))
                for item in requirements
            ),
            read_only=cast(bool, value["readOnly"]),
            idempotent=cast(bool, value["idempotent"]),
        )


@dataclass(frozen=True, slots=True)
class OperationResult:
    """Normalized Data and safe provenance returned to consumer code."""

    data: JsonValue
    catalog: str
    operation_contract: str
    operation_version: str
    resources: tuple[ResourceRef, ...]
    request_id: str
    execution_id: str
    operation_fingerprint: str
    registry_fingerprint: str
    capability_fingerprint: str
    provenance: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", deep_freeze(self.data))
        if self.catalog not in REGISTERED_CATALOGS:
            raise ValueError(f"unsupported Meridian Catalog name: {self.catalog!r}")
        for field_name in (
            "operation_contract",
            "operation_version",
            "request_id",
            "execution_id",
        ):
            object.__setattr__(
                self, field_name, _bounded(getattr(self, field_name), field_name, 256)
            )
        for field_name in (
            "operation_fingerprint",
            "registry_fingerprint",
            "capability_fingerprint",
        ):
            object.__setattr__(
                self,
                field_name,
                _fingerprint(getattr(self, field_name), field_name),
            )
        resources = tuple(ResourceRef.parse(item) for item in self.resources)
        if not resources or len(set(resources)) != len(resources):
            raise ValueError("an Operation result requires a non-empty unique Resource set")
        if any(item.catalog != self.catalog for item in resources):
            raise ValueError("every Operation result Resource must belong to its Catalog")
        object.__setattr__(self, "resources", resources)
        object.__setattr__(self, "provenance", _safe_metadata(self.provenance))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "data": deep_thaw(self.data),
            "catalog": self.catalog,
            "operationContract": self.operation_contract,
            "operationVersion": self.operation_version,
            "resources": [item.to_dict() for item in self.resources],
            "requestId": self.request_id,
            "executionId": self.execution_id,
            "operationFingerprint": self.operation_fingerprint,
            "registryFingerprint": self.registry_fingerprint,
            "capabilityFingerprint": self.capability_fingerprint,
            "provenance": dict(self.provenance),
        }


def _public_contract(filename: str) -> Mapping[str, object]:
    packaged = package_resources.files("meridian_storage").joinpath("contracts", filename)
    if packaged.is_file():
        return cast(Mapping[str, object], json.loads(packaged.read_text(encoding="utf-8")))
    source = Path(__file__).resolve().parents[3] / "contracts/public-api" / filename
    return cast(Mapping[str, object], json.loads(source.read_text(encoding="utf-8")))


def expression_contract() -> Mapping[str, object]:
    """Return the released Expression JSON Schema."""

    return _public_contract("meridian-expression.v1.schema.json")


def operation_contract() -> Mapping[str, object]:
    """Return the released Operation JSON Schema."""

    return _public_contract("meridian-operation.v1.schema.json")


__all__ = [
    "EXPRESSION_FORMAT_VERSION",
    "OPERATION_FORMAT_VERSION",
    "REGISTERED_CATALOG_METHODS",
    "CatalogManifest",
    "Expression",
    "Operation",
    "OperationContract",
    "OperationResult",
    "expression_contract",
    "operation_contract",
]
