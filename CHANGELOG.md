<!-- SPDX-License-Identifier: Apache-2.0 -->

# Changelog

All notable changes are documented here. This project follows Semantic
Versioning for the Python distribution and its released public contracts.

## 1.1.0 - 2026-09-07

- Treat descriptor tested-release tables as historical metadata, preserving
  profile identity, operation contracts and canonical V1 fingerprints.
- Share contract and deployment-lock validation between runtime and the public
  conformance runner. Keep legacy coreVersion as the SPI contract; expose
  explicit installed-distribution and authenticated-observation pins.
- Add optional authenticated server-release provenance without changing V1
  manifest/config wire fields or inventing observations for legacy adapters.
- Ship canonical descriptor/config/manifest fixtures and classify every Core
  gate. Capture real PostgreSQL local/cluster image digests, observed versions,
  package download hashes and test results in release conformance artifacts.

## 1.0.1 - 2026-09-06

- Include canonical effective scope in pre-dispatch replay identity, preventing
  result reuse and suppressed writes across scopes with the same idempotency key.
- Preserve the existing same-scope replay/conflict and transaction behavior.
- Verify Catalog-owned mode fingerprints and exact version/capability rejection
  using the unchanged public SPI and serialization envelopes.
- Gate CI and releases on built-wheel conformance against published adapters,
  including real PostgreSQL local and two-standby cluster profiles, explicit
  mixed-Catalog composition, provisional results and rollback boundaries.

## 1.0.0 - 2026-08-24

Initial Meridian V1 release:

- in-process `Meridian` lifecycle and stable root facade;
- exact five-Catalog registry with mapping-first Expression and serialized
  Operation contracts;
- closed runtime configuration and machine-readable JSON Schema;
- trusted local adapter, schema-provider, and plugin discovery;
- immutable logical resource registry and deterministic binding resolution;
- capability and physical fingerprint verification with fail-closed startup;
- bounded operation context, safe retry, idempotency, and result enforcement;
- binding-scoped transactions requiring atomic/no-dirty-read guarantees and
  atomic compatible registry refresh;
- typed, redacted public errors and bounded telemetry observer contract;
- adapter SPI, capability manifest contract, and black-box conformance runner;
- no concrete Catalog, engine, broker client, or adapter dependency;
- Apache-2.0 source, package metadata, CI, reproducible package checks, SBOM,
  and release provenance.
