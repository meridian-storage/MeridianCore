"""Absolute admission bounds survive clock advance; no sleeps or native I/O."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib import import_module, metadata
from threading import Event

import pytest

import meridian_storage.context as context_module
from meridian_storage import MeridianTimeoutError, OperationContext, bind_context, current_context
from tests.support import make_runtime

runtime_module = import_module("meridian_storage.runtime.runtime")


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        tick = 100.0
        wall = datetime(2026, 9, 21, tzinfo=UTC)

    value = Clock()

    class WallClock:
        @staticmethod
        def now(tz):
            return value.wall

    monkeypatch.setattr(context_module.time, "monotonic", lambda: value.tick)
    monkeypatch.setattr(context_module, "datetime", WallClock)
    monkeypatch.setattr(runtime_module, "datetime", WallClock)
    return value


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.setattr(metadata, "entry_points", lambda: metadata.EntryPoints(()))
    instance, *_ = make_runtime()
    return instance


def effective(runtime, ctx, *, timeout_ms=5000):
    binding = runtime._config.bindings[0]
    binding = replace(binding, client=replace(binding.client, operation_timeout_ms=timeout_ms))
    return runtime._effective_context(ctx, binding)


@pytest.mark.parametrize("utc_seconds", [None, 0.5, 10.0])
def test_absolute_bound_intersects_utc_without_extension(clock, runtime, utc_seconds):
    ctx = OperationContext(
        "owner",
        monotonic_deadline=102.0,
        deadline=None if utc_seconds is None else clock.wall + timedelta(seconds=utc_seconds),
    )
    expected = 102.0 if utc_seconds is None else min(102.0, 100.0 + utc_seconds)
    clock.tick += 0.25
    # Wall-clock rollback must not enlarge the bound already admitted from UTC.
    clock.wall -= timedelta(hours=1)
    result = effective(runtime, ctx)
    assert result.monotonic_deadline == expected
    assert result.remaining_seconds() == expected - clock.tick
    assert result.resolve_request_id().monotonic_deadline == expected


def test_utc_only_context_retains_original_conversion(clock, runtime):
    ctx = OperationContext("owner", deadline=clock.wall + timedelta(seconds=2))
    clock.wall -= timedelta(days=1)
    clock.tick += 1
    assert effective(runtime, ctx).monotonic_deadline == 102.0


@pytest.mark.parametrize("inherited", [None, 101.0, 110.0])
def test_client_cap_can_only_tighten(clock, runtime, inherited):
    ctx = OperationContext("owner", monotonic_deadline=inherited)
    result = effective(runtime, ctx, timeout_ms=500)
    assert result.monotonic_deadline == 100.5
    assert result.remaining_seconds() == 0.5


def test_global_validation_cap_is_intersected(clock, runtime):
    runtime._config = replace(
        runtime._config,
        validation=replace(runtime._config.validation, default_operation_timeout_ms=250),
    )
    assert effective(runtime, OperationContext("owner")).monotonic_deadline == 100.25


@pytest.mark.parametrize("deadline", [100.0, 99.0, -1.0])
@pytest.mark.parametrize("pause", [0.0, 1.0, 1000000.0])
def test_expired_admission_cannot_resurrect(clock, runtime, deadline, pause):
    ctx = OperationContext("owner", monotonic_deadline=deadline)
    clock.tick += pause
    assert ctx.remaining_seconds() == 0
    with pytest.raises(MeridianTimeoutError):
        effective(runtime, ctx)
    with pytest.raises(MeridianTimeoutError):
        effective(runtime, ctx.resolve_request_id())


@pytest.mark.parametrize("pause", [0.5, 1000000.0])
def test_r2a10_pause_between_clock_samples(clock, runtime, monkeypatch, pause):
    ctx = OperationContext("owner", monotonic_deadline=100.0)
    samples = iter((99.0, 99.0, 100.0 + pause))
    monkeypatch.setattr(context_module.time, "monotonic", lambda: next(samples, 100.0 + pause))
    with pytest.raises(MeridianTimeoutError):
        effective(runtime, ctx)
    assert ctx.remaining_seconds() == 0.0


@pytest.mark.parametrize("cancel", [False, True])
def test_pause_or_cancellation_during_context_construction(clock, runtime, monkeypatch, cancel):
    event = Event()
    ctx = OperationContext("owner", monotonic_deadline=102.0, cancellation=event)
    original_replace = runtime_module.replace

    def delayed_replace(*args, **kwargs):
        if cancel:
            event.set()
        else:
            clock.tick += 1000
        return original_replace(*args, **kwargs)

    monkeypatch.setattr(runtime_module, "replace", delayed_replace)
    with pytest.raises(MeridianTimeoutError):
        effective(runtime, ctx)


def test_cancelled_unbounded_context_stays_cancelled(clock, runtime):
    event = Event()
    ctx = OperationContext("owner", cancellation=event)
    event.set()
    clock.tick += 1000000
    with bind_context(ctx) as resolved:
        assert resolved.cancellation is event
        with pytest.raises(MeridianTimeoutError):
            effective(runtime, resolved)


def test_client_budget_consumed_during_construction_is_not_restarted(clock, runtime, monkeypatch):
    original_replace = runtime_module.replace

    def delayed_replace(*args, **kwargs):
        clock.tick += 1
        return original_replace(*args, **kwargs)

    monkeypatch.setattr(runtime_module, "replace", delayed_replace)
    with pytest.raises(MeridianTimeoutError):
        effective(runtime, OperationContext("owner"), timeout_ms=500)


def test_nested_contexts_and_new_request_ids_keep_absolute_bound(clock, runtime):
    original = OperationContext("owner", deadline=clock.wall + timedelta(seconds=2))
    with bind_context(original) as outer:
        assert outer.monotonic_deadline == 102.0
        clock.tick += 0.25
        narrowed = effective(runtime, outer)
        with bind_context(replace(narrowed, request_id=None)) as inner:
            assert inner.request_id != outer.request_id
            clock.tick += 0.25
            nested = effective(runtime, inner)
            assert nested.monotonic_deadline == 102.0
            assert nested.remaining_seconds() == 1.5
        assert current_context() is outer
        clock.tick += 1000
        with pytest.raises(MeridianTimeoutError):
            effective(runtime, outer)


def test_wall_clock_advance_can_tighten_but_not_extend(clock, runtime):
    ctx = OperationContext("owner", deadline=clock.wall + timedelta(seconds=2))
    clock.wall += timedelta(seconds=3)
    with pytest.raises(MeridianTimeoutError):
        effective(runtime, ctx)
