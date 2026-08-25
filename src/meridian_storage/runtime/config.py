# SPDX-License-Identifier: Apache-2.0
"""Closed, immutable ``meridian-config.v1`` loader and binding model."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass, field
from importlib import resources as package_resources
from pathlib import Path
from types import MappingProxyType
from typing import cast

from meridian_storage._canonical import deep_freeze, deep_thaw, sha256_fingerprint
from meridian_storage._types import JsonValue
from meridian_storage.errors import ConfigurationError, ErrorCode, SafeCause
from meridian_storage.registry.resources import REGISTERED_CATALOGS, ResourceRef

CONFIG_FORMAT_VERSION = "meridian-config.v1"
CONFIG_ENVIRONMENT_VARIABLE = "MERIDIAN_CONFIG"
_MAX_CONFIG_BYTES = 1024 * 1024
_FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _fail(message: str, *, code: ErrorCode = ErrorCode.CONFIG_INVALID) -> None:
    raise ConfigurationError(code, message)


def _mapping(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        _fail(f"{path} must be an object")
    return cast(Mapping[str, object], value)


def _closed(
    value: object,
    path: str,
    *,
    required: set[str],
    optional: Set[str] = frozenset(),
) -> Mapping[str, object]:
    result = _mapping(value, path)
    missing = required - set(result)
    if missing:
        _fail(f"{path} is missing required fields: {sorted(missing)!r}")
    unknown = set(result) - required - optional
    if unknown:
        _fail(
            f"{path} contains unknown fields: {sorted(unknown)!r}",
            code=ErrorCode.CONFIG_UNKNOWN_FIELD,
        )
    return result


def _array(value: object, path: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        _fail(f"{path} must be an array")
    return cast(Sequence[object], value)


def _string(value: object, path: str, *, maximum: int = 512) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        _fail(f"{path} must be a bounded non-empty string")
    return cast(str, value)


def _optional_string(value: object, path: str, *, maximum: int = 512) -> str | None:
    if value is None:
        return None
    return _string(value, path, maximum=maximum)


def _fingerprint(value: object, path: str) -> str:
    result = _string(value, path, maximum=71)
    if _FINGERPRINT_RE.fullmatch(result) is None:
        _fail(f"{path} must be a sha256 fingerprint")
    return result


def _optional_fingerprint(value: object, path: str) -> str | None:
    return None if value is None else _fingerprint(value, path)


def _boolean(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        _fail(f"{path} must be a boolean")
    return cast(bool, value)


def _integer(value: object, path: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        _fail(f"{path} must be an integer between {minimum} and {maximum}")
    return cast(int, value)


def _json_mapping(value: object, path: str) -> Mapping[str, JsonValue]:
    result = _mapping(value, path)
    try:
        return cast(Mapping[str, JsonValue], deep_freeze(cast(JsonValue, result)))
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(
            ErrorCode.CONFIG_INVALID,
            f"{path} must contain finite JSON values",
            cause=SafeCause.from_exception(exc),
        ) from exc


def _string_mapping(value: object, path: str) -> Mapping[str, str]:
    result = _mapping(value, path)
    normalized = {
        _string(key, f"{path} key", maximum=128): _string(item, f"{path}.{key}")
        for key, item in result.items()
    }
    return MappingProxyType(dict(sorted(normalized.items())))


def _unique(values: Sequence[object], path: str) -> tuple[str, ...]:
    normalized = tuple(sorted(_string(item, path, maximum=256) for item in values))
    if len(set(normalized)) != len(normalized):
        _fail(f"{path} entries must be unique", code=ErrorCode.CONFIG_DUPLICATE_ID)
    return normalized


@dataclass(frozen=True, slots=True)
class SecretReference:
    provider: str
    reference: str = field(repr=False)

    @classmethod
    def from_mapping(cls, value: object, path: str) -> SecretReference:
        item = _closed(value, path, required={"provider", "reference"})
        return cls(
            provider=_string(item["provider"], f"{path}.provider", maximum=128),
            reference=_string(item["reference"], f"{path}.reference", maximum=2048),
        )

    def to_dict(self) -> dict[str, str]:
        return {"provider": self.provider, "reference": self.reference}


@dataclass(frozen=True, slots=True)
class TLSPolicy:
    mode: str
    server_name: str | None
    ca_ref: SecretReference | None
    client_certificate_ref: SecretReference | None

    @classmethod
    def from_mapping(cls, value: object, path: str) -> TLSPolicy:
        item = _closed(
            value,
            path,
            required={"mode", "serverName", "caRef", "clientCertificateRef"},
        )
        mode = _string(item["mode"], f"{path}.mode", maximum=32)
        if mode not in {"disabled", "server", "mutual"}:
            _fail(f"{path}.mode must be disabled, server, or mutual")
        server_name = _optional_string(item["serverName"], f"{path}.serverName")
        ca_ref = (
            None
            if item["caRef"] is None
            else SecretReference.from_mapping(item["caRef"], f"{path}.caRef")
        )
        client = (
            None
            if item["clientCertificateRef"] is None
            else SecretReference.from_mapping(
                item["clientCertificateRef"],
                f"{path}.clientCertificateRef",
            )
        )
        if mode == "disabled" and any(value is not None for value in (server_name, ca_ref, client)):
            _fail(f"{path} disabled mode cannot carry TLS material")
        if mode in {"server", "mutual"} and (server_name is None or ca_ref is None):
            _fail(f"{path} authenticated TLS requires serverName and caRef")
        if mode == "mutual" and client is None:
            _fail(f"{path} mutual TLS requires clientCertificateRef")
        if mode == "server" and client is not None:
            _fail(f"{path} server TLS cannot carry clientCertificateRef")
        return cls(mode, server_name, ca_ref, client)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "mode": self.mode,
            "serverName": self.server_name,
            "caRef": None if self.ca_ref is None else self.ca_ref.to_dict(),
            "clientCertificateRef": (
                None
                if self.client_certificate_ref is None
                else self.client_certificate_ref.to_dict()
            ),
        }


@dataclass(frozen=True, slots=True)
class ClientPolicy:
    min_size: int
    max_size: int
    acquire_timeout_ms: int
    idle_timeout_ms: int
    operation_timeout_ms: int
    max_result_bytes: int
    iterator_lifetime_ms: int

    @classmethod
    def from_mapping(cls, value: object, path: str) -> ClientPolicy:
        fields = {
            "minSize",
            "maxSize",
            "acquireTimeoutMs",
            "idleTimeoutMs",
            "operationTimeoutMs",
            "maxResultBytes",
            "iteratorLifetimeMs",
        }
        item = _closed(value, path, required=fields)
        result = cls(
            min_size=_integer(item["minSize"], f"{path}.minSize", minimum=0, maximum=10_000),
            max_size=_integer(item["maxSize"], f"{path}.maxSize", minimum=1, maximum=10_000),
            acquire_timeout_ms=_integer(
                item["acquireTimeoutMs"],
                f"{path}.acquireTimeoutMs",
                minimum=1,
                maximum=3_600_000,
            ),
            idle_timeout_ms=_integer(
                item["idleTimeoutMs"],
                f"{path}.idleTimeoutMs",
                minimum=0,
                maximum=86_400_000,
            ),
            operation_timeout_ms=_integer(
                item["operationTimeoutMs"],
                f"{path}.operationTimeoutMs",
                minimum=1,
                maximum=3_600_000,
            ),
            max_result_bytes=_integer(
                item["maxResultBytes"],
                f"{path}.maxResultBytes",
                minimum=1,
                maximum=2_147_483_647,
            ),
            iterator_lifetime_ms=_integer(
                item["iteratorLifetimeMs"],
                f"{path}.iteratorLifetimeMs",
                minimum=1,
                maximum=86_400_000,
            ),
        )
        if result.min_size > result.max_size:
            _fail(f"{path}.minSize cannot exceed maxSize")
        return result

    def to_dict(self) -> dict[str, int]:
        return {
            "minSize": self.min_size,
            "maxSize": self.max_size,
            "acquireTimeoutMs": self.acquire_timeout_ms,
            "idleTimeoutMs": self.idle_timeout_ms,
            "operationTimeoutMs": self.operation_timeout_ms,
            "maxResultBytes": self.max_result_bytes,
            "iteratorLifetimeMs": self.iterator_lifetime_ms,
        }


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int
    base_delay_ms: int
    max_delay_ms: int
    jitter_ratio: float

    @classmethod
    def from_mapping(cls, value: object, path: str) -> RetryPolicy:
        item = _closed(
            value,
            path,
            required={"maxAttempts", "baseDelayMs", "maxDelayMs", "jitterRatio"},
        )
        ratio = item["jitterRatio"]
        if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not 0 <= ratio <= 1:
            _fail(f"{path}.jitterRatio must be a number between 0 and 1")
        result = cls(
            max_attempts=_integer(
                item["maxAttempts"], f"{path}.maxAttempts", minimum=1, maximum=20
            ),
            base_delay_ms=_integer(
                item["baseDelayMs"], f"{path}.baseDelayMs", minimum=0, maximum=60_000
            ),
            max_delay_ms=_integer(
                item["maxDelayMs"], f"{path}.maxDelayMs", minimum=0, maximum=600_000
            ),
            jitter_ratio=float(cast(int | float, ratio)),
        )
        if result.base_delay_ms > result.max_delay_ms:
            _fail(f"{path}.baseDelayMs cannot exceed maxDelayMs")
        return result

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "maxAttempts": self.max_attempts,
            "baseDelayMs": self.base_delay_ms,
            "maxDelayMs": self.max_delay_ms,
            "jitterRatio": self.jitter_ratio,
        }


@dataclass(frozen=True, slots=True)
class CatalogConfig:
    name: str
    package: str
    contract: str
    required_fingerprint: str

    @classmethod
    def from_mapping(cls, value: object, path: str) -> CatalogConfig:
        item = _closed(
            value,
            path,
            required={"name", "package", "contract", "requiredFingerprint"},
        )
        name = _string(item["name"], f"{path}.name", maximum=32)
        if name not in REGISTERED_CATALOGS:
            _fail(f"{path}.name is not a registered Meridian V1 Catalog")
        return cls(
            name=name,
            package=_string(item["package"], f"{path}.package", maximum=256),
            contract=_string(item["contract"], f"{path}.contract", maximum=64),
            required_fingerprint=_fingerprint(
                item["requiredFingerprint"],
                f"{path}.requiredFingerprint",
            ),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "package": self.package,
            "contract": self.contract,
            "requiredFingerprint": self.required_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class CatalogsConfig:
    providers: tuple[CatalogConfig, ...]
    extensions: Mapping[str, JsonValue] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: object, path: str) -> CatalogsConfig:
        item = _closed(value, path, required={"providers", "extensions"})
        providers = tuple(
            CatalogConfig.from_mapping(entry, f"{path}.providers[{index}]")
            for index, entry in enumerate(_array(item["providers"], f"{path}.providers"))
        )
        if not providers:
            _fail(f"{path}.providers cannot be empty")
        if len({provider.name for provider in providers}) != len(providers):
            _fail(
                f"{path}.providers contains duplicate Catalog names",
                code=ErrorCode.CONFIG_DUPLICATE_ID,
            )
        return cls(
            tuple(sorted(providers, key=lambda provider: provider.name)),
            _json_mapping(item["extensions"], f"{path}.extensions"),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "providers": [item.to_dict() for item in self.providers],
            "extensions": deep_thaw(cast(JsonValue, self.extensions)),
        }


@dataclass(frozen=True, slots=True)
class ResourcePin:
    ref: ResourceRef
    provider_id: str
    required_fingerprint: str

    @classmethod
    def from_mapping(cls, value: object, path: str) -> ResourcePin:
        item = _closed(
            value,
            path,
            required={"ref", "providerId", "requiredFingerprint"},
        )
        try:
            ref = ResourceRef.parse(_mapping(item["ref"], f"{path}.ref"))
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(
                ErrorCode.CONFIG_INVALID,
                f"{path}.ref is invalid",
                cause=SafeCause.from_exception(exc),
            ) from exc
        return cls(
            ref=ref,
            provider_id=_string(item["providerId"], f"{path}.providerId"),
            required_fingerprint=_fingerprint(
                item["requiredFingerprint"], f"{path}.requiredFingerprint"
            ),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "ref": self.ref.to_dict(),
            "providerId": self.provider_id,
            "requiredFingerprint": self.required_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class ResourcesConfig:
    pins: tuple[ResourcePin, ...]
    extensions: Mapping[str, JsonValue] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: object, path: str) -> ResourcesConfig:
        item = _closed(value, path, required={"pins", "extensions"})
        pins = tuple(
            ResourcePin.from_mapping(entry, f"{path}.pins[{index}]")
            for index, entry in enumerate(_array(item["pins"], f"{path}.pins"))
        )
        if not pins:
            _fail(f"{path}.pins cannot be empty")
        if len({pin.ref for pin in pins}) != len(pins):
            _fail(
                f"{path}.pins contains duplicate Resource references",
                code=ErrorCode.CONFIG_DUPLICATE_ID,
            )
        return cls(
            tuple(sorted(pins, key=lambda pin: pin.ref)),
            _json_mapping(item["extensions"], f"{path}.extensions"),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "pins": [item.to_dict() for item in self.pins],
            "extensions": deep_thaw(cast(JsonValue, self.extensions)),
        }


@dataclass(frozen=True, slots=True)
class SchemaProviderConfig:
    id: str
    package: str
    contract: str
    required_fingerprint: str

    @classmethod
    def from_mapping(cls, value: object, path: str) -> SchemaProviderConfig:
        item = _closed(
            value,
            path,
            required={"id", "package", "contract", "requiredFingerprint"},
        )
        return cls(
            id=_string(item["id"], f"{path}.id"),
            package=_string(item["package"], f"{path}.package"),
            contract=_string(item["contract"], f"{path}.contract", maximum=64),
            required_fingerprint=_fingerprint(
                item["requiredFingerprint"], f"{path}.requiredFingerprint"
            ),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "package": self.package,
            "contract": self.contract,
            "requiredFingerprint": self.required_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class LiveSchemaConfig:
    enabled: bool
    required: bool
    provider_id: str | None

    @classmethod
    def from_mapping(cls, value: object, path: str) -> LiveSchemaConfig:
        item = _closed(value, path, required={"enabled", "required", "providerId"})
        result = cls(
            enabled=_boolean(item["enabled"], f"{path}.enabled"),
            required=_boolean(item["required"], f"{path}.required"),
            provider_id=_optional_string(item["providerId"], f"{path}.providerId"),
        )
        if result.required and not result.enabled:
            _fail(f"{path}.required cannot be true when live schemas are disabled")
        if result.enabled and result.provider_id is None:
            _fail(f"{path}.providerId is required when live schemas are enabled")
        if not result.enabled and result.provider_id is not None:
            _fail(f"{path}.providerId must be null when live schemas are disabled")
        return result

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "enabled": self.enabled,
            "required": self.required,
            "providerId": self.provider_id,
        }


@dataclass(frozen=True, slots=True)
class SchemaConfig:
    providers: tuple[SchemaProviderConfig, ...]
    live: LiveSchemaConfig
    extensions: Mapping[str, JsonValue] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: object, path: str) -> SchemaConfig:
        item = _closed(value, path, required={"providers", "live", "extensions"})
        providers = tuple(
            SchemaProviderConfig.from_mapping(entry, f"{path}.providers[{index}]")
            for index, entry in enumerate(_array(item["providers"], f"{path}.providers"))
        )
        if not providers:
            _fail(f"{path}.providers cannot be empty")
        if len({provider.id for provider in providers}) != len(providers):
            _fail(f"{path}.providers contains duplicate ids", code=ErrorCode.CONFIG_DUPLICATE_ID)
        return cls(
            providers=tuple(sorted(providers, key=lambda provider: provider.id)),
            live=LiveSchemaConfig.from_mapping(item["live"], f"{path}.live"),
            extensions=_json_mapping(item["extensions"], f"{path}.extensions"),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "providers": [item.to_dict() for item in self.providers],
            "live": self.live.to_dict(),
            "extensions": deep_thaw(cast(JsonValue, self.extensions)),
        }


@dataclass(frozen=True, slots=True)
class BindingConfig:
    id: str
    adapter_id: str
    adapter_contract: str
    engine_profile: str
    engine_version: str
    endpoint: str | None
    service_ref: str | None
    physical_namespace: str
    tls: TLSPolicy
    identity_ref: SecretReference
    secret_ref: SecretReference
    client: ClientPolicy
    required_capability_fingerprint: str
    required_physical_fingerprint: str | None
    compatibility_pins: Mapping[str, str]
    settings: Mapping[str, JsonValue]
    extensions: Mapping[str, JsonValue]

    @classmethod
    def from_mapping(cls, value: object, path: str) -> BindingConfig:
        item = _closed(
            value,
            path,
            required={
                "id",
                "adapterId",
                "adapterContract",
                "engineProfile",
                "engineVersion",
                "endpoint",
                "serviceRef",
                "physicalNamespace",
                "tls",
                "identityRef",
                "secretRef",
                "client",
                "requiredCapabilityFingerprint",
                "requiredPhysicalFingerprint",
                "compatibilityPins",
                "settings",
                "extensions",
            },
        )
        endpoint = _optional_string(item["endpoint"], f"{path}.endpoint", maximum=2048)
        service_ref = _optional_string(item["serviceRef"], f"{path}.serviceRef", maximum=512)
        if (endpoint is None) == (service_ref is None):
            _fail(f"{path} requires exactly one of endpoint or serviceRef")
        return cls(
            id=_string(item["id"], f"{path}.id"),
            adapter_id=_string(item["adapterId"], f"{path}.adapterId"),
            adapter_contract=_string(
                item["adapterContract"], f"{path}.adapterContract", maximum=64
            ),
            engine_profile=_string(item["engineProfile"], f"{path}.engineProfile"),
            engine_version=_string(item["engineVersion"], f"{path}.engineVersion", maximum=64),
            endpoint=endpoint,
            service_ref=service_ref,
            physical_namespace=_string(
                item["physicalNamespace"], f"{path}.physicalNamespace", maximum=512
            ),
            tls=TLSPolicy.from_mapping(item["tls"], f"{path}.tls"),
            identity_ref=SecretReference.from_mapping(item["identityRef"], f"{path}.identityRef"),
            secret_ref=SecretReference.from_mapping(item["secretRef"], f"{path}.secretRef"),
            client=ClientPolicy.from_mapping(item["client"], f"{path}.client"),
            required_capability_fingerprint=_fingerprint(
                item["requiredCapabilityFingerprint"],
                f"{path}.requiredCapabilityFingerprint",
            ),
            required_physical_fingerprint=_optional_fingerprint(
                item["requiredPhysicalFingerprint"],
                f"{path}.requiredPhysicalFingerprint",
            ),
            compatibility_pins=_string_mapping(
                item["compatibilityPins"], f"{path}.compatibilityPins"
            ),
            settings=_json_mapping(item["settings"], f"{path}.settings"),
            extensions=_json_mapping(item["extensions"], f"{path}.extensions"),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "id": self.id,
            "adapterId": self.adapter_id,
            "adapterContract": self.adapter_contract,
            "engineProfile": self.engine_profile,
            "engineVersion": self.engine_version,
            "endpoint": self.endpoint,
            "serviceRef": self.service_ref,
            "physicalNamespace": self.physical_namespace,
            "tls": self.tls.to_dict(),
            "identityRef": self.identity_ref.to_dict(),
            "secretRef": self.secret_ref.to_dict(),
            "client": self.client.to_dict(),
            "requiredCapabilityFingerprint": self.required_capability_fingerprint,
            "requiredPhysicalFingerprint": self.required_physical_fingerprint,
            "compatibilityPins": dict(self.compatibility_pins),
            "settings": deep_thaw(cast(JsonValue, self.settings)),
            "extensions": deep_thaw(cast(JsonValue, self.extensions)),
        }


@dataclass(frozen=True, slots=True)
class PlacementSelector:
    resources: tuple[ResourceRef, ...]
    catalog: str | None
    labels: Mapping[str, str]

    @classmethod
    def from_mapping(cls, value: object, path: str) -> PlacementSelector:
        item = _closed(value, path, required={"resources", "catalog", "labels"})
        refs: list[ResourceRef] = []
        for index, entry in enumerate(_array(item["resources"], f"{path}.resources")):
            try:
                refs.append(ResourceRef.parse(_mapping(entry, f"{path}.resources[{index}]")))
            except (TypeError, ValueError) as exc:
                raise ConfigurationError(
                    ErrorCode.CONFIG_INVALID,
                    f"{path}.resources[{index}] is invalid",
                    cause=SafeCause.from_exception(exc),
                ) from exc
        catalog = _optional_string(item["catalog"], f"{path}.catalog", maximum=32)
        if catalog is not None and catalog not in REGISTERED_CATALOGS:
            _fail(f"{path}.catalog is not registered")
        labels = _string_mapping(item["labels"], f"{path}.labels")
        if not refs and catalog is None and not labels:
            _fail(f"{path} must select exact Resources, a Catalog, or labels")
        if len(set(refs)) != len(refs):
            _fail(f"{path}.resources contains duplicates", code=ErrorCode.CONFIG_DUPLICATE_ID)
        return cls(tuple(sorted(refs)), catalog, labels)

    def matches(self, ref: ResourceRef, labels: Mapping[str, str]) -> bool:
        exact = ref in self.resources if self.resources else True
        selected_catalog = self.catalog is None or self.catalog == ref.catalog
        selected_labels = all(labels.get(key) == value for key, value in self.labels.items())
        return exact and selected_catalog and selected_labels

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "resources": [item.to_dict() for item in self.resources],
            "catalog": self.catalog,
            "labels": dict(self.labels),
        }


@dataclass(frozen=True, slots=True)
class PlacementRule:
    id: str
    selector: PlacementSelector
    binding_id: str
    extensions: Mapping[str, JsonValue]

    @classmethod
    def from_mapping(cls, value: object, path: str) -> PlacementRule:
        item = _closed(value, path, required={"id", "selector", "bindingId", "extensions"})
        return cls(
            id=_string(item["id"], f"{path}.id"),
            selector=PlacementSelector.from_mapping(item["selector"], f"{path}.selector"),
            binding_id=_string(item["bindingId"], f"{path}.bindingId"),
            extensions=_json_mapping(item["extensions"], f"{path}.extensions"),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "id": self.id,
            "selector": self.selector.to_dict(),
            "bindingId": self.binding_id,
            "extensions": deep_thaw(cast(JsonValue, self.extensions)),
        }


@dataclass(frozen=True, slots=True)
class ValidationConfig:
    strict: bool
    require_physical_fingerprints: bool
    default_operation_timeout_ms: int
    idempotency_cache_entries: int
    retry: RetryPolicy

    @classmethod
    def from_mapping(cls, value: object, path: str) -> ValidationConfig:
        item = _closed(
            value,
            path,
            required={
                "strict",
                "requirePhysicalFingerprints",
                "defaultOperationTimeoutMs",
                "idempotencyCacheEntries",
                "retry",
            },
        )
        result = cls(
            strict=_boolean(item["strict"], f"{path}.strict"),
            require_physical_fingerprints=_boolean(
                item["requirePhysicalFingerprints"], f"{path}.requirePhysicalFingerprints"
            ),
            default_operation_timeout_ms=_integer(
                item["defaultOperationTimeoutMs"],
                f"{path}.defaultOperationTimeoutMs",
                minimum=1,
                maximum=3_600_000,
            ),
            idempotency_cache_entries=_integer(
                item["idempotencyCacheEntries"],
                f"{path}.idempotencyCacheEntries",
                minimum=1,
                maximum=1_000_000,
            ),
            retry=RetryPolicy.from_mapping(item["retry"], f"{path}.retry"),
        )
        if not result.strict:
            _fail(f"{path}.strict must be true in Meridian V1")
        return result

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "strict": self.strict,
            "requirePhysicalFingerprints": self.require_physical_fingerprints,
            "defaultOperationTimeoutMs": self.default_operation_timeout_ms,
            "idempotencyCacheEntries": self.idempotency_cache_entries,
            "retry": self.retry.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class TelemetryConfig:
    enabled: bool
    service_name: str | None
    suppress_exporter_recursion: bool
    attributes: Mapping[str, str]
    extensions: Mapping[str, JsonValue]

    @classmethod
    def from_mapping(cls, value: object, path: str) -> TelemetryConfig:
        item = _closed(
            value,
            path,
            required={
                "enabled",
                "serviceName",
                "suppressExporterRecursion",
                "attributes",
                "extensions",
            },
        )
        result = cls(
            enabled=_boolean(item["enabled"], f"{path}.enabled"),
            service_name=_optional_string(item["serviceName"], f"{path}.serviceName"),
            suppress_exporter_recursion=_boolean(
                item["suppressExporterRecursion"], f"{path}.suppressExporterRecursion"
            ),
            attributes=_string_mapping(item["attributes"], f"{path}.attributes"),
            extensions=_json_mapping(item["extensions"], f"{path}.extensions"),
        )
        if result.enabled and result.service_name is None:
            _fail(f"{path}.serviceName is required when telemetry is enabled")
        if not result.suppress_exporter_recursion:
            _fail(f"{path}.suppressExporterRecursion must be true")
        return result

    @classmethod
    def disabled(cls) -> TelemetryConfig:
        return cls(False, None, True, MappingProxyType({}), MappingProxyType({}))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "enabled": self.enabled,
            "serviceName": self.service_name,
            "suppressExporterRecursion": self.suppress_exporter_recursion,
            "attributes": dict(self.attributes),
            "extensions": deep_thaw(cast(JsonValue, self.extensions)),
        }


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    profile: str
    catalogs: CatalogsConfig
    resources: ResourcesConfig
    schemas: SchemaConfig
    bindings: tuple[BindingConfig, ...]
    placements: tuple[PlacementRule, ...]
    validation: ValidationConfig
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig.disabled)
    extensions: Mapping[str, JsonValue] = field(default_factory=dict)
    format_version: str = CONFIG_FORMAT_VERSION

    @property
    def fingerprint(self) -> str:
        return sha256_fingerprint(cast(JsonValue, self.to_dict()))

    def binding(self, binding_id: str) -> BindingConfig:
        for binding in self.bindings:
            if binding.id == binding_id:
                return binding
        raise KeyError(binding_id)

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> RuntimeConfig:
        item = _closed(
            value,
            "$",
            required={
                "formatVersion",
                "profile",
                "catalogs",
                "resources",
                "schemas",
                "bindings",
                "placements",
                "validation",
            },
            optional={"telemetry", "extensions"},
        )
        if item["formatVersion"] != CONFIG_FORMAT_VERSION:
            _fail(f"$.formatVersion must be {CONFIG_FORMAT_VERSION!r}")
        bindings = tuple(
            BindingConfig.from_mapping(entry, f"$.bindings[{index}]")
            for index, entry in enumerate(_array(item["bindings"], "$.bindings"))
        )
        placements = tuple(
            PlacementRule.from_mapping(entry, f"$.placements[{index}]")
            for index, entry in enumerate(_array(item["placements"], "$.placements"))
        )
        if not bindings or not placements:
            _fail("$.bindings and $.placements cannot be empty")
        if len({binding.id for binding in bindings}) != len(bindings):
            _fail("$.bindings contains duplicate ids", code=ErrorCode.CONFIG_DUPLICATE_ID)
        if len({placement.id for placement in placements}) != len(placements):
            _fail("$.placements contains duplicate ids", code=ErrorCode.CONFIG_DUPLICATE_ID)
        binding_ids = {binding.id for binding in bindings}
        for placement in placements:
            if placement.binding_id not in binding_ids:
                _fail(f"placement {placement.id!r} references an unknown binding")
        catalogs = CatalogsConfig.from_mapping(item["catalogs"], "$.catalogs")
        resource_config = ResourcesConfig.from_mapping(item["resources"], "$.resources")
        schemas = SchemaConfig.from_mapping(item["schemas"], "$.schemas")
        catalog_names = {catalog.name for catalog in catalogs.providers}
        schema_provider_ids = {provider.id for provider in schemas.providers}
        for pin in resource_config.pins:
            if pin.ref.catalog not in catalog_names:
                _fail(f"Resource {pin.ref} belongs to an unconfigured Catalog")
            if pin.provider_id not in schema_provider_ids:
                _fail(f"Resource {pin.ref} references an unconfigured Schema provider")
        if (
            schemas.live.provider_id is not None
            and schemas.live.provider_id not in schema_provider_ids
        ):
            _fail("$.schemas.live.providerId references an unconfigured Schema provider")
        result = cls(
            profile=_string(item["profile"], "$.profile"),
            catalogs=catalogs,
            resources=resource_config,
            schemas=schemas,
            bindings=tuple(sorted(bindings, key=lambda binding: binding.id)),
            placements=tuple(sorted(placements, key=lambda placement: placement.id)),
            validation=ValidationConfig.from_mapping(item["validation"], "$.validation"),
            telemetry=(
                TelemetryConfig.disabled()
                if "telemetry" not in item
                else TelemetryConfig.from_mapping(item["telemetry"], "$.telemetry")
            ),
            extensions=_json_mapping(item.get("extensions", {}), "$.extensions"),
        )
        if result.validation.require_physical_fingerprints and any(
            binding.required_physical_fingerprint is None for binding in result.bindings
        ):
            _fail("every Binding requires a physical fingerprint under strict validation")
        return result

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "formatVersion": self.format_version,
            "profile": self.profile,
            "catalogs": self.catalogs.to_dict(),
            "resources": self.resources.to_dict(),
            "schemas": self.schemas.to_dict(),
            "bindings": [item.to_dict() for item in self.bindings],
            "placements": [item.to_dict() for item in self.placements],
            "validation": self.validation.to_dict(),
            "telemetry": self.telemetry.to_dict(),
            "extensions": deep_thaw(cast(JsonValue, self.extensions)),
        }


def load_runtime_config(path: str | os.PathLike[str]) -> RuntimeConfig:
    """Load one bounded, canonical UTF-8 JSON deployment document."""

    source = Path(path)
    try:
        size = source.stat().st_size
        if size > _MAX_CONFIG_BYTES:
            _fail("runtime configuration exceeds the 1 MiB V1 limit")
        raw = source.read_bytes()
        if len(raw) > _MAX_CONFIG_BYTES:
            _fail("runtime configuration exceeds the 1 MiB V1 limit")
        text = raw.decode("utf-8")
        value = json.loads(text)
    except ConfigurationError:
        raise
    except FileNotFoundError as exc:
        raise ConfigurationError(
            ErrorCode.CONFIG_NOT_FOUND,
            "runtime configuration file was not found",
        ) from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigurationError(
            ErrorCode.CONFIG_INVALID,
            "runtime configuration could not be decoded as UTF-8 JSON",
            cause=SafeCause.from_exception(exc),
        ) from exc
    return RuntimeConfig.from_mapping(_mapping(value, "$"))


def load_runtime_config_from_environment(
    environment: Mapping[str, str] | None = None,
) -> RuntimeConfig:
    values = os.environ if environment is None else environment
    path = values.get(CONFIG_ENVIRONMENT_VARIABLE)
    if path is None or not path:
        raise ConfigurationError(
            ErrorCode.CONFIG_NOT_FOUND,
            f"{CONFIG_ENVIRONMENT_VARIABLE} is not set",
        )
    return load_runtime_config(path)


def runtime_config_contract() -> Mapping[str, object]:
    """Return the packaged released runtime configuration JSON Schema."""

    packaged = package_resources.files("meridian_storage.runtime").joinpath(
        "contracts/meridian-config.v1.schema.json"
    )
    if packaged.is_file():
        return cast(
            Mapping[str, object],
            json.loads(packaged.read_text(encoding="utf-8")),
        )
    source = (
        Path(__file__).resolve().parents[3]
        / "contracts/runtime-config/meridian-config.v1.schema.json"
    )
    return cast(Mapping[str, object], json.loads(source.read_text(encoding="utf-8")))


__all__ = [
    "CONFIG_ENVIRONMENT_VARIABLE",
    "CONFIG_FORMAT_VERSION",
    "BindingConfig",
    "CatalogConfig",
    "CatalogsConfig",
    "ClientPolicy",
    "LiveSchemaConfig",
    "PlacementRule",
    "PlacementSelector",
    "ResourcePin",
    "ResourcesConfig",
    "RetryPolicy",
    "RuntimeConfig",
    "SchemaConfig",
    "SchemaProviderConfig",
    "SecretReference",
    "TLSPolicy",
    "TelemetryConfig",
    "ValidationConfig",
    "load_runtime_config",
    "load_runtime_config_from_environment",
    "runtime_config_contract",
]
