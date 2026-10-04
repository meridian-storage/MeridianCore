<!-- SPDX-License-Identifier: Apache-2.0 -->

# Meridian Storage Core

[![CI](https://github.com/zephytiju/MeridianCore/actions/workflows/ci.yml/badge.svg)](https://github.com/zephytiju/MeridianCore/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-%3E%3D3.12-blue.svg)](pyproject.toml)

`meridian-storage-core` is the Python 3.12+ in-process runtime and stable public
contract for Meridian V1. Consumer code expresses intent through mapping-first
Catalog Expressions. Core validates and serializes an engine-neutral Operation,
resolves every referenced logical Resource to exactly one configured Binding,
and executes it through a trusted local adapter.

The V1 Catalog registry is exactly `structured`, `object`, `cache`, `evidence`,
and `streaming`. Catalog syntax is supplied by independently released Catalog
packages. Query and projection are shared libraries, while specialized storage
behaviors are Catalog profiles rather than additional Catalog names.

This repository owns one Python distribution and no concrete Catalog or
adapter. It does not provision engines, execute production DDL, expose a
network service, own application domain models, or accept secret bytes in
runtime configuration.

## Install

```console
python -m pip install meridian-storage-core
```

A deployment also installs the independently released Catalog, schema-provider,
adapter, and optional plugin distributions selected by its infrastructure code.
Business packages do not import or name adapter or engine concepts.

## Public facade

```python
from meridian_storage import Meridian, OperationContext

meridian = Meridian.from_environment()
meridian.start()
structured = meridian.catalog("structured")

request = OperationContext(
    principal_ref="identity:user/123",
    tenant="tenant-a",
    scope={"workspace": "investigations"},
)

with meridian.context(request):
    expression = structured.get(
        resource="investigation.cases",
        where={"id": "case-2026-001"},
    )
    result = meridian.execute(expression)
    case = result.data

resource = meridian.resource("structured:investigation.cases")
assert resource.profile == "relational"
meridian.close()
```

The runtime generates request and execution identifiers when they are absent;
consumer code never authors an Operation identifier. Logical handles expose
Namespace, Schema, and Resource metadata without revealing Bindings, endpoints,
physical mappings, or engine details.

`Meridian.from_environment()` reads the bounded JSON document named by
`MERIDIAN_CONFIG`. Construction performs no external I/O. `start()` discovers
trusted local entry points, validates package and contract pins, resolves opaque
secret references, opens and probes every Binding, loads schemas, resolves
placements, verifies capabilities and physical mappings, and atomically installs
an immutable registry snapshot. Any missing, ambiguous, incompatible, or
unverifiable input leaves that runtime instance in terminal `FAILED` state.

See the [example configuration](examples/meridian-config.example.json) and its
released [JSON Schema](contracts/runtime-config/meridian-config.v1.schema.json).

## V1 guarantees

- One reusable in-process runtime per application composition root.
- One internal Binding per serialized Operation and per transaction.
- Immutable Namespace, Schema, Resource, context, manifest, Operation, and
  result envelopes.
- Explicit `NEW → STARTING → READY → DRAINING → CLOSED` lifecycle, with terminal
  `FAILED` startup state and idempotent close.
- Deadline bounds, result-size limits, context propagation, bounded safe retry,
  and scoped idempotency replay.
- Atomic registry refresh: active Resources cannot disappear, change schema, or
  move Binding while the runtime is active.
- Typed, redacted errors with stable codes and safe adapter provenance.
- Deterministic configuration, Catalog, schema, capability, physical, and
  registry fingerprints.
- Local extension discovery through `meridian_storage.catalogs`,
  `meridian_storage.adapters`, `meridian_storage.schemas`, and
  `meridian_storage.plugins`.

Transactions require the selected adapter to advertise `atomic` and
`no-dirty-reads` guarantees. Nested transactions join only when runtime,
Binding, and request owner match; any nested failure marks the outer transaction
for rollback. A transaction callback is never replayed by Core.

Replay identity includes the complete effective scope mapping as well as Binding,
principal, and tenant. Separate single-Catalog Operations can share an explicit
transaction across Catalogs on the same capable Binding and request owner. Results
are provisional until the outermost commit. See the
[runtime contract](docs/runtime-contract.md) for caught-validation and explicit
rollback-only behavior.

## Adapter conformance

Adapter repositories run the shipped black-box conformance runner against their
own fixtures:

```python
from meridian_storage.testing.adapter_conformance import run_adapter_conformance

report = run_adapter_conformance(target)
assert report.adapter_id == "vendor.adapter"
```

The report contains contract and fingerprint evidence but excludes secret
bytes, endpoints, and physical mapping values. Unit fixtures in this repository
validate Core; an adapter release remains responsible for conformance against
its supported real engine versions. See [Adapter authoring](docs/adapter-authoring.md).

## Scope boundary

The V1 public surface is enumerated in
[`meridian-core.v1.json`](contracts/public-api/meridian-core.v1.json). Engine-
specific expression escape hatches, cross-Binding transactions, federated
execution, production DDL, arbitrary engine administration, and untrusted remote
adapter loading are outside V1. Streaming Catalog behavior and broker-specific
adapters are separate downstream distributions; Core has no broker client
dependency.

## Development

```console
python -m pip install -e '.[test]'
ruff format --check src tests scripts
ruff check src tests scripts
mypy src/meridian_storage
python scripts/verify_contracts.py
pytest --cov=meridian_storage --cov-report=term-missing
python -m build
python scripts/verify_artifacts.py dist/*
```

CI also installs released adapter wheels and tests the built Core wheel against
disposable PostgreSQL local and two-standby cluster profiles. The reproducible
runner is `bash scripts/run-postgresql-conformance.sh local|cluster <test command>`;
see the [conformance workflow](.github/workflows/conformance.yml) for the isolated
environment and exact published fixtures.

More detail is in the [runtime contract](docs/runtime-contract.md),
[error model](docs/error-model.md), [contribution guide](CONTRIBUTING.md), and
[release procedure](RELEASING.md).

## License

Copyright 2026 Meridian contributors. Licensed under the Apache License 2.0;
see [LICENSE](LICENSE) and [NOTICE](NOTICE).

Release provenance, historical tested versions, deployment integrity and retained
contract checks are documented in [release validation](docs/release-validation.md).

## Build and release (Jumbo)

This repository is jumbo-managed (Jumbo Build & Versioning Standard,
section 3.5): resolution, builds, and releases run through jumbo, never
ad-hoc pip/uv installs.

```sh
jumbo lock   # resolve internal packages from the JumboIndex, third-party from PyPI
jumbo build  # build + tests at the resolved closure
```

The internal dependencies (the internal platform packages) are resolved from the JumboIndex;
the lock records the exact promoted build of each. Consumers likewise
resolve this package (`meridian-storage-core`) from the JumboIndex. Releases are dispatch-only through `.github/workflows/jumbo-publish.yml`;
as a public package, external publication is driven by the jumbo-computed
version, and every artifact's SHA-256 is recorded in the append-only
JumboIndex.
