<!-- SPDX-License-Identifier: Apache-2.0 -->

# Adapter authoring

An adapter is an independently released distribution. It depends on
`meridian-storage-core`, owns its own package/repository, and registers one
trusted local entry point in `meridian_storage.adapters`.

```toml
[project.entry-points."meridian_storage.adapters"]
vendor_adapter = "vendor_meridian_adapter:AdapterFactory"
```

The entry point resolves to an object or zero-argument class implementing
`AdapterFactory`. Its stable `adapter_id` must match the deployment Binding.
`create()` receives an `AdapterCreateContext` containing the immutable
`BindingConfig` and resolved `SecretValue` objects for identity, credential, and
configured TLS material. Secret bytes are available only through
`SecretValue.reveal()` and must never enter exceptions, logs, telemetry,
evidence, fingerprints, or returned values.

## Runtime and session lifecycle

An `AdapterRuntime` implements `open`, `probe`, `verify_physical`,
`open_session`, and `close`. A session implements `begin`, `execute`, `commit`,
`rollback`, and `close`, even when the adapter does not advertise transaction
support. Core calls transactional methods only after validating the versioned
transaction Capability and its guarantees.

`probe()` is authenticated and returns an immutable `CapabilityManifest` for
the selected engine profile and version. The descriptor declares:

- stable adapter contract, driver, and supported engine versions;
- each supported versioned Operation contract;
- guarantees and numeric limits per Operation;
- cursor, migration, and health-probe behavior; and
- authenticated availability for the selected Binding.

The canonical Capability fingerprint is deployment-pinned. A change requires a
new reviewed deployment pin; Core never silently accepts drift.

`verify_physical()` is read-only. It receives generic `PhysicalResource`
requests and returns a canonical fingerprint plus exactly one opaque, non-empty
mapping for every requested logical Resource. It must not create, migrate,
repair, or administer engine state. Infrastructure code owns those actions.

`execute()` accepts only an immutable engine-neutral `ExecutionRequest` and
returns an `ExecutionResult` with a bounded data payload, byte count, and safe
provenance. Adapters translate known engine failures into the public typed error
hierarchy. Core normalizes unknown exceptions to redacted, non-retryable
`InternalError` values.

## Conformance and release evidence

Construct `AdapterConformanceTarget` with ordinary adapter fixtures and run the
public black-box suite:

```python
def test_meridian_v1_conformance(adapter_target):
    from meridian_storage.testing.adapter_conformance import run_adapter_conformance

    report = run_adapter_conformance(adapter_target)
    assert report.capability_fingerprint == (
        adapter_target.create_context.binding.required_capability_fingerprint
    )
```

The runner checks factory/runtime/session protocols, identity and contract
compatibility, deterministic manifests and physical verification, exact mapping
coverage, normalized results, advertised transaction lifecycle, and cleanup.
Its `to_dict()` output is deterministic and redacted.

Core's fake fixtures prove only the Core integration contract. Each adapter
release must additionally run the shared suite against every supported real
engine profile/version and retain compatibility pins, security/lifecycle
evidence, and artifact fingerprints in its own release ledger.
