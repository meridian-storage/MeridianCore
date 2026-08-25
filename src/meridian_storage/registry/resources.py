# SPDX-License-Identifier: Apache-2.0
"""Immutable logical resources shared by Catalog providers and Core."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import cast

from meridian_storage._canonical import deep_freeze, deep_thaw, sha256_fingerprint
from meridian_storage._types import JsonValue

_CATALOGS = frozenset({"structured", "object", "cache", "evidence", "streaming"})
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,254}[A-Za-z0-9]$|^[A-Za-z]$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")


def _name(value: str, field_name: str) -> str:
    if not isinstance(value, str) or _NAME_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a bounded logical name")
    return value


def _token(value: str, field_name: str) -> str:
    if not isinstance(value, str) or _TOKEN_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a bounded contract token")
    return value


def _catalog(value: str) -> str:
    if value not in _CATALOGS:
        raise ValueError(f"unsupported Meridian Catalog name: {value!r}")
    return value


def _labels(values: Mapping[str, str]) -> Mapping[str, str]:
    if len(values) > 64:
        raise ValueError("labels may contain at most 64 entries")
    result: dict[str, str] = {}
    for key, value in values.items():
        result[_name(key, "label key")] = _name(value, "label value")
    return MappingProxyType(dict(sorted(result.items())))


@dataclass(frozen=True, slots=True, order=True)
class ResourceRef:
    """Logical Resource address; never a physical engine identifier."""

    catalog: str
    namespace: str
    name: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "catalog", _catalog(self.catalog))
        object.__setattr__(self, "namespace", _name(self.namespace, "resource namespace"))
        object.__setattr__(self, "name", _name(self.name, "resource name"))

    @property
    def logical_name(self) -> str:
        return f"{self.namespace}.{self.name}"

    @property
    def canonical(self) -> str:
        return f"{self.catalog}:{self.logical_name}"

    def __str__(self) -> str:
        return self.canonical

    def to_dict(self) -> dict[str, str]:
        return {
            "catalog": self.catalog,
            "namespace": self.namespace,
            "name": self.name,
        }

    @classmethod
    def parse(
        cls,
        value: ResourceRef | str | Mapping[str, object],
        *,
        catalog: str | None = None,
    ) -> ResourceRef:
        if isinstance(value, cls):
            if catalog is not None and value.catalog != catalog:
                raise ValueError("resource Catalog does not match the requested Catalog")
            return value
        if isinstance(value, Mapping):
            expected = {"catalog", "namespace", "name"}
            if set(value) != expected:
                raise ValueError("resource reference requires catalog, namespace, and name")
            return cls(
                cast(str, value["catalog"]),
                cast(str, value["namespace"]),
                cast(str, value["name"]),
            )
        if not isinstance(value, str):
            raise TypeError("resource reference must be a ResourceRef, mapping, or string")
        selected_catalog = catalog
        logical_name = value
        if ":" in value:
            selected_catalog, logical_name = value.split(":", 1)
        if selected_catalog is None:
            raise ValueError("an unqualified resource reference requires a Catalog name")
        if "." not in logical_name:
            raise ValueError("resource string must be '<namespace>.<name>'")
        namespace, name = logical_name.rsplit(".", 1)
        return cls(selected_catalog, namespace, name)


@dataclass(frozen=True, slots=True, order=True)
class SchemaRef:
    """Exact logical Schema version address."""

    catalog: str
    namespace: str
    name: str
    version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "catalog", _catalog(self.catalog))
        object.__setattr__(self, "namespace", _name(self.namespace, "schema namespace"))
        object.__setattr__(self, "name", _name(self.name, "schema name"))
        object.__setattr__(self, "version", _token(self.version, "schema version"))

    @property
    def logical_name(self) -> str:
        return f"{self.namespace}.{self.name}"

    @property
    def canonical(self) -> str:
        return f"{self.catalog}:{self.logical_name}@{self.version}"

    def __str__(self) -> str:
        return self.canonical

    def to_dict(self) -> dict[str, str]:
        return {
            "catalog": self.catalog,
            "namespace": self.namespace,
            "name": self.name,
            "version": self.version,
        }

    @classmethod
    def parse(cls, value: SchemaRef | Mapping[str, object]) -> SchemaRef:
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise TypeError("schema reference must be a SchemaRef or mapping")
        expected = {"catalog", "namespace", "name", "version"}
        if set(value) != expected:
            raise ValueError("schema reference requires catalog, namespace, name, and version")
        return cls(
            cast(str, value["catalog"]),
            cast(str, value["namespace"]),
            cast(str, value["name"]),
            cast(str, value["version"]),
        )


@dataclass(frozen=True, slots=True)
class CapabilityRequirement:
    """One Operation contract, guarantee, and limit requirement."""

    operation_contract: str
    operation_version: str
    guarantees: tuple[str, ...] = ()
    minimum_limits: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "operation_contract",
            _token(self.operation_contract, "operation contract"),
        )
        object.__setattr__(
            self,
            "operation_version",
            _token(self.operation_version, "operation version"),
        )
        guarantees = tuple(sorted({_token(item, "guarantee") for item in self.guarantees}))
        if len(guarantees) != len(self.guarantees):
            raise ValueError("capability guarantees must be unique")
        object.__setattr__(self, "guarantees", guarantees)
        limits: dict[str, int] = {}
        for key, value in self.minimum_limits.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("minimum capability limits must be non-negative integers")
            limits[_token(key, "limit name")] = value
        object.__setattr__(self, "minimum_limits", MappingProxyType(dict(sorted(limits.items()))))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "operationContract": self.operation_contract,
            "operationVersion": self.operation_version,
            "guarantees": list(self.guarantees),
            "minimumLimits": dict(self.minimum_limits),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> CapabilityRequirement:
        allowed = {
            "operationContract",
            "operationVersion",
            "guarantees",
            "minimumLimits",
        }
        required = {"operationContract", "operationVersion"}
        missing = required - set(value)
        if missing:
            raise ValueError(f"missing capability requirement fields: {sorted(missing)!r}")
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown capability requirement fields: {sorted(unknown)!r}")
        guarantees = value.get("guarantees", ())
        limits = value.get("minimumLimits", {})
        if not isinstance(guarantees, Sequence) or isinstance(guarantees, (str, bytes)):
            raise TypeError("guarantees must be an array")
        if not isinstance(limits, Mapping):
            raise TypeError("minimumLimits must be an object")
        return cls(
            operation_contract=cast(str, value["operationContract"]),
            operation_version=cast(str, value["operationVersion"]),
            guarantees=tuple(cast(str, item) for item in guarantees),
            minimum_limits=cast(Mapping[str, int], limits),
        )


@dataclass(frozen=True, slots=True)
class NamespaceDefinition:
    catalog: str
    name: str
    labels: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "catalog", _catalog(self.catalog))
        object.__setattr__(self, "name", _name(self.name, "namespace name"))
        object.__setattr__(self, "labels", _labels(self.labels))

    @property
    def canonical(self) -> str:
        return f"{self.catalog}:{self.name}"

    @property
    def fingerprint(self) -> str:
        return sha256_fingerprint(cast(JsonValue, self.to_dict()))

    def to_dict(self) -> dict[str, JsonValue]:
        return {"catalog": self.catalog, "name": self.name, "labels": dict(self.labels)}


@dataclass(frozen=True, slots=True)
class SchemaDefinition:
    ref: SchemaRef
    definition: Mapping[str, JsonValue]
    dependencies: tuple[SchemaRef, ...] = ()
    extensions: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "ref", SchemaRef.parse(self.ref))
        object.__setattr__(
            self,
            "definition",
            cast(Mapping[str, JsonValue], deep_freeze(cast(JsonValue, self.definition))),
        )
        dependencies = tuple(sorted(SchemaRef.parse(item) for item in self.dependencies))
        if len(set(dependencies)) != len(dependencies):
            raise ValueError("schema dependencies must be unique")
        object.__setattr__(self, "dependencies", dependencies)
        object.__setattr__(
            self,
            "extensions",
            cast(Mapping[str, JsonValue], deep_freeze(cast(JsonValue, self.extensions))),
        )

    @property
    def fingerprint(self) -> str:
        return sha256_fingerprint(cast(JsonValue, self.to_dict()))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "formatVersion": "meridian-schema.v1",
            "ref": self.ref.to_dict(),
            "definition": deep_thaw(cast(JsonValue, self.definition)),
            "dependencies": [item.to_dict() for item in self.dependencies],
            "extensions": deep_thaw(cast(JsonValue, self.extensions)),
        }


@dataclass(frozen=True, slots=True)
class ResourceDefinition:
    ref: ResourceRef
    profile: str
    schema: SchemaRef | None = None
    labels: Mapping[str, str] = field(default_factory=dict)
    requirements: tuple[CapabilityRequirement, ...] = ()
    required_scope: tuple[str, ...] = ()
    related_resources: tuple[ResourceRef, ...] = ()
    extensions: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "ref", ResourceRef.parse(self.ref))
        object.__setattr__(self, "profile", _token(self.profile, "resource profile"))
        if self.schema is not None:
            schema = SchemaRef.parse(self.schema)
            if (schema.catalog, schema.namespace) != (self.ref.catalog, self.ref.namespace):
                raise ValueError("resource and schema must share a Catalog and Namespace")
            object.__setattr__(self, "schema", schema)
        object.__setattr__(self, "labels", _labels(self.labels))
        requirements = tuple(
            sorted(
                self.requirements,
                key=lambda item: (item.operation_contract, item.operation_version),
            )
        )
        if len({item.operation_contract for item in requirements}) != len(requirements):
            raise ValueError("resource capability requirements must have unique contracts")
        object.__setattr__(self, "requirements", requirements)
        scopes = tuple(sorted({_name(item, "scope key") for item in self.required_scope}))
        if len(scopes) != len(self.required_scope):
            raise ValueError("required scope keys must be unique")
        object.__setattr__(self, "required_scope", scopes)
        related = tuple(sorted(ResourceRef.parse(item) for item in self.related_resources))
        if len(set(related)) != len(related):
            raise ValueError("related Resource references must be unique")
        object.__setattr__(self, "related_resources", related)
        object.__setattr__(
            self,
            "extensions",
            cast(Mapping[str, JsonValue], deep_freeze(cast(JsonValue, self.extensions))),
        )

    @property
    def fingerprint(self) -> str:
        return sha256_fingerprint(cast(JsonValue, self.to_dict()))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "formatVersion": "meridian-resource.v1",
            "ref": self.ref.to_dict(),
            "profile": self.profile,
            "schema": None if self.schema is None else self.schema.to_dict(),
            "labels": dict(self.labels),
            "requirements": [item.to_dict() for item in self.requirements],
            "requiredScope": list(self.required_scope),
            "relatedResources": [item.to_dict() for item in self.related_resources],
            "extensions": deep_thaw(cast(JsonValue, self.extensions)),
        }


@dataclass(frozen=True, slots=True)
class ResourceBundle:
    """One immutable bootstrap/live metadata contribution."""

    provider_id: str
    provider_version: str
    provider_contract_version: str
    namespaces: tuple[NamespaceDefinition, ...] = ()
    schemas: tuple[SchemaDefinition, ...] = ()
    resources: tuple[ResourceDefinition, ...] = ()
    extensions: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("provider_id", "provider_version", "provider_contract_version"):
            object.__setattr__(self, field_name, _token(getattr(self, field_name), field_name))
        namespaces = tuple(sorted(self.namespaces, key=lambda item: item.canonical))
        schemas = tuple(sorted(self.schemas, key=lambda item: item.ref))
        resources = tuple(sorted(self.resources, key=lambda item: item.ref))
        if len({item.canonical for item in namespaces}) != len(namespaces):
            raise ValueError("bundle Namespace definitions must be unique")
        if len({item.ref for item in schemas}) != len(schemas):
            raise ValueError("bundle Schema definitions must be unique")
        if len({item.ref for item in resources}) != len(resources):
            raise ValueError("bundle Resource definitions must be unique")
        object.__setattr__(self, "namespaces", namespaces)
        object.__setattr__(self, "schemas", schemas)
        object.__setattr__(self, "resources", resources)
        object.__setattr__(
            self,
            "extensions",
            cast(Mapping[str, JsonValue], deep_freeze(cast(JsonValue, self.extensions))),
        )

    @property
    def fingerprint(self) -> str:
        return sha256_fingerprint(cast(JsonValue, self.to_dict()))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "formatVersion": "meridian-resource-bundle.v1",
            "providerId": self.provider_id,
            "providerVersion": self.provider_version,
            "providerContractVersion": self.provider_contract_version,
            "namespaces": [item.to_dict() for item in self.namespaces],
            "schemas": [item.to_dict() for item in self.schemas],
            "resources": [item.to_dict() for item in self.resources],
            "extensions": deep_thaw(cast(JsonValue, self.extensions)),
        }


REGISTERED_CATALOGS = tuple(sorted(_CATALOGS))

__all__ = [
    "REGISTERED_CATALOGS",
    "CapabilityRequirement",
    "NamespaceDefinition",
    "ResourceBundle",
    "ResourceDefinition",
    "ResourceRef",
    "SchemaDefinition",
    "SchemaRef",
]
