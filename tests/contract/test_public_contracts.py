# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

import meridian_storage
from meridian_storage import ErrorCategory, ErrorCode, Expression, Operation, ResourceRef
from meridian_storage.runtime import (
    REGISTERED_CATALOG_METHODS,
    RuntimeConfig,
    expression_contract,
    operation_contract,
    runtime_config_contract,
)
from meridian_storage.spi import adapter_capability_contract
from tests.support import capability_manifest, catalog_manifest

ROOT = Path(__file__).resolve().parents[2]


def load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_public_api_ledger_matches_runtime_exactly() -> None:
    contract = load(ROOT / "contracts/public-api/meridian-core.v1.json")
    assert contract["coreVersion"] == meridian_storage.__version__
    assert contract["exports"] == sorted(meridian_storage.__all__)
    assert contract["errorCodes"] == sorted(str(item) for item in ErrorCode)
    assert contract["errorCategories"] == sorted(str(item) for item in ErrorCategory)
    assert contract["entryPointGroups"] == {
        "adapters": "meridian_storage.adapters",
        "catalogs": "meridian_storage.catalogs",
        "plugins": "meridian_storage.plugins",
        "schemaProviders": "meridian_storage.schemas",
    }
    assert contract["catalogMethods"] == {
        name: list(methods) for name, methods in REGISTERED_CATALOG_METHODS.items()
    }


def test_all_json_schemas_are_valid_draft_2020_12() -> None:
    for schema in (
        runtime_config_contract(),
        adapter_capability_contract(),
        expression_contract(),
        operation_contract(),
    ):
        jsonschema.Draft202012Validator.check_schema(schema)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_runtime_example_and_capability_manifest_validate() -> None:
    example = load(ROOT / "examples/meridian-config.example.json")
    jsonschema.Draft202012Validator(runtime_config_contract()).validate(example)
    assert RuntimeConfig.from_mapping(example).format_version == "meridian-config.v1"
    manifest = capability_manifest()
    jsonschema.Draft202012Validator(adapter_capability_contract()).validate(manifest.to_dict())


def test_expression_and_operation_wire_contracts_round_trip() -> None:
    expression = Expression(
        "structured",
        "get",
        {"resource": "investigation.cases", "where": {"id": "case-1"}},
    )
    jsonschema.Draft202012Validator(expression_contract()).validate(expression.to_dict())
    assert Expression.from_mapping(expression.to_dict()) == expression
    operation = Operation(
        "structured",
        "meridian.structured.get",
        "1.0.0",
        (ResourceRef("structured", "investigation", "cases"),),
        expression.arguments,
        read_only=True,
        idempotent=True,
    )
    jsonschema.Draft202012Validator(operation_contract()).validate(operation.to_dict())
    assert Operation.from_mapping(operation.to_dict()) == operation


def test_catalog_registry_is_exact_and_provider_manifest_conforms() -> None:
    assert REGISTERED_CATALOG_METHODS == {
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
    assert (
        tuple(item.method for item in catalog_manifest().operations)
        == REGISTERED_CATALOG_METHODS["structured"]
    )


def test_core_has_no_broker_dependency_or_consumer_engine_fields() -> None:
    pyproject = load(ROOT / "compatibility.json")
    assert pyproject["operationFormats"] == ["meridian-operation.v1"]
    python_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "src/meridian_storage").rglob("*.py"))
    ).lower()
    assert "import kafka" not in python_sources
    assert "from kafka" not in python_sources
    expression = Expression("streaming", "publish", {"resource": "events.main", "data": {}})
    serialized = json.dumps(expression.to_dict(), sort_keys=True).lower()
    assert "adapter" not in serialized
    assert "engine" not in serialized
