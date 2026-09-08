<!-- SPDX-License-Identifier: Apache-2.0 -->

# Public release closure acceptance

This probe consumes the public Core, PostgreSQL Adapter, Catalogs and Constructs
packages. It compares the selected Adapter's actual CapabilityManifest with the
generic Constructs renderer for both PostgreSQL profiles and both allowed modes.
It never imports a sibling repository, replaces an installed package with source,
or changes a required contract to make the renderer pass.

Run from the Core repository root with Python 3.13 and Node 22 or newer:

```sh
python3.13 -m venv .closure-consumer/runtime
.closure-consumer/runtime/bin/python -m pip install -r tests/released_closure/requirements.txt
.closure-consumer/runtime/bin/python -m pip check
npm install --prefix .closure-consumer --ignore-scripts --save-exact @zephytiju/meridian-storage-constructs@1.4.0
.closure-consumer/runtime/bin/python tests/released_closure/export_postgresql_contracts.py .closure-consumer/postgresql-contracts.json
cp tests/released_closure/constructs_contracts.mjs .closure-consumer/
cd .closure-consumer
node constructs_contracts.mjs postgresql-contracts.json results.json
```

The final command is an acceptance gate and exits nonzero on a mismatch. It writes
all results before failing. `read-control` must render deterministically;
`unsupported-version-control` must remain rejected. `put-v2` and `atomic-evidence`
must agree with Core's real selected descriptor checks. Resource, provider-bundle,
CapabilityManifest and compiled physical-layout fingerprints are independently
computed using the installed public packages.

## Current blocker

Constructs 1.4.0 rejects `meridian.structured.put@2.0.0` and the
`meridian.evidence.append@1.0.0` `atomic-evidence` guarantee that PostgreSQL Adapter
2.3.1 advertises and Core 1.1.0 accepts. This occurs in local-single-primary and
cluster profiles, in managed and external planning modes: eight contract
mismatches among sixteen cases. All eight positive/negative control cases pass.

The public Constructs profile helper fixes operation versions to `1.0.0` and its
PostgreSQL Evidence capability omits `atomic-evidence`. Generic `planDeployment`
validates against that compiled profile. Supplying the real manifest fingerprint
and complete deployment package coordinates does not update those capabilities.
The public binding input has no replacement capability-manifest field.

The same result was reproduced in two clean public environments, including an
installation locked to archive SHA-256 hashes. Four valid rendered control
configurations parse and round-trip through Core 1.1.0 with stable typed
fingerprints. The complete Python closure resolves and passes `pip check`.

This is metadata/preview evidence, not real-engine or provider support evidence.
It blocks the all-family acceptance gate before the required write/Evidence route
can render. Repair and publicly release the owning Constructs package, preserve
the unsupported-contract negatives, then rerun this probe and the remaining
real-engine closure. No Core runtime change or weaker requirement resolves this
ownership boundary. Historical PostgreSQL Adapter 2.2.0 delivery evidence remains
separate; this probe uses the selected 2.3.1 / Semantics 2.1.0 metadata closure.
