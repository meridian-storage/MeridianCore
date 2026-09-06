# SPDX-License-Identifier: Apache-2.0
"""Fail-closed Meridian V1 runtime and internal Binding resolver."""

from __future__ import annotations

import os
import random
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager, suppress
from contextvars import ContextVar
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import MappingProxyType, TracebackType
from typing import cast

from meridian_storage._canonical import canonical_json_bytes, sha256_fingerprint
from meridian_storage._versions import contract_matches
from meridian_storage.context import OperationContext, bind_context, current_context
from meridian_storage.errors import (
    CatalogNotFound,
    CompatibilityError,
    ConfigurationError,
    ErrorCode,
    InternalError,
    LifecycleError,
    MeridianError,
    MeridianTimeoutError,
    SafeCause,
    TransactionError,
    UnavailableError,
    ValidationError,
)
from meridian_storage.registry.handles import NamespaceHandle, ResourceHandle, SchemaHandle
from meridian_storage.registry.registry import (
    Registry,
    RegistrySnapshot,
    _reject_schema_cycles,
    _validate_references,
    build_registry,
)
from meridian_storage.registry.resources import (
    REGISTERED_CATALOGS,
    CapabilityRequirement,
    ResourceBundle,
    ResourceDefinition,
    ResourceRef,
)
from meridian_storage.spi.adapters import (
    AdapterCreateContext,
    AdapterFactory,
    AdapterProbe,
    AdapterRuntime,
    AdapterSession,
    ExecutionRequest,
    ExecutionResult,
    PhysicalResource,
    PhysicalVerification,
    SecretResolver,
    SecretValue,
)
from meridian_storage.spi.capabilities import CapabilityManifest, capability_violations
from meridian_storage.spi.discovery import (
    ADAPTER_ENTRY_POINT_GROUP,
    CATALOG_ENTRY_POINT_GROUP,
    PLUGIN_ENTRY_POINT_GROUP,
    SCHEMA_ENTRY_POINT_GROUP,
    DiscoveryResult,
    discover_components,
)
from meridian_storage.spi.providers import (
    CatalogProvider,
    CatalogSurface,
    Observer,
    PluginFactory,
    PluginManifest,
    SchemaProvider,
)
from meridian_storage.transactions.manager import (
    Transaction,
    TransactionFrame,
    TransactionLease,
    current_transaction,
    install_transaction,
    reset_transaction,
)

from .config import (
    BindingConfig,
    RuntimeConfig,
    load_runtime_config_from_environment,
)
from .idempotency import IdempotencyClaim, IdempotencyStore
from .lifecycle import (
    BindingStartupReport,
    EvidenceStatus,
    RuntimeState,
    StartupEvidence,
    StartupReport,
)
from .operations import (
    CatalogManifest,
    Expression,
    Operation,
    OperationContract,
    OperationResult,
)
from .secrets import default_secret_resolver

CORE_CONTRACT_VERSION = "1.0.0"
TRANSACTION_OPERATION_CONTRACT = "meridian.transaction"
TRANSACTION_OPERATION_VERSION = "1.0.0"

_in_observer: ContextVar[bool] = ContextVar("meridian_in_observer", default=False)


class _NullObserver:
    def emit(self, name: str, attributes: Mapping[str, str]) -> None:
        del name, attributes


class _ContextBoundary(AbstractContextManager[OperationContext]):
    def __init__(self, context: OperationContext) -> None:
        self._manager = bind_context(context)

    def __enter__(self) -> OperationContext:
        return self._manager.__enter__()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        return self._manager.__exit__(exc_type, exc, traceback)


class Meridian:
    """In-process composition runtime for installed Meridian V1 packages."""

    def __init__(
        self,
        config: RuntimeConfig,
        *,
        adapter_factories: Iterable[AdapterFactory] = (),
        catalog_providers: Iterable[CatalogProvider] = (),
        schema_providers: Iterable[SchemaProvider] = (),
        plugin_factories: Iterable[PluginFactory] = (),
        secret_resolver: SecretResolver | None = None,
        observer: Observer | None = None,
        _sleep: Callable[[float], None] = time.sleep,
        _random: Callable[[], float] = random.random,
    ) -> None:
        if not isinstance(config, RuntimeConfig):
            raise TypeError("config must be a RuntimeConfig")
        self._config = config
        self._injected_adapters = tuple(adapter_factories)
        self._injected_catalogs = tuple(catalog_providers)
        self._injected_schemas = tuple(schema_providers)
        self._injected_plugins = tuple(plugin_factories)
        self._secret_resolver = secret_resolver or default_secret_resolver()
        self._observer: Observer = observer or _NullObserver()
        self._sleep = _sleep
        self._random = _random
        self._pid = os.getpid()
        self._identity = id(self)

        self._condition = threading.Condition(threading.RLock())
        self._state = RuntimeState.NEW
        self._active_operations = 0
        self._active_transactions = 0
        self._start_error: MeridianError | None = None
        self._startup_report: StartupReport | None = None

        self._registry = Registry()
        self._adapter_runtimes: dict[str, AdapterRuntime] = {}
        self._capability_manifests: dict[str, CapabilityManifest] = {}
        self._opened_bindings: list[str] = []
        self._catalog_providers: Mapping[str, CatalogProvider] = MappingProxyType({})
        self._catalog_manifests: Mapping[str, CatalogManifest] = MappingProxyType({})
        self._catalog_surfaces: Mapping[str, CatalogSurface] = MappingProxyType({})
        self._schema_providers: Mapping[str, SchemaProvider] = MappingProxyType({})
        self._plugin_factories: Mapping[str, PluginFactory] = MappingProxyType({})
        self._plugin_manifests: Mapping[str, PluginManifest] = MappingProxyType({})
        self._plugins: dict[str, object] = {}
        self._bundles: tuple[ResourceBundle, ...] = ()
        self._idempotency = IdempotencyStore(config.validation.idempotency_cache_entries)

    @classmethod
    def from_config(cls, config: RuntimeConfig, **dependencies: object) -> Meridian:
        return cls(config, **dependencies)  # type: ignore[arg-type]

    @classmethod
    def from_environment(
        cls,
        *,
        environment: Mapping[str, str] | None = None,
        **dependencies: object,
    ) -> Meridian:
        return cls(
            load_runtime_config_from_environment(environment),
            **dependencies,  # type: ignore[arg-type]
        )

    @property
    def state(self) -> RuntimeState:
        with self._condition:
            return self._state

    @property
    def startup_report(self) -> StartupReport | None:
        with self._condition:
            return self._startup_report

    def start(self) -> StartupReport:
        """Discover, authenticate, validate, and atomically enter ``READY``."""

        self._check_process()
        with self._condition:
            while self._state is RuntimeState.STARTING:
                self._condition.wait()
            if self._state is RuntimeState.READY:
                assert self._startup_report is not None
                return self._startup_report
            if self._state is RuntimeState.FAILED:
                assert self._start_error is not None
                raise self._start_error
            if self._state is not RuntimeState.NEW:
                raise LifecycleError(
                    ErrorCode.RUNTIME_STATE,
                    f"cannot start a runtime in state {self._state.value}",
                )
            self._state = RuntimeState.STARTING

        try:
            report = self._start_once()
        except BaseException as exc:
            error = self._startup_error(exc)
            self._close_opened_adapters(suppress=True)
            with self._condition:
                self._start_error = error
                self._state = RuntimeState.FAILED
                self._condition.notify_all()
            raise error from exc
        with self._condition:
            self._startup_report = report
            self._state = RuntimeState.READY
            self._condition.notify_all()
        self._emit("meridian.runtime.ready", {"profile": self._config.profile})
        return report

    def _start_once(self) -> StartupReport:
        started_at = datetime.now(UTC)
        evidence: list[StartupEvidence] = [
            StartupEvidence(
                "configuration",
                EvidenceStatus.PASSED,
                self._config.profile,
                self._config.fingerprint,
            )
        ]
        adapters = discover_components(
            ADAPTER_ENTRY_POINT_GROUP,
            self._injected_adapters,
            component_id=lambda item: item.adapter_id,
        )
        catalogs = discover_components(
            CATALOG_ENTRY_POINT_GROUP,
            self._injected_catalogs,
            component_id=lambda item: item.catalog_name,
        )
        schemas = discover_components(
            SCHEMA_ENTRY_POINT_GROUP,
            self._injected_schemas,
            component_id=lambda item: item.provider_id,
        )
        plugins = discover_components(
            PLUGIN_ENTRY_POINT_GROUP,
            self._injected_plugins,
            component_id=lambda item: item.plugin_id,
        )
        evidence.append(
            StartupEvidence(
                "discovery",
                EvidenceStatus.PASSED,
                "local-entry-points",
                details={
                    "adapters": str(len(adapters.components)),
                    "catalogs": str(len(catalogs.components)),
                    "schemaProviders": str(len(schemas.components)),
                    "plugins": str(len(plugins.components)),
                },
            )
        )

        catalog_providers, catalog_manifests, catalog_surfaces = self._prepare_catalogs(
            catalogs, evidence
        )
        plugin_manifests = self._prepare_plugins(plugins, evidence)
        self._plugin_factories = plugins.components
        self._plugin_manifests = MappingProxyType(plugin_manifests)

        secret_cache: dict[tuple[str, str], SecretValue] = {}

        def resolve(reference: object) -> SecretValue:
            key = (reference.provider, reference.reference)  # type: ignore[attr-defined]
            if key not in secret_cache:
                secret_cache[key] = self._secret_resolver.resolve(reference)  # type: ignore[arg-type]
            return secret_cache[key]

        for binding in self._config.bindings:
            factory = adapters.components.get(binding.adapter_id)
            if factory is None:
                raise UnavailableError(
                    ErrorCode.ADAPTER_NOT_FOUND,
                    f"Adapter {binding.adapter_id!r} required by Binding "
                    f"{binding.id!r} is unavailable",
                )
            if not isinstance(factory, AdapterFactory):
                raise CompatibilityError(
                    ErrorCode.ADAPTER_CONTRACT,
                    f"Adapter {binding.adapter_id!r} does not implement the V1 factory contract",
                )
            self._verify_distribution(adapters, binding.adapter_id, None)
            tls_ca = None if binding.tls.ca_ref is None else resolve(binding.tls.ca_ref)
            tls_client = (
                None
                if binding.tls.client_certificate_ref is None
                else resolve(binding.tls.client_certificate_ref)
            )
            runtime = factory.create(
                AdapterCreateContext(
                    binding=binding,
                    identity=resolve(binding.identity_ref),
                    credential=resolve(binding.secret_ref),
                    tls_ca=tls_ca,
                    tls_client_certificate=tls_client,
                )
            )
            if not isinstance(runtime, AdapterRuntime):
                raise CompatibilityError(
                    ErrorCode.ADAPTER_CONTRACT,
                    f"Adapter {binding.adapter_id!r} returned an invalid runtime",
                )
            self._adapter_runtimes[binding.id] = runtime
            self._opened_bindings.append(binding.id)
            runtime.open()
            probe = runtime.probe()
            if not isinstance(probe, AdapterProbe):
                raise CompatibilityError(
                    ErrorCode.ADAPTER_CONTRACT,
                    f"Adapter {binding.adapter_id!r} returned an invalid probe",
                )
            manifest = probe.manifest
            self._verify_adapter_manifest(binding, manifest)
            self._capability_manifests[binding.id] = manifest
            evidence.append(
                StartupEvidence(
                    "adapter-probe",
                    EvidenceStatus.PASSED,
                    binding.id,
                    manifest.fingerprint,
                    details={
                        "adapterId": manifest.adapter_id,
                        "engineProfile": manifest.engine_profile,
                        "engineVersion": manifest.engine_version,
                    },
                )
            )

        bundles, schema_providers = self._load_bundles(schemas, evidence)
        planned = self._plan_physical_resources(bundles)
        physical_fingerprints: dict[str, str] = {}
        physical_mappings: dict[str, Mapping[str, str]] = {}
        for binding in self._config.bindings:
            verification = self._adapter_runtimes[binding.id].verify_physical(planned[binding.id])
            if not isinstance(verification, PhysicalVerification):
                raise CompatibilityError(
                    ErrorCode.ADAPTER_CONTRACT,
                    f"Binding {binding.id!r} returned invalid physical verification",
                )
            expected_keys = {str(item.resource_ref) for item in planned[binding.id]}
            if set(verification.mappings) != expected_keys or any(
                not value for value in verification.mappings.values()
            ):
                raise CompatibilityError(
                    ErrorCode.PHYSICAL_FINGERPRINT,
                    f"Binding {binding.id!r} returned incomplete physical mappings",
                )
            if (
                binding.required_physical_fingerprint is not None
                and verification.fingerprint != binding.required_physical_fingerprint
            ):
                raise CompatibilityError(
                    ErrorCode.PHYSICAL_FINGERPRINT,
                    f"Binding {binding.id!r} physical fingerprint does not match "
                    "its deployment pin",
                )
            physical_fingerprints[binding.id] = verification.fingerprint
            physical_mappings[binding.id] = verification.mappings
            evidence.append(
                StartupEvidence(
                    "physical-verification",
                    EvidenceStatus.PASSED,
                    binding.id,
                    verification.fingerprint,
                    details={"resources": str(len(expected_keys))},
                )
            )

        snapshot = build_registry(
            bundles,
            self._config,
            catalog_manifests,
            self._capability_manifests,
            physical_fingerprints,
            physical_mappings,
            revision=1,
        )
        self._registry.install_initial(snapshot)
        self._catalog_providers = MappingProxyType(catalog_providers)
        self._catalog_manifests = MappingProxyType(catalog_manifests)
        self._catalog_surfaces = MappingProxyType(catalog_surfaces)
        self._schema_providers = MappingProxyType(schema_providers)
        self._bundles = bundles
        ready_at = datetime.now(UTC)
        return StartupReport(
            profile=self._config.profile,
            config_fingerprint=self._config.fingerprint,
            registry_revision=snapshot.revision,
            registry_fingerprint=snapshot.fingerprint,
            started_at=started_at,
            ready_at=ready_at,
            bindings=tuple(
                BindingStartupReport(
                    binding_id=binding.id,
                    adapter_id=self._capability_manifests[binding.id].adapter_id,
                    adapter_contract_version=self._capability_manifests[
                        binding.id
                    ].adapter_contract_version,
                    engine_profile=self._capability_manifests[binding.id].engine_profile,
                    engine_version=self._capability_manifests[binding.id].engine_version,
                    capability_fingerprint=self._capability_manifests[binding.id].fingerprint,
                    physical_fingerprint=physical_fingerprints[binding.id],
                )
                for binding in self._config.bindings
            ),
            adapters=tuple(sorted({item.adapter_id for item in self._config.bindings})),
            catalogs=tuple(sorted(catalog_providers)),
            schema_providers=tuple(sorted(schema_providers)),
            resources=tuple(sorted(str(ref) for ref in snapshot.resources)),
            plugins=tuple(sorted(plugin_manifests)),
            evidence=tuple(evidence),
        )

    def _prepare_catalogs(
        self,
        discovery: DiscoveryResult[CatalogProvider],
        evidence: list[StartupEvidence],
    ) -> tuple[
        dict[str, CatalogProvider],
        dict[str, CatalogManifest],
        dict[str, CatalogSurface],
    ]:
        providers: dict[str, CatalogProvider] = {}
        manifests: dict[str, CatalogManifest] = {}
        surfaces: dict[str, CatalogSurface] = {}
        for configured in self._config.catalogs.providers:
            provider = discovery.components.get(configured.name)
            if provider is None:
                raise UnavailableError(
                    ErrorCode.CATALOG_UNAVAILABLE,
                    f"configured Catalog {configured.name!r} is not installed",
                    resource_ref=configured.name,
                )
            if not isinstance(provider, CatalogProvider):
                raise CompatibilityError(
                    ErrorCode.CATALOG_CONTRACT,
                    f"Catalog {configured.name!r} does not implement the V1 provider contract",
                    resource_ref=configured.name,
                )
            self._verify_distribution(discovery, configured.name, configured.package)
            manifest = provider.manifest()
            if not isinstance(manifest, CatalogManifest):
                raise CompatibilityError(
                    ErrorCode.CATALOG_CONTRACT,
                    f"Catalog {configured.name!r} returned an invalid manifest",
                    resource_ref=configured.name,
                )
            if (
                manifest.catalog_name != configured.name
                or manifest.package_name != configured.package
            ):
                raise CompatibilityError(
                    ErrorCode.CATALOG_CONTRACT,
                    f"Catalog {configured.name!r} package identity does not match "
                    "its deployment pin",
                    resource_ref=configured.name,
                )
            if not contract_matches(manifest.catalog_contract_version, configured.contract):
                raise CompatibilityError(
                    ErrorCode.CATALOG_CONTRACT,
                    f"Catalog {configured.name!r} contract is incompatible",
                    resource_ref=configured.name,
                )
            if manifest.fingerprint != configured.required_fingerprint:
                raise CompatibilityError(
                    ErrorCode.CATALOG_CONTRACT,
                    f"Catalog {configured.name!r} manifest fingerprint does not match",
                    resource_ref=configured.name,
                )
            surface = provider.create_surface()
            if not isinstance(surface, CatalogSurface) or surface.catalog_name != configured.name:
                raise CompatibilityError(
                    ErrorCode.CATALOG_CONTRACT,
                    f"Catalog {configured.name!r} returned an invalid Expression surface",
                    resource_ref=configured.name,
                )
            providers[configured.name] = provider
            manifests[configured.name] = manifest
            surfaces[configured.name] = surface
            evidence.append(
                StartupEvidence(
                    "catalog-manifest",
                    EvidenceStatus.PASSED,
                    configured.name,
                    manifest.fingerprint,
                    details={"package": manifest.package_name},
                )
            )
        return providers, manifests, surfaces

    def _prepare_plugins(
        self,
        discovery: DiscoveryResult[PluginFactory],
        evidence: list[StartupEvidence],
    ) -> dict[str, PluginManifest]:
        manifests: dict[str, PluginManifest] = {}
        for plugin_id, factory in discovery.components.items():
            if not isinstance(factory, PluginFactory):
                raise CompatibilityError(
                    ErrorCode.PLUGIN_CONTRACT,
                    f"Plugin {plugin_id!r} does not implement the V1 factory contract",
                )
            manifest = factory.manifest()
            if not isinstance(manifest, PluginManifest):
                raise CompatibilityError(
                    ErrorCode.PLUGIN_CONTRACT,
                    f"Plugin {plugin_id!r} returned an invalid manifest",
                )
            if manifest.plugin_id != plugin_id:
                raise CompatibilityError(
                    ErrorCode.PLUGIN_CONTRACT,
                    f"Plugin {plugin_id!r} manifest identity does not match its entry point",
                )
            try:
                plugin_compatible = contract_matches(
                    manifest.plugin_contract_version, "1.x"
                ) and contract_matches(CORE_CONTRACT_VERSION, manifest.core_contract)
            except ValueError as exc:
                raise CompatibilityError(
                    ErrorCode.PLUGIN_CONTRACT,
                    f"Plugin {plugin_id!r} contains an invalid contract range",
                    cause=SafeCause.from_exception(exc),
                ) from exc
            if not plugin_compatible:
                raise CompatibilityError(
                    ErrorCode.PLUGIN_CONTRACT,
                    f"Plugin {plugin_id!r} is incompatible with Meridian Core V1",
                )
            manifests[plugin_id] = manifest
            evidence.append(
                StartupEvidence(
                    "plugin-manifest",
                    EvidenceStatus.PASSED,
                    plugin_id,
                    details={"version": manifest.plugin_version},
                )
            )
        return manifests

    def _load_bundles(
        self,
        discovery: DiscoveryResult[SchemaProvider],
        evidence: list[StartupEvidence],
    ) -> tuple[tuple[ResourceBundle, ...], dict[str, SchemaProvider]]:
        bundles: list[ResourceBundle] = []
        providers: dict[str, SchemaProvider] = {}
        for configured in self._config.schemas.providers:
            provider = discovery.components.get(configured.id)
            if provider is None:
                raise UnavailableError(
                    ErrorCode.DISCOVERY_FAILED,
                    f"Schema provider {configured.id!r} is not installed",
                )
            if not isinstance(provider, SchemaProvider):
                raise CompatibilityError(
                    ErrorCode.ADAPTER_CONTRACT,
                    f"Schema provider {configured.id!r} does not implement the V1 contract",
                )
            self._verify_distribution(discovery, configured.id, configured.package)
            try:
                compatible = contract_matches(
                    provider.provider_contract_version, configured.contract
                )
            except ValueError as exc:
                raise CompatibilityError(
                    ErrorCode.ADAPTER_CONTRACT,
                    f"Schema provider {configured.id!r} has an invalid contract",
                    cause=SafeCause.from_exception(exc),
                ) from exc
            if not compatible:
                raise CompatibilityError(
                    ErrorCode.ADAPTER_CONTRACT,
                    f"Schema provider {configured.id!r} is incompatible",
                )
            bundle = provider.load()
            if not isinstance(bundle, ResourceBundle):
                raise CompatibilityError(
                    ErrorCode.REGISTRY_REFERENCE,
                    f"Schema provider {configured.id!r} returned an invalid Resource bundle",
                )
            if (
                bundle.provider_id != configured.id
                or bundle.provider_contract_version != provider.provider_contract_version
            ):
                raise CompatibilityError(
                    ErrorCode.REGISTRY_REFERENCE,
                    f"Schema provider {configured.id!r} returned an inconsistent bundle",
                )
            if bundle.fingerprint != configured.required_fingerprint:
                raise CompatibilityError(
                    ErrorCode.REGISTRY_REFERENCE,
                    f"Schema provider {configured.id!r} bundle fingerprint does not match",
                )
            providers[configured.id] = provider
            bundles.append(bundle)
            evidence.append(
                StartupEvidence(
                    "schema-provider",
                    EvidenceStatus.PASSED,
                    configured.id,
                    bundle.fingerprint,
                )
            )

        live = self._config.schemas.live
        if live.enabled:
            assert live.provider_id is not None
            provider = providers[live.provider_id]
            loader = getattr(provider, "load_live", None)
            if not callable(loader):
                if live.required:
                    raise UnavailableError(
                        ErrorCode.REGISTRY_REFERENCE,
                        "required live Schema metadata provider is unavailable",
                    )
                evidence.append(
                    StartupEvidence(
                        "live-schema",
                        EvidenceStatus.SKIPPED,
                        live.provider_id,
                        details={"reason": "provider-does-not-publish-live-metadata"},
                    )
                )
            else:
                live_bundle = loader()
                if not isinstance(live_bundle, ResourceBundle):
                    raise CompatibilityError(
                        ErrorCode.REGISTRY_REFERENCE,
                        "live Schema provider returned an invalid Resource bundle",
                    )
                bundles.append(live_bundle)
                evidence.append(
                    StartupEvidence(
                        "live-schema",
                        EvidenceStatus.PASSED,
                        live.provider_id,
                        live_bundle.fingerprint,
                    )
                )
        return tuple(bundles), providers

    def _plan_physical_resources(
        self,
        bundles: tuple[ResourceBundle, ...],
    ) -> dict[str, tuple[PhysicalResource, ...]]:
        available: dict[ResourceRef, tuple[ResourceDefinition, str]] = {}
        namespaces = {}
        schemas = {}
        for bundle in bundles:
            for namespace in bundle.namespaces:
                key = (namespace.catalog, namespace.name)
                if key in namespaces:
                    raise ConfigurationError(
                        ErrorCode.REGISTRY_DUPLICATE,
                        "duplicate Namespace definition",
                    )
                namespaces[key] = namespace
            for schema in bundle.schemas:
                if schema.ref in schemas:
                    raise ConfigurationError(
                        ErrorCode.REGISTRY_DUPLICATE,
                        "duplicate Schema version definition",
                    )
                schemas[schema.ref] = schema
            for resource in bundle.resources:
                if resource.ref in available:
                    raise ConfigurationError(
                        ErrorCode.REGISTRY_DUPLICATE,
                        "duplicate Resource definition",
                    )
                available[resource.ref] = (resource, bundle.provider_id)
        pinned_refs = {pin.ref for pin in self._config.resources.pins}
        for placement in self._config.placements:
            unknown = set(placement.selector.resources) - pinned_refs
            if unknown:
                raise ConfigurationError(
                    ErrorCode.REGISTRY_REFERENCE,
                    f"placement {placement.id!r} selects an unconfigured Resource",
                )
        selected_resources: dict[ResourceRef, ResourceDefinition] = {}
        for pin in self._config.resources.pins:
            published = available.get(pin.ref)
            if published is None:
                raise ConfigurationError(
                    ErrorCode.REGISTRY_REFERENCE,
                    f"configured Resource {pin.ref} is unavailable",
                    resource_ref=str(pin.ref),
                )
            resource, provider_id = published
            if provider_id != pin.provider_id or resource.fingerprint != pin.required_fingerprint:
                raise CompatibilityError(
                    ErrorCode.REGISTRY_REFERENCE,
                    f"configured Resource {pin.ref} does not match its provider pin",
                    resource_ref=str(pin.ref),
                )
            selected_resources[pin.ref] = resource
        _validate_references(namespaces, schemas, selected_resources)
        _reject_schema_cycles(schemas)
        planned: dict[str, list[PhysicalResource]] = {
            binding.id: [] for binding in self._config.bindings
        }
        placement_hits = {placement.id: 0 for placement in self._config.placements}
        for pin in self._config.resources.pins:
            resource = selected_resources[pin.ref]
            matches = [
                placement
                for placement in self._config.placements
                if placement.selector.matches(pin.ref, resource.labels)
            ]
            if not matches:
                raise ConfigurationError(
                    ErrorCode.PLACEMENT_UNRESOLVED,
                    f"Resource {pin.ref} matches no placement rule",
                    resource_ref=str(pin.ref),
                )
            if len(matches) > 1:
                raise ConfigurationError(
                    ErrorCode.PLACEMENT_AMBIGUOUS,
                    f"Resource {pin.ref} matches multiple placement rules",
                    resource_ref=str(pin.ref),
                )
            placement = matches[0]
            placement_hits[placement.id] += 1
            manifest = self._capability_manifests[placement.binding_id]
            violations = capability_violations(manifest, resource.requirements)
            if violations:
                raise CompatibilityError(
                    ErrorCode.CAPABILITY_UNSUPPORTED,
                    f"Binding {placement.binding_id!r} cannot satisfy Resource {pin.ref}: "
                    f"{violations[0].reason}",
                    resource_ref=str(pin.ref),
                )
            planned[placement.binding_id].append(
                PhysicalResource(
                    resource_ref=pin.ref,
                    resource_fingerprint=resource.fingerprint,
                    schema_fingerprint=(
                        None if resource.schema is None else schemas[resource.schema].fingerprint
                    ),
                    profile=resource.profile,
                )
            )
        if any(hits == 0 for hits in placement_hits.values()):
            raise ConfigurationError(
                ErrorCode.PLACEMENT_UNRESOLVED,
                "a placement rule selects no configured Resource",
            )
        if any(not resources for resources in planned.values()):
            raise ConfigurationError(
                ErrorCode.PLACEMENT_UNRESOLVED,
                "every configured Binding must own at least one Resource",
            )
        return {
            binding_id: tuple(sorted(resources, key=lambda item: item.resource_ref))
            for binding_id, resources in planned.items()
        }

    def _verify_adapter_manifest(
        self,
        binding: BindingConfig,
        manifest: CapabilityManifest,
    ) -> None:
        if manifest.adapter_id != binding.adapter_id:
            raise CompatibilityError(
                ErrorCode.ADAPTER_CONTRACT,
                f"Binding {binding.id!r} probed an unexpected Adapter identity",
            )
        try:
            compatible = contract_matches(
                manifest.adapter_contract_version, binding.adapter_contract
            )
        except ValueError as exc:
            raise CompatibilityError(
                ErrorCode.ADAPTER_CONTRACT,
                f"Binding {binding.id!r} contains an invalid Adapter contract range",
                cause=SafeCause.from_exception(exc),
            ) from exc
        if not compatible:
            raise CompatibilityError(
                ErrorCode.ADAPTER_CONTRACT,
                f"Binding {binding.id!r} Adapter contract is incompatible",
            )
        if (manifest.engine_profile, manifest.engine_version) != (
            binding.engine_profile,
            binding.engine_version,
        ):
            raise CompatibilityError(
                ErrorCode.ADAPTER_CONTRACT,
                f"Binding {binding.id!r} probed an unexpected Engine profile or version",
            )
        if manifest.fingerprint != binding.required_capability_fingerprint:
            raise CompatibilityError(
                ErrorCode.CAPABILITY_FINGERPRINT,
                f"Binding {binding.id!r} Capability fingerprint does not match its pin",
            )
        known_pins = {
            "coreVersion": CORE_CONTRACT_VERSION,
            "driver": manifest.descriptor.driver,
            "adapterContract": manifest.adapter_contract_version,
            "engineProfile": manifest.engine_profile,
            "engineVersion": manifest.engine_version,
        }
        for name, expected in binding.compatibility_pins.items():
            actual = known_pins.get(name)
            if actual is None:
                extension = manifest.extensions.get(name)
                actual = extension if isinstance(extension, str) else None
            if actual is None or actual != expected:
                raise CompatibilityError(
                    ErrorCode.ADAPTER_CONTRACT,
                    f"Binding {binding.id!r} compatibility pin {name!r} is unsatisfied",
                )

    @staticmethod
    def _verify_distribution(
        discovery: DiscoveryResult[object],
        component_id: str,
        expected_package: str | None,
    ) -> None:
        if expected_package is None:
            return
        record = next(item for item in discovery.records if item.component_id == component_id)
        if record.injected:
            return

        def normalize(value: str) -> str:
            return value.lower().replace("_", "-").replace(".", "-")

        if normalize(record.distribution) != normalize(expected_package):
            raise CompatibilityError(
                ErrorCode.DISCOVERY_FAILED,
                f"component {component_id!r} was loaded from an unexpected package",
            )

    def catalog(self, name: str) -> CatalogSurface:
        """Return one installed mapping-first Catalog Expression surface."""

        if name not in REGISTERED_CATALOGS:
            raise CatalogNotFound(name)
        self._require_ready()
        surface = self._catalog_surfaces.get(name)
        if surface is None:
            raise UnavailableError(
                ErrorCode.CATALOG_UNAVAILABLE,
                f"registered Catalog {name!r} is not configured in this runtime",
                resource_ref=name,
            )
        return surface

    def context(self, context: OperationContext) -> _ContextBoundary:
        return _ContextBoundary(context)

    def namespace(self, catalog: str, name: str) -> NamespaceHandle:
        self._require_catalog_name(catalog)
        return NamespaceHandle(self, catalog, name)

    def schema(
        self,
        catalog: str,
        namespace: str,
        name: str,
        version: str | None = None,
    ) -> SchemaHandle:
        self._require_catalog_name(catalog)
        return SchemaHandle(self, catalog, namespace, name, version)

    def resource(
        self,
        ref: ResourceRef | str | Mapping[str, object],
        *,
        catalog: str | None = None,
    ) -> ResourceHandle:
        snapshot = self._snapshot_for_handle()
        if isinstance(ref, str) and ":" not in ref and catalog is not None:
            resolved = ResourceRef.parse(ref, catalog=catalog)
            snapshot.resource(resolved)
        else:
            resolved = snapshot.resolve_resource(ref)
        return ResourceHandle(self, resolved)

    def execute(self, expression: Expression) -> OperationResult:
        """Normalize one Expression, prove one Binding, and execute it."""

        if not isinstance(expression, Expression):
            raise ValidationError(
                ErrorCode.OPERATION_INVALID,
                "Meridian.execute requires a versioned Expression",
            )
        context = cast(OperationContext, current_context()).resolve_request_id()
        assert context.request_id is not None
        execution_id = str(uuid.uuid4())
        snapshot = self._begin_operation()
        operation: Operation | None = None
        try:
            provider = self._catalog_providers.get(expression.catalog)
            manifest = self._catalog_manifests.get(expression.catalog)
            if provider is None or manifest is None:
                if expression.catalog not in REGISTERED_CATALOGS:
                    raise CatalogNotFound(expression.catalog)
                raise UnavailableError(
                    ErrorCode.CATALOG_UNAVAILABLE,
                    f"registered Catalog {expression.catalog!r} is unavailable",
                    resource_ref=expression.catalog,
                )
            try:
                contract = manifest.operation_for(expression.method)
            except KeyError as exc:
                raise ValidationError(
                    ErrorCode.OPERATION_INVALID,
                    f"Catalog method {expression.method!r} is not registered",
                    resource_ref=expression.catalog,
                ) from exc
            try:
                normalized = provider.normalize(expression)
            except MeridianError:
                raise
            except (TypeError, ValueError, KeyError) as exc:
                raise ValidationError(
                    ErrorCode.OPERATION_INVALID,
                    "Catalog Expression could not be normalized",
                    cause=SafeCause.from_exception(exc),
                ) from exc
            operation = self._validate_operation(normalized, expression, contract)
            binding_id, capability_fingerprint = self._resolve_operation(
                operation, snapshot, context
            )
            binding = self._config.binding(binding_id)
            effective_context = self._effective_context(context, binding)
            result = self._execute_operation(
                operation,
                effective_context,
                execution_id,
                binding,
                snapshot,
            )
            normalized_result_bytes = len(canonical_json_bytes(result.data))
            if max(result.result_bytes, normalized_result_bytes) > binding.client.max_result_bytes:
                raise ValidationError(
                    ErrorCode.OPERATION_RESULT_LIMIT,
                    "Adapter result exceeds the configured result byte limit",
                )
            response = OperationResult(
                data=result.data,
                catalog=operation.catalog,
                operation_contract=operation.operation_contract,
                operation_version=operation.operation_version,
                resources=operation.resources,
                request_id=effective_context.request_id or "",
                execution_id=execution_id,
                operation_fingerprint=operation.request_fingerprint,
                registry_fingerprint=snapshot.fingerprint,
                capability_fingerprint=capability_fingerprint,
                provenance=result.provenance,
            )
            self._emit(
                "meridian.operation.completed",
                {
                    "catalog": operation.catalog,
                    "operationContract": operation.operation_contract,
                    "requestId": response.request_id,
                    "executionId": response.execution_id,
                    "registryFingerprint": snapshot.fingerprint,
                },
            )
            return response
        except BaseException as exc:
            error = self._operation_error(
                exc,
                operation=operation,
                request_id=context.request_id,
                execution_id=execution_id,
                snapshot=snapshot,
            )
            self._emit(
                "meridian.operation.failed",
                {
                    "code": error.code,
                    "requestId": context.request_id,
                    "executionId": execution_id,
                },
            )
            if error is exc:
                raise
            raise error from exc
        finally:
            self._end_operation()

    def _validate_operation(
        self,
        operation: Operation,
        expression: Expression,
        contract: OperationContract,
    ) -> Operation:
        if not isinstance(operation, Operation):
            raise CompatibilityError(
                ErrorCode.CATALOG_CONTRACT,
                f"Catalog {expression.catalog!r} returned an invalid Operation",
            )
        expected = (
            operation.catalog == expression.catalog
            and operation.operation_contract == contract.operation_contract
            and operation.operation_version == contract.operation_version
            and operation.read_only == contract.read_only
            and (
                contract.idempotency == "conditional"
                or operation.idempotent == (contract.idempotency == "always")
            )
        )
        if not expected:
            raise CompatibilityError(
                ErrorCode.CATALOG_CONTRACT,
                f"Catalog {expression.catalog!r} normalized an incompatible Operation",
                operation_contract=operation.operation_contract,
            )
        return Operation(
            catalog=operation.catalog,
            operation_contract=operation.operation_contract,
            operation_version=operation.operation_version,
            resources=operation.resources,
            input=operation.input,
            requirements=_merge_requirements((contract.requirement, *operation.requirements)),
            read_only=operation.read_only,
            idempotent=operation.idempotent,
        )

    def _resolve_operation(
        self,
        operation: Operation,
        snapshot: RegistrySnapshot,
        context: OperationContext,
    ) -> tuple[str, str]:
        records = []
        requirements = list(operation.requirements)
        for ref in operation.resources:
            resource = snapshot.resource(ref)
            missing_scope = [key for key in resource.required_scope if key not in context.scope]
            if missing_scope:
                raise ValidationError(
                    ErrorCode.OPERATION_SCOPE,
                    f"Operation context is missing required scope keys: {missing_scope!r}",
                    operation_contract=operation.operation_contract,
                    resource_ref=str(ref),
                )
            requirements.extend(resource.requirements)
            records.append(snapshot.binding_for(ref))
        binding_ids = {record.binding_id for record in records}
        if len(binding_ids) != 1:
            raise ValidationError(
                ErrorCode.OPERATION_SCOPE,
                "one Operation cannot cross internal Bindings",
                operation_contract=operation.operation_contract,
            )
        binding_id = next(iter(binding_ids))
        manifest = self._capability_manifests[binding_id]
        violations = capability_violations(manifest, _merge_requirements(requirements))
        if violations:
            raise CompatibilityError(
                ErrorCode.CAPABILITY_UNSUPPORTED,
                f"Operation Capability requirement is unsatisfied: {violations[0].reason}",
                operation_contract=operation.operation_contract,
                resource_ref=str(operation.resources[0]),
                adapter_provenance={
                    "adapterId": manifest.adapter_id,
                    "capabilityFingerprint": manifest.fingerprint,
                },
            )
        transaction = current_transaction()
        if transaction is not None:
            if transaction.runtime_identity != self._identity:
                raise TransactionError(
                    ErrorCode.TRANSACTION_SCOPE,
                    "active transaction belongs to a different Meridian runtime",
                )
            if transaction.binding_id != binding_id:
                raise TransactionError(
                    ErrorCode.TRANSACTION_SCOPE,
                    "Operation would cross the active transaction Binding",
                    operation_contract=operation.operation_contract,
                )
            if transaction.owner_request_id != context.request_id:
                raise TransactionError(
                    ErrorCode.TRANSACTION_SCOPE,
                    "Operation context does not own the active transaction",
                    operation_contract=operation.operation_contract,
                )
        return binding_id, manifest.fingerprint

    def _effective_context(
        self,
        context: OperationContext,
        binding: BindingConfig,
    ) -> OperationContext:
        timeout_ms = min(
            binding.client.operation_timeout_ms,
            self._config.validation.default_operation_timeout_ms,
        )
        configured_deadline = datetime.now(UTC) + timedelta(milliseconds=timeout_ms)
        deadline = (
            configured_deadline
            if context.deadline is None
            else min(context.deadline, configured_deadline)
        )
        if deadline <= datetime.now(UTC):
            raise MeridianTimeoutError(
                ErrorCode.DEADLINE_EXCEEDED,
                "Operation deadline elapsed before Adapter execution",
                request_id=context.request_id,
            )
        return replace(context, deadline=deadline)

    def _execute_operation(
        self,
        operation: Operation,
        context: OperationContext,
        execution_id: str,
        binding: BindingConfig,
        snapshot: RegistrySnapshot,
    ) -> ExecutionResult:
        transaction = current_transaction()
        claim: IdempotencyClaim | None = None
        if (
            transaction is None
            and not operation.read_only
            and operation.idempotent
            and context.idempotency_key is not None
        ):
            claim = self._idempotency.claim(
                (
                    binding.id,
                    context.principal_ref,
                    context.tenant or "",
                    # Scope is the effective visibility/partition context sent
                    # to the adapter. Incidental request/trace/deadline metadata
                    # must not prevent replay of the same scoped request.
                    sha256_fingerprint(dict(context.scope)),
                    context.idempotency_key,
                ),
                operation.request_fingerprint,
            )
            if not claim.owner:
                assert claim.replay is not None
                return claim.replay
        try:
            result = self._execute_with_retries(
                operation,
                context,
                execution_id,
                binding,
                snapshot,
                transaction.session if transaction is not None else None,
            )
            if claim is not None:
                self._idempotency.complete(claim, result)
            return result
        except BaseException:
            if claim is not None:
                self._idempotency.abort(claim)
            raise

    def _execute_with_retries(
        self,
        operation: Operation,
        context: OperationContext,
        execution_id: str,
        binding: BindingConfig,
        snapshot: RegistrySnapshot,
        transaction_session: AdapterSession | None,
    ) -> ExecutionResult:
        retry = self._config.validation.retry
        retry_allowed = transaction_session is None and (
            operation.read_only or (operation.idempotent and context.idempotency_key is not None)
        )
        for attempt in range(1, retry.max_attempts + 1):
            request = ExecutionRequest(
                operation=operation,
                context=context,
                request_id=context.request_id or "",
                execution_id=execution_id,
                binding_id=binding.id,
                registry_revision=snapshot.revision,
                registry_fingerprint=snapshot.fingerprint,
                attempt=attempt,
            )
            try:
                result = (
                    transaction_session.execute(request)
                    if transaction_session is not None
                    else self._execute_one_session(binding.id, request)
                )
                if not isinstance(result, ExecutionResult):
                    raise TypeError("Adapter returned a non-ExecutionResult value")
                return result
            except BaseException as exc:
                error = self._operation_error(
                    exc,
                    operation=operation,
                    request_id=context.request_id,
                    execution_id=execution_id,
                    snapshot=snapshot,
                )
                if not retry_allowed or not error.retryable or attempt >= retry.max_attempts:
                    if error is exc:
                        raise
                    raise error from exc
                remaining = context.remaining_seconds()
                if remaining is not None and remaining <= 0:
                    raise MeridianTimeoutError(
                        ErrorCode.DEADLINE_EXCEEDED,
                        "Operation retry budget exhausted its deadline",
                        operation_contract=operation.operation_contract,
                        request_id=context.request_id,
                        execution_id=execution_id,
                    ) from exc
                delay_ms = min(retry.max_delay_ms, retry.base_delay_ms * (2 ** (attempt - 1)))
                jitter = 1 + retry.jitter_ratio * (2 * self._random() - 1)
                delay = max(0.0, delay_ms * jitter / 1000)
                if remaining is not None and delay >= remaining:
                    raise MeridianTimeoutError(
                        ErrorCode.DEADLINE_EXCEEDED,
                        "Operation retry delay would exceed its deadline",
                        operation_contract=operation.operation_contract,
                        request_id=context.request_id,
                        execution_id=execution_id,
                    ) from exc
                self._sleep(delay)
        raise AssertionError("retry loop did not return or raise")

    def _execute_one_session(self, binding_id: str, request: ExecutionRequest) -> ExecutionResult:
        session = self._adapter_runtimes[binding_id].open_session(transactional=False)
        if not isinstance(session, AdapterSession):
            raise CompatibilityError(
                ErrorCode.ADAPTER_CONTRACT,
                f"Binding {binding_id!r} returned an invalid Adapter session",
            )
        primary: BaseException | None = None
        try:
            return session.execute(request)
        except BaseException as exc:
            primary = exc
            raise
        finally:
            try:
                session.close()
            except BaseException:
                if primary is None:
                    raise

    def transaction(
        self,
        resource: ResourceRef | str | Mapping[str, object],
    ) -> Transaction:
        return Transaction(self, resource)

    def _enter_transaction(
        self,
        resource: ResourceRef | str | Mapping[str, object],
    ) -> TransactionLease:
        context = cast(OperationContext, current_context()).resolve_request_id()
        assert context.request_id is not None
        snapshot = self._snapshot_for_handle()
        ref = snapshot.resolve_resource(resource)
        binding = snapshot.binding_for(ref)
        current = current_transaction()
        if current is not None:
            if (
                current.runtime_identity != self._identity
                or current.binding_id != binding.binding_id
                or current.owner_request_id != context.request_id
            ):
                raise TransactionError(
                    ErrorCode.TRANSACTION_SCOPE,
                    "nested transaction does not match runtime, Binding, and request owner",
                    resource_ref=str(ref),
                )
            current.depth += 1
            return TransactionLease(current, nested=True)

        requirement = CapabilityRequirement(
            TRANSACTION_OPERATION_CONTRACT,
            TRANSACTION_OPERATION_VERSION,
            guarantees=("atomic", "no-dirty-reads"),
        )
        violations = capability_violations(
            self._capability_manifests[binding.binding_id],
            (requirement,),
        )
        if violations:
            raise TransactionError(
                ErrorCode.TRANSACTION_SCOPE,
                "Resource Binding does not advertise the transaction contract",
                resource_ref=str(ref),
            )
        with self._condition:
            self._require_ready_locked()
            self._active_transactions += 1
        session: AdapterSession | None = None
        try:
            session = self._adapter_runtimes[binding.binding_id].open_session(transactional=True)
            if not isinstance(session, AdapterSession):
                raise CompatibilityError(
                    ErrorCode.ADAPTER_CONTRACT,
                    f"Binding {binding.binding_id!r} returned an invalid transactional session",
                )
            session.begin()
            frame = TransactionFrame(
                runtime_identity=self._identity,
                binding_id=binding.binding_id,
                owner_request_id=context.request_id,
                resource_ref=str(ref),
                session=session,
                snapshot=snapshot,
            )
            token = install_transaction(frame)
            return TransactionLease(frame, nested=False, token=token)
        except BaseException as exc:
            if session is not None:
                with suppress(BaseException):
                    session.close()
            with self._condition:
                self._active_transactions -= 1
                self._condition.notify_all()
            error = self._transaction_component_error(
                exc,
                binding_id=binding.binding_id,
                resource_ref=str(ref),
                request_id=context.request_id,
            )
            if error is exc:
                raise
            raise error from exc

    def _exit_transaction(
        self,
        lease: TransactionLease,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc, traceback
        if lease.exited:
            raise RuntimeError("transaction lease was already exited")
        lease.exited = True
        frame = lease.frame
        if exc_type is not None:
            frame.rollback_only = True
        if lease.nested:
            frame.depth -= 1
            return
        if frame.depth != 1 or lease.token is None:
            raise RuntimeError("transaction nesting state is corrupt")
        failure: BaseException | None = None
        try:
            if frame.rollback_only:
                frame.session.rollback()
            else:
                frame.session.commit()
        except BaseException as caught:
            failure = caught
        try:
            frame.session.close()
        except BaseException as caught:
            if failure is None:
                failure = caught
        finally:
            reset_transaction(lease.token)
            with self._condition:
                self._active_transactions -= 1
                self._condition.notify_all()
        if failure is not None:
            lifecycle_error = self._transaction_component_error(
                failure,
                binding_id=frame.binding_id,
                resource_ref=frame.resource_ref,
                request_id=frame.owner_request_id,
            )
            if lifecycle_error is failure:
                raise lifecycle_error
            raise lifecycle_error from failure

    def refresh_registry(self) -> RegistrySnapshot:
        """Build, physically prove, and atomically install a compatible revision."""

        snapshot = self._begin_operation()
        try:
            bundles = []
            for configured in self._config.schemas.providers:
                provider = self._schema_providers[configured.id]
                bundle = provider.load()
                if (
                    not isinstance(bundle, ResourceBundle)
                    or bundle.provider_id != configured.id
                    or bundle.provider_contract_version != provider.provider_contract_version
                    or bundle.fingerprint != configured.required_fingerprint
                ):
                    raise CompatibilityError(
                        ErrorCode.REGISTRY_REFRESH,
                        f"Schema provider {configured.id!r} changed its pinned bundle",
                    )
                bundles.append(bundle)
            live = self._config.schemas.live
            if live.enabled:
                assert live.provider_id is not None
                loader = getattr(self._schema_providers[live.provider_id], "load_live", None)
                if callable(loader):
                    live_bundle = loader()
                    if not isinstance(live_bundle, ResourceBundle):
                        raise CompatibilityError(
                            ErrorCode.REGISTRY_REFRESH,
                            "live Schema provider returned an invalid Resource bundle",
                        )
                    bundles.append(live_bundle)
                elif live.required:
                    raise UnavailableError(
                        ErrorCode.REGISTRY_REFRESH,
                        "required live Schema metadata is unavailable during refresh",
                    )
            bundle_tuple = tuple(bundles)
            planned = self._plan_physical_resources(bundle_tuple)
            fingerprints: dict[str, str] = {}
            mappings: dict[str, Mapping[str, str]] = {}
            for binding in self._config.bindings:
                verification = self._adapter_runtimes[binding.id].verify_physical(
                    planned[binding.id]
                )
                if not isinstance(verification, PhysicalVerification):
                    raise CompatibilityError(
                        ErrorCode.REGISTRY_REFRESH,
                        f"Binding {binding.id!r} returned invalid physical verification",
                    )
                expected = {str(item.resource_ref) for item in planned[binding.id]}
                if set(verification.mappings) != expected:
                    raise CompatibilityError(
                        ErrorCode.REGISTRY_REFRESH,
                        f"Binding {binding.id!r} returned incomplete refresh mappings",
                    )
                if (
                    binding.required_physical_fingerprint is not None
                    and verification.fingerprint != binding.required_physical_fingerprint
                ):
                    raise CompatibilityError(
                        ErrorCode.REGISTRY_REFRESH,
                        f"Binding {binding.id!r} physical fingerprint changed",
                    )
                fingerprints[binding.id] = verification.fingerprint
                mappings[binding.id] = verification.mappings
            candidate = build_registry(
                bundle_tuple,
                self._config,
                self._catalog_manifests,
                self._capability_manifests,
                fingerprints,
                mappings,
                revision=snapshot.revision + 1,
            )
            self._registry.swap_compatible(candidate)
            self._bundles = bundle_tuple
            self._emit(
                "meridian.registry.refreshed",
                {
                    "revision": str(candidate.revision),
                    "registryFingerprint": candidate.fingerprint,
                },
            )
            return candidate
        finally:
            self._end_operation()

    def plugin(self, plugin_id: str) -> object:
        self._require_ready()
        if plugin_id in self._plugins:
            return self._plugins[plugin_id]
        factory = self._plugin_factories.get(plugin_id)
        if factory is None:
            raise UnavailableError(
                ErrorCode.PLUGIN_NOT_FOUND,
                f"Plugin {plugin_id!r} is not installed",
            )
        with self._condition:
            if plugin_id not in self._plugins:
                try:
                    self._plugins[plugin_id] = factory.create(self)
                except BaseException as exc:
                    raise InternalError(
                        ErrorCode.PLUGIN_CONTRACT,
                        f"Plugin {plugin_id!r} could not be created",
                        cause=SafeCause.from_exception(exc),
                    ) from exc
            return self._plugins[plugin_id]

    def close(self) -> None:
        """Drain accepted work and close Adapter runtimes in reverse order."""

        self._check_process()
        with self._condition:
            while self._state is RuntimeState.STARTING:
                self._condition.wait()
            if self._state in {RuntimeState.CLOSED, RuntimeState.FAILED}:
                return
            if self._state is RuntimeState.NEW:
                self._state = RuntimeState.CLOSED
                self._condition.notify_all()
                return
            if self._state is RuntimeState.DRAINING:
                while self._state is RuntimeState.DRAINING:
                    self._condition.wait()
                return
            self._state = RuntimeState.DRAINING
            while self._active_operations or self._active_transactions:
                self._condition.wait()
        close_error = self._close_opened_adapters(suppress=False)
        with self._condition:
            self._state = RuntimeState.CLOSED
            self._condition.notify_all()
        if close_error is not None:
            raise close_error

    def __enter__(self) -> Meridian:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        self.close()

    def _close_opened_adapters(self, *, suppress: bool) -> MeridianError | None:
        first: MeridianError | None = None
        for binding_id in reversed(self._opened_bindings):
            try:
                self._adapter_runtimes[binding_id].close()
            except BaseException as exc:
                if first is None:
                    first = InternalError(
                        ErrorCode.ADAPTER_FAILURE,
                        f"Adapter runtime for Binding {binding_id!r} failed during close",
                        cause=SafeCause.from_exception(exc),
                    )
        self._opened_bindings.clear()
        return None if suppress else first

    def _begin_operation(self) -> RegistrySnapshot:
        self._check_process()
        with self._condition:
            self._require_ready_locked()
            snapshot = self._registry.snapshot()
            self._active_operations += 1
            return snapshot

    def _end_operation(self) -> None:
        with self._condition:
            self._active_operations -= 1
            self._condition.notify_all()

    def _snapshot_for_handle(self) -> RegistrySnapshot:
        self._require_ready()
        return self._registry.snapshot()

    def _require_ready(self) -> None:
        self._check_process()
        with self._condition:
            self._require_ready_locked()

    def _require_ready_locked(self) -> None:
        if self._state is not RuntimeState.READY:
            code = (
                ErrorCode.RUNTIME_CLOSED
                if self._state is RuntimeState.CLOSED
                else ErrorCode.RUNTIME_STATE
            )
            raise LifecycleError(
                code,
                f"Meridian operation requires READY state, not {self._state.value}",
            )

    @staticmethod
    def _require_catalog_name(name: str) -> None:
        if name not in REGISTERED_CATALOGS:
            raise CatalogNotFound(name)

    def _check_process(self) -> None:
        if os.getpid() != self._pid:
            raise LifecycleError(
                ErrorCode.RUNTIME_STATE,
                "Meridian runtime cannot be reused after process fork",
            )

    def _startup_error(self, exc: BaseException) -> MeridianError:
        if isinstance(exc, MeridianError):
            return exc
        return LifecycleError(
            ErrorCode.RUNTIME_STARTUP,
            "Meridian startup failed in an internal component",
            cause=SafeCause.from_exception(exc),
        )

    def _operation_error(
        self,
        exc: BaseException,
        *,
        operation: Operation | None,
        request_id: str | None,
        execution_id: str,
        snapshot: RegistrySnapshot,
    ) -> MeridianError:
        if isinstance(exc, MeridianError):
            error = exc
        else:
            error = InternalError(
                ErrorCode.ADAPTER_FAILURE,
                "an internal component failed during Operation execution",
                cause=SafeCause.from_exception(exc),
            )
        if error.operation_contract is None and operation is not None:
            error.operation_contract = operation.operation_contract
        if error.resource_ref is None and operation is not None and operation.resources:
            error.resource_ref = str(operation.resources[0])
        if error.request_id is None:
            error.request_id = request_id
        if error.execution_id is None:
            error.execution_id = execution_id
        if not error.adapter_provenance and operation is not None:
            try:
                binding = snapshot.binding_for(operation.resources[0])
            except MeridianError:
                pass
            else:
                error.adapter_provenance = MappingProxyType(
                    {
                        "adapterId": binding.adapter_id,
                        "capabilityFingerprint": binding.capability_fingerprint,
                    }
                )
        return error

    def _transaction_component_error(
        self,
        exc: BaseException,
        *,
        binding_id: str,
        resource_ref: str,
        request_id: str,
    ) -> MeridianError:
        if isinstance(exc, MeridianError):
            error = exc
        else:
            error = InternalError(
                ErrorCode.ADAPTER_FAILURE,
                "an Adapter failed during transaction lifecycle processing",
                cause=SafeCause.from_exception(exc),
            )
        if error.resource_ref is None:
            error.resource_ref = resource_ref
        if error.request_id is None:
            error.request_id = request_id
        if not error.adapter_provenance:
            manifest = self._capability_manifests.get(binding_id)
            if manifest is not None:
                error.adapter_provenance = MappingProxyType(
                    {
                        "adapterId": manifest.adapter_id,
                        "capabilityFingerprint": manifest.fingerprint,
                    }
                )
        return error

    def _emit(self, name: str, attributes: Mapping[str, str]) -> None:
        if not self._config.telemetry.enabled or _in_observer.get():
            return
        token = _in_observer.set(True)
        try:
            self._observer.emit(name, MappingProxyType(dict(attributes)))
        except BaseException:
            return
        finally:
            _in_observer.reset(token)


def _merge_requirements(
    requirements: Iterable[CapabilityRequirement],
) -> tuple[CapabilityRequirement, ...]:
    grouped: dict[tuple[str, str], tuple[set[str], dict[str, int]]] = {}
    for requirement in requirements:
        key = (requirement.operation_contract, requirement.operation_version)
        guarantees, limits = grouped.setdefault(key, (set(), {}))
        guarantees.update(requirement.guarantees)
        for name, value in requirement.minimum_limits.items():
            limits[name] = max(value, limits.get(name, 0))
    return tuple(
        CapabilityRequirement(
            operation_contract=contract,
            operation_version=version,
            guarantees=tuple(sorted(guarantees)),
            minimum_limits=limits,
        )
        for (contract, version), (guarantees, limits) in sorted(grouped.items())
    )


__all__ = [
    "CORE_CONTRACT_VERSION",
    "TRANSACTION_OPERATION_CONTRACT",
    "TRANSACTION_OPERATION_VERSION",
    "Meridian",
]
