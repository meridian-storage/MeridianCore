<!-- SPDX-License-Identifier: Apache-2.0 -->

# Meridian V1 runtime contract

## Consumer model

The only V1 Catalog names are `structured`, `object`, `cache`, `evidence`, and
`streaming`. An installed Catalog package owns its mapping-first method surface
and returns an immutable `Expression`. Core validates the selected Catalog
method, asks that provider to normalize the Expression, verifies the resulting
serialized `Operation`, and then resolves it. Consumer code does not select,
import, or name adapters, engines, endpoints, physical resources, or execution
modes.

An Operation carries a versioned contract, one or more logical Resource
references, immutable input, Capability requirements, and read/idempotency
semantics. It has no consumer-authored identifier. Core generates a request ID
when needed and a fresh execution ID for every accepted execution; retry
attempts retain both identifiers.

## Lifecycle and startup validation

Construction accepts an immutable `RuntimeConfig` and allocates only in-memory
state. It does not discover packages, resolve secrets, open sockets, provision
infrastructure, create physical resources, or create threads.

`start()` is synchronized and idempotent after success. The winning caller runs
this fail-closed validation sequence while concurrent callers wait:

1. validate the closed `meridian-config.v1` document and its fingerprint;
2. discover trusted local Catalog, adapter, schema-provider, and plugin entry
   points;
3. validate Catalog manifests, exact method registries, package identities, and
   contract/fingerprint pins;
4. validate plugin manifests without instantiating plugins;
5. resolve and cache opaque identity, credential, and TLS secret references;
6. create and open each adapter runtime, run its authenticated probe, and verify
   adapter, engine profile/version, compatibility, and Capability pins;
7. load bootstrap and configured live schema/resource bundles and verify pins;
8. resolve every configured Resource through exactly one placement and verify
   its Operation Capability requirements;
9. obtain read-only physical verification for every Binding and require a
   complete opaque mapping plus the pinned physical fingerprint; and
10. build and atomically install one immutable registry snapshot before
    transitioning to `READY`.

Failure closes every adapter runtime created so far in reverse order, records a
redacted typed error, and leaves the instance non-restartable in `FAILED`. A
fresh instance is required.

`close()` rejects new work, waits for accepted Operations and transactions,
closes adapters in reverse order, and transitions to `CLOSED`. Closing a `NEW`,
`FAILED`, or already `CLOSED` instance is safe. Runtime instances cannot be
reused after process fork.

## Configuration and authority

Core loads one bounded UTF-8 JSON document. Required top-level groups are
`formatVersion`, `profile`, `catalogs`, `resources`, `schemas`, `bindings`,
`placements`, and `validation`; `telemetry` and `extensions` are optional. Every
V1 object is closed except for explicitly named extension/settings maps.

Bindings contain engine profile/version and deployment pins but only opaque
secret references, never secret bytes. Platform/application infrastructure code
owns adapter and engine selection, provisioning/reference, state, identity,
ACLs, migrations, recovery, and lifecycle. Core owns only in-process loading,
validation, logical resolution, and execution routing. It performs no production
DDL or control-plane mutation.

## Operation routing

Each execution requires an `OperationContext`. Core captures a registry
revision, validates required tenant/scope values, merges Catalog and Resource
Capability requirements, and proves that all referenced Resources resolve to
one Binding. Cross-Binding Operations fail before an adapter session is opened.

Core applies the tighter of the caller and configured deadlines. Reads may retry
typed retryable failures within the bounded policy. Mutations may retry only
when the Operation is declared idempotent and the context has an idempotency
key. The idempotency key is scoped by Binding, principal, tenant, and the complete
effective scope mapping supplied to the adapter. Scope order is insignificant;
request, correlation, trace, and deadline metadata do not change replay identity.
A different request fingerprint within that domain conflicts. Failed owners release their claim. Result byte
counts are enforced before returning an immutable `OperationResult`.

Catalog providers own put syntax and normalize existence mode into Operation
input. Core preserves that input in the request fingerprint, matches the
normalized Operation version to its Catalog manifest, and checks exact adapter
versions and guarantees before opening a mutation session. It does not supply
defaults for serialized omissions or downgrade an unsupported contract to upsert.
The existing Expression and Operation envelopes can carry a Catalog-owned new
operation version without changing the Core SPI. Tests exercise a version-2
Catalog manifest and operation with the existing deployment contract pin.

## Transactions

Transactions are context-local and Binding-scoped. Core requires the adapter to
advertise the versioned transaction contract with both `atomic` and
`no-dirty-reads` guarantees. The first boundary opens one transactional adapter
session and pins the registry snapshot. Compatible nested boundaries join that
session. A nested exception or explicit rollback-only flag causes the outermost
boundary to roll back. Runtime identity, Binding, and request owner must all
match. Core never retries work inside a transaction or replays a transaction
callback.

Each Operation references Resources from exactly one Catalog. Separate child
Operations from different Catalogs may join the same explicit transaction and
session when Binding, runtime, request owner, and capabilities match. Returned
results remain provisional until the outermost commit. An exception escaping
that boundary or `set_rollback_only()` rolls back its writes. Catching a child
validation error inside the boundary retains existing behavior: earlier writes
can commit unless the caller re-raises or marks rollback-only. Core neither
generates audit Data nor checks for omitted audit Operations. Required evidence
uses the exact `atomic-evidence` guarantee; another guarantee is not an alias.

## Released dependency conformance

`tests/real_engine` runs the facade on disposable PostgreSQL/PostGIS local and
primary-with-two-standby cluster profiles. It verifies scope-separated writes,
same-scope replay/conflict, mixed-Catalog session reuse, visibility before commit,
rollback, ownership and cross-Binding rejection. Adapter calls are observed but
their execution, descriptors, and guarantees are unmodified. Current released
OpenSearch projection and ClickHouse append-version descriptors reject the new
authoritative put version. These tests run before CI artifacts or releases pass.

Fixtures install exact published PostgreSQL, Semantics, Query, Evidence,
OpenSearch, and ClickHouse 1.0.0 wheels, then replace only Core with its candidate
wheel. This is an explicit compatibility experiment: those releases' metadata
still pins Core 1.0.0. Consumers must publish their updated dependency pins and
put contracts before treating the new set as a resolver-compatible release.
Core's package version changes to 1.0.1; its SPI compatibility pin remains 1.0.0.
The released PostgreSQL fixture rejects `atomic-evidence` as expected; advertising
and verifying that capability belongs to its independent adapter task.

## Handles and registry refresh

`NamespaceHandle`, `SchemaHandle`, and `ResourceHandle` expose immutable logical
metadata from the active snapshot. They intentionally omit Binding identifiers,
adapter identity, endpoints, credentials, and physical mappings.

Refresh is explicit. Core reloads pinned providers, obtains fresh read-only
physical evidence, and builds a candidate snapshot off-path. The atomic swap
rejects removal of an active Resource, a change to its Schema reference, or a
Binding move. In-flight work retains its captured revision; newly accepted work
observes the replacement only after the swap.
