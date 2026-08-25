# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import replace

import pytest

from meridian_storage import ErrorCode, Meridian, OperationContext, RuntimeConfig, TransientError
from meridian_storage.spi import AdapterDescriptor, CapabilityManifest, ExecutionResult
from tests.integration.test_runtime_execution import _two_binding_runtime
from tests.support import (
    FakeAdapterFactory,
    FakeCatalogProvider,
    StaticSchemaProvider,
    StaticSecretResolver,
    bundle_for,
    config_mapping,
    context,
    make_runtime,
)


def test_transaction_commits_and_nested_boundaries_join_one_session() -> None:
    runtime, factory, _, _, _ = make_runtime()
    runtime.start()
    structured = runtime.catalog("structured")
    with (
        runtime.context(context(request_id="transaction-owner")),
        runtime.transaction("investigation.cases"),
        runtime.transaction("structured:investigation.cases"),
    ):
        runtime.execute(
            structured.put(  # type: ignore[attr-defined]
                resource="investigation.cases",
                data={"id": "case-1", "title": "Committed"},
            )
        )
    adapter = factory.runtimes[0]
    assert adapter.events.count("runtime.open_session:True") == 1
    assert adapter.events.count("session.begin") == 1
    assert adapter.events.count("session.commit") == 1
    assert "session.rollback" not in adapter.events
    runtime.close()


def test_transaction_exception_and_rollback_only_roll_back() -> None:
    runtime, factory, _, _, _ = make_runtime()
    runtime.start()
    structured = runtime.catalog("structured")
    with runtime.context(context()):
        with (
            pytest.raises(RuntimeError, match="abort"),
            runtime.transaction("investigation.cases"),
        ):
            runtime.execute(
                structured.put(  # type: ignore[attr-defined]
                    resource="investigation.cases", data={"id": "case-1"}
                )
            )
            raise RuntimeError("abort")
        with runtime.transaction("investigation.cases") as transaction:
            transaction.set_rollback_only()
    adapter = factory.runtimes[0]
    assert adapter.events.count("session.rollback") == 2
    assert "structured:investigation.cases:case-1" not in adapter.records
    runtime.close()


def test_transaction_never_retries_and_requires_same_owner() -> None:
    runtime, factory, _, _, _ = make_runtime()
    runtime.start()
    expression = runtime.catalog("structured").get(  # type: ignore[attr-defined]
        resource="investigation.cases", where={"id": "case-1"}
    )
    adapter = factory.runtimes[0]
    with (
        runtime.context(context(request_id="owner")),
        runtime.transaction("investigation.cases"),
    ):
        adapter.execution_failures.append(TransientError(ErrorCode.ADAPTER_FAILURE, "temporary"))
        with pytest.raises(TransientError):
            runtime.execute(expression)
        with (
            runtime.context(
                OperationContext(
                    "other",
                    request_id="other-owner",
                    scope={"workspace": "workspace-test"},
                )
            ),
            pytest.raises(Exception) as wrong_owner,
        ):
            runtime.execute(expression)
    assert wrong_owner.value.code == ErrorCode.TRANSACTION_SCOPE
    assert len(adapter.requests) == 1
    runtime.close()


def test_transaction_rejects_cross_binding_access() -> None:
    runtime, factory, _ = _two_binding_runtime()
    runtime.start()
    structured = runtime.catalog("structured")
    with (
        runtime.context(context()),
        runtime.transaction("investigation.cases"),
        pytest.raises(Exception) as failure,
    ):
        runtime.execute(
            structured.get(  # type: ignore[attr-defined]
                resource="investigation.notes", where={"id": "note-1"}
            )
        )
    assert failure.value.code == ErrorCode.TRANSACTION_SCOPE
    assert not factory.runtimes[1].requests
    runtime.close()


def test_transaction_requires_advertised_atomic_contract() -> None:
    factory = FakeAdapterFactory()
    descriptor = factory.manifest.descriptor
    without_transaction = AdapterDescriptor(
        descriptor.adapter_id,
        descriptor.adapter_contract_version,
        descriptor.driver,
        descriptor.supported_engine_versions,
        tuple(
            item
            for item in descriptor.capabilities
            if item.operation_contract != "meridian.transaction"
        ),
    )
    factory.manifest = CapabilityManifest(without_transaction, "test-engine", "1.0.0")
    catalog = FakeCatalogProvider()
    bundle = bundle_for()
    runtime = Meridian.from_config(
        RuntimeConfig.from_mapping(config_mapping(factory, bundle, catalog)),
        adapter_factories=(factory,),
        catalog_providers=(catalog,),
        schema_providers=(StaticSchemaProvider(bundle),),
        secret_resolver=StaticSecretResolver(),
    )
    runtime.start()
    with (
        runtime.context(context()),
        pytest.raises(Exception) as failure,
        runtime.transaction("investigation.cases"),
    ):
        pass
    assert failure.value.code == ErrorCode.TRANSACTION_SCOPE
    runtime.close()


def test_transaction_requires_no_dirty_read_guarantee() -> None:
    factory = FakeAdapterFactory()
    descriptor = factory.manifest.descriptor
    capabilities = tuple(
        replace(item, guarantees=("atomic",))
        if item.operation_contract == "meridian.transaction"
        else item
        for item in descriptor.capabilities
    )
    factory.manifest = CapabilityManifest(
        AdapterDescriptor(
            descriptor.adapter_id,
            descriptor.adapter_contract_version,
            descriptor.driver,
            descriptor.supported_engine_versions,
            capabilities,
        ),
        "test-engine",
        "1.0.0",
    )
    catalog = FakeCatalogProvider()
    bundle = bundle_for()
    runtime = Meridian.from_config(
        RuntimeConfig.from_mapping(config_mapping(factory, bundle, catalog)),
        adapter_factories=(factory,),
        catalog_providers=(catalog,),
        schema_providers=(StaticSchemaProvider(bundle),),
        secret_resolver=StaticSecretResolver(),
    )
    runtime.start()
    with (
        runtime.context(context()),
        pytest.raises(Exception) as rejected,
        runtime.transaction("investigation.cases"),
    ):
        pass
    assert rejected.value.code == ErrorCode.TRANSACTION_SCOPE
    runtime.close()


@pytest.mark.parametrize("failure_stage", ["begin", "commit", "rollback", "close"])
def test_transaction_lifecycle_failures_are_typed_redacted_and_cleaned_up(
    failure_stage: str,
) -> None:
    runtime, factory, _, _, _ = make_runtime()
    runtime.start()

    class FailingTransactionSession:
        def begin(self) -> None:
            if failure_stage == "begin":
                raise RuntimeError("credential=begin-secret")

        def execute(self, request: object) -> ExecutionResult:
            del request
            return ExecutionResult(None)

        def commit(self) -> None:
            if failure_stage == "commit":
                raise RuntimeError("credential=commit-secret")

        def rollback(self) -> None:
            if failure_stage == "rollback":
                raise RuntimeError("credential=rollback-secret")

        def close(self) -> None:
            if failure_stage == "close":
                raise RuntimeError("credential=close-secret")

    factory.runtimes[0].open_session = (  # type: ignore[method-assign]
        lambda *, transactional: FailingTransactionSession()
    )
    with (
        runtime.context(context(request_id="transaction-redaction")),
        pytest.raises(Exception) as failure,
        runtime.transaction("investigation.cases") as transaction,
    ):
        if failure_stage == "rollback":
            transaction.set_rollback_only()
    assert failure.value.code == ErrorCode.ADAPTER_FAILURE
    payload = failure.value.to_dict()
    assert payload["requestId"] == "transaction-redaction"
    assert payload["resourceRef"] == "structured:investigation.cases"
    assert payload["adapterProvenance"]["adapterId"] == "test.adapter"
    assert "credential" not in str(payload)
    assert runtime._active_transactions == 0
    runtime.close()


def test_refresh_is_explicit_atomic_and_monotonic() -> None:
    runtime, factory, _, provider, observer = make_runtime(telemetry=True)
    first = runtime.start()
    refreshed = runtime.refresh_registry()
    assert refreshed.revision == first.registry_revision + 1
    assert runtime.resource("investigation.cases").fingerprint
    assert provider.load_count == 2
    assert factory.runtimes[0].events.count("runtime.verify_physical") == 2
    assert observer.events[-1][0] == "meridian.registry.refreshed"
    runtime.close()


def test_refresh_rejects_provider_fingerprint_drift_without_swapping() -> None:
    runtime, _, _, provider, _ = make_runtime()
    initial = runtime.start()
    provider.replace(bundle_for(required_scope=("workspace", "region")))
    with pytest.raises(Exception) as failure:
        runtime.refresh_registry()
    assert failure.value.code == ErrorCode.REGISTRY_REFRESH
    assert runtime.startup_report is initial
    assert runtime._snapshot_for_handle().revision == 1
    runtime.close()
