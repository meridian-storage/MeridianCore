# SPDX-License-Identifier: Apache-2.0
"""Facade contracts against an unmodified released PostgreSQL adapter."""

from dataclasses import replace

import pytest

from meridian_storage import (
    CompatibilityError,
    ConflictError,
    ErrorCode,
    Operation,
    OperationContext,
    ResourceRef,
    TransactionError,
)


def request_context(**kwargs):
    return OperationContext(
        "coretest:principal", tenant="tenant", scope={"workspace": "one"}, **kwargs
    )


def test_real_scope_replay_executes_each_partition_and_preserves_conflict(database):
    runtime = database.runtime
    structured = runtime.catalog("structured")
    expression = structured.put(
        resource="coretest.records", data={"id": "same", "value": "original"}
    )
    ctx = request_context(idempotency_key="same-key")
    for scope in ({"workspace": "one"}, {"workspace": "two"}):
        with runtime.context(replace(ctx, scope=scope)):
            first = runtime.execute(expression)
            assert runtime.execute(expression).data == first.data
            assert first.data["recordVersion"] == 1
            with pytest.raises(ConflictError) as conflict:
                runtime.execute(
                    structured.put(
                        resource="coretest.records", data={"id": "same", "value": "changed"}
                    )
                )
            assert conflict.value.code == ErrorCode.IDEMPOTENCY_CONFLICT
    assert database.rows("records") == [("same", "original"), ("same", "original")]
    assert len(database.calls) == 2


@pytest.mark.parametrize("outcome", ["commit", "escape", "rollback-only"])
def test_mixed_catalog_results_provisional_until_outer_commit(database, outcome):
    runtime = database.runtime
    structured, evidence = runtime.catalog("structured"), runtime.catalog("evidence")
    try:
        with (
            runtime.context(request_context(idempotency_key="group-is-not-replayed")),
            runtime.transaction("structured:coretest.records") as transaction,
        ):
            source = runtime.execute(
                structured.put(resource="coretest.records", data={"id": "one", "value": "source"})
            )
            with runtime.transaction("evidence:coretest.events"):
                audit = runtime.execute(
                    evidence.append(
                        resource="coretest.events", data={"id": "one", "value": "audit"}
                    )
                )
            assert source.data["value"] == "source"
            assert audit.data["value"] == "audit"
            # A separate real connection cannot see either provisional row.
            assert database.rows("records") == database.rows("events") == []
            assert len(database.sessions) == 1
            assert len({session_id for session_id, _ in database.calls}) == 1
            assert {call.operation.catalog for _, call in database.calls} == {
                "structured",
                "evidence",
            }
            assert len({call.context.request_id for _, call in database.calls}) == 1
            if outcome == "escape":
                raise RuntimeError("required operation failed")
            if outcome == "rollback-only":
                transaction.set_rollback_only()
    except RuntimeError as error:
        assert outcome == "escape" and str(error) == "required operation failed"
    assert database.rows("records") == ([("one", "source")] if outcome == "commit" else [])
    assert database.rows("events") == ([("one", "audit")] if outcome == "commit" else [])


@pytest.mark.parametrize("failure", ["cross-binding", "wrong-owner", "missing-atomic-guarantee"])
@pytest.mark.parametrize("handling", ["caught", "escape", "rollback-only"])
def test_child_validation_preserves_existing_rollback_boundary(database, failure, handling):
    runtime = database.runtime
    structured, evidence = runtime.catalog("structured"), runtime.catalog("evidence")
    expected_type = (
        CompatibilityError if failure == "missing-atomic-guarantee" else TransactionError
    )
    expected_code = (
        ErrorCode.CAPABILITY_UNSUPPORTED
        if failure == "missing-atomic-guarantee"
        else ErrorCode.TRANSACTION_SCOPE
    )
    try:
        with (
            runtime.context(request_context(request_id="owner")),
            runtime.transaction("structured:coretest.records") as transaction,
        ):
            runtime.execute(
                structured.put(resource="coretest.records", data={"id": "one", "value": "source"})
            )
            assert database.rows("events") == []  # No automatic audit.
            child = evidence.append(
                resource="coretest.remote" if failure == "cross-binding" else "coretest.events",
                data={"id": "one", "value": "audit"},
                require_atomic=failure == "missing-atomic-guarantee",
            )
            try:
                with runtime.context(
                    request_context(request_id="other" if failure == "wrong-owner" else "owner")
                ):
                    runtime.execute(child)
            except expected_type as error:
                assert error.code == expected_code
                assert len(database.calls) == 1  # Child never reaches adapter execution.
                if handling == "escape":
                    raise
                if handling == "rollback-only":
                    transaction.set_rollback_only()
            else:
                pytest.fail("invalid child must be rejected before execution")
    except expected_type:
        assert handling == "escape"
    assert database.rows("events") == []
    assert database.rows("records") == ([("one", "source")] if handling == "caught" else [])


def test_one_operation_cannot_own_resources_from_two_catalogs():
    with pytest.raises(ValueError, match="every Operation Resource must belong to its Catalog"):
        Operation(
            "structured",
            "meridian.structured.put",
            "1.0.0",
            (
                ResourceRef.parse("structured:coretest.records"),
                ResourceRef.parse("evidence:coretest.events"),
            ),
        )


def test_escaping_engine_error_rolls_back_all_prior_children(database):
    runtime = database.runtime
    structured, evidence = runtime.catalog("structured"), runtime.catalog("evidence")
    with (
        runtime.context(request_context()),
        pytest.raises(ConflictError),
        runtime.transaction("structured:coretest.records"),
    ):
        runtime.execute(
            structured.put(resource="coretest.records", data={"id": "one", "value": "source"})
        )
        runtime.execute(
            evidence.append(resource="coretest.events", data={"id": "one", "value": "audit"})
        )
        runtime.execute(
            evidence.append(resource="coretest.events", data={"id": "one", "value": "duplicate"})
        )
    assert database.rows("records") == database.rows("events") == []


def test_rolled_back_transaction_result_is_not_replayed_as_a_durable_write(database):
    runtime = database.runtime
    expression = runtime.catalog("structured").put(
        resource="coretest.records", data={"id": "one", "value": "source"}
    )
    with runtime.context(request_context(idempotency_key="same-key")):
        with runtime.transaction("structured:coretest.records") as transaction:
            provisional = runtime.execute(expression)
            transaction.set_rollback_only()
        assert database.rows("records") == []
        durable = runtime.execute(expression)
        for field in ("id", "value", "recordVersion"):
            assert durable.data[field] == provisional.data[field]
        assert runtime.execute(expression).data == durable.data
    assert len(database.calls) == 2
    assert database.rows("records") == [("one", "source")]
