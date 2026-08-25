# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import replace
from importlib import metadata

import pytest

from meridian_storage import ConfigurationError, ErrorCode, Operation, OperationContext, ResourceRef
from meridian_storage.registry import CapabilityRequirement
from meridian_storage.runtime import SecretReference
from meridian_storage.runtime.idempotency import IdempotencyStore
from meridian_storage.runtime.secrets import (
    CompositeSecretResolver,
    EnvironmentSecretResolver,
    FileSecretResolver,
)
from meridian_storage.spi import (
    AdapterDescriptor,
    AdapterProbe,
    CapabilityManifest,
    ExecutionRequest,
    ExecutionResult,
    OperationCapability,
    PhysicalResource,
    PhysicalVerification,
    SecretValue,
    capability_violations,
)
from meridian_storage.spi.discovery import discover_components
from tests.support import FakeAdapterFactory, capability_manifest


def test_secret_value_never_renders_bytes() -> None:
    value = SecretValue(b"super-secret")
    assert value.reveal() == b"super-secret"
    assert "super-secret" not in repr(value)
    assert "super-secret" not in str(value)
    with pytest.raises(ValueError, match="non-empty"):
        SecretValue(b"")


def test_environment_and_file_secret_resolvers(tmp_path: object) -> None:
    environment = EnvironmentSecretResolver({"VALID_SECRET": "value"})
    assert environment.resolve(SecretReference("environment", "VALID_SECRET")).reveal() == b"value"
    with pytest.raises(ConfigurationError, match="invalid variable"):
        environment.resolve(SecretReference("environment", "bad-name"))
    with pytest.raises(ConfigurationError, match="could not be resolved"):
        environment.resolve(SecretReference("environment", "MISSING"))
    with pytest.raises(ConfigurationError, match="not supported"):
        environment.resolve(SecretReference("file", "/tmp/value"))

    path = tmp_path / "secret"  # type: ignore[operator]
    path.write_bytes(b"file-value")
    resolver = FileSecretResolver()
    assert resolver.resolve(SecretReference("file", str(path))).reveal() == b"file-value"
    with pytest.raises(ConfigurationError, match="absolute"):
        resolver.resolve(SecretReference("file", "relative"))
    symlink = tmp_path / "link"  # type: ignore[operator]
    symlink.symlink_to(path)
    with pytest.raises(ConfigurationError, match="non-symlink"):
        resolver.resolve(SecretReference("file", str(symlink)))


def test_composite_secret_resolver_routes_and_rejects_duplicates() -> None:
    composite = CompositeSecretResolver(
        (EnvironmentSecretResolver({"A": "b"}), FileSecretResolver())
    )
    assert composite.resolve(SecretReference("environment", "A")).reveal() == b"b"
    with pytest.raises(ConfigurationError, match="no resolver"):
        composite.resolve(SecretReference("unknown", "opaque"))
    with pytest.raises(ValueError, match="duplicate"):
        CompositeSecretResolver((EnvironmentSecretResolver({}), EnvironmentSecretResolver({})))


def test_capability_manifest_and_descriptor_are_deterministic() -> None:
    manifest = capability_manifest()
    assert manifest.fingerprint == capability_manifest().fingerprint
    assert manifest.adapter_id == "test.adapter"
    assert manifest.descriptor.capability_for("meridian.structured.get") is not None
    assert manifest.descriptor.capability_for("missing") is None
    assert manifest.to_dict()["engineProfile"] == "test-engine"
    with pytest.raises(ValueError, match="not advertised"):
        CapabilityManifest(manifest.descriptor, "test-engine", "2.0.0")
    with pytest.raises(ValueError, match="absent"):
        CapabilityManifest(
            manifest.descriptor,
            "test-engine",
            "1.0.0",
            available_operation_contracts=("missing",),
        )


def test_adapter_envelopes_validate_fingerprints_and_safe_metadata() -> None:
    manifest = capability_manifest()
    with pytest.raises(ValueError, match="CapabilityManifest"):
        AdapterProbe(object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="sha256"):
        PhysicalVerification("not-a-fingerprint")
    with pytest.raises(ValueError, match="bounded"):
        PhysicalVerification("sha256:" + "0" * 64, {"resource": "\n"})
    with pytest.raises(ValueError, match="bounded"):
        AdapterProbe(manifest, {"probe": "\n"})
    with pytest.raises(ValueError, match="bounded"):
        ExecutionResult({}, provenance={"driver": "\n"})

    ref = ResourceRef("structured", "n", "r")
    resource = PhysicalResource(ref, "sha256:" + "1" * 64, None, "relational")
    assert resource.schema_fingerprint is None
    operation = Operation("structured", "op", "1", (ref,), read_only=True, idempotent=True)
    context = OperationContext("principal", request_id="request")
    request = ExecutionRequest(
        operation,
        context,
        "request",
        "execution",
        "binding",
        1,
        "sha256:" + "2" * 64,
        1,
    )
    assert request.attempt == 1
    with pytest.raises(ValueError, match="operation"):
        replace(request, operation=object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="context"):
        replace(request, context=object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="registry_revision"):
        replace(request, registry_revision=0)
    with pytest.raises(ValueError, match="attempt"):
        replace(request, attempt=0)
    with pytest.raises(ValueError, match="non-negative"):
        ExecutionResult({}, result_bytes="1")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non-negative"):
        ExecutionResult({}, result_bytes=-1)


def test_operation_capability_validation_and_violations() -> None:
    capability = OperationCapability(
        "operation",
        ("1.0.0",),
        guarantees=("atomic",),
        limits={"page": 100},
    )
    descriptor = AdapterDescriptor(
        "adapter",
        "1.0.0",
        "driver",
        {"engine": ("1",)},
        (capability,),
    )
    manifest = CapabilityManifest(descriptor, "engine", "1")
    assert not capability_violations(
        manifest,
        (CapabilityRequirement("operation", "1.0.0", ("atomic",), {"page": 50}),),
    )
    reasons = [
        capability_violations(manifest, (requirement,))[0].reason
        for requirement in (
            CapabilityRequirement("missing", "1.0.0"),
            CapabilityRequirement("operation", "2.0.0"),
            CapabilityRequirement("operation", "1.0.0", ("serializable",)),
            CapabilityRequirement("operation", "1.0.0", minimum_limits={"page": 101}),
        )
    ]
    assert reasons == [
        "Operation contract is unavailable in the authenticated probe",
        "Operation version is not advertised",
        "required guarantee is not advertised",
        "limit 'page' is below the required minimum",
    ]
    with pytest.raises(ValueError, match="non-negative"):
        OperationCapability("op", ("1",), limits={"bad": -1})
    with pytest.raises(ValueError, match="unique"):
        AdapterDescriptor("a", "1", "d", {"e": ("1",)}, (capability, capability))


class _Distribution:
    name = "test-distribution"
    version = "1.0.0"


class _Point:
    def __init__(self, name: str, value: str, loaded: object, *, fail: bool = False) -> None:
        self.name = name
        self.value = value
        self.dist = _Distribution()
        self._loaded = loaded
        self._fail = fail

    def load(self) -> object:
        if self._fail:
            raise RuntimeError("unsafe vendor detail")
        return self._loaded


class _Points(tuple[object, ...]):
    def select(self, *, group: str) -> _Points:
        del group
        return self


def test_discovery_loads_sorts_and_records_injected_components(monkeypatch: object) -> None:
    first = FakeAdapterFactory("z.adapter")
    second = FakeAdapterFactory("a.adapter")
    monkeypatch.setattr(  # type: ignore[attr-defined]
        metadata,
        "entry_points",
        lambda: _Points((_Point("z", "module:factory", first),)),
    )
    result = discover_components(
        "test.group",
        (second,),
        component_id=lambda item: item.adapter_id,
    )
    assert tuple(result.components) == ("a.adapter", "z.adapter")
    assert result.records[0].injected
    assert result.records[1].distribution == "test-distribution"


def test_discovery_rejects_duplicate_names_ids_and_load_failures(monkeypatch: object) -> None:
    factory = FakeAdapterFactory()
    monkeypatch.setattr(  # type: ignore[attr-defined]
        metadata,
        "entry_points",
        lambda: _Points(
            (
                _Point("same", "a", factory),
                _Point("same", "b", FakeAdapterFactory("other")),
            )
        ),
    )
    with pytest.raises(ConfigurationError) as duplicate_name:
        discover_components("g", (), component_id=lambda item: item.adapter_id)
    assert duplicate_name.value.code == ErrorCode.DISCOVERY_DUPLICATE

    monkeypatch.setattr(  # type: ignore[attr-defined]
        metadata,
        "entry_points",
        lambda: _Points((_Point("one", "a", factory),)),
    )
    with pytest.raises(ConfigurationError, match="duplicate component"):
        discover_components("g", (factory,), component_id=lambda item: item.adapter_id)

    monkeypatch.setattr(  # type: ignore[attr-defined]
        metadata,
        "entry_points",
        lambda: _Points((_Point("bad", "a", factory, fail=True),)),
    )
    with pytest.raises(ConfigurationError) as failed:
        discover_components("g", (), component_id=lambda item: item.adapter_id)
    assert failed.value.cause is not None
    assert "unsafe vendor detail" not in str(failed.value.to_dict())


def test_idempotency_store_replays_conflicts_aborts_and_bounds() -> None:
    store = IdempotencyStore(1)
    claim = store.claim(("key",), "fingerprint")
    result = ExecutionResult({"ok": True})
    store.complete(claim, result)
    replay = store.claim(("key",), "fingerprint")
    assert replay.replay is result
    with pytest.raises(Exception) as conflict:
        store.claim(("key",), "different")
    assert conflict.value.code == ErrorCode.IDEMPOTENCY_CONFLICT
    replacement = store.claim(("replacement",), "fingerprint")
    store.abort(replacement)
    assert store.claim(("replacement",), "fingerprint").owner


def test_idempotency_store_capacity_and_owner_guards() -> None:
    store = IdempotencyStore(1)
    active = store.claim(("active",), "fingerprint")
    with pytest.raises(Exception, match="capacity"):
        store.claim(("other",), "fingerprint")
    with pytest.raises(RuntimeError, match="owner"):
        store.complete(replace(active, owner=False), ExecutionResult(None))
    store.abort(active)
