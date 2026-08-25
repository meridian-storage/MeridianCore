# SPDX-License-Identifier: Apache-2.0
"""Bounded in-process idempotency replay and conflict detection."""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass

from meridian_storage.errors import ConflictError, ErrorCode, UnavailableError
from meridian_storage.spi.adapters import ExecutionResult


@dataclass(slots=True)
class _Entry:
    fingerprint: str
    result: ExecutionResult | None = None
    pending: bool = True


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    key: tuple[str, ...]
    fingerprint: str
    owner: bool
    replay: ExecutionResult | None = None


class IdempotencyStore:
    def __init__(self, maximum_entries: int) -> None:
        self._maximum_entries = maximum_entries
        self._entries: OrderedDict[tuple[str, ...], _Entry] = OrderedDict()
        self._condition = threading.Condition(threading.RLock())

    def claim(self, key: tuple[str, ...], fingerprint: str) -> IdempotencyClaim:
        with self._condition:
            while True:
                entry = self._entries.get(key)
                if entry is None:
                    self._make_space()
                    self._entries[key] = _Entry(fingerprint=fingerprint)
                    return IdempotencyClaim(key=key, fingerprint=fingerprint, owner=True)
                if entry.fingerprint != fingerprint:
                    raise ConflictError(
                        ErrorCode.IDEMPOTENCY_CONFLICT,
                        "idempotency key was already used with a different request fingerprint",
                    )
                self._entries.move_to_end(key)
                if not entry.pending:
                    if entry.result is None:
                        raise RuntimeError("completed idempotency entry has no result")
                    return IdempotencyClaim(
                        key=key,
                        fingerprint=fingerprint,
                        owner=False,
                        replay=entry.result,
                    )
                self._condition.wait()

    def complete(self, claim: IdempotencyClaim, result: ExecutionResult) -> None:
        if not claim.owner:
            raise RuntimeError("only the idempotency claim owner may complete it")
        with self._condition:
            entry = self._entries.get(claim.key)
            if entry is None or entry.fingerprint != claim.fingerprint or not entry.pending:
                raise RuntimeError("idempotency claim is no longer active")
            entry.result = result
            entry.pending = False
            self._entries.move_to_end(claim.key)
            self._condition.notify_all()

    def abort(self, claim: IdempotencyClaim) -> None:
        if not claim.owner:
            return
        with self._condition:
            entry = self._entries.get(claim.key)
            if entry is not None and entry.fingerprint == claim.fingerprint and entry.pending:
                del self._entries[claim.key]
                self._condition.notify_all()

    def _make_space(self) -> None:
        while len(self._entries) >= self._maximum_entries:
            completed_key = next(
                (key for key, value in self._entries.items() if not value.pending), None
            )
            if completed_key is None:
                raise UnavailableError(
                    ErrorCode.RUNTIME_STATE,
                    "idempotency capacity is temporarily exhausted by active operations",
                    retryable=True,
                )
            del self._entries[completed_key]


__all__ = ["IdempotencyClaim", "IdempotencyStore"]
