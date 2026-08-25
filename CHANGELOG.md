<!-- SPDX-License-Identifier: Apache-2.0 -->

# Changelog

All notable changes are documented here. This project follows Semantic
Versioning for the Python distribution and its released public contracts.

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
