# SPDX-License-Identifier: Apache-2.0
"""Binding-scoped transaction context with safe nested joining."""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass
from types import TracebackType
from typing import TYPE_CHECKING, Protocol

from meridian_storage.registry.resources import ResourceRef
from meridian_storage.spi.adapters import AdapterSession

if TYPE_CHECKING:
    from meridian_storage.registry import RegistrySnapshot


@dataclass(slots=True)
class TransactionFrame:
    runtime_identity: int
    binding_id: str
    owner_request_id: str
    resource_ref: str
    session: AdapterSession
    snapshot: RegistrySnapshot
    depth: int = 1
    rollback_only: bool = False


@dataclass(slots=True)
class TransactionLease:
    frame: TransactionFrame
    nested: bool
    token: Token[TransactionFrame | None] | None = None
    exited: bool = False


_current_transaction: ContextVar[TransactionFrame | None] = ContextVar(
    "meridian_transaction", default=None
)


def current_transaction() -> TransactionFrame | None:
    return _current_transaction.get()


def install_transaction(frame: TransactionFrame) -> Token[TransactionFrame | None]:
    return _current_transaction.set(frame)


def reset_transaction(token: Token[TransactionFrame | None]) -> None:
    _current_transaction.reset(token)


class TransactionRuntime(Protocol):
    def _enter_transaction(
        self, resource: ResourceRef | str | Mapping[str, object]
    ) -> TransactionLease: ...

    def _exit_transaction(
        self,
        lease: TransactionLease,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class Transaction:
    """Public transaction boundary; adapter sessions remain private."""

    def __init__(
        self,
        runtime: TransactionRuntime,
        resource: ResourceRef | str | Mapping[str, object],
    ) -> None:
        self._runtime = runtime
        self._resource = resource
        self._lease: TransactionLease | None = None

    def __enter__(self) -> Transaction:
        if self._lease is not None:
            raise RuntimeError("a Transaction context manager cannot be entered twice")
        self._lease = self._runtime._enter_transaction(self._resource)
        return self

    def set_rollback_only(self) -> None:
        if self._lease is None or self._lease.exited:
            raise RuntimeError("transaction is not active")
        self._lease.frame.rollback_only = True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._lease is None:
            raise RuntimeError("transaction was not entered")
        self._runtime._exit_transaction(self._lease, exc_type, exc, traceback)


__all__ = ["Transaction"]
