# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import replace

import pytest

from meridian_storage import Operation, ResourceRef
from meridian_storage.runtime import RuntimeConfig
from meridian_storage.spi import AdapterCreateContext, PhysicalResource, SecretValue
from meridian_storage.testing import AdapterConformanceTarget, run_adapter_conformance
from tests.support import (
    FakeAdapterFactory,
    FakeCatalogProvider,
    bundle_for,
    config_mapping,
    context,
)


def target() -> AdapterConformanceTarget:
    factory = FakeAdapterFactory()
    catalog = FakeCatalogProvider()
    bundle = bundle_for(required_scope=())
    config = RuntimeConfig.from_mapping(config_mapping(factory, bundle, catalog))
    resource = bundle.resources[0]
    schema = bundle.schemas[0]
    return AdapterConformanceTarget(
        factory=factory,
        create_context=AdapterCreateContext(
            config.bindings[0],
            SecretValue(b"identity"),
            SecretValue(b"credential"),
        ),
        resources=(
            PhysicalResource(
                resource.ref,
                resource.fingerprint,
                schema.fingerprint,
                resource.profile,
            ),
        ),
        operation=Operation(
            "structured",
            "meridian.structured.get",
            "1.0.0",
            (ResourceRef("structured", "investigation", "cases"),),
            {"where": {"id": "case-1"}},
            read_only=True,
            idempotent=True,
        ),
        context=context(),
        assert_result=lambda result: (
            None
            if result.data == {"id": "case-1", "version": 1}
            else (_ for _ in ()).throw(AssertionError("unexpected Data"))
        ),
    )


def test_conformance_runner_checks_complete_spi_lifecycle() -> None:
    selected = target()
    report = run_adapter_conformance(selected)
    assert report.adapter_id == "test.adapter"
    assert (
        report.capability_fingerprint
        == selected.create_context.binding.required_capability_fingerprint
    )
    assert (
        report.physical_fingerprint == selected.create_context.binding.required_physical_fingerprint
    )
    assert report.checks == (
        "authenticated-open",
        "deterministic-capability-manifest",
        "deterministic-physical-verification",
        "normalized-execution",
        "transaction-commit-rollback",
    )
    assert report.to_dict()["resourceCount"] == 1


def test_conformance_runner_rejects_binding_identity_and_closes_on_failure() -> None:
    selected = target()
    wrong_binding = replace(
        selected.create_context.binding,
        adapter_id="different.adapter",
    )
    with pytest.raises(AssertionError, match="identities"):
        run_adapter_conformance(
            replace(
                selected, create_context=replace(selected.create_context, binding=wrong_binding)
            )
        )

    selected = target()
    selected.factory.runtime_initializer = lambda runtime: setattr(  # type: ignore[attr-defined]
        runtime,
        "fail_verify",
        RuntimeError("physical failure"),
    )
    with pytest.raises(RuntimeError, match="physical failure"):
        run_adapter_conformance(selected)
    assert selected.factory.runtimes[0].closed  # type: ignore[attr-defined]

    selected = target()
    selected.factory.runtime_initializer = lambda runtime: setattr(  # type: ignore[attr-defined]
        runtime,
        "fail_open",
        RuntimeError("partial open failure"),
    )
    with pytest.raises(RuntimeError, match="partial open failure"):
        run_adapter_conformance(selected)
    assert selected.factory.runtimes[0].closed  # type: ignore[attr-defined]
