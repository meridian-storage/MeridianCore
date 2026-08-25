# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import cast

import pytest

from meridian_storage import (
    CompatibilityError,
    ErrorCode,
    Meridian,
    RuntimeConfig,
    RuntimeState,
)
from meridian_storage.registry import ResourceBundle, ResourceDefinition, SchemaRef
from meridian_storage.runtime import CatalogManifest
from meridian_storage.spi import (
    AdapterDescriptor,
    CapabilityManifest,
    PhysicalVerification,
    PluginManifest,
)
from tests.support import (
    FakeAdapterFactory,
    FakeCatalogProvider,
    FakePluginFactory,
    StaticSchemaProvider,
    StaticSecretResolver,
    bundle_for,
    config_mapping,
    make_runtime,
)


def _runtime_for(
    factory: object,
    catalog: FakeCatalogProvider,
    bundle: ResourceBundle,
    schema_provider: object,
    *,
    plugin_factories: tuple[object, ...] = (),
    mapping: dict[str, object] | None = None,
) -> Meridian:
    return Meridian.from_config(
        RuntimeConfig.from_mapping(
            config_mapping(
                cast(FakeAdapterFactory, factory),
                bundle,
                catalog,
            )
            if mapping is None
            else mapping
        ),
        adapter_factories=(factory,),
        catalog_providers=(catalog,),
        schema_providers=(schema_provider,),
        plugin_factories=plugin_factories,
        secret_resolver=StaticSecretResolver(),
        _sleep=lambda _: None,
    )


def test_startup_report_proves_discovery_binding_and_resources() -> None:
    runtime, factory, catalog, schemas, _ = make_runtime()
    report = runtime.start()
    assert runtime.state is RuntimeState.READY
    assert report.profile == "test"
    assert report.registry_revision == 1
    assert report.registry_fingerprint.startswith("sha256:")
    assert report.catalogs == ("structured",)
    assert report.adapters == ("test.adapter",)
    assert report.schema_providers == ("test.schemas",)
    assert report.resources == ("structured:investigation.cases",)
    assert report.bindings[0].engine_profile == "test-engine"
    assert report.to_dict()["catalogs"] == ["structured"]
    assert schemas.load_count == 1
    assert len(factory.runtimes) == 1
    assert catalog.surface is runtime.catalog("structured")
    assert runtime.start() is report
    runtime.close()


def test_constructor_and_environment_loader_enforce_runtime_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(TypeError, match="RuntimeConfig"):
        Meridian(cast(RuntimeConfig, object()))

    factory = FakeAdapterFactory()
    catalog = FakeCatalogProvider()
    bundle = bundle_for()
    path = tmp_path / "meridian.json"
    path.write_text(json.dumps(config_mapping(factory, bundle, catalog)), encoding="utf-8")
    monkeypatch.setenv("MERIDIAN_CONFIG", str(path))
    runtime = Meridian.from_environment(
        adapter_factories=(factory,),
        catalog_providers=(catalog,),
        schema_providers=(StaticSchemaProvider(bundle),),
        secret_resolver=StaticSecretResolver(),
    )
    assert runtime.start().profile == "test"
    runtime.close()


def test_concurrent_start_has_one_winner_and_shared_report() -> None:
    runtime, factory, _, _, _ = make_runtime()
    reports: list[object] = []
    failures: list[BaseException] = []

    def start() -> None:
        try:
            reports.append(runtime.start())
        except BaseException as exc:
            failures.append(exc)

    threads = [threading.Thread(target=start) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert not failures
    assert len(reports) == 6
    assert len({id(report) for report in reports}) == 1
    assert len(factory.runtimes) == 1
    runtime.close()


@pytest.mark.parametrize("failure_point", ["open", "probe", "verify"])
def test_failed_startup_closes_partial_runtime_and_is_terminal(failure_point: str) -> None:
    runtime, factory, _, _, _ = make_runtime()

    def initialize(adapter: object) -> None:
        setattr(adapter, f"fail_{failure_point}", RuntimeError("vendor secret detail"))

    factory.runtime_initializer = initialize
    with pytest.raises(Exception) as failed:
        runtime.start()
    assert runtime.state is RuntimeState.FAILED
    assert "vendor secret detail" not in str(failed.value.to_dict())
    assert factory.runtimes[0].closed
    with pytest.raises(Exception) as repeated:
        runtime.start()
    assert repeated.value is failed.value
    runtime.close()
    assert runtime.state is RuntimeState.FAILED


def test_startup_rejects_capability_and_physical_fingerprint_drift() -> None:
    factory = FakeAdapterFactory()
    catalog = FakeCatalogProvider()
    bundle = bundle_for()
    schema = StaticSchemaProvider(bundle)
    value = config_mapping(factory, bundle, catalog)
    value["bindings"][0]["requiredCapabilityFingerprint"] = "sha256:" + "0" * 64  # type: ignore[index]
    runtime = Meridian.from_config(
        RuntimeConfig.from_mapping(value),
        adapter_factories=(factory,),
        catalog_providers=(catalog,),
        schema_providers=(schema,),
        secret_resolver=StaticSecretResolver(),
    )
    with pytest.raises(CompatibilityError) as capability:
        runtime.start()
    assert capability.value.code == ErrorCode.CAPABILITY_FINGERPRINT

    factory = FakeAdapterFactory()
    catalog = FakeCatalogProvider()
    value = config_mapping(
        factory,
        bundle,
        catalog,
        physical_fingerprint="sha256:" + "0" * 64,
    )
    runtime = Meridian.from_config(
        RuntimeConfig.from_mapping(value),
        adapter_factories=(factory,),
        catalog_providers=(catalog,),
        schema_providers=(schema,),
        secret_resolver=StaticSecretResolver(),
    )
    with pytest.raises(CompatibilityError) as physical:
        runtime.start()
    assert physical.value.code == ErrorCode.PHYSICAL_FINGERPRINT


def test_startup_rejects_catalog_and_schema_contract_drift() -> None:
    factory = FakeAdapterFactory()
    catalog = FakeCatalogProvider()
    bundle = bundle_for()
    schema = StaticSchemaProvider(bundle)
    value = config_mapping(factory, bundle, catalog)
    value["catalogs"]["providers"][0]["requiredFingerprint"] = "sha256:" + "0" * 64  # type: ignore[index]
    runtime = Meridian.from_config(
        RuntimeConfig.from_mapping(value),
        adapter_factories=(factory,),
        catalog_providers=(catalog,),
        schema_providers=(schema,),
        secret_resolver=StaticSecretResolver(),
    )
    with pytest.raises(CompatibilityError) as failure:
        runtime.start()
    assert failure.value.code == ErrorCode.CATALOG_CONTRACT

    value = config_mapping(factory, bundle, catalog)
    value["schemas"]["providers"][0]["requiredFingerprint"] = "sha256:" + "0" * 64  # type: ignore[index]
    runtime = Meridian.from_config(
        RuntimeConfig.from_mapping(value),
        adapter_factories=(factory,),
        catalog_providers=(catalog,),
        schema_providers=(schema,),
        secret_resolver=StaticSecretResolver(),
    )
    with pytest.raises(CompatibilityError) as failure:
        runtime.start()
    assert failure.value.code == ErrorCode.REGISTRY_REFERENCE


def test_startup_rejects_missing_required_components() -> None:
    factory = FakeAdapterFactory()
    catalog = FakeCatalogProvider()
    bundle = bundle_for()
    value = config_mapping(factory, bundle, catalog)
    config = RuntimeConfig.from_mapping(value)
    missing_catalog = Meridian.from_config(
        config,
        adapter_factories=(factory,),
        schema_providers=(StaticSchemaProvider(bundle),),
        secret_resolver=StaticSecretResolver(),
    )
    with pytest.raises(Exception) as failure:
        missing_catalog.start()
    assert failure.value.code == ErrorCode.CATALOG_UNAVAILABLE

    missing_adapter = Meridian.from_config(
        config,
        catalog_providers=(catalog,),
        schema_providers=(StaticSchemaProvider(bundle),),
        secret_resolver=StaticSecretResolver(),
    )
    with pytest.raises(Exception) as failure:
        missing_adapter.start()
    assert failure.value.code == ErrorCode.ADAPTER_NOT_FOUND

    missing_schema = Meridian.from_config(
        config,
        adapter_factories=(factory,),
        catalog_providers=(catalog,),
        secret_resolver=StaticSecretResolver(),
    )
    with pytest.raises(Exception) as failure:
        missing_schema.start()
    assert failure.value.code == ErrorCode.DISCOVERY_FAILED


def test_startup_rejects_invalid_adapter_runtime_and_physical_evidence() -> None:
    catalog = FakeCatalogProvider()
    bundle = bundle_for()

    class InvalidRuntimeFactory(FakeAdapterFactory):
        def create(self, context: object) -> object:
            del context
            return object()

    invalid_factory = InvalidRuntimeFactory()
    runtime = _runtime_for(invalid_factory, catalog, bundle, StaticSchemaProvider(bundle))
    with pytest.raises(Exception) as invalid:
        runtime.start()
    assert invalid.value.code == ErrorCode.ADAPTER_CONTRACT

    factory = FakeAdapterFactory()

    def omit_mapping(adapter: object) -> None:
        def verify(resources: object) -> PhysicalVerification:
            del resources
            return PhysicalVerification(factory.physical_fingerprint, {}, {})

        adapter.verify_physical = verify  # type: ignore[attr-defined,method-assign]

    factory.runtime_initializer = omit_mapping
    runtime = _runtime_for(factory, catalog, bundle, StaticSchemaProvider(bundle))
    with pytest.raises(Exception) as incomplete:
        runtime.start()
    assert incomplete.value.code == ErrorCode.PHYSICAL_FINGERPRINT


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        ("factory-contract", ErrorCode.ADAPTER_CONTRACT),
        ("probe-envelope", ErrorCode.ADAPTER_CONTRACT),
        ("physical-envelope", ErrorCode.ADAPTER_CONTRACT),
        ("catalog-contract", ErrorCode.CATALOG_CONTRACT),
        ("catalog-manifest", ErrorCode.CATALOG_CONTRACT),
        ("schema-contract", ErrorCode.ADAPTER_CONTRACT),
        ("schema-bundle", ErrorCode.REGISTRY_REFERENCE),
        ("plugin-contract", ErrorCode.PLUGIN_CONTRACT),
        ("plugin-manifest", ErrorCode.PLUGIN_CONTRACT),
    ],
)
def test_startup_rejects_malformed_extension_envelopes(
    failure: str, expected_code: ErrorCode
) -> None:
    factory = FakeAdapterFactory()
    catalog = FakeCatalogProvider()
    bundle = bundle_for()
    mapping = config_mapping(factory, bundle, catalog)
    schema: object = StaticSchemaProvider(bundle)
    plugins: tuple[object, ...] = ()
    adapters: tuple[object, ...] = (factory,)
    catalogs: tuple[object, ...] = (catalog,)

    if failure == "factory-contract":

        class MissingCreate:
            adapter_id = factory.adapter_id

        adapters = (MissingCreate(),)
    elif failure == "probe-envelope":
        factory.runtime_initializer = lambda runtime: setattr(runtime, "probe", lambda: object())
    elif failure == "physical-envelope":
        factory.runtime_initializer = lambda runtime: setattr(
            runtime, "verify_physical", lambda resources: object()
        )
    elif failure == "catalog-contract":

        class MissingCatalogMethods:
            catalog_name = "structured"

        catalogs = (MissingCatalogMethods(),)
    elif failure == "catalog-manifest":
        catalog.manifest = lambda: object()  # type: ignore[method-assign]
    elif failure == "schema-contract":

        class MissingSchemaLoad:
            provider_id = bundle.provider_id
            provider_contract_version = bundle.provider_contract_version

        schema = MissingSchemaLoad()
    elif failure == "schema-bundle":

        class InvalidSchemaBundle:
            provider_id = bundle.provider_id
            provider_contract_version = bundle.provider_contract_version

            def load(self) -> object:
                return object()

        schema = InvalidSchemaBundle()
    elif failure == "plugin-contract":

        class MissingPluginMethods:
            plugin_id = "test.plugin"

        plugins = (MissingPluginMethods(),)
    else:

        class InvalidPluginManifest:
            plugin_id = "test.plugin"

            def manifest(self) -> object:
                return object()

            def create(self, meridian: Meridian) -> object:
                del meridian
                return object()

        plugins = (InvalidPluginManifest(),)

    runtime = Meridian.from_config(
        RuntimeConfig.from_mapping(mapping),
        adapter_factories=adapters,
        catalog_providers=catalogs,
        schema_providers=(schema,),
        plugin_factories=plugins,
        secret_resolver=StaticSecretResolver(),
    )
    with pytest.raises(Exception) as rejected:
        runtime.start()
    assert rejected.value.code == expected_code


def test_logical_reference_validation_precedes_physical_adapter_io() -> None:
    factory = FakeAdapterFactory()
    catalog = FakeCatalogProvider()
    original = bundle_for()
    resource = original.resources[0]
    broken = ResourceDefinition(
        resource.ref,
        resource.profile,
        SchemaRef("structured", "investigation", "missing", "1.0.0"),
        resource.labels,
        resource.requirements,
        resource.required_scope,
    )
    bundle = ResourceBundle(
        original.provider_id,
        original.provider_version,
        original.provider_contract_version,
        original.namespaces,
        (),
        (broken,),
    )
    runtime = _runtime_for(factory, catalog, bundle, StaticSchemaProvider(bundle))
    with pytest.raises(Exception) as rejected:
        runtime.start()
    assert rejected.value.code == ErrorCode.REGISTRY_REFERENCE
    assert "runtime.verify_physical" not in factory.runtimes[0].events


@pytest.mark.parametrize("failure", ["identity", "contract", "surface"])
def test_startup_rejects_catalog_provider_contract_failures(failure: str) -> None:
    factory = FakeAdapterFactory()
    catalog = FakeCatalogProvider()
    bundle = bundle_for()
    original = catalog.manifest()
    if failure == "identity":
        catalog._manifest = CatalogManifest(  # type: ignore[attr-defined]
            original.catalog_name,
            "unexpected-package",
            original.package_version,
            original.catalog_contract_version,
            original.operations,
        )
    elif failure == "contract":
        catalog._manifest = CatalogManifest(  # type: ignore[attr-defined]
            original.catalog_name,
            original.package_name,
            original.package_version,
            "2.0.0",
            original.operations,
        )
    else:
        catalog.create_surface = lambda: object()  # type: ignore[method-assign]
    mapping = config_mapping(factory, bundle, FakeCatalogProvider())
    runtime = _runtime_for(
        factory,
        catalog,
        bundle,
        StaticSchemaProvider(bundle),
        mapping=mapping,
    )
    with pytest.raises(Exception) as rejected:
        runtime.start()
    assert rejected.value.code == ErrorCode.CATALOG_CONTRACT


@pytest.mark.parametrize("failure", ["identity", "invalid-range", "incompatible"])
def test_startup_rejects_plugin_contract_failures(failure: str) -> None:
    factory = FakeAdapterFactory()
    catalog = FakeCatalogProvider()
    bundle = bundle_for()

    class InvalidPlugin:
        plugin_id = "test.plugin"

        def manifest(self) -> PluginManifest:
            if failure == "identity":
                return PluginManifest("other.plugin", "1.0.0", "1.0.0", "1.x")
            if failure == "invalid-range":
                return PluginManifest("test.plugin", "1.0.0", "invalid", "1.x")
            return PluginManifest("test.plugin", "1.0.0", "2.0.0", "1.x")

        def create(self, meridian: Meridian) -> object:
            del meridian
            return object()

    runtime = _runtime_for(
        factory,
        catalog,
        bundle,
        StaticSchemaProvider(bundle),
        plugin_factories=(InvalidPlugin(),),
    )
    with pytest.raises(Exception) as rejected:
        runtime.start()
    assert rejected.value.code == ErrorCode.PLUGIN_CONTRACT


@pytest.mark.parametrize("failure", ["invalid-contract", "incompatible", "inconsistent"])
def test_startup_rejects_schema_provider_contract_failures(failure: str) -> None:
    factory = FakeAdapterFactory()
    catalog = FakeCatalogProvider()
    bundle = bundle_for()

    class InvalidSchemaProvider:
        provider_id = bundle.provider_id
        provider_contract_version = "invalid" if failure == "invalid-contract" else "2.0.0"

        def load(self) -> ResourceBundle:
            if failure == "inconsistent":
                return ResourceBundle(
                    "other.schemas",
                    bundle.provider_version,
                    self.provider_contract_version,
                    bundle.namespaces,
                    bundle.schemas,
                    bundle.resources,
                )
            return bundle

    provider: object
    if failure == "inconsistent":
        provider = InvalidSchemaProvider()
        provider.provider_contract_version = bundle.provider_contract_version  # type: ignore[attr-defined]
    else:
        provider = InvalidSchemaProvider()
    runtime = _runtime_for(factory, catalog, bundle, provider)
    with pytest.raises(Exception) as rejected:
        runtime.start()
    assert rejected.value.code in {ErrorCode.ADAPTER_CONTRACT, ErrorCode.REGISTRY_REFERENCE}


def test_live_schema_optional_and_required_paths() -> None:
    factory = FakeAdapterFactory()
    catalog = FakeCatalogProvider()
    bundle = bundle_for()

    class BootstrapOnly:
        provider_id = bundle.provider_id
        provider_contract_version = bundle.provider_contract_version

        def load(self) -> object:
            return bundle

    value = config_mapping(factory, bundle, catalog)
    value["schemas"]["live"] = {  # type: ignore[index]
        "enabled": True,
        "required": False,
        "providerId": bundle.provider_id,
    }
    runtime = Meridian.from_config(
        RuntimeConfig.from_mapping(value),
        adapter_factories=(factory,),
        catalog_providers=(catalog,),
        schema_providers=(BootstrapOnly(),),
        secret_resolver=StaticSecretResolver(),
    )
    report = runtime.start()
    assert any(
        item.stage == "live-schema" and item.status.value == "SKIPPED" for item in report.evidence
    )
    runtime.close()

    value["schemas"]["live"]["required"] = True  # type: ignore[index]
    runtime = Meridian.from_config(
        RuntimeConfig.from_mapping(value),
        adapter_factories=(FakeAdapterFactory(),),
        catalog_providers=(catalog,),
        schema_providers=(BootstrapOnly(),),
        secret_resolver=StaticSecretResolver(),
    )
    with pytest.raises(Exception, match="live Schema"):
        runtime.start()


def test_live_schema_success_and_invalid_bundle_paths() -> None:
    factory = FakeAdapterFactory()
    catalog = FakeCatalogProvider()
    bundle = bundle_for()
    value = config_mapping(factory, bundle, catalog)
    value["schemas"]["live"] = {  # type: ignore[index]
        "enabled": True,
        "required": True,
        "providerId": bundle.provider_id,
    }
    provider = StaticSchemaProvider(bundle)
    provider.live_bundle = ResourceBundle("live.schemas", "1.0.0", "1.0.0")
    runtime = _runtime_for(factory, catalog, bundle, provider, mapping=value)
    assert any(item.stage == "live-schema" for item in runtime.start().evidence)
    runtime.close()

    class InvalidLiveProvider(StaticSchemaProvider):
        def load_live(self) -> object:
            return object()

    factory = FakeAdapterFactory()
    value = config_mapping(factory, bundle, catalog)
    value["schemas"]["live"] = {  # type: ignore[index]
        "enabled": True,
        "required": True,
        "providerId": bundle.provider_id,
    }
    runtime = _runtime_for(
        factory,
        catalog,
        bundle,
        InvalidLiveProvider(bundle),
        mapping=value,
    )
    with pytest.raises(Exception) as invalid:
        runtime.start()
    assert invalid.value.code == ErrorCode.REGISTRY_REFERENCE


@pytest.mark.parametrize(
    "failure",
    ["identity", "invalid-contract", "incompatible", "engine", "compatibility-pin"],
)
def test_startup_rejects_adapter_manifest_contract_failures(failure: str) -> None:
    factory = FakeAdapterFactory()
    original = factory.manifest.descriptor
    adapter_id = "other.adapter" if failure == "identity" else original.adapter_id
    contract = (
        "invalid"
        if failure == "invalid-contract"
        else ("2.0.0" if failure == "incompatible" else original.adapter_contract_version)
    )
    engine_profile = "other-engine" if failure == "engine" else "test-engine"
    descriptor = AdapterDescriptor(
        adapter_id,
        contract,
        original.driver,
        {engine_profile: ("1.0.0",)},
        original.capabilities,
    )
    factory.manifest = CapabilityManifest(descriptor, engine_profile, "1.0.0")
    catalog = FakeCatalogProvider()
    bundle = bundle_for()
    mapping = config_mapping(factory, bundle, catalog)
    if failure == "compatibility-pin":
        mapping["bindings"][0]["compatibilityPins"]["unknownPin"] = "required"  # type: ignore[index]
    runtime = _runtime_for(
        factory,
        catalog,
        bundle,
        StaticSchemaProvider(bundle),
        mapping=mapping,
    )
    with pytest.raises(Exception) as rejected:
        runtime.start()
    assert rejected.value.code == ErrorCode.ADAPTER_CONTRACT


def test_plugin_is_validated_then_created_lazily() -> None:
    plugin = FakePluginFactory()
    runtime, _, _, _, _ = make_runtime(plugin=plugin)
    report = runtime.start()
    assert report.plugins == ("test.plugin",)
    assert plugin.create_count == 0
    first = runtime.plugin("test.plugin")
    assert first == {"runtimeState": "READY"}
    assert runtime.plugin("test.plugin") is first
    assert plugin.create_count == 1
    with pytest.raises(Exception) as missing:
        runtime.plugin("missing")
    assert missing.value.code == ErrorCode.PLUGIN_NOT_FOUND
    runtime.close()


def test_close_is_idempotent_for_new_ready_and_closed() -> None:
    runtime, _, _, _, _ = make_runtime()
    runtime.close()
    assert runtime.state is RuntimeState.CLOSED
    runtime.close()
    with pytest.raises(Exception) as start_closed:
        runtime.start()
    assert start_closed.value.code == ErrorCode.RUNTIME_STATE

    runtime, factory, _, _, _ = make_runtime()
    with runtime:
        assert runtime.state is RuntimeState.READY
    assert runtime.state is RuntimeState.CLOSED
    assert factory.runtimes[0].closed
    runtime.close()


def test_telemetry_records_safe_lifecycle_attributes() -> None:
    runtime, _, _, _, observer = make_runtime(telemetry=True)
    runtime.start()
    assert observer.events[0][0] == "meridian.runtime.ready"
    assert observer.events[0][1] == {"profile": "test"}
    runtime.close()
