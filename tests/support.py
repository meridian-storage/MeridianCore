# SPDX-License-Identifier: Apache-2.0
"""Deterministic test providers; these do not claim Adapter release conformance."""

from __future__ import annotations

import copy
import threading
from collections.abc import Mapping
from dataclasses import dataclass

from meridian_storage import Expression, Meridian, Operation, OperationContext, ResourceRef
from meridian_storage._canonical import sha256_fingerprint
from meridian_storage._types import JsonValue
from meridian_storage.registry import (
    NamespaceDefinition,
    ResourceBundle,
    ResourceDefinition,
    SchemaDefinition,
    SchemaRef,
)
from meridian_storage.runtime import CatalogManifest, OperationContract, RuntimeConfig
from meridian_storage.runtime.runtime import (
    TRANSACTION_OPERATION_CONTRACT,
    TRANSACTION_OPERATION_VERSION,
)
from meridian_storage.spi import (
    AdapterCreateContext,
    AdapterDescriptor,
    AdapterProbe,
    CapabilityManifest,
    ExecutionRequest,
    ExecutionResult,
    OperationCapability,
    PhysicalResource,
    PhysicalVerification,
    PluginManifest,
    SecretValue,
)

STRUCTURED_METHODS = {
    "aggregate": (True, "always"),
    "create_resource": (False, "always"),
    "delete": (False, "conditional"),
    "get": (True, "always"),
    "patch": (False, "conditional"),
    "publish_schema": (False, "always"),
    "put": (False, "always"),
    "query": (True, "always"),
    "search": (True, "always"),
    "traverse": (True, "always"),
}


def catalog_manifest() -> CatalogManifest:
    return CatalogManifest(
        catalog_name="structured",
        package_name="test-catalog",
        package_version="1.0.0",
        catalog_contract_version="1.0.0",
        operations=tuple(
            OperationContract(
                method=method,
                operation_contract=f"meridian.structured.{method}",
                operation_version="1.0.0",
                read_only=read_only,
                idempotency=idempotency,
            )
            for method, (read_only, idempotency) in STRUCTURED_METHODS.items()
        ),
    )


class FakeStructuredSurface:
    catalog_name = "structured"

    def _expression(self, method: str, arguments: Mapping[str, JsonValue]) -> Expression:
        return Expression(self.catalog_name, method, arguments)

    def publish_schema(self, **arguments: JsonValue) -> Expression:
        return self._expression("publish_schema", arguments)

    def create_resource(self, **arguments: JsonValue) -> Expression:
        return self._expression("create_resource", arguments)

    def put(self, *, resource: str, data: Mapping[str, JsonValue]) -> Expression:
        return self._expression("put", {"resource": resource, "data": data})

    def get(self, *, resource: str, where: Mapping[str, JsonValue]) -> Expression:
        return self._expression("get", {"resource": resource, "where": where})

    def patch(
        self,
        *,
        resource: str,
        where: Mapping[str, JsonValue],
        changes: Mapping[str, JsonValue],
        expected_version: int | None = None,
    ) -> Expression:
        return self._expression(
            "patch",
            {
                "resource": resource,
                "where": where,
                "changes": changes,
                "expectedVersion": expected_version,
            },
        )

    def delete(self, **arguments: JsonValue) -> Expression:
        return self._expression("delete", arguments)

    def query(self, *, resource: str, where: Mapping[str, JsonValue]) -> Expression:
        return self._expression("query", {"resource": resource, "where": where})

    def search(self, **arguments: JsonValue) -> Expression:
        return self._expression("search", arguments)

    def aggregate(self, **arguments: JsonValue) -> Expression:
        return self._expression("aggregate", arguments)

    def traverse(
        self,
        *,
        resources: tuple[str, ...],
        start: Mapping[str, JsonValue],
    ) -> Expression:
        return self._expression("traverse", {"resources": resources, "start": start})


class FakeCatalogProvider:
    catalog_name = "structured"

    def __init__(self) -> None:
        self._manifest = catalog_manifest()
        self.surface = FakeStructuredSurface()
        self.normalized: list[Expression] = []

    def manifest(self) -> CatalogManifest:
        return self._manifest

    def create_surface(self) -> FakeStructuredSurface:
        return self.surface

    def normalize(self, expression: Expression) -> Operation:
        self.normalized.append(expression)
        contract = self._manifest.operation_for(expression.method)
        raw_resources = expression.arguments.get("resources")
        if raw_resources is None:
            raw_resource = expression.arguments.get("resource", "investigation.cases")
            resources = (ResourceRef.parse(str(raw_resource), catalog="structured"),)
        else:
            resources = tuple(
                ResourceRef.parse(str(value), catalog="structured") for value in raw_resources
            )
        if contract.idempotency == "always":
            idempotent = True
        elif contract.idempotency == "never":
            idempotent = False
        else:
            idempotent = expression.arguments.get("expectedVersion") is not None
        return Operation(
            catalog="structured",
            operation_contract=contract.operation_contract,
            operation_version=contract.operation_version,
            resources=resources,
            input=expression.arguments,
            requirements=(contract.requirement,),
            read_only=contract.read_only,
            idempotent=idempotent,
        )


def capability_manifest(adapter_id: str = "test.adapter") -> CapabilityManifest:
    operation_capabilities = (
        *(
            OperationCapability(
                operation_contract=f"meridian.structured.{method}",
                operation_versions=("1.0.0",),
                guarantees=("bounded",),
                limits={"maxResultBytes": 10_000_000},
                cursor_behavior=("opaque" if method in {"query", "search", "traverse"} else "none"),
                migration_behavior="external",
            )
            for method in sorted(STRUCTURED_METHODS)
        ),
        OperationCapability(
            TRANSACTION_OPERATION_CONTRACT,
            (TRANSACTION_OPERATION_VERSION,),
            guarantees=("atomic", "no-dirty-reads"),
        ),
    )
    descriptor = AdapterDescriptor(
        adapter_id=adapter_id,
        adapter_contract_version="1.0.0",
        driver="test-driver-1.0.0",
        supported_engine_versions={"test-engine": ("1.0.0",)},
        capabilities=operation_capabilities,
    )
    return CapabilityManifest(descriptor, "test-engine", "1.0.0")


def bundle_for(
    *,
    provider_id: str = "test.schemas",
    resource_names: tuple[str, ...] = ("cases",),
    required_scope: tuple[str, ...] = ("workspace",),
) -> ResourceBundle:
    namespace = NamespaceDefinition("structured", "investigation", {"owner": "test"})
    schema_ref = SchemaRef("structured", "investigation", "case", "1.0.0")
    schema = SchemaDefinition(
        schema_ref,
        {
            "fields": {
                "id": {"type": "string"},
                "title": {"type": "string"},
            },
            "identity": ["id"],
        },
    )
    requirements = tuple(item.requirement for item in catalog_manifest().operations)
    resources = tuple(
        ResourceDefinition(
            ResourceRef("structured", "investigation", name),
            profile="relational",
            schema=schema_ref,
            labels={"tier": "primary" if index == 0 else "secondary"},
            requirements=requirements,
            required_scope=required_scope,
        )
        for index, name in enumerate(resource_names)
    )
    return ResourceBundle(
        provider_id=provider_id,
        provider_version="1.0.0",
        provider_contract_version="1.0.0",
        namespaces=(namespace,),
        schemas=(schema,),
        resources=resources,
    )


class StaticSchemaProvider:
    def __init__(self, bundle: ResourceBundle) -> None:
        self._bundle = bundle
        self.load_count = 0
        self.live_bundle: ResourceBundle | None = None

    @property
    def provider_id(self) -> str:
        return self._bundle.provider_id

    @property
    def provider_contract_version(self) -> str:
        return self._bundle.provider_contract_version

    def load(self) -> ResourceBundle:
        self.load_count += 1
        return self._bundle

    def load_live(self) -> ResourceBundle:
        if self.live_bundle is None:
            return ResourceBundle(
                self.provider_id + ".live",
                "1.0.0",
                "1.0.0",
            )
        return self.live_bundle

    def replace(self, bundle: ResourceBundle) -> None:
        self._bundle = bundle


class StaticSecretResolver:
    def __init__(self, value: bytes = b"test-secret") -> None:
        self.value = value
        self.references: list[tuple[str, str]] = []

    def resolve(self, reference: object) -> SecretValue:
        self.references.append((reference.provider, reference.reference))  # type: ignore[attr-defined]
        return SecretValue(self.value)


class RecordingObserver:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, str]]] = []

    def emit(self, name: str, attributes: Mapping[str, str]) -> None:
        self.events.append((name, dict(attributes)))


class FakeSession:
    def __init__(self, runtime: FakeAdapterRuntime, transactional: bool) -> None:
        self.runtime = runtime
        self.transactional = transactional
        self.snapshot: dict[str, JsonValue] | None = None
        self.closed = False

    def begin(self) -> None:
        self.runtime.events.append("session.begin")
        self.snapshot = copy.deepcopy(self.runtime.records)

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.runtime.events.append(f"session.execute:{request.attempt}")
        self.runtime.requests.append(request)
        if self.runtime.block_execute is not None:
            self.runtime.block_execute.set()
            self.runtime.release_execute.wait(timeout=5)
        if self.runtime.execution_failures:
            raise self.runtime.execution_failures.pop(0)
        method = request.operation.operation_contract.rsplit(".", 1)[-1]
        resource = str(request.operation.resources[0])
        payload = request.operation.input
        where = payload.get("where", {})
        record_id = str(where.get("id", "record-1")) if isinstance(where, Mapping) else "record-1"
        key = f"{resource}:{record_id}"
        if method == "put":
            data = dict(payload["data"])  # type: ignore[arg-type]
            record_id = str(data.get("id", "record-1"))
            key = f"{resource}:{record_id}"
            record: JsonValue = {"id": record_id, "version": 1, **data}
            self.runtime.records[key] = record
            return ExecutionResult(record, 64, {"driver": "test"})
        if method == "get":
            return ExecutionResult(
                self.runtime.records.get(key, {"id": record_id, "version": 1}),
                64,
                {"driver": "test"},
            )
        if method == "patch":
            current = dict(self.runtime.records.get(key, {"id": record_id, "version": 0}))  # type: ignore[arg-type]
            current.update(dict(payload["changes"]))  # type: ignore[arg-type]
            current["version"] = int(current["version"]) + 1
            self.runtime.records[key] = current
            return ExecutionResult(current, 64, {"driver": "test"})
        if method == "query":
            rows = [
                value
                for record_key, value in self.runtime.records.items()
                if record_key.startswith(resource)
            ]
            return ExecutionResult(rows, 128, {"cursor": "opaque-test"})
        return ExecutionResult({"ok": True}, self.runtime.result_bytes)

    def commit(self) -> None:
        self.runtime.events.append("session.commit")

    def rollback(self) -> None:
        self.runtime.events.append("session.rollback")
        if self.snapshot is not None:
            self.runtime.records = self.snapshot

    def close(self) -> None:
        self.closed = True
        self.runtime.events.append("session.close")


class FakeAdapterRuntime:
    def __init__(self, manifest: CapabilityManifest, physical_fingerprint: str) -> None:
        self.manifest = manifest
        self.physical_fingerprint = physical_fingerprint
        self.events: list[str] = []
        self.records: dict[str, JsonValue] = {}
        self.requests: list[ExecutionRequest] = []
        self.execution_failures: list[BaseException] = []
        self.fail_open: BaseException | None = None
        self.fail_probe: BaseException | None = None
        self.fail_verify: BaseException | None = None
        self.closed = False
        self.result_bytes = 8
        self.block_execute: threading.Event | None = None
        self.release_execute = threading.Event()

    def open(self) -> None:
        self.events.append("runtime.open")
        if self.fail_open is not None:
            raise self.fail_open

    def probe(self) -> AdapterProbe:
        self.events.append("runtime.probe")
        if self.fail_probe is not None:
            raise self.fail_probe
        return AdapterProbe(self.manifest, {"probe": "authenticated"})

    def verify_physical(self, resources: tuple[PhysicalResource, ...]) -> PhysicalVerification:
        self.events.append("runtime.verify_physical")
        if self.fail_verify is not None:
            raise self.fail_verify
        return PhysicalVerification(
            self.physical_fingerprint,
            {
                str(resource.resource_ref): f"opaque-{index}"
                for index, resource in enumerate(resources)
            },
            {"probe": "read-only"},
        )

    def open_session(self, *, transactional: bool) -> FakeSession:
        self.events.append(f"runtime.open_session:{transactional}")
        return FakeSession(self, transactional)

    def close(self) -> None:
        self.events.append("runtime.close")
        self.closed = True


class FakeAdapterFactory:
    def __init__(self, adapter_id: str = "test.adapter") -> None:
        self._adapter_id = adapter_id
        self.manifest = capability_manifest(adapter_id)
        self.physical_fingerprint = sha256_fingerprint(
            {"adapterId": adapter_id, "physical": "test"}
        )
        self.runtimes: list[FakeAdapterRuntime] = []
        self.create_contexts: list[AdapterCreateContext] = []
        self.runtime_initializer: object | None = None

    @property
    def adapter_id(self) -> str:
        return self._adapter_id

    def create(self, context: AdapterCreateContext) -> FakeAdapterRuntime:
        self.create_contexts.append(context)
        runtime = FakeAdapterRuntime(self.manifest, self.physical_fingerprint)
        if callable(self.runtime_initializer):
            self.runtime_initializer(runtime)
        self.runtimes.append(runtime)
        return runtime


@dataclass
class FakePluginFactory:
    plugin_id: str = "test.plugin"
    create_count: int = 0

    def manifest(self) -> PluginManifest:
        return PluginManifest(self.plugin_id, "1.0.0", "1.0.0", "1.x")

    def create(self, meridian: Meridian) -> object:
        self.create_count += 1
        return {"runtimeState": meridian.state.value}


def config_mapping(
    factory: FakeAdapterFactory,
    bundle: ResourceBundle,
    provider: FakeCatalogProvider,
    *,
    binding_id: str = "primary",
    resource_names: tuple[str, ...] | None = None,
    physical_fingerprint: str | object | None = ...,
    max_result_bytes: int = 1024,
    telemetry: bool = False,
) -> dict[str, object]:
    selected = resource_names or tuple(item.ref.name for item in bundle.resources)
    if physical_fingerprint is ...:
        physical_fingerprint = factory.physical_fingerprint
    pins = [item for item in bundle.resources if item.ref.name in selected]
    return {
        "formatVersion": "meridian-config.v1",
        "profile": "test",
        "catalogs": {
            "providers": [
                {
                    "name": "structured",
                    "package": "test-catalog",
                    "contract": "1.x",
                    "requiredFingerprint": provider.manifest().fingerprint,
                }
            ],
            "extensions": {},
        },
        "resources": {
            "pins": [
                {
                    "ref": item.ref.to_dict(),
                    "providerId": bundle.provider_id,
                    "requiredFingerprint": item.fingerprint,
                }
                for item in pins
            ],
            "extensions": {},
        },
        "schemas": {
            "providers": [
                {
                    "id": bundle.provider_id,
                    "package": "test-schemas",
                    "contract": "1.x",
                    "requiredFingerprint": bundle.fingerprint,
                }
            ],
            "live": {"enabled": False, "required": False, "providerId": None},
            "extensions": {},
        },
        "bindings": [
            {
                "id": binding_id,
                "adapterId": factory.adapter_id,
                "adapterContract": "1.x",
                "engineProfile": "test-engine",
                "engineVersion": "1.0.0",
                "endpoint": "memory://logical-endpoint",
                "serviceRef": None,
                "physicalNamespace": "test-namespace",
                "tls": {
                    "mode": "disabled",
                    "serverName": None,
                    "caRef": None,
                    "clientCertificateRef": None,
                },
                "identityRef": {"provider": "test", "reference": "opaque-identity"},
                "secretRef": {"provider": "test", "reference": "opaque-secret"},
                "client": {
                    "minSize": 0,
                    "maxSize": 4,
                    "acquireTimeoutMs": 1000,
                    "idleTimeoutMs": 1000,
                    "operationTimeoutMs": 5000,
                    "maxResultBytes": max_result_bytes,
                    "iteratorLifetimeMs": 5000,
                },
                "requiredCapabilityFingerprint": factory.manifest.fingerprint,
                "requiredPhysicalFingerprint": physical_fingerprint,
                "compatibilityPins": {
                    "coreVersion": "1.0.0",
                    "driver": "test-driver-1.0.0",
                },
                "settings": {},
                "extensions": {},
            }
        ],
        "placements": [
            {
                "id": "primary-placement",
                "selector": {
                    "resources": [item.ref.to_dict() for item in pins],
                    "catalog": None,
                    "labels": {},
                },
                "bindingId": binding_id,
                "extensions": {},
            }
        ],
        "validation": {
            "strict": True,
            "requirePhysicalFingerprints": physical_fingerprint is not None,
            "defaultOperationTimeoutMs": 5000,
            "idempotencyCacheEntries": 64,
            "retry": {
                "maxAttempts": 3,
                "baseDelayMs": 0,
                "maxDelayMs": 0,
                "jitterRatio": 0,
            },
        },
        "telemetry": {
            "enabled": telemetry,
            "serviceName": "test-runtime" if telemetry else None,
            "suppressExporterRecursion": True,
            "attributes": {},
            "extensions": {},
        },
        "extensions": {},
    }


def make_runtime(
    *,
    resource_names: tuple[str, ...] = ("cases",),
    required_scope: tuple[str, ...] = ("workspace",),
    plugin: FakePluginFactory | None = None,
    telemetry: bool = False,
) -> tuple[
    Meridian,
    FakeAdapterFactory,
    FakeCatalogProvider,
    StaticSchemaProvider,
    RecordingObserver,
]:
    factory = FakeAdapterFactory()
    catalog = FakeCatalogProvider()
    bundle = bundle_for(resource_names=resource_names, required_scope=required_scope)
    schema_provider = StaticSchemaProvider(bundle)
    observer = RecordingObserver()
    runtime = Meridian.from_config(
        RuntimeConfig.from_mapping(config_mapping(factory, bundle, catalog, telemetry=telemetry)),
        adapter_factories=(factory,),
        catalog_providers=(catalog,),
        schema_providers=(schema_provider,),
        plugin_factories=() if plugin is None else (plugin,),
        secret_resolver=StaticSecretResolver(),
        observer=observer if telemetry else None,
        _sleep=lambda _: None,
        _random=lambda: 0.5,
    )
    return runtime, factory, catalog, schema_provider, observer


def context(
    *, request_id: str | None = None, idempotency_key: str | None = None
) -> OperationContext:
    return OperationContext(
        principal_ref="principal:test",
        request_id=request_id,
        tenant="tenant-test",
        scope={"workspace": "workspace-test"},
        idempotency_key=idempotency_key,
    )
