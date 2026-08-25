# SPDX-License-Identifier: Apache-2.0
"""Safe default resolvers for opaque environment and mounted-file references."""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Iterable, Mapping
from pathlib import Path

from meridian_storage.errors import ConfigurationError, ErrorCode, SafeCause
from meridian_storage.spi.adapters import SecretResolver, SecretValue

from .config import SecretReference

_ENVIRONMENT_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_MAX_SECRET_BYTES = 1024 * 1024


class EnvironmentSecretResolver:
    provider = "environment"

    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self._environ = os.environ if environ is None else environ

    def resolve(self, reference: SecretReference) -> SecretValue:
        if reference.provider != self.provider:
            raise ConfigurationError(
                ErrorCode.CONFIG_SECRET_REFERENCE,
                f"secret provider {reference.provider!r} is not supported by this resolver",
            )
        if _ENVIRONMENT_NAME_RE.fullmatch(reference.reference) is None:
            raise ConfigurationError(
                ErrorCode.CONFIG_SECRET_REFERENCE,
                "environment secret reference has an invalid variable name",
            )
        value = self._environ.get(reference.reference)
        if value is None:
            raise ConfigurationError(
                ErrorCode.CONFIG_SECRET_REFERENCE,
                "an environment secret reference could not be resolved",
            )
        encoded = value.encode("utf-8")
        if not encoded or len(encoded) > _MAX_SECRET_BYTES:
            raise ConfigurationError(
                ErrorCode.CONFIG_SECRET_REFERENCE,
                "resolved secret bytes are empty or exceed the configured bound",
            )
        return SecretValue(encoded)


class FileSecretResolver:
    provider = "file"

    def resolve(self, reference: SecretReference) -> SecretValue:
        if reference.provider != self.provider:
            raise ConfigurationError(
                ErrorCode.CONFIG_SECRET_REFERENCE,
                f"secret provider {reference.provider!r} is not supported by this resolver",
            )
        path = Path(reference.reference)
        if not path.is_absolute():
            raise ConfigurationError(
                ErrorCode.CONFIG_SECRET_REFERENCE,
                "mounted-file secret references must be absolute paths",
            )
        try:
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise ConfigurationError(
                    ErrorCode.CONFIG_SECRET_REFERENCE,
                    "mounted-file secret reference must name a regular non-symlink file",
                )
            if metadata.st_size <= 0 or metadata.st_size > _MAX_SECRET_BYTES:
                raise ConfigurationError(
                    ErrorCode.CONFIG_SECRET_REFERENCE,
                    "mounted-file secret bytes are empty or exceed the configured bound",
                )
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            try:
                opened = os.fstat(descriptor)
                if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
                    metadata.st_dev,
                    metadata.st_ino,
                ):
                    raise ConfigurationError(
                        ErrorCode.CONFIG_SECRET_REFERENCE,
                        "mounted-file secret changed during secure resolution",
                    )
                with os.fdopen(descriptor, "rb", closefd=False) as stream:
                    value = stream.read(_MAX_SECRET_BYTES + 1)
                if not value or len(value) > _MAX_SECRET_BYTES:
                    raise ConfigurationError(
                        ErrorCode.CONFIG_SECRET_REFERENCE,
                        "mounted-file secret bytes are empty or exceed the configured bound",
                    )
                return SecretValue(value)
            finally:
                os.close(descriptor)
        except ConfigurationError:
            raise
        except OSError as exc:
            raise ConfigurationError(
                ErrorCode.CONFIG_SECRET_REFERENCE,
                "a mounted-file secret reference could not be resolved",
                cause=SafeCause.from_exception(exc),
            ) from exc


class CompositeSecretResolver:
    def __init__(self, resolvers: Iterable[SecretResolver]) -> None:
        by_provider: dict[str, SecretResolver] = {}
        for resolver in resolvers:
            provider = getattr(resolver, "provider", None)
            if not isinstance(provider, str) or not provider:
                raise ValueError("a composite secret resolver requires stable provider ids")
            if provider in by_provider:
                raise ValueError(f"duplicate secret resolver provider {provider!r}")
            by_provider[provider] = resolver
        self._resolvers = by_provider

    def resolve(self, reference: SecretReference) -> SecretValue:
        resolver = self._resolvers.get(reference.provider)
        if resolver is None:
            raise ConfigurationError(
                ErrorCode.CONFIG_SECRET_REFERENCE,
                f"no resolver is configured for secret provider {reference.provider!r}",
            )
        return resolver.resolve(reference)


def default_secret_resolver(
    environ: Mapping[str, str] | None = None,
) -> CompositeSecretResolver:
    return CompositeSecretResolver(
        (EnvironmentSecretResolver(environ=environ), FileSecretResolver())
    )


__all__ = [
    "CompositeSecretResolver",
    "EnvironmentSecretResolver",
    "FileSecretResolver",
    "default_secret_resolver",
]
