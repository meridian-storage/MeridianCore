# SPDX-License-Identifier: Apache-2.0
"""Catalog-owned version/mode input survives Core's generic compatibility gate."""

from dataclasses import replace

import pytest

from meridian_storage import (
    CompatibilityError,
    ConflictError,
    ErrorCode,
    Expression,
    Meridian,
    Operation,
    RuntimeConfig,
)
from tests.support import (
    FakeAdapterFactory,
    FakeCatalogProvider,
    StaticSchemaProvider,
    StaticSecretResolver,
    bundle_for,
    config_mapping,
    context,
)


def write_runtime(*, adapter_versions=("1.0.0", "2.0.0"), guarantees=(), catalog_pin="2.x"):
    factory = FakeAdapterFactory()
    catalog = FakeCatalogProvider()
    catalog._manifest = replace(
        catalog.manifest(),
        catalog_contract_version="2.0.0",
        operations=tuple(
            replace(item, operation_version="2.0.0", guarantees=guarantees)
            if item.method == "put"
            else item
            for item in catalog.manifest().operations
        ),
    )
    descriptor = factory.manifest.descriptor
    factory.manifest = replace(
        factory.manifest,
        descriptor=replace(
            descriptor,
            capabilities=tuple(
                replace(item, operation_versions=adapter_versions)
                if item.operation_contract == "meridian.structured.put"
                else item
                for item in descriptor.capabilities
            ),
        ),
    )
    bundle = bundle_for()
    config = config_mapping(factory, bundle, catalog)
    config["catalogs"]["providers"][0]["contract"] = catalog_pin
    runtime = Meridian.from_config(
        RuntimeConfig.from_mapping(config),
        adapter_factories=(factory,),
        catalog_providers=(catalog,),
        schema_providers=(StaticSchemaProvider(bundle),),
        secret_resolver=StaticSecretResolver(),
    )
    return runtime, factory, catalog


def test_old_deployment_catalog_pin_rejects_new_contract_before_adapter_creation() -> None:
    runtime, factory, _ = write_runtime(catalog_pin="1.x")
    with pytest.raises(CompatibilityError) as rejected:
        runtime.start()
    assert rejected.value.code == ErrorCode.CATALOG_CONTRACT
    assert factory.runtimes == []
    runtime.close()


def put_expression(mode):
    return Expression.from_mapping(
        Expression(
            "structured",
            "put",
            {"resource": "investigation.cases", "data": {"id": "one"}, "mode": mode},
        ).to_dict()
    )


def test_normalized_put_mode_changes_fingerprint_without_core_syntax_or_wire_change() -> None:
    runtime, factory, _ = write_runtime()
    runtime.start()
    fingerprints = set()
    with runtime.context(context()):
        for mode in ("if_absent", "update", "upsert"):
            result = runtime.execute(put_expression(mode))
            request = factory.runtimes[0].requests[-1]
            assert request.operation.input["mode"] == mode
            assert request.operation.operation_version == "2.0.0"
            assert Operation.from_mapping(request.operation.to_dict()) == request.operation
            fingerprints.add(result.operation_fingerprint)
    assert len(fingerprints) == 3
    runtime.close()


def test_mode_change_conflicts_in_the_same_replay_domain() -> None:
    runtime, factory, _ = write_runtime()
    runtime.start()
    with runtime.context(context(idempotency_key="same-request")):
        first = runtime.execute(put_expression("if_absent"))
        assert runtime.execute(put_expression("if_absent")).data == first.data
        with pytest.raises(ConflictError) as failure:
            runtime.execute(put_expression("upsert"))
    assert failure.value.code == ErrorCode.IDEMPOTENCY_CONFLICT
    assert len(factory.runtimes[0].requests) == 1
    runtime.close()


@pytest.mark.parametrize("mode", ["if_absent", "update", "upsert"])
@pytest.mark.parametrize("failure", ["old-adapter", "missing-guarantee", "old-normalizer"])
def test_unsupported_put_contract_rejected_before_opening_mutation_session(mode, failure) -> None:
    runtime, factory, catalog = write_runtime(
        adapter_versions=("1.0.0",) if failure == "old-adapter" else ("1.0.0", "2.0.0"),
        guarantees=("fixture-required-guarantee",) if failure == "missing-guarantee" else (),
    )
    runtime.start()
    if failure == "old-normalizer":
        normalize = catalog.normalize
        catalog.normalize = lambda expression: replace(
            normalize(expression), operation_version="1.0.0"
        )
    with runtime.context(context()), pytest.raises(CompatibilityError) as rejected:
        runtime.execute(put_expression(mode))
    expected = (
        ErrorCode.CATALOG_CONTRACT
        if failure == "old-normalizer"
        else ErrorCode.CAPABILITY_UNSUPPORTED
    )
    assert rejected.value.code == expected
    assert not factory.runtimes[0].requests
    assert not any(event.startswith("runtime.open_session") for event in factory.runtimes[0].events)
    runtime.close()


def test_core_preserves_serialized_omission_for_catalog_owned_validation() -> None:
    runtime, factory, catalog = write_runtime()
    runtime.start()
    legacy = Expression.from_mapping(
        {
            "formatVersion": "meridian-expression.v1",
            "catalog": "structured",
            "method": "put",
            "arguments": {"resource": "investigation.cases", "data": {"id": "one"}},
        }
    )
    seen = []

    def reject_legacy(expression):
        seen.append(expression.to_dict())
        raise ValueError("new Catalog contract requires an explicit normalized mode")

    catalog.normalize = reject_legacy
    with runtime.context(context()), pytest.raises(Exception) as rejected:
        runtime.execute(legacy)
    assert rejected.value.code == ErrorCode.OPERATION_INVALID
    assert seen == [legacy.to_dict()]
    assert "mode" not in seen[0]["arguments"]
    assert not factory.runtimes[0].requests
    runtime.close()
