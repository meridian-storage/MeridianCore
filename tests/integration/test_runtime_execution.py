# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import threading
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from meridian_storage import (
    CompatibilityError,
    ErrorCode,
    Expression,
    InternalError,
    Meridian,
    Operation,
    OperationContext,
    RuntimeConfig,
    RuntimeState,
    TransientError,
    ValidationError,
)
from meridian_storage.spi import AdapterDescriptor, CapabilityManifest, ExecutionResult
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


def test_mapping_first_expression_executes_and_returns_generated_metadata() -> None:
    runtime, factory, catalog, _, _ = make_runtime()
    runtime.start()
    structured = runtime.catalog("structured")
    expression = structured.put(  # type: ignore[attr-defined]
        resource="investigation.cases",
        data={"id": "case-1", "title": "First"},
    )
    assert isinstance(expression, Expression)
    assert "adapter" not in expression.to_dict()
    assert "engine" not in expression.to_dict()
    with runtime.context(context(idempotency_key="put-case")) as resolved:
        result = runtime.execute(expression)
        assert result.data == {"id": "case-1", "version": 1, "title": "First"}
        assert result.request_id == resolved.request_id
        assert result.execution_id != result.request_id
        assert result.provenance == {"driver": "test"}
        assert result.resources[0].canonical == "structured:investigation.cases"
    request = factory.runtimes[0].requests[0]
    assert request.context.scope == {"workspace": "workspace-test"}
    assert request.operation.operation_contract == "meridian.structured.put"
    assert catalog.normalized == [expression]
    runtime.close()


def test_execute_requires_context_and_registered_method() -> None:
    runtime, _, _, _, _ = make_runtime()
    runtime.start()
    structured = runtime.catalog("structured")
    expression = structured.get(  # type: ignore[attr-defined]
        resource="investigation.cases",
        where={"id": "case-1"},
    )
    with pytest.raises(Exception) as missing_context:
        runtime.execute(expression)
    assert missing_context.value.code == ErrorCode.CONTEXT_REQUIRED
    with runtime.context(context()), pytest.raises(Exception) as invalid:
        runtime.execute(Expression("structured", "not_registered", {}))
    assert invalid.value.code == ErrorCode.OPERATION_INVALID
    runtime.close()


def test_execute_rejects_non_expression_and_unavailable_catalogs() -> None:
    runtime, _, _, _, _ = make_runtime()
    runtime.start()
    with pytest.raises(ValidationError) as invalid:
        runtime.execute(object())  # type: ignore[arg-type]
    assert invalid.value.code == ErrorCode.OPERATION_INVALID

    with runtime.context(context()), pytest.raises(Exception) as unavailable:
        runtime.execute(Expression("object", "get", {}))
    assert unavailable.value.code == ErrorCode.CATALOG_UNAVAILABLE

    corrupted = Expression("object", "get", {})
    object.__setattr__(corrupted, "catalog", "unknown")
    with runtime.context(context()), pytest.raises(Exception) as unknown:
        runtime.execute(corrupted)
    assert unknown.value.code == ErrorCode.CATALOG_NOT_FOUND
    runtime.close()


def test_catalog_lookup_distinguishes_unknown_and_unavailable() -> None:
    runtime, _, _, _, _ = make_runtime()
    runtime.start()
    with pytest.raises(Exception) as unknown:
        runtime.catalog("query")
    assert unknown.value.code == ErrorCode.CATALOG_NOT_FOUND
    with pytest.raises(Exception) as unavailable:
        runtime.catalog("object")
    assert unavailable.value.code == ErrorCode.CATALOG_UNAVAILABLE
    runtime.close()


def test_required_scope_fails_before_adapter_execution() -> None:
    runtime, factory, _, _, _ = make_runtime()
    runtime.start()
    expression = runtime.catalog("structured").get(  # type: ignore[attr-defined]
        resource="investigation.cases", where={"id": "case-1"}
    )
    context_without_scope = OperationContext("principal", tenant="tenant")
    with runtime.context(context_without_scope), pytest.raises(Exception) as failure:
        runtime.execute(expression)
    assert failure.value.code == ErrorCode.OPERATION_SCOPE
    assert not factory.runtimes[0].requests
    runtime.close()


def test_read_retries_retryable_failures_with_stable_execution_identity() -> None:
    runtime, factory, _, _, _ = make_runtime()
    runtime.start()
    adapter = factory.runtimes[0]
    adapter.execution_failures.extend(
        [
            TransientError(ErrorCode.ADAPTER_FAILURE, "temporary"),
            TransientError(ErrorCode.ADAPTER_FAILURE, "temporary"),
        ]
    )
    expression = runtime.catalog("structured").get(  # type: ignore[attr-defined]
        resource="investigation.cases", where={"id": "case-1"}
    )
    with runtime.context(context(request_id="request-stable")):
        result = runtime.execute(expression)
    assert [request.attempt for request in adapter.requests] == [1, 2, 3]
    assert {request.execution_id for request in adapter.requests} == {result.execution_id}
    assert {request.request_id for request in adapter.requests} == {"request-stable"}
    runtime.close()


def test_mutation_retry_requires_idempotency_key() -> None:
    runtime, factory, _, _, _ = make_runtime()
    runtime.start()
    adapter = factory.runtimes[0]
    expression = runtime.catalog("structured").put(  # type: ignore[attr-defined]
        resource="investigation.cases", data={"id": "case-1"}
    )
    adapter.execution_failures.append(TransientError(ErrorCode.ADAPTER_FAILURE, "temporary"))
    with runtime.context(context()), pytest.raises(TransientError):
        runtime.execute(expression)
    assert len(adapter.requests) == 1

    adapter.execution_failures.append(TransientError(ErrorCode.ADAPTER_FAILURE, "temporary"))
    with runtime.context(context(idempotency_key="stable-key")):
        runtime.execute(expression)
    assert [request.attempt for request in adapter.requests[-2:]] == [1, 2]
    runtime.close()


def test_idempotency_replay_and_conflict_are_deterministic() -> None:
    runtime, factory, _, _, _ = make_runtime()
    runtime.start()
    structured = runtime.catalog("structured")
    first = structured.put(  # type: ignore[attr-defined]
        resource="investigation.cases", data={"id": "case-1", "title": "One"}
    )
    with runtime.context(context(idempotency_key="same")):
        original = runtime.execute(first)
        replay = runtime.execute(first)
        assert replay.data == original.data
        changed = structured.put(  # type: ignore[attr-defined]
            resource="investigation.cases", data={"id": "case-1", "title": "Changed"}
        )
        with pytest.raises(Exception) as conflict:
            runtime.execute(changed)
    assert conflict.value.code == ErrorCode.IDEMPOTENCY_CONFLICT
    assert len(factory.runtimes[0].requests) == 1
    runtime.close()


def test_result_limit_and_elapsed_deadline_fail_closed() -> None:
    factory = FakeAdapterFactory()
    catalog = FakeCatalogProvider()
    bundle = bundle_for()
    value = config_mapping(factory, bundle, catalog, max_result_bytes=32)
    runtime = Meridian.from_config(
        RuntimeConfig.from_mapping(value),
        adapter_factories=(factory,),
        catalog_providers=(catalog,),
        schema_providers=(StaticSchemaProvider(bundle),),
        secret_resolver=StaticSecretResolver(),
    )
    runtime.start()
    expression = runtime.catalog("structured").get(  # type: ignore[attr-defined]
        resource="investigation.cases", where={"id": "case-1"}
    )
    with runtime.context(context()), pytest.raises(Exception) as too_large:
        runtime.execute(expression)
    assert too_large.value.code == ErrorCode.OPERATION_RESULT_LIMIT
    expired = OperationContext(
        "principal",
        scope={"workspace": "one"},
        deadline=datetime.now(UTC) - timedelta(seconds=1),
    )
    with runtime.context(expired), pytest.raises(Exception) as deadline:
        runtime.execute(expression)
    assert deadline.value.code == ErrorCode.DEADLINE_EXCEEDED
    runtime.close()


def test_result_limit_does_not_trust_an_underreported_adapter_byte_count() -> None:
    runtime, factory, _, _, _ = make_runtime()
    runtime.start()
    factory.runtimes[0].records["structured:investigation.cases:case-1"] = {
        "id": "case-1",
        "payload": "x" * 2048,
    }
    expression = runtime.catalog("structured").get(  # type: ignore[attr-defined]
        resource="investigation.cases", where={"id": "case-1"}
    )
    with runtime.context(context()), pytest.raises(Exception) as too_large:
        runtime.execute(expression)
    assert too_large.value.code == ErrorCode.OPERATION_RESULT_LIMIT
    runtime.close()


def test_unknown_adapter_exception_is_redacted_and_non_retryable() -> None:
    runtime, factory, _, _, _ = make_runtime()
    runtime.start()
    factory.runtimes[0].execution_failures.append(RuntimeError("credential=do-not-leak"))
    expression = runtime.catalog("structured").get(  # type: ignore[attr-defined]
        resource="investigation.cases", where={"id": "case-1"}
    )
    with (
        runtime.context(context(request_id="safe-request")),
        pytest.raises(InternalError) as failure,
    ):
        runtime.execute(expression)
    payload = failure.value.to_dict()
    assert payload["code"] == ErrorCode.ADAPTER_FAILURE
    assert payload["requestId"] == "safe-request"
    assert "credential" not in str(payload)
    assert not failure.value.retryable
    assert len(factory.runtimes[0].requests) == 1
    runtime.close()


def test_provider_cannot_change_normalized_contract() -> None:
    runtime, _, catalog, _, _ = make_runtime()
    runtime.start()
    original = catalog.normalize

    def incompatible(expression: Expression) -> Operation:
        result = original(expression)
        return Operation(
            result.catalog,
            "different.contract",
            result.operation_version,
            result.resources,
            result.input,
            result.requirements,
            result.read_only,
            result.idempotent,
        )

    catalog.normalize = incompatible  # type: ignore[method-assign]
    expression = runtime.catalog("structured").get(  # type: ignore[attr-defined]
        resource="investigation.cases", where={"id": "case-1"}
    )
    with runtime.context(context()), pytest.raises(Exception) as failure:
        runtime.execute(expression)
    assert failure.value.code == ErrorCode.CATALOG_CONTRACT
    runtime.close()


@pytest.mark.parametrize("failure", ["validation", "typed", "invalid-operation", "internal"])
def test_provider_normalization_failures_are_typed_and_redacted(failure: str) -> None:
    runtime, _, catalog, _, _ = make_runtime()
    runtime.start()

    def fail_normalize(expression: Expression) -> object:
        del expression
        if failure == "validation":
            raise ValueError("malformed consumer mapping")
        if failure == "typed":
            raise ValidationError(ErrorCode.OPERATION_INVALID, "typed failure")
        if failure == "internal":
            raise RuntimeError("secret provider detail")
        return object()

    catalog.normalize = fail_normalize  # type: ignore[method-assign]
    expression = runtime.catalog("structured").get(  # type: ignore[attr-defined]
        resource="investigation.cases", where={"id": "case-1"}
    )
    with (
        runtime.context(context(request_id="normalize-request")),
        pytest.raises(Exception) as error,
    ):
        runtime.execute(expression)
    if failure == "invalid-operation":
        assert isinstance(error.value, CompatibilityError)
        assert error.value.code == ErrorCode.CATALOG_CONTRACT
    elif failure == "internal":
        assert isinstance(error.value, InternalError)
        assert "secret provider detail" not in str(error.value.to_dict())
    else:
        assert error.value.code == ErrorCode.OPERATION_INVALID
    assert error.value.request_id == "normalize-request"
    runtime.close()


def test_execution_rechecks_capability_manifest_after_startup() -> None:
    runtime, _, _, _, _ = make_runtime()
    runtime.start()
    manifest = runtime._capability_manifests["primary"]
    descriptor = manifest.descriptor
    only_transaction = tuple(
        item
        for item in descriptor.capabilities
        if item.operation_contract == "meridian.transaction"
    )
    runtime._capability_manifests["primary"] = CapabilityManifest(
        AdapterDescriptor(
            descriptor.adapter_id,
            descriptor.adapter_contract_version,
            descriptor.driver,
            descriptor.supported_engine_versions,
            only_transaction,
        ),
        manifest.engine_profile,
        manifest.engine_version,
    )
    expression = runtime.catalog("structured").get(  # type: ignore[attr-defined]
        resource="investigation.cases", where={"id": "case-1"}
    )
    with runtime.context(context()), pytest.raises(CompatibilityError) as unsupported:
        runtime.execute(expression)
    assert unsupported.value.code == ErrorCode.CAPABILITY_UNSUPPORTED
    runtime.close()


def test_failed_idempotent_mutation_aborts_claim_for_safe_retry() -> None:
    runtime, factory, _, _, _ = make_runtime()
    runtime.start()
    adapter = factory.runtimes[0]
    adapter.execution_failures.append(RuntimeError("one-shot failure"))
    expression = runtime.catalog("structured").put(  # type: ignore[attr-defined]
        resource="investigation.cases", data={"id": "case-1"}
    )
    with runtime.context(context(idempotency_key="retry-after-failure")):
        with pytest.raises(InternalError):
            runtime.execute(expression)
        result = runtime.execute(expression)
    assert result.data == {"id": "case-1", "version": 1}
    assert len(adapter.requests) == 2
    runtime.close()


@pytest.mark.parametrize("execution_fails", [False, True])
def test_adapter_session_contract_and_close_failures_are_normalized(
    execution_fails: bool,
) -> None:
    runtime, factory, _, _, _ = make_runtime()
    runtime.start()
    adapter = factory.runtimes[0]

    class InvalidSession:
        def begin(self) -> None:
            return None

        def execute(self, request: object) -> object:
            del request
            if execution_fails:
                raise RuntimeError("execute-secret")
            return object()

        def commit(self) -> None:
            return None

        def rollback(self) -> None:
            return None

        def close(self) -> None:
            raise RuntimeError("close-secret")

    def open_session(*, transactional: bool) -> InvalidSession:
        assert not transactional
        return InvalidSession()

    adapter.open_session = open_session  # type: ignore[method-assign]
    expression = runtime.catalog("structured").get(  # type: ignore[attr-defined]
        resource="investigation.cases", where={"id": "case-1"}
    )
    with runtime.context(context()), pytest.raises(InternalError) as failure:
        runtime.execute(expression)
    assert failure.value.code == ErrorCode.ADAPTER_FAILURE
    assert "secret" not in str(failure.value.to_dict())
    runtime.close()


def test_adapter_session_close_failure_after_valid_result_is_not_hidden() -> None:
    runtime, factory, _, _, _ = make_runtime()
    runtime.start()
    adapter = factory.runtimes[0]

    class ClosingSession:
        def begin(self) -> None:
            return None

        def execute(self, request: object) -> ExecutionResult:
            del request
            return ExecutionResult({"ok": True}, 1)

        def commit(self) -> None:
            return None

        def rollback(self) -> None:
            return None

        def close(self) -> None:
            raise RuntimeError("session-close-secret")

    adapter.open_session = lambda *, transactional: ClosingSession()  # type: ignore[method-assign]
    expression = runtime.catalog("structured").get(  # type: ignore[attr-defined]
        resource="investigation.cases", where={"id": "case-1"}
    )
    with runtime.context(context()), pytest.raises(InternalError) as failure:
        runtime.execute(expression)
    assert failure.value.code == ErrorCode.ADAPTER_FAILURE
    runtime.close()


def test_adapter_must_return_a_complete_session_protocol() -> None:
    runtime, factory, _, _, _ = make_runtime()
    runtime.start()
    factory.runtimes[0].open_session = lambda *, transactional: object()  # type: ignore[method-assign]
    expression = runtime.catalog("structured").get(  # type: ignore[attr-defined]
        resource="investigation.cases", where={"id": "case-1"}
    )
    with runtime.context(context()), pytest.raises(CompatibilityError) as invalid:
        runtime.execute(expression)
    assert invalid.value.code == ErrorCode.ADAPTER_CONTRACT
    runtime.close()


def _two_binding_runtime() -> tuple[Meridian, FakeAdapterFactory, FakeCatalogProvider]:
    factory = FakeAdapterFactory()
    catalog = FakeCatalogProvider()
    bundle = bundle_for(resource_names=("cases", "notes"))
    value = config_mapping(factory, bundle, catalog)
    primary = value["bindings"][0]  # type: ignore[index]
    secondary = deepcopy(primary)
    secondary["id"] = "secondary"
    secondary["endpoint"] = "memory://secondary"
    value["bindings"].append(secondary)  # type: ignore[union-attr]
    placements = value["placements"]  # type: ignore[assignment]
    placements[0]["selector"]["resources"] = [bundle.resources[0].ref.to_dict()]
    placements.append(
        {
            "id": "secondary-placement",
            "selector": {
                "resources": [bundle.resources[1].ref.to_dict()],
                "catalog": None,
                "labels": {},
            },
            "bindingId": "secondary",
            "extensions": {},
        }
    )
    runtime = Meridian.from_config(
        RuntimeConfig.from_mapping(value),
        adapter_factories=(factory,),
        catalog_providers=(catalog,),
        schema_providers=(StaticSchemaProvider(bundle),),
        secret_resolver=StaticSecretResolver(),
        _sleep=lambda _: None,
    )
    return runtime, factory, catalog


def test_cross_binding_operation_is_rejected_before_engine_call() -> None:
    runtime, factory, _ = _two_binding_runtime()
    runtime.start()
    expression = runtime.catalog("structured").traverse(  # type: ignore[attr-defined]
        resources=("investigation.cases", "investigation.notes"),
        start={"id": "case-1"},
    )
    with runtime.context(context()), pytest.raises(Exception) as failure:
        runtime.execute(expression)
    assert failure.value.code == ErrorCode.OPERATION_SCOPE
    assert not any(adapter.requests for adapter in factory.runtimes)
    runtime.close()


def test_close_drains_an_already_accepted_operation() -> None:
    runtime, factory, _, _, _ = make_runtime()
    runtime.start()
    adapter = factory.runtimes[0]
    adapter.block_execute = threading.Event()
    expression = runtime.catalog("structured").get(  # type: ignore[attr-defined]
        resource="investigation.cases", where={"id": "case-1"}
    )
    failures: list[BaseException] = []

    def execute() -> None:
        try:
            with runtime.context(context()):
                runtime.execute(expression)
        except BaseException as exc:
            failures.append(exc)

    operation_thread = threading.Thread(target=execute)
    operation_thread.start()
    assert adapter.block_execute.wait(timeout=5)
    close_thread = threading.Thread(target=runtime.close)
    close_thread.start()
    assert runtime.state is RuntimeState.DRAINING
    adapter.release_execute.set()
    operation_thread.join(timeout=5)
    close_thread.join(timeout=5)
    assert not failures
    assert runtime.state is RuntimeState.CLOSED
