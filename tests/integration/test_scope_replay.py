# SPDX-License-Identifier: Apache-2.0
"""Replay identity must isolate the context sent to the adapter."""

from dataclasses import replace

import pytest

from meridian_storage import ConflictError, ErrorCode, OperationContext
from meridian_storage.spi import ExecutionRequest, ExecutionResult
from tests.support import FakeSession, context, make_runtime


@pytest.mark.parametrize(
    "other_scope",
    [
        {"workspace": "other"},
        {"workspace": "workspace-test", "region": "west"},
        {"workspace": "workspace-test", "label": "a=b,c=d"},
    ],
)
def test_replay_does_not_reuse_foreign_scope_result_or_suppress_write(other_scope) -> None:
    runtime, factory, _, _, _ = make_runtime()
    runtime.start()
    adapter = factory.runtimes[0]
    writes: dict[tuple[tuple[str, str], ...], object] = {}

    class ScopedSession(FakeSession):
        def execute(self, request: ExecutionRequest) -> ExecutionResult:
            adapter.requests.append(request)
            scope = tuple(sorted(request.context.scope.items()))
            writes[scope] = request.operation.input["data"]
            return ExecutionResult({"scope": dict(scope), "write": len(writes)})

    adapter.open_session = lambda *, transactional: ScopedSession(adapter, transactional)
    expression = runtime.catalog("structured").put(
        resource="investigation.cases", data={"id": "same"}
    )
    first_context = context(idempotency_key="same-key")
    with runtime.context(first_context):
        first = runtime.execute(expression)
    second_context = replace(first_context, scope=other_scope)
    with runtime.context(second_context):
        second = runtime.execute(expression)
        replay = runtime.execute(expression)
    assert second.data == {"scope": other_scope, "write": 2}
    assert second.data != first.data
    assert replay.data == second.data
    assert len(writes) == len(adapter.requests) == 2
    assert writes[tuple(sorted(first_context.scope.items()))] == {"id": "same"}
    assert writes[tuple(sorted(second_context.scope.items()))] == {"id": "same"}
    runtime.close()


def test_scope_order_and_incidental_request_metadata_do_not_break_replay() -> None:
    runtime, factory, _, _, _ = make_runtime()
    runtime.start()
    structured = runtime.catalog("structured")
    expression = structured.put(resource="investigation.cases", data={"id": "same"})
    original_context = replace(
        context(idempotency_key="same-key"), scope={"workspace": "one", "region": "west"}
    )
    with runtime.context(original_context):
        first = runtime.execute(expression)
    retry_context = replace(
        original_context,
        request_id="new-request",
        correlation_id="new-correlation",
        trace_context={"trace": "new-trace"},
        scope={"region": "west", "workspace": "one"},
    )
    with runtime.context(retry_context):
        replay = runtime.execute(expression)
        with pytest.raises(ConflictError) as conflict:
            runtime.execute(structured.put(resource="investigation.cases", data={"id": "other"}))
    assert first.data == replay.data
    assert conflict.value.code == ErrorCode.IDEMPOTENCY_CONFLICT
    assert len(factory.runtimes[0].requests) == 1
    runtime.close()


@pytest.mark.parametrize(
    "changes",
    [{"principal_ref": "other"}, {"tenant": "other"}, {"tenant": None}],
)
def test_principal_and_tenant_remain_replay_boundaries(changes) -> None:
    runtime, factory, _, _, _ = make_runtime()
    runtime.start()
    expression = runtime.catalog("structured").put(
        resource="investigation.cases", data={"id": "same"}
    )
    first_context: OperationContext = context(idempotency_key="same-key")
    with runtime.context(first_context):
        runtime.execute(expression)
    with runtime.context(replace(first_context, **changes)):
        runtime.execute(expression)
    assert len(factory.runtimes[0].requests) == 2
    runtime.close()
