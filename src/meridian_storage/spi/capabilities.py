# SPDX-License-Identifier: Apache-2.0
"""Versioned Adapter descriptors and Operation capability manifests."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import cast

from meridian_storage._canonical import deep_freeze, deep_thaw, sha256_fingerprint
from meridian_storage._types import JsonValue
from meridian_storage.registry.resources import CapabilityRequirement

CAPABILITY_FORMAT_VERSION = "meridian-adapter-capabilities.v1"


def _nonempty(value: str, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 256
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{field_name} must be a bounded non-empty string")
    return value


def _unique(values: Iterable[str], field_name: str) -> tuple[str, ...]:
    result = tuple(sorted(_nonempty(item, field_name) for item in values))
    if len(set(result)) != len(result):
        raise ValueError(f"{field_name} must be unique")
    return result


def _limits(values: Mapping[str, int]) -> Mapping[str, int]:
    result: dict[str, int] = {}
    for key, value in values.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("Capability limits must be non-negative integers")
        result[_nonempty(key, "Capability limit name")] = value
    return MappingProxyType(dict(sorted(result.items())))


@dataclass(frozen=True, slots=True)
class OperationCapability:
    """Adapter support for one versioned engine-neutral Operation contract."""

    operation_contract: str
    operation_versions: tuple[str, ...]
    guarantees: tuple[str, ...] = ()
    limits: Mapping[str, int] = field(default_factory=dict)
    cursor_behavior: str = "none"
    migration_behavior: str = "external"
    health_probes: tuple[str, ...] = ("authenticated",)
    extensions: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "operation_contract",
            _nonempty(self.operation_contract, "Operation contract"),
        )
        versions = _unique(self.operation_versions, "Operation versions")
        if not versions:
            raise ValueError("Operation capability must advertise at least one version")
        object.__setattr__(self, "operation_versions", versions)
        object.__setattr__(self, "guarantees", _unique(self.guarantees, "guarantees"))
        object.__setattr__(self, "limits", _limits(self.limits))
        object.__setattr__(
            self,
            "cursor_behavior",
            _nonempty(self.cursor_behavior, "cursor behavior"),
        )
        object.__setattr__(
            self,
            "migration_behavior",
            _nonempty(self.migration_behavior, "migration behavior"),
        )
        probes = _unique(self.health_probes, "health probes")
        if not probes:
            raise ValueError("Operation capability must advertise a health probe")
        object.__setattr__(self, "health_probes", probes)
        object.__setattr__(
            self,
            "extensions",
            cast(Mapping[str, JsonValue], deep_freeze(cast(JsonValue, self.extensions))),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "operationContract": self.operation_contract,
            "operationVersions": list(self.operation_versions),
            "guarantees": list(self.guarantees),
            "limits": dict(self.limits),
            "cursorBehavior": self.cursor_behavior,
            "migrationBehavior": self.migration_behavior,
            "healthProbes": list(self.health_probes),
            "extensions": deep_thaw(cast(JsonValue, self.extensions)),
        }


@dataclass(frozen=True, slots=True)
class AdapterDescriptor:
    """Stable Adapter identity, SPI range, driver, engines, and capabilities."""

    adapter_id: str
    adapter_contract_version: str
    driver: str
    supported_engine_versions: Mapping[str, tuple[str, ...]]
    capabilities: tuple[OperationCapability, ...]

    def __post_init__(self) -> None:
        for field_name in ("adapter_id", "adapter_contract_version", "driver"):
            object.__setattr__(self, field_name, _nonempty(getattr(self, field_name), field_name))
        engines: dict[str, tuple[str, ...]] = {}
        for profile, versions in self.supported_engine_versions.items():
            normalized = _unique(versions, "supported Engine versions")
            if not normalized:
                raise ValueError("an Engine profile must advertise at least one version")
            engines[_nonempty(profile, "Engine profile")] = normalized
        if not engines:
            raise ValueError("an Adapter descriptor must advertise an Engine profile")
        object.__setattr__(
            self,
            "supported_engine_versions",
            MappingProxyType(dict(sorted(engines.items()))),
        )
        capabilities = tuple(sorted(self.capabilities, key=lambda item: item.operation_contract))
        if not capabilities:
            raise ValueError("an Adapter descriptor must advertise an Operation capability")
        if len({item.operation_contract for item in capabilities}) != len(capabilities):
            raise ValueError("Operation capability contracts must be unique")
        object.__setattr__(self, "capabilities", capabilities)

    @property
    def fingerprint(self) -> str:
        return sha256_fingerprint(cast(JsonValue, self.to_dict()))

    def capability_for(self, operation_contract: str) -> OperationCapability | None:
        return next(
            (item for item in self.capabilities if item.operation_contract == operation_contract),
            None,
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "adapterId": self.adapter_id,
            "adapterContractVersion": self.adapter_contract_version,
            "driver": self.driver,
            "supportedEngineVersions": {
                profile: list(versions)
                for profile, versions in self.supported_engine_versions.items()
            },
            "capabilities": [item.to_dict() for item in self.capabilities],
        }


@dataclass(frozen=True, slots=True)
class CapabilityManifest:
    """Authenticated probe result for one selected Engine profile and version."""

    descriptor: AdapterDescriptor
    engine_profile: str
    engine_version: str
    available_operation_contracts: tuple[str, ...] = ()
    extensions: Mapping[str, JsonValue] = field(default_factory=dict)
    format_version: str = CAPABILITY_FORMAT_VERSION

    def __post_init__(self) -> None:
        if self.format_version != CAPABILITY_FORMAT_VERSION:
            raise ValueError(f"format_version must be {CAPABILITY_FORMAT_VERSION!r}")
        profile = _nonempty(self.engine_profile, "Engine profile")
        version = _nonempty(self.engine_version, "Engine version")
        supported = self.descriptor.supported_engine_versions.get(profile, ())
        if version not in supported:
            raise ValueError("probed Engine version is not advertised by the Adapter descriptor")
        available = self.available_operation_contracts or tuple(
            item.operation_contract for item in self.descriptor.capabilities
        )
        available = _unique(available, "available Operation contracts")
        advertised = {item.operation_contract for item in self.descriptor.capabilities}
        if not set(available) <= advertised:
            raise ValueError("probe exposed an Operation absent from the Adapter descriptor")
        object.__setattr__(self, "engine_profile", profile)
        object.__setattr__(self, "engine_version", version)
        object.__setattr__(self, "available_operation_contracts", available)
        object.__setattr__(
            self,
            "extensions",
            cast(Mapping[str, JsonValue], deep_freeze(cast(JsonValue, self.extensions))),
        )

    @property
    def adapter_id(self) -> str:
        return self.descriptor.adapter_id

    @property
    def adapter_contract_version(self) -> str:
        return self.descriptor.adapter_contract_version

    @property
    def fingerprint(self) -> str:
        return sha256_fingerprint(cast(JsonValue, self.to_dict()))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "formatVersion": self.format_version,
            "descriptor": self.descriptor.to_dict(),
            "engineProfile": self.engine_profile,
            "engineVersion": self.engine_version,
            "availableOperationContracts": list(self.available_operation_contracts),
            "extensions": deep_thaw(cast(JsonValue, self.extensions)),
        }


@dataclass(frozen=True, slots=True)
class CapabilityViolation:
    requirement: CapabilityRequirement
    reason: str


def capability_violations(
    manifest: CapabilityManifest,
    requirements: Iterable[CapabilityRequirement],
) -> tuple[CapabilityViolation, ...]:
    """Return every unsatisfied requirement in deterministic order."""

    violations: list[CapabilityViolation] = []
    for requirement in sorted(
        requirements,
        key=lambda item: (item.operation_contract, item.operation_version),
    ):
        capability = manifest.descriptor.capability_for(requirement.operation_contract)
        reason: str | None = None
        if requirement.operation_contract not in manifest.available_operation_contracts:
            reason = "Operation contract is unavailable in the authenticated probe"
        elif capability is None:
            reason = "Operation contract is not advertised"
        elif requirement.operation_version not in capability.operation_versions:
            reason = "Operation version is not advertised"
        elif not set(requirement.guarantees) <= set(capability.guarantees):
            reason = "required guarantee is not advertised"
        else:
            for name, minimum in requirement.minimum_limits.items():
                if capability.limits.get(name, -1) < minimum:
                    reason = f"limit {name!r} is below the required minimum"
                    break
        if reason is not None:
            violations.append(CapabilityViolation(requirement, reason))
    return tuple(violations)


def adapter_capability_contract() -> Mapping[str, object]:
    """Return the packaged released Adapter manifest JSON Schema."""

    import json

    packaged = resources.files("meridian_storage.spi").joinpath(
        "contracts/meridian-adapter-capabilities.v1.schema.json"
    )
    if packaged.is_file():
        return cast(Mapping[str, object], json.loads(packaged.read_text(encoding="utf-8")))
    source = (
        Path(__file__).resolve().parents[3]
        / "contracts/adapter-capability/meridian-adapter-capabilities.v1.schema.json"
    )
    return cast(Mapping[str, object], json.loads(source.read_text(encoding="utf-8")))


__all__ = [
    "CAPABILITY_FORMAT_VERSION",
    "AdapterDescriptor",
    "CapabilityManifest",
    "CapabilityRequirement",
    "CapabilityViolation",
    "OperationCapability",
    "adapter_capability_contract",
    "capability_violations",
]
