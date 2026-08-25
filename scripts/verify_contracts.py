#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Verify checked-in public contracts against the Python implementation."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

import meridian_storage
from meridian_storage import ErrorCategory, ErrorCode
from meridian_storage.runtime import (
    REGISTERED_CATALOG_METHODS,
    Expression,
    Operation,
    RuntimeConfig,
    expression_contract,
    operation_contract,
    runtime_config_contract,
)
from meridian_storage.spi import adapter_capability_contract

ROOT = Path(__file__).resolve().parents[1]


def load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    public = load(ROOT / "contracts/public-api/meridian-core.v1.json")
    assert public["coreVersion"] == meridian_storage.__version__
    assert public["exports"] == sorted(meridian_storage.__all__)
    assert public["errorCodes"] == sorted(str(item) for item in ErrorCode)
    assert public["errorCategories"] == sorted(str(item) for item in ErrorCategory)
    assert public["catalogMethods"] == {
        name: list(methods) for name, methods in REGISTERED_CATALOG_METHODS.items()
    }

    runtime_schema = runtime_config_contract()
    capability_schema = adapter_capability_contract()
    expression_schema = expression_contract()
    operation_schema = operation_contract()
    jsonschema.Draft202012Validator.check_schema(runtime_schema)
    jsonschema.Draft202012Validator.check_schema(capability_schema)
    jsonschema.Draft202012Validator.check_schema(expression_schema)
    jsonschema.Draft202012Validator.check_schema(operation_schema)

    example = load(ROOT / "examples/meridian-config.example.json")
    jsonschema.Draft202012Validator(runtime_schema).validate(example)
    RuntimeConfig.from_mapping(example)
    expression = Expression("structured", "get", {"resource": "investigation.cases"})
    jsonschema.Draft202012Validator(expression_schema).validate(expression.to_dict())
    assert Expression.from_mapping(expression.to_dict()) == expression
    operation = Operation(
        catalog="structured",
        operation_contract="meridian.structured.get",
        operation_version="1.0.0",
        resources=(meridian_storage.ResourceRef("structured", "investigation", "cases"),),
        read_only=True,
        idempotent=True,
    )
    jsonschema.Draft202012Validator(operation_schema).validate(operation.to_dict())
    assert Operation.from_mapping(operation.to_dict()) == operation
    print("public contracts and example configuration are valid")


if __name__ == "__main__":
    main()
