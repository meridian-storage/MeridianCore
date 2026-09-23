"""Owner deadline and transaction-cleanup tests; no native resources."""

from datetime import UTC, datetime, timedelta
from importlib import metadata
from threading import Event

import pytest

import meridian_storage.context as context_module
from meridian_storage import CommitOutcomeError, CommitState, MeridianTimeoutError, OperationContext
from tests.support import context, make_runtime


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.setattr(metadata, "entry_points", lambda: metadata.EntryPoints(()))


def test_deadline_uses_monotonic_clock_and_resolve_does_not_extend(monkeypatch):
    ticks = [100.0]
    monkeypatch.setattr(context_module.time, "monotonic", lambda: ticks[0])
    ctx = OperationContext("synthetic", deadline=datetime.now(UTC) + timedelta(seconds=2))
    remaining = ctx.remaining_seconds()
    ticks[0] += 1
    resolved = ctx.resolve_request_id()
    assert resolved.remaining_seconds() <= remaining - 1
    ticks[0] += 1
    with pytest.raises(MeridianTimeoutError):
        resolved.check_budget()


def test_cancellation_prevents_core_admission():
    event = Event()
    event.set()
    runtime, factory, *_ = make_runtime()
    runtime.start()
    try:
        with runtime.context(OperationContext("synthetic", cancellation=event)):  # noqa: SIM117
            with pytest.raises(MeridianTimeoutError):
                with runtime.transaction("investigation.cases"):
                    pytest.fail("must not enter")
        assert "session.begin" not in factory.runtimes[0].events
    finally:
        runtime.close()


def test_body_failure_survives_rollback_and_close_failure(monkeypatch):
    runtime, _factory, *_ = make_runtime()
    runtime.start()
    failure = ValueError("original body failure")
    try:
        with runtime.context(context()), pytest.raises(ValueError) as caught:  # noqa: SIM117
            with runtime.transaction("investigation.cases") as tx:
                session = tx._lease.frame.session

                def broken():
                    raise RuntimeError("cleanup failure")

                monkeypatch.setattr(type(session), "rollback", lambda _: broken())
                monkeypatch.setattr(type(session), "close", lambda _: broken())
                raise failure
        assert caught.value is failure
        assert tx.commit_state == CommitState.KNOWN_NOT_COMMITTED
    finally:
        runtime.close()


def test_core_precommit_timeout_has_known_not_committed_outcome(monkeypatch):
    runtime, factory, *_ = make_runtime()
    runtime.start()
    try:
        with runtime.context(context()), pytest.raises(CommitOutcomeError) as caught:  # noqa: SIM117
            with runtime.transaction("investigation.cases") as tx:
                monkeypatch.setattr(OperationContext, "remaining_seconds", lambda self, **kw: 0)
        assert caught.value.commit_state == tx.commit_state == CommitState.KNOWN_NOT_COMMITTED
        assert "session.commit" not in factory.runtimes[0].events
    finally:
        runtime.close()
