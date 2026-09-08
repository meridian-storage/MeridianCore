// SPDX-License-Identifier: Apache-2.0
// Run from a consumer directory with the public npm package installed.
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import {
  defaultClientPolicy,
  defaultValidationPolicy,
  disabledTelemetryCapability,
  engineProfiles,
  planDeployment,
  validateRuntimeConfig,
} from "@zephytiju/meridian-storage-constructs";

const input = JSON.parse(readFileSync(process.argv[2], "utf8"));
const results = [];
for (const exported of input.profiles) {
  const profile = engineProfiles[exported.id];
  for (const mode of profile.allowedModes) {
    for (const test of exported.cases) {
      const resource = input.resources[test.catalog];
      const reference = { provider: "closure-fixture", reference: "opaque-reference" };
      const spec = {
        profile: "closure-contract-metadata",
        catalogs: input.catalogs,
        schemaProviders: input.schemaProviders,
        resources: [{
          selector: resource.selector,
          schemas: [{
            providerId: input.schemaProviders[0].id,
            package: input.schemaProviders[0].package,
            version: "1.0.0",
            resourceFingerprint: resource.fingerprint,
          }],
          operations: [{
            contract: test.contract,
            version: test.version,
            guarantees: test.guarantees,
            limits: {},
          }],
          guarantees: { required: [] },
          limits: { values: {} },
          dataClass: "isolated-conformance-fixture",
          labels: {},
        }],
        bindings: [{
          id: "closure-db",
          profileId: profile.id,
          requiredCapabilityFingerprint: exported.manifestFingerprint,
          connection: {
            physicalNamespace: "closure",
            identityRef: reference,
            secretRef: reference,
            tls: {
              mode: "server", serverName: "localhost", caRef: reference,
              clientCertificateRef: null,
            },
            endpoint: "postgresql://localhost:5432/closure",
            serviceRef: null,
            requiredPhysicalFingerprint: exported.physicalFingerprint,
            settings: {
              formatVersion: "meridian.postgresql.settings.v1",
              scopeKeys: [], resources: exported.layouts,
              topology: { expectedStandbys: profile.id.endsWith("cluster") ? 2 : 0 },
            },
            extensions: {},
          },
          mode,
          topology: profile.defaultTopology,
          engineVersion: exported.engineVersion,
          client: defaultClientPolicy,
          compatibilityPins: input.packages,
          acl: reference,
          migration: {
            contract: "meridian.migration.apply", version: "1.0.0",
            appliedFingerprint: resource.fingerprint,
          },
          observability: { enabled: false, labels: {} },
          recovery: {
            method: "backup-restore", owner: "conformance-fixture", policyRef: "fixture",
            rpoSeconds: 300, rtoSeconds: 14400,
            validationFingerprint: resource.fingerprint,
          },
        }],
        placements: [{
          id: "closure-placement", bindingId: "closure-db", extensions: {},
          selector: { resources: [resource.selector], catalog: null, labels: {} },
        }],
        liveSchemas: { enabled: false, required: false, providerId: null },
        validation: defaultValidationPolicy,
        telemetry: disabledTelemetryCapability,
        extensions: {},
      };
      let accepts = false;
      let failure;
      try {
        const plan = planDeployment(spec);
        validateRuntimeConfig(plan.runtimeConfig);
        assert.deepEqual(planDeployment(spec), plan);
        writeFileSync(`${profile.id}-${mode}-${test.name}.config.json`, plan.runtimeConfigJson);
        accepts = true;
      } catch (error) {
        failure = { code: error.code, message: error.message };
      }
      results.push({
        profile: profile.id, mode, case: test.name,
        coreAccepts: test.coreAccepts, constructsAccepts: accepts,
        matchesReleasedContract: accepts === test.coreAccepts,
        compiledCapability: profile.operations[test.contract],
        failure,
      });
    }
  }
}
writeFileSync(process.argv[3], JSON.stringify({
  classification: input.classification,
  results,
}, null, 2) + "\n");
const mismatch = results.filter((r) => !r.matchesReleasedContract);
console.log(JSON.stringify({ total: results.length, mismatches: mismatch.length, results }, null, 2));
assert.equal(mismatch.length, 0, "Constructs must accept the actual selected public Adapter contracts");
