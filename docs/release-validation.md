<!-- SPDX-License-Identifier: Apache-2.0 -->

# Release provenance and contract validation

The owning deployment selects library releases and Engine images independently.
A historical recipe or a descriptor's `supportedEngineVersions` values cannot
reject an otherwise contract-conforming selection. Core does not claim that an
unlisted combination works; metadata acceptance is unverified until tested with
real Engines. `compatibility.json` and real-engine requirements record exact
reproducible experiments, never startup allowlists.

## Gate inventory (all Core entry points)

| Gate / owner | Classification | Behavior |
| --- | --- | --- |
| `AdapterDescriptor.supported_engine_versions` values / `CapabilityManifest` construction | Release metadata | Preserve nonempty, bounded, unique historical values and canonical content. Remove membership rejection. |
| Descriptor profile keys and Binding profile | Contract identity | Reject absent or mismatched profile identities. Core has no engine-family registry or provider provisioning helper. All advertised profiles use this path. |
| Adapter contract, Catalog/Schema/Plugin provider ranges and Operation versions | Contract | Retain supported ranges, exact Operation requirements, guarantees, limits, availability and public SPI checks. |
| `compatibilityPins.coreVersion` | Legacy Core SPI contract | Remains `1.0.0`, as documented in Core 1.0.1. Never reinterpret persisted V1 pins as distribution releases. `coreContractVersion` is an explicit equivalent name. |
| `compatibilityPins.coreDistributionVersion` | Deployment integrity | Optional exact deployment-selected lock compared with the installed Core distribution version. No compiled recipe selects its expected value. |
| `compatibilityPins.observedEngineVersion` | Deployment integrity | Optional exact observed server-release expectation; missing observations fail. Manifest extensions cannot spoof this observation. |
| Binding `engineVersion`, `driver`, other declared compatibility pins | Deployment integrity / declared protocol | Compare the selected deployment document with the actual manifest, retaining drift errors. These are not tested-table membership checks. |
| Capability, Catalog, Schema, Resource, config and physical fingerprints | Content integrity | Preserve canonical hashes and exact declared expectations. Release metadata changes may require a new deployment pin, without proving semantic incompatibility. |
| Runtime config, coordinates, secrets, TLS, namespace and placements | Configuration / security / integrity | Closed fields, required values, valid references and fingerprint shapes remain mandatory. Authentication, provider features and physical verification remain adapter-owned. |
| `run_adapter_conformance` | Same contract/integrity checks | Calls the same validator as startup; no helper-specific release gate. Retains physical mapping, execution, transaction and cleanup checks. |
| Distribution discovery / package checks | Package ownership / release integrity | Retain expected package identity. Catalog package releases are manifest provenance, not a compiled release predicate. Build/tag/ledger checks verify the artifact being published. |
| CI fixture package pins and PostgreSQL image | Conformance provenance | Retain released dependencies and both real-engine profiles. Exact tested versions/hashes/digests are recorded for each run; no runtime fallback or weakened acceptance. |

Package installation and image builds own full lock resolution, package hashes
and image digests. Core's closed V1 configuration does not introduce a second
package manager or invent new lock fields. Unknown or missing compatibility
expectations still fail; fingerprint and Engine selection mismatches retain
fail-closed behavior. Provider, protocol, auth/TLS and physical schema checks
remain owned by separately released adapters.

## Provenance and V1 migration

`AdapterProbe.observed_engine_version` is optional, bounded, non-secret Python
SPI metadata. Adapter implementations supply it only from authenticated server
observations. `None` means unavailable; Core never derives it from config,
manifest fields, descriptor tables or arbitrary evidence strings. Startup
`adapter-probe` details distinguish `selectedEngineVersion`,
`manifestEngineVersion`, `observedEngineVersion` (or `unavailable`),
`coreDistributionVersion` and `coreContractVersion`. A successful startup proves
its required checks, not blanket compatibility with all future releases.

Legacy S3 `2006-03-01` is an API identifier and OCI `1.1.1` is a Distribution
specification identifier. Their manifest/config fields retain that meaning;
server software releases go only in the separate observation. Core does not
perform provider negotiation or assert support for arbitrary protocol versions.

Core 1.1.0 changes no V1 descriptor, manifest or config serialized fields or
canonical algorithm. The new Python probe field and optional pins are additive.
A future incompatible wire change requires a new explicit format and separately
released consumer changes. Old descriptors and legacy coreVersion pins continue
to round trip byte-for-byte. `contracts/adapter-capability/fixtures/` ships in
the sdist and at `meridian_storage/spi/contracts/fixtures/` in the wheel for
released-artifact consumers. Synthetic fixtures prove only metadata behavior.

## Exact conformance evidence

CI and tag releases run built Core wheels against the published closure in
`tests/real_engine/requirements.txt`, on PostgreSQL/PostGIS local single-primary
and two-streaming-standby cluster profiles. These preserve mixed Catalog
transactions, explicit Evidence, rollback, replay and required capability tests.
The candidate replaces only Core; old adapter dependency metadata is documented
as an experiment and must be repinned by each owning release task.

Each `core-postgresql-{local,cluster}-{commit}` artifact contains JUnit results,
public dependency installation reports with archive hashes, candidate wheel
hash, installed distribution versions, actual image repository digests, and
independently queried PostgreSQL/PostGIS versions/standby counts. The runner
rejects skipped real-engine acceptance. Projection/append descriptors from
published OpenSearch/ClickHouse are negative contract fixtures; this task does
not claim their real-engine behavior or other adapters' conformance.
