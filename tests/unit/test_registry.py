# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from copy import deepcopy

import pytest

from meridian_storage import (
    CompatibilityError,
    ErrorCode,
    NotFoundError,
    RuntimeConfig,
    ValidationError,
)
from meridian_storage.registry import (
    BindingRecord,
    NamespaceDefinition,
    Registry,
    RegistrySnapshot,
    ResourceBundle,
    ResourceDefinition,
    ResourceRef,
    SchemaDefinition,
    SchemaRef,
    build_registry,
)
from meridian_storage.registry.registry import _reject_schema_cycles, _validate_references
from meridian_storage.runtime import CatalogManifest
from meridian_storage.spi import CapabilityManifest, OperationCapability
from tests.support import (
    FakeAdapterFactory,
    FakeCatalogProvider,
    bundle_for,
    config_mapping,
    make_runtime,
)


def test_registry_and_handles_expose_only_logical_metadata() -> None:
    runtime, _, _, _, _ = make_runtime()
    runtime.start()
    namespace = runtime.namespace("structured", "investigation")
    assert namespace.catalog == "structured"
    assert namespace.name == "investigation"
    assert namespace.labels == {"owner": "test"}
    assert [item.ref.name for item in namespace.resources()] == ["cases"]
    schema = namespace.schema("case")
    assert schema.ref == SchemaRef("structured", "investigation", "case", "1.0.0")
    assert schema.fingerprint.startswith("sha256:")
    assert schema.definition["identity"] == ("id",)
    assert [item.ref.name for item in namespace.schemas()] == ["case"]
    resource = namespace.resource("cases")
    assert resource.ref == ResourceRef("structured", "investigation", "cases")
    assert resource.profile == "relational"
    assert resource.labels == {"tier": "primary"}
    assert resource.fingerprint.startswith("sha256:")
    assert resource.schema is not None
    assert not hasattr(resource, "binding")
    assert not hasattr(resource, "physical_mapping")
    assert runtime.resource("investigation.cases").ref == resource.ref
    assert runtime.resource("investigation.cases", catalog="structured").ref == resource.ref
    runtime.close()


def test_handles_require_ready_runtime_and_unknown_catalog_is_typed() -> None:
    runtime, _, _, _, _ = make_runtime()
    with pytest.raises(Exception) as unavailable:
        runtime.namespace("structured", "investigation")
    assert unavailable.value.code == ErrorCode.RUNTIME_STATE
    with pytest.raises(Exception) as unknown:
        runtime.namespace("query", "anything")
    assert unknown.value.code == ErrorCode.CATALOG_NOT_FOUND


def test_snapshot_schema_latest_and_not_found() -> None:
    namespace = NamespaceDefinition("structured", "n")
    first_ref = SchemaRef("structured", "n", "schema", "1.0.0")
    second_ref = SchemaRef("structured", "n", "schema", "2.0.0")
    first = SchemaDefinition(first_ref, {"version": 1})
    second = SchemaDefinition(second_ref, {"version": 2})
    resource_ref = ResourceRef("structured", "n", "resource")
    resource = ResourceDefinition(resource_ref, "relational", second_ref)
    snapshot = RegistrySnapshot(
        1,
        "sha256:" + "0" * 64,
        {},
        {("structured", "n"): namespace},
        {first_ref: first, second_ref: second},
        {resource_ref: resource},
        {},
    )
    assert snapshot.schema("structured", "n", "schema").ref == second_ref
    assert snapshot.schema("structured", "n", "schema", "1.0.0").ref == first_ref
    with pytest.raises(NotFoundError):
        snapshot.schema("structured", "n", "missing")
    with pytest.raises(NotFoundError):
        snapshot.schema("structured", "n", "schema", "9.0.0")
    with pytest.raises(NotFoundError):
        snapshot.namespace("structured", "missing")


def test_snapshot_falls_back_to_lexical_schema_versions_and_lists_binding_resources() -> None:
    namespace = NamespaceDefinition("structured", "n")
    alpha_ref = SchemaRef("structured", "n", "schema", "draft-a")
    beta_ref = SchemaRef("structured", "n", "schema", "draft-b")
    resource_ref = ResourceRef("structured", "n", "resource")
    record = BindingRecord("primary", "test.adapter", "cap", "physical", "opaque")
    snapshot = RegistrySnapshot(
        1,
        "sha256:" + "0" * 64,
        {},
        {("structured", "n"): namespace},
        {
            alpha_ref: SchemaDefinition(alpha_ref, {"version": "a"}),
            beta_ref: SchemaDefinition(beta_ref, {"version": "b"}),
        },
        {resource_ref: ResourceDefinition(resource_ref, "relational")},
        {resource_ref: record},
    )
    assert snapshot.schema("structured", "n", "schema").ref == beta_ref
    assert snapshot.binding_for(resource_ref) is record
    assert snapshot.resources_for_binding("primary")[0].ref == resource_ref
    assert snapshot.resources_for_binding("absent") == ()


def test_unqualified_resource_resolution_rejects_ambiguity() -> None:
    structured = ResourceRef("structured", "n", "same")
    object_ref = ResourceRef("object", "n", "same")
    snapshot = RegistrySnapshot(
        1,
        "sha256:" + "0" * 64,
        {},
        {},
        {},
        {
            structured: ResourceDefinition(structured, "relational"),
            object_ref: ResourceDefinition(object_ref, "media"),
        },
        {},
    )
    with pytest.raises(ValidationError, match="ambiguous"):
        snapshot.resolve_resource("n.same")
    with pytest.raises(NotFoundError):
        snapshot.resolve_resource("n.missing")


def test_registry_install_and_swap_guards() -> None:
    runtime, _, _, _, _ = make_runtime(required_scope=())
    runtime.start()
    snapshot = runtime._snapshot_for_handle()
    registry = Registry()
    with pytest.raises(ValidationError):
        registry.snapshot()
    registry.install_initial(snapshot)
    with pytest.raises(ValidationError, match="already"):
        registry.install_initial(snapshot)
    assert registry.snapshot() is snapshot
    runtime.close()


def _build_inputs() -> tuple[
    ResourceBundle,
    RuntimeConfig,
    dict[str, CatalogManifest],
    dict[str, CapabilityManifest],
    dict[str, str],
    dict[str, dict[str, str]],
]:
    factory = FakeAdapterFactory()
    provider = FakeCatalogProvider()
    bundle = bundle_for(required_scope=())
    config = RuntimeConfig.from_mapping(config_mapping(factory, bundle, provider))
    return (
        bundle,
        config,
        {"structured": provider.manifest()},
        {"primary": factory.manifest},
        {"primary": factory.physical_fingerprint},
        {
            "primary": {
                str(resource.ref): f"opaque-{index}"
                for index, resource in enumerate(bundle.resources)
            }
        },
    )


def test_build_registry_happy_path_is_deterministic() -> None:
    bundle, config, catalogs, capabilities, fingerprints, mappings = _build_inputs()
    first = build_registry(
        (bundle,), config, catalogs, capabilities, fingerprints, mappings, revision=7
    )
    second = build_registry(
        (bundle,), config, catalogs, capabilities, fingerprints, mappings, revision=7
    )
    assert first.revision == 7
    assert first.fingerprint == second.fingerprint
    assert first.binding_for(bundle.resources[0].ref).binding_id == "primary"


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("missing_catalog", ErrorCode.CATALOG_UNAVAILABLE),
        ("extra_catalog", ErrorCode.CATALOG_UNAVAILABLE),
        ("missing_resource", ErrorCode.REGISTRY_REFERENCE),
        ("wrong_provider", ErrorCode.REGISTRY_REFERENCE),
        ("wrong_fingerprint", ErrorCode.REGISTRY_REFERENCE),
        ("missing_mapping", ErrorCode.PHYSICAL_FINGERPRINT),
    ],
)
def test_build_registry_rejects_invalid_pins_and_evidence(mutation: str, code: ErrorCode) -> None:
    bundle, config, catalogs, capabilities, fingerprints, mappings = _build_inputs()
    if mutation == "missing_catalog":
        catalogs = {}
    elif mutation == "extra_catalog":
        catalogs["object"] = catalogs["structured"]
    elif mutation == "missing_resource":
        bundle = ResourceBundle(
            bundle.provider_id,
            bundle.provider_version,
            bundle.provider_contract_version,
            bundle.namespaces,
            bundle.schemas,
            (),
        )
    elif mutation == "wrong_provider":
        bundle = ResourceBundle(
            "unexpected",
            bundle.provider_version,
            bundle.provider_contract_version,
            bundle.namespaces,
            bundle.schemas,
            bundle.resources,
        )
    elif mutation == "wrong_fingerprint":
        changed = ResourceDefinition(
            bundle.resources[0].ref,
            bundle.resources[0].profile,
            bundle.resources[0].schema,
            {"changed": "yes"},
            bundle.resources[0].requirements,
        )
        bundle = ResourceBundle(
            bundle.provider_id,
            bundle.provider_version,
            bundle.provider_contract_version,
            bundle.namespaces,
            bundle.schemas,
            (changed,),
        )
    else:
        mappings = {"primary": {}}
    with pytest.raises(Exception) as failure:
        build_registry(
            (bundle,), config, catalogs, capabilities, fingerprints, mappings, revision=1
        )
    assert failure.value.code == code


def test_build_registry_rejects_unresolved_ambiguous_and_unsupported_placements() -> None:
    bundle, config, catalogs, capabilities, fingerprints, mappings = _build_inputs()
    unresolved_mapping = config_mapping(FakeAdapterFactory(), bundle, FakeCatalogProvider())
    unresolved_mapping["placements"][0]["selector"]["resources"] = [  # type: ignore[index]
        ResourceRef("structured", "investigation", "other").to_dict()
    ]
    unresolved = RuntimeConfig.from_mapping(unresolved_mapping)
    with pytest.raises(Exception) as failure:
        build_registry(
            (bundle,), unresolved, catalogs, capabilities, fingerprints, mappings, revision=1
        )
    assert failure.value.code == ErrorCode.PLACEMENT_UNRESOLVED

    ambiguous_mapping = config_mapping(FakeAdapterFactory(), bundle, FakeCatalogProvider())
    ambiguous_mapping["placements"].append(  # type: ignore[union-attr,index]
        deepcopy(ambiguous_mapping["placements"][0])  # type: ignore[index]
    )
    ambiguous_mapping["placements"][1]["id"] = "duplicate"  # type: ignore[index]
    ambiguous = RuntimeConfig.from_mapping(ambiguous_mapping)
    with pytest.raises(Exception) as failure:
        build_registry(
            (bundle,), ambiguous, catalogs, capabilities, fingerprints, mappings, revision=1
        )
    assert failure.value.code == ErrorCode.PLACEMENT_AMBIGUOUS

    descriptor = capabilities["primary"].descriptor
    insufficient = CapabilityManifest(
        type(descriptor)(
            descriptor.adapter_id,
            descriptor.adapter_contract_version,
            descriptor.driver,
            descriptor.supported_engine_versions,
            (OperationCapability("meridian.structured.get", ("1.0.0",)),),
        ),
        "test-engine",
        "1.0.0",
    )
    with pytest.raises(CompatibilityError) as failure:
        build_registry(
            (bundle,),
            config,
            catalogs,
            {"primary": insufficient},
            fingerprints,
            mappings,
            revision=1,
        )
    assert failure.value.code == ErrorCode.CAPABILITY_UNSUPPORTED


def test_reference_and_cycle_validation_is_fail_closed() -> None:
    namespace = NamespaceDefinition("structured", "n")
    first_ref = SchemaRef("structured", "n", "first", "1.0.0")
    second_ref = SchemaRef("structured", "n", "second", "1.0.0")
    resource_ref = ResourceRef("structured", "n", "resource")

    with pytest.raises(Exception, match="missing Namespace"):
        _validate_references({}, {first_ref: SchemaDefinition(first_ref, {})}, {})
    with pytest.raises(Exception, match="missing dependency"):
        _validate_references(
            {("structured", "n"): namespace},
            {first_ref: SchemaDefinition(first_ref, {}, (second_ref,))},
            {},
        )
    with pytest.raises(Exception, match="missing Namespace"):
        _validate_references({}, {}, {resource_ref: ResourceDefinition(resource_ref, "relational")})
    with pytest.raises(Exception, match="missing Schema"):
        _validate_references(
            {("structured", "n"): namespace},
            {},
            {resource_ref: ResourceDefinition(resource_ref, "relational", first_ref)},
        )
    related = ResourceRef("structured", "n", "missing")
    with pytest.raises(Exception, match="missing related Resource"):
        _validate_references(
            {("structured", "n"): namespace},
            {},
            {
                resource_ref: ResourceDefinition(
                    resource_ref, "relational", related_resources=(related,)
                )
            },
        )

    cyclic = {
        first_ref: SchemaDefinition(first_ref, {}, (second_ref,)),
        second_ref: SchemaDefinition(second_ref, {}, (first_ref,)),
    }
    with pytest.raises(Exception) as cycle:
        _reject_schema_cycles(cyclic)
    assert cycle.value.code == ErrorCode.REGISTRY_CYCLE
    _reject_schema_cycles({first_ref: SchemaDefinition(first_ref, {})})


def test_registry_swap_rejects_removal_schema_change_and_binding_move() -> None:
    bundle, config, catalogs, capabilities, fingerprints, mappings = _build_inputs()
    current = build_registry(
        (bundle,), config, catalogs, capabilities, fingerprints, mappings, revision=1
    )
    registry = Registry()
    registry.install_initial(current)

    empty = RegistrySnapshot(2, current.fingerprint, catalogs, current.namespaces, {}, {}, {})
    with pytest.raises(CompatibilityError, match="remove"):
        registry.swap_compatible(empty)

    ref = bundle.resources[0].ref
    changed_resource = ResourceDefinition(ref, bundle.resources[0].profile)
    schema_changed = RegistrySnapshot(
        2,
        current.fingerprint,
        catalogs,
        current.namespaces,
        current.schemas,
        {ref: changed_resource},
        {ref: current.binding_for(ref)},
    )
    with pytest.raises(CompatibilityError, match="Schema"):
        registry.swap_compatible(schema_changed)

    record = current.binding_for(ref)
    moved = RegistrySnapshot(
        2,
        current.fingerprint,
        catalogs,
        current.namespaces,
        current.schemas,
        current.resources,
        {
            ref: BindingRecord(
                "secondary",
                record.adapter_id,
                record.capability_fingerprint,
                record.physical_fingerprint,
                "opaque",
            )
        },
    )
    with pytest.raises(CompatibilityError, match="Binding"):
        registry.swap_compatible(moved)

    replacement = RegistrySnapshot(
        2,
        current.fingerprint,
        catalogs,
        current.namespaces,
        current.schemas,
        current.resources,
        current._bindings,
    )
    assert registry.swap_compatible(replacement) is current
    assert registry.snapshot() is replacement
