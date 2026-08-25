# SPDX-License-Identifier: Apache-2.0
"""Revisioned registry assembly and deterministic internal Binding resolution."""

from __future__ import annotations

import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from meridian_storage._canonical import sha256_fingerprint
from meridian_storage._types import JsonValue
from meridian_storage._versions import ContractVersion
from meridian_storage.errors import (
    CompatibilityError,
    ConfigurationError,
    ErrorCode,
    NotFoundError,
    ValidationError,
)
from meridian_storage.runtime.config import RuntimeConfig
from meridian_storage.runtime.operations import CatalogManifest
from meridian_storage.spi.capabilities import CapabilityManifest, capability_violations

from .resources import (
    NamespaceDefinition,
    ResourceBundle,
    ResourceDefinition,
    ResourceRef,
    SchemaDefinition,
    SchemaRef,
)


@dataclass(frozen=True, slots=True)
class BindingRecord:
    """Internal, redacted association for one logical Resource."""

    binding_id: str
    adapter_id: str
    capability_fingerprint: str
    physical_fingerprint: str
    physical_mapping: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class RegistrySnapshot:
    revision: int
    fingerprint: str
    catalogs: Mapping[str, CatalogManifest]
    namespaces: Mapping[tuple[str, str], NamespaceDefinition]
    schemas: Mapping[SchemaRef, SchemaDefinition]
    resources: Mapping[ResourceRef, ResourceDefinition]
    _bindings: Mapping[ResourceRef, BindingRecord] = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "catalogs", MappingProxyType(dict(self.catalogs)))
        object.__setattr__(self, "namespaces", MappingProxyType(dict(self.namespaces)))
        object.__setattr__(self, "schemas", MappingProxyType(dict(self.schemas)))
        object.__setattr__(self, "resources", MappingProxyType(dict(self.resources)))
        object.__setattr__(self, "_bindings", MappingProxyType(dict(self._bindings)))

    def resource(self, ref: ResourceRef) -> ResourceDefinition:
        try:
            return self.resources[ref]
        except KeyError as exc:
            raise NotFoundError(
                ErrorCode.RESOURCE_NOT_FOUND,
                f"Resource {ref} is not registered",
                resource_ref=str(ref),
            ) from exc

    def resolve_resource(self, value: ResourceRef | str | Mapping[str, object]) -> ResourceRef:
        if not isinstance(value, str) or ":" in value:
            try:
                ref = ResourceRef.parse(value)
            except (TypeError, ValueError) as exc:
                raise ValidationError(
                    ErrorCode.OPERATION_INVALID,
                    "Resource reference is invalid",
                ) from exc
            self.resource(ref)
            return ref
        matches = [ref for ref in self.resources if ref.logical_name == value]
        if not matches:
            raise NotFoundError(
                ErrorCode.RESOURCE_NOT_FOUND,
                f"Resource {value!r} is not registered",
                resource_ref=value,
            )
        if len(matches) > 1:
            raise ValidationError(
                ErrorCode.OPERATION_INVALID,
                f"Resource {value!r} is ambiguous; qualify it with a Catalog name",
                resource_ref=value,
            )
        return matches[0]

    def binding_for(self, ref: ResourceRef) -> BindingRecord:
        self.resource(ref)
        return self._bindings[ref]

    def namespace(self, catalog: str, name: str) -> NamespaceDefinition:
        try:
            return self.namespaces[(catalog, name)]
        except KeyError as exc:
            raise NotFoundError(
                ErrorCode.RESOURCE_NOT_FOUND,
                f"Namespace {catalog}:{name} is not registered",
                resource_ref=f"{catalog}:{name}",
            ) from exc

    def schema(
        self,
        catalog: str,
        namespace: str,
        name: str,
        version: str | None = None,
    ) -> SchemaDefinition:
        if version is not None:
            ref = SchemaRef(catalog, namespace, name, version)
            try:
                return self.schemas[ref]
            except KeyError as exc:
                raise NotFoundError(
                    ErrorCode.RESOURCE_NOT_FOUND,
                    f"Schema {ref} is not registered",
                    resource_ref=str(ref),
                ) from exc
        matches = [
            schema
            for ref, schema in self.schemas.items()
            if (ref.catalog, ref.namespace, ref.name) == (catalog, namespace, name)
        ]
        if not matches:
            raise NotFoundError(
                ErrorCode.RESOURCE_NOT_FOUND,
                f"Schema {catalog}:{namespace}.{name} is not registered",
                resource_ref=f"{catalog}:{namespace}.{name}",
            )
        return max(matches, key=_schema_version_key)

    def resources_for_binding(self, binding_id: str) -> tuple[ResourceDefinition, ...]:
        return tuple(
            self.resources[ref]
            for ref, record in sorted(self._bindings.items(), key=lambda item: item[0])
            if record.binding_id == binding_id
        )


def _schema_version_key(schema: SchemaDefinition) -> tuple[int, object]:
    try:
        return (1, ContractVersion.parse(schema.ref.version))
    except ValueError:
        return (0, schema.ref.version)


class Registry:
    """Thread-safe owner of one atomically replaceable immutable snapshot."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._snapshot: RegistrySnapshot | None = None

    def snapshot(self) -> RegistrySnapshot:
        with self._lock:
            if self._snapshot is None:
                raise ValidationError(ErrorCode.RUNTIME_STATE, "the Meridian registry is not ready")
            return self._snapshot

    def install_initial(self, snapshot: RegistrySnapshot) -> None:
        with self._lock:
            if self._snapshot is not None:
                raise ValidationError(
                    ErrorCode.RUNTIME_STATE,
                    "the initial registry snapshot is already installed",
                )
            self._snapshot = snapshot

    def swap_compatible(self, candidate: RegistrySnapshot) -> RegistrySnapshot:
        with self._lock:
            current = self.snapshot()
            for ref, resource in current.resources.items():
                replacement = candidate.resources.get(ref)
                if replacement is None:
                    raise CompatibilityError(
                        ErrorCode.REGISTRY_REFRESH,
                        f"refresh would remove active Resource {ref}",
                        resource_ref=str(ref),
                    )
                if replacement.schema != resource.schema:
                    raise CompatibilityError(
                        ErrorCode.REGISTRY_REFRESH,
                        f"refresh would change active Schema for Resource {ref}",
                        resource_ref=str(ref),
                    )
                if candidate.binding_for(ref).binding_id != current.binding_for(ref).binding_id:
                    raise CompatibilityError(
                        ErrorCode.REGISTRY_REFRESH,
                        f"refresh would change Binding for Resource {ref}",
                        resource_ref=str(ref),
                    )
            self._snapshot = candidate
            return current


def build_registry(
    bundles: Iterable[ResourceBundle],
    config: RuntimeConfig,
    catalog_manifests: Mapping[str, CatalogManifest],
    capability_manifests: Mapping[str, CapabilityManifest],
    physical_fingerprints: Mapping[str, str],
    physical_mappings: Mapping[str, Mapping[str, str]],
    *,
    revision: int,
) -> RegistrySnapshot:
    """Build and validate a candidate without mutating runtime state."""

    namespaces: dict[tuple[str, str], NamespaceDefinition] = {}
    schemas: dict[SchemaRef, SchemaDefinition] = {}
    available_resources: dict[ResourceRef, tuple[ResourceDefinition, str]] = {}
    for bundle in sorted(bundles, key=lambda item: item.provider_id):
        for namespace in bundle.namespaces:
            _insert_unique(
                namespaces,
                (namespace.catalog, namespace.name),
                namespace,
                "Namespace",
            )
        for schema in bundle.schemas:
            _insert_unique(schemas, schema.ref, schema, "Schema version")
        for resource in bundle.resources:
            _insert_unique(
                available_resources,
                resource.ref,
                (resource, bundle.provider_id),
                "Resource",
            )

    configured_catalogs = {item.name for item in config.catalogs.providers}
    if set(catalog_manifests) != configured_catalogs:
        missing = sorted(configured_catalogs - set(catalog_manifests))
        extra = sorted(set(catalog_manifests) - configured_catalogs)
        raise ConfigurationError(
            ErrorCode.CATALOG_UNAVAILABLE,
            "Catalog discovery does not match configuration "
            f"(missing={missing!r}, extra={extra!r})",
        )

    resources: dict[ResourceRef, ResourceDefinition] = {}
    for pin in config.resources.pins:
        available = available_resources.get(pin.ref)
        if available is None:
            raise ConfigurationError(
                ErrorCode.REGISTRY_REFERENCE,
                f"configured Resource {pin.ref} was not published by its provider",
                resource_ref=str(pin.ref),
            )
        definition, provider_id = available
        if provider_id != pin.provider_id:
            raise CompatibilityError(
                ErrorCode.REGISTRY_REFERENCE,
                f"Resource {pin.ref} was published by an unexpected provider",
                resource_ref=str(pin.ref),
            )
        if definition.fingerprint != pin.required_fingerprint:
            raise CompatibilityError(
                ErrorCode.REGISTRY_REFERENCE,
                f"Resource fingerprint mismatch for {pin.ref}",
                resource_ref=str(pin.ref),
            )
        resources[pin.ref] = definition

    _validate_references(namespaces, schemas, resources)
    _reject_schema_cycles(schemas)

    binding_by_id = {binding.id: binding for binding in config.bindings}
    resolved: dict[ResourceRef, BindingRecord] = {}
    for ref, resource in sorted(resources.items()):
        matches = [
            placement
            for placement in config.placements
            if placement.selector.matches(ref, resource.labels)
        ]
        if not matches:
            raise ConfigurationError(
                ErrorCode.PLACEMENT_UNRESOLVED,
                f"Resource {ref} matches no placement rule",
                resource_ref=str(ref),
            )
        if len(matches) > 1:
            raise ConfigurationError(
                ErrorCode.PLACEMENT_AMBIGUOUS,
                f"Resource {ref} matches multiple placement rules",
                resource_ref=str(ref),
            )
        binding = binding_by_id[matches[0].binding_id]
        manifest = capability_manifests[binding.id]
        violations = capability_violations(manifest, resource.requirements)
        if violations:
            raise CompatibilityError(
                ErrorCode.CAPABILITY_UNSUPPORTED,
                f"Binding {binding.id!r} cannot satisfy Resource {ref}: {violations[0].reason}",
                resource_ref=str(ref),
                adapter_provenance={
                    "adapterId": manifest.adapter_id,
                    "capabilityFingerprint": manifest.fingerprint,
                },
            )
        mapping = physical_mappings.get(binding.id, {}).get(str(ref))
        if mapping is None or not mapping:
            raise CompatibilityError(
                ErrorCode.PHYSICAL_FINGERPRINT,
                f"physical verification omitted Resource {ref}",
                resource_ref=str(ref),
            )
        resolved[ref] = BindingRecord(
            binding_id=binding.id,
            adapter_id=binding.adapter_id,
            capability_fingerprint=manifest.fingerprint,
            physical_fingerprint=physical_fingerprints[binding.id],
            physical_mapping=mapping,
        )

    content: JsonValue = {
        "catalogs": [
            {"name": name, "fingerprint": manifest.fingerprint}
            for name, manifest in sorted(catalog_manifests.items())
        ],
        "namespaces": [
            item.to_dict() for _, item in sorted(namespaces.items(), key=lambda pair: pair[0])
        ],
        "schemas": [
            item.to_dict() for _, item in sorted(schemas.items(), key=lambda pair: pair[0])
        ],
        "resources": [
            item.to_dict() for _, item in sorted(resources.items(), key=lambda pair: pair[0])
        ],
        "bindings": [
            {
                "resourceRef": ref.to_dict(),
                "bindingId": record.binding_id,
                "adapterId": record.adapter_id,
                "capabilityFingerprint": record.capability_fingerprint,
                "physicalFingerprint": record.physical_fingerprint,
            }
            for ref, record in sorted(resolved.items())
        ],
    }
    return RegistrySnapshot(
        revision=revision,
        fingerprint=sha256_fingerprint(content),
        catalogs=catalog_manifests,
        namespaces=namespaces,
        schemas=schemas,
        resources=resources,
        _bindings=resolved,
    )


def _insert_unique[K, V](target: dict[K, V], key: K, value: V, kind: str) -> None:
    if key in target:
        raise ConfigurationError(
            ErrorCode.REGISTRY_DUPLICATE,
            f"duplicate {kind} definition",
        )
    target[key] = value


def _validate_references(
    namespaces: Mapping[tuple[str, str], NamespaceDefinition],
    schemas: Mapping[SchemaRef, SchemaDefinition],
    resources: Mapping[ResourceRef, ResourceDefinition],
) -> None:
    for schema in schemas.values():
        namespace = (schema.ref.catalog, schema.ref.namespace)
        if namespace not in namespaces:
            raise ConfigurationError(
                ErrorCode.REGISTRY_REFERENCE,
                f"Schema {schema.ref} references a missing Namespace",
                resource_ref=str(schema.ref),
            )
        for dependency in schema.dependencies:
            if dependency not in schemas:
                raise ConfigurationError(
                    ErrorCode.REGISTRY_REFERENCE,
                    f"Schema {schema.ref} references a missing dependency",
                    resource_ref=str(schema.ref),
                )
    for ref, resource in resources.items():
        if (ref.catalog, ref.namespace) not in namespaces:
            raise ConfigurationError(
                ErrorCode.REGISTRY_REFERENCE,
                f"Resource {ref} references a missing Namespace",
                resource_ref=str(ref),
            )
        if resource.schema is not None and resource.schema not in schemas:
            raise ConfigurationError(
                ErrorCode.REGISTRY_REFERENCE,
                f"Resource {ref} references a missing Schema",
                resource_ref=str(ref),
            )
        for related in resource.related_resources:
            if related not in resources:
                raise ConfigurationError(
                    ErrorCode.REGISTRY_REFERENCE,
                    f"Resource {ref} references a missing related Resource",
                    resource_ref=str(ref),
                )


def _reject_schema_cycles(schemas: Mapping[SchemaRef, SchemaDefinition]) -> None:
    visiting: set[SchemaRef] = set()
    visited: set[SchemaRef] = set()

    def visit(ref: SchemaRef) -> None:
        if ref in visiting:
            raise ConfigurationError(
                ErrorCode.REGISTRY_CYCLE,
                f"Schema dependency cycle contains {ref}",
                resource_ref=str(ref),
            )
        if ref in visited:
            return
        visiting.add(ref)
        for dependency in schemas[ref].dependencies:
            visit(dependency)
        visiting.remove(ref)
        visited.add(ref)

    for ref in sorted(schemas):
        visit(ref)


__all__ = ["BindingRecord", "Registry", "RegistrySnapshot", "build_registry"]
