# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

import pytest

from meridian_storage._canonical import (
    canonical_json_bytes,
    deep_freeze,
    deep_thaw,
    sha256_fingerprint,
)
from meridian_storage._versions import ContractVersion, contract_matches
from meridian_storage.registry import (
    REGISTERED_CATALOGS,
    CapabilityRequirement,
    NamespaceDefinition,
    ResourceBundle,
    ResourceDefinition,
    ResourceRef,
    SchemaDefinition,
    SchemaRef,
)
from meridian_storage.runtime import (
    CatalogManifest,
    Expression,
    Operation,
    OperationContract,
    OperationResult,
)
from tests.support import catalog_manifest


def test_canonical_json_is_immutable_sorted_and_deterministic() -> None:
    source = {"z": [1, {"x": True}], "a": "é"}
    frozen = deep_freeze(source)
    assert isinstance(frozen, MappingProxyType)
    assert deep_thaw(frozen) == source
    assert canonical_json_bytes(source) == b'{"a":"\xc3\xa9","z":[1,{"x":true}]}'
    assert sha256_fingerprint(source) == sha256_fingerprint({"a": "é", "z": [1, {"x": True}]})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), object(), b"bytes"])
def test_canonical_json_rejects_non_json_values(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        deep_freeze(value)  # type: ignore[arg-type]


def test_canonical_json_rejects_cycles_and_non_string_keys() -> None:
    cyclic: list[object] = []
    cyclic.append(cyclic)
    with pytest.raises(ValueError, match="cyclic"):
        deep_freeze(cyclic)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="keys"):
        deep_freeze({1: "bad"})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("version", "expression", "matches"),
    [
        ("1.2.3", "1.x", True),
        ("1.2.3", "1.2.x", True),
        ("1.2.3", "^1.2", True),
        ("2.0.0", "^1.2", False),
        ("0.2.5", "^0.2.1", True),
        ("1.2.3", "~1.2", True),
        ("1.3.0", "~1.2", False),
        ("1.2.3", ">=1.0,<2.0", True),
        ("1.2.3", "1.2.3", True),
    ],
)
def test_contract_version_ranges(version: str, expression: str, matches: bool) -> None:
    assert contract_matches(version, expression) is matches


def test_contract_versions_order_prereleases() -> None:
    assert ContractVersion.parse("1.0.0-alpha.1") < ContractVersion.parse("1.0.0-alpha.2")
    assert ContractVersion.parse("1.0.0-alpha") < ContractVersion.parse("1.0.0")
    with pytest.raises(ValueError, match="semantic version"):
        ContractVersion.parse("one")
    with pytest.raises(ValueError, match="contract range"):
        contract_matches("1.0.0", ">=1.0,,<2")


def test_resource_and_schema_references_round_trip() -> None:
    ref = ResourceRef("structured", "investigation", "cases")
    assert ref.logical_name == "investigation.cases"
    assert ref.canonical == "structured:investigation.cases"
    assert ResourceRef.parse(ref.to_dict()) == ref
    assert ResourceRef.parse("structured:investigation.cases") == ref
    assert ResourceRef.parse("investigation.cases", catalog="structured") == ref
    schema = SchemaRef("structured", "investigation", "case", "1.0.0")
    assert SchemaRef.parse(schema.to_dict()) == schema
    assert schema.canonical == "structured:investigation.case@1.0.0"
    assert set(REGISTERED_CATALOGS) == {"structured", "object", "cache", "evidence", "streaming"}
    with pytest.raises(ValueError):
        ResourceRef.parse({"catalog": "structured", "namespace": 1, "name": "cases"})
    with pytest.raises(ValueError):
        SchemaRef.parse({"catalog": "structured", "namespace": "n", "name": "s", "version": 1})


@pytest.mark.parametrize(
    "value",
    ["query:x.y", "ontology:x.y", "structured:missing", "structured:"],
)
def test_resource_reference_rejects_invalid_names(value: str) -> None:
    with pytest.raises(ValueError):
        ResourceRef.parse(value)
    with pytest.raises(ValueError):
        ResourceRef.parse("investigation.cases")


def test_capability_requirement_is_closed_and_canonical() -> None:
    requirement = CapabilityRequirement(
        "meridian.structured.query",
        "1.0.0",
        guarantees=("bounded", "stable-cursor"),
        minimum_limits={"maxPageSize": 100},
    )
    assert CapabilityRequirement.from_mapping(requirement.to_dict()) == requirement
    with pytest.raises(ValueError, match="unknown"):
        CapabilityRequirement.from_mapping(
            {
                **requirement.to_dict(),
                "unexpected": True,
            }
        )
    with pytest.raises(ValueError, match="unique"):
        CapabilityRequirement("x", "1.0.0", guarantees=("same", "same"))
    with pytest.raises(ValueError, match="non-negative"):
        CapabilityRequirement("x", "1.0.0", minimum_limits={"size": -1})
    with pytest.raises(ValueError, match="missing"):
        CapabilityRequirement.from_mapping({"operationContract": "x"})
    with pytest.raises(ValueError):
        CapabilityRequirement.from_mapping(
            {
                "operationContract": "x",
                "operationVersion": "1.0.0",
                "guarantees": [1],
            }
        )


def test_logical_definitions_are_immutable_and_fingerprinted() -> None:
    namespace = NamespaceDefinition("structured", "investigation", {"owner": "team"})
    schema_ref = SchemaRef("structured", "investigation", "case", "1.0.0")
    schema = SchemaDefinition(schema_ref, {"fields": {"id": {"type": "string"}}})
    requirement = CapabilityRequirement("meridian.structured.get", "1.0.0")
    resource = ResourceDefinition(
        ResourceRef("structured", "investigation", "cases"),
        "relational",
        schema_ref,
        {"tier": "primary"},
        (requirement,),
        ("workspace",),
    )
    bundle = ResourceBundle("schemas", "1.0.0", "1.0.0", (namespace,), (schema,), (resource,))
    assert namespace.fingerprint.startswith("sha256:")
    assert schema.fingerprint.startswith("sha256:")
    assert resource.fingerprint.startswith("sha256:")
    assert bundle.fingerprint.startswith("sha256:")
    with pytest.raises(TypeError):
        resource.labels["tier"] = "changed"  # type: ignore[index]
    with pytest.raises(ValueError, match="share"):
        ResourceDefinition(
            ResourceRef("structured", "other", "cases"),
            "relational",
            schema_ref,
        )


def test_bundle_and_resource_reject_duplicate_contracts() -> None:
    ref = ResourceRef("structured", "investigation", "cases")
    requirement = CapabilityRequirement("same", "1.0.0")
    with pytest.raises(ValueError, match="unique contracts"):
        ResourceDefinition(ref, "relational", requirements=(requirement, requirement))
    namespace = NamespaceDefinition("structured", "investigation")
    with pytest.raises(ValueError, match="unique"):
        ResourceBundle("p", "1.0.0", "1.0.0", (namespace, namespace))


def test_expression_and_operation_round_trip_without_consumer_operation_id() -> None:
    expression = Expression(
        "structured",
        "get",
        {"resource": "investigation.cases", "where": {"id": "case-1"}},
    )
    assert Expression.from_mapping(expression.to_dict()) == expression
    assert "operationId" not in expression.to_dict()
    operation = Operation(
        "structured",
        "meridian.structured.get",
        "1.0.0",
        (ResourceRef("structured", "investigation", "cases"),),
        {"where": {"id": "case-1"}},
        (CapabilityRequirement("meridian.structured.get", "1.0.0"),),
        True,
        True,
    )
    assert Operation.from_mapping(operation.to_dict()) == operation
    assert operation.request_fingerprint.startswith("sha256:")
    assert "operationId" not in operation.to_dict()
    with pytest.raises(ValueError, match="envelope"):
        Expression.from_mapping({**expression.to_dict(), "extra": 1})
    with pytest.raises(ValueError, match="envelope"):
        Operation.from_mapping({**operation.to_dict(), "extra": 1})
    malformed_expression = expression.to_dict()
    malformed_expression["method"] = 1
    with pytest.raises(ValueError):
        Expression.from_mapping(malformed_expression)
    malformed_operation = operation.to_dict()
    malformed_operation["readOnly"] = "false"
    with pytest.raises(ValueError, match="booleans"):
        Operation.from_mapping(malformed_operation)


def test_expression_and_operation_envelopes_reject_malformed_values() -> None:
    ref = ResourceRef("structured", "n", "r")
    with pytest.raises(ValueError, match="format_version"):
        Expression("structured", "get", {}, format_version="other")
    with pytest.raises(ValueError, match="unsupported"):
        Expression("query", "get", {})
    with pytest.raises(TypeError, match="object"):
        Expression.from_mapping(
            {
                "formatVersion": "meridian-expression.v1",
                "catalog": "structured",
                "method": "get",
                "arguments": [],
            }
        )
    expression = Expression("structured", "get", {})
    assert expression.fingerprint.startswith("sha256:")

    with pytest.raises(ValueError, match="format_version"):
        Operation("structured", "op", "1", (ref,), format_version="other")
    with pytest.raises(ValueError, match="unsupported"):
        Operation("query", "op", "1", (ref,))
    requirement = CapabilityRequirement("op", "1")
    with pytest.raises(ValueError, match="requirements"):
        Operation("structured", "op", "1", (ref,), requirements=(requirement, requirement))
    for field, invalid in (("resources", "bad"), ("input", []), ("requirements", "bad")):
        payload = Operation("structured", "op", "1", (ref,)).to_dict()
        payload[field] = invalid
        with pytest.raises(TypeError):
            Operation.from_mapping(payload)


def test_operation_rejects_cross_catalog_and_duplicate_resources() -> None:
    structured = ResourceRef("structured", "n", "r")
    other = ResourceRef("object", "n", "r")
    with pytest.raises(ValueError, match="unique"):
        Operation("structured", "op", "1", (structured, structured))
    with pytest.raises(ValueError, match="Catalog"):
        Operation("structured", "op", "1", (other,))


def test_catalog_manifest_is_exhaustive_and_fingerprinted() -> None:
    manifest = catalog_manifest()
    assert manifest.operation_for("put").operation_contract == "meridian.structured.put"
    assert manifest.fingerprint.startswith("sha256:")
    with pytest.raises(KeyError):
        manifest.operation_for("missing")
    with pytest.raises(ValueError, match="method registry"):
        CatalogManifest(
            "structured",
            "test-catalog",
            "1.0.0",
            "1.0.0",
            (
                OperationContract(
                    "get",
                    "meridian.structured.get",
                    "1.0.0",
                    True,
                    "always",
                ),
            ),
        )
    with pytest.raises(ValueError, match="idempotency"):
        OperationContract("get", "op", "1", True, "sometimes")
    with pytest.raises(ValueError, match="boolean"):
        OperationContract("get", "op", "1", "yes", "always")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="format_version"):
        CatalogManifest(
            manifest.catalog_name,
            manifest.package_name,
            manifest.package_version,
            manifest.catalog_contract_version,
            manifest.operations,
            format_version="other",
        )
    with pytest.raises(ValueError, match="supported"):
        CatalogManifest("query", "package", "1.0.0", "1.0.0", manifest.operations)
    with pytest.raises(ValueError, match="at least one"):
        CatalogManifest("structured", "package", "1.0.0", "1.0.0", ())
    with pytest.raises(ValueError, match="method names"):
        CatalogManifest(
            manifest.catalog_name,
            manifest.package_name,
            manifest.package_version,
            manifest.catalog_contract_version,
            (*manifest.operations, manifest.operations[0]),
        )
    duplicate_contracts = tuple(
        replace(item, operation_contract=manifest.operations[0].operation_contract)
        if item.method == "create_resource"
        else item
        for item in manifest.operations
    )
    with pytest.raises(ValueError, match="Operation contracts"):
        CatalogManifest(
            manifest.catalog_name,
            manifest.package_name,
            manifest.package_version,
            manifest.catalog_contract_version,
            duplicate_contracts,
        )
    with pytest.raises(ValueError, match="unique"):
        OperationContract("get", "op", "1", True, "always", ("same", "same"))
    with pytest.raises(ValueError, match="non-negative"):
        OperationContract("get", "op", "1", True, "always", minimum_limits={"page": -1})


def test_operation_result_is_immutable_serialized_data() -> None:
    ref = ResourceRef("structured", "investigation", "cases")
    result = OperationResult(
        {"id": "case-1"},
        "structured",
        "meridian.structured.get",
        "1.0.0",
        (ref,),
        "request",
        "execution",
        "sha256:" + "1" * 64,
        "sha256:" + "2" * 64,
        "sha256:" + "3" * 64,
        {"cursor": "opaque"},
    )
    assert result.to_dict()["data"] == {"id": "case-1"}
    with pytest.raises(TypeError):
        result.data["id"] = "changed"  # type: ignore[index]
    with pytest.raises(ValueError, match="sha256"):
        OperationResult(
            None,
            "structured",
            "meridian.structured.get",
            "1.0.0",
            (ref,),
            "request",
            "execution",
            "bad",
            "sha256:" + "2" * 64,
            "sha256:" + "3" * 64,
        )
    with pytest.raises(ValueError, match="provenance"):
        OperationResult(
            None,
            "structured",
            "meridian.structured.get",
            "1.0.0",
            (ref,),
            "request",
            "execution",
            "sha256:" + "1" * 64,
            "sha256:" + "2" * 64,
            "sha256:" + "3" * 64,
            {"unsafe": "\n"},
        )
    with pytest.raises(ValueError, match="unsupported"):
        replace(result, catalog="query")
    with pytest.raises(ValueError, match="non-empty"):
        replace(result, resources=())
    with pytest.raises(ValueError, match="belong"):
        replace(result, resources=(ResourceRef("object", "investigation", "cases"),))
    with pytest.raises(ValueError, match="at most"):
        replace(result, provenance={f"key-{index}": "value" for index in range(65)})
