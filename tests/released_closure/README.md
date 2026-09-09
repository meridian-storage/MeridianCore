<!-- SPDX-License-Identifier: Apache-2.0 -->

# Local all-family public release closure

The selected closure is Core 1.1.0, Constructs 1.6.1, ClickHouse 1.1.3 and the
other 16 Meridian distributions in `requirements.txt`. Installation uses normal
public dependency resolution with the exact 67-distribution hash lock and npm
lockfiles. No candidate Core wheel, sibling implementation, dependency override
or editable package participates in this gate. Core still publishes only
`meridian-storage-core`; this change publishes conformance evidence through CI.

## Reproduce

Run with Python 3.13, Node 24.14.0, Docker and an isolated PostgreSQL fixture:

```sh
export MERIDIAN_POSTGRESQL_TEST_DSN='host=127.0.0.1 port=5432 dbname=meridian user=meridian password=meridian'
export MERIDIAN_POSTGRESQL_ENGINE_VERSION=17-postgis-3.5
export ENGINE_IMAGE=postgis/postgis@sha256:894f570c0cf0664ed5576a8fd5d5bfb8fb1b19d592885b686c3a88c8bd90c41f
bash tests/released_closure/run.sh /tmp/meridian-core-closure
```

The DSN must identify a disposable database: public fixtures explicitly create and
remove their own physical schemas. CI provisions the digest-pinned PostgreSQL
16 and 17 services independently, then publishes immutable artifacts containing
JUnit, exact installed distribution hashes, engine details, host file hashes,
configuration/manifest fixtures and the scoped upstream records. `acceptance.json`
indexes every evidence file by SHA-256. A missing, empty, failed, errored or skipped
required current suite fails the gate. The ordinary Core quality, reproducible
package and historical candidate-wheel conformance gates remain required.

## Acceptance layers

| Layer | Required evidence | Meaning |
| --- | --- | --- |
| Published artifacts | Registry bytes for 18 Meridian wheels and npm; all Python/npm dependencies hash locked | Artifact integrity and reproducibility |
| Export inventory | 76 exports, 12 profiles, 28 declared mode/topology routes | Exhaustive gate classification in `gate_inventory.json` |
| Contract agreement | Original 16 cases; 1,014 family cases; 1,688 configured limits; 2,988 configured append comparisons | Public Core/Adapter/Constructs agreement and retained negatives |
| Independent selection | 252 cases with independent package/server provenance and contract/provider/TLS/lock negatives | No compiled release allowlist; invented versions make no engine-support claim |
| Serialization | Original 12 real resource/provider/physical pins and every accepted generated config; public Core golden fixtures | Stable typed round trips, independently preserved fingerprint domains |
| Packaged host | 39 real PostgreSQL/process tests per engine version | Required Evidence commit/rollback, restart, durable claims, exact acknowledgement, v1 after v2, v2/latest/tombstones, graceful drain and separate failed-drain expiry recovery |
| Local operations | 26 routes and both Collector modes; exact records in `evidence/upstream-records.zip` | Measured engine/infra recovery with explicit release-scoped reuse |
| Plugins | Public ConfigArtifact 21, Cost 16, Observability 5, Usage 44 plus its one successful ClickHouse retry | Retained real-engine plugin regression scope |

The two AWS routes are explicitly excluded by the approved local delivery scope.
Metadata checks still inventory their protocol/provider gates. They are not
reported as operationally verified, and this closure makes no production claim.

## Measured upstream evidence

`evidence/index.json` records original task attachments, complete archive hashes,
and exact member hashes for the reviewable snapshot. The snapshot preserves raw
results, image locks, selected/observed provenance, current configurations and
historical failures. `check_evidence.py` checks all route identities against the
currently installed exports, verifies the referenced successful raw runs, parses
the 24 effective plans with public Core and checks Collector recovery evidence.
Combined object bindings account for 26 distinct local routes in those 24 plans.

Operational Attempt 5 reran both stock Collector modes on public Constructs 1.6.1
and ClickHouse 1.1.3. It measured public plugin emission, typed log/span/metric
reads, exact nanoseconds, canonical identities and floats, Core append/query,
retries, same-identity distinct content, terminating pagination, TLS/private-relay
and pre-ACK negatives, acknowledged SIGKILL recovery, repeat migration and zero
startup DDL. Attempt 4 supplies actual ClickHouse disk/S3 restoration into new
volumes, replica expansion and member/Keeper recovery. Other engines use Attempt
3's actual managed/external startup, provider authority and engine lifecycle runs.
The 1.6.0 and 1.6.1 effective plans/configs are byte-identical; unchanged product
code and dependency hashes are recorded. These earlier runs retain their actual
versions and timestamps rather than being relabeled as fresh runs.

The plugin evidence retains its original shared lock, including ClickHouse 1.1.1.
The subsequent ClickHouse/Collector regressions cover the changed ingestion,
append, precision and pagination paths; unchanged plugin implementations retain
their measured scope. The original Usage fixture failed one test because its
scratch principal lacked CREATE DATABASE. Its exact successful rerun is checked
separately; the original failure is retained and never counted as passed.

## Golden fixture migration and original failure

Public Constructs source commit `266eb37bb22ffc429225726aecbddade06115569` supplies
only test fixtures and contracts. Its archive SHA-256 is verified before use and
all product imports resolve to installed packages. The historical inventory has
two ClickHouse distribution coordinates at 1.1.1. `prepare.py` changes exactly
those two coordinates to the selected 1.1.3 and records the adaptation; every
contract, schema and fingerprint expectation remains unchanged. The complete
upstream inventory assertion runs against that selected golden. Its original
provenance-only failure is retained in the upstream snapshot.

The original Core-owned probe and unsupported-version control remain unchanged.
On Constructs 1.4.0 it found eight false rejections among 16 cases: PostgreSQL
`put@2.0.0` and `atomic-evidence` across two profiles and two modes. Historical
commit `c3ab7af660bec7b6451bcf2886be50a58ce7a8b2` and the task's original failure
attachment preserve that reproduction. The repaired public package must pass
those original inputs without removing requirements or adding an inline manifest.
