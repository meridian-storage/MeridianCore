// SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

assert(process.env.CONSTRUCTS_MODULE.includes("node_modules/"));
const c = await import(pathToFileURL(process.env.CONSTRUCTS_MODULE).href);
const inventory = JSON.parse(readFileSync(process.argv[2], "utf8"));
assert.deepEqual(Object.keys(c).sort(), Object.keys(inventory.exports).sort());
const routes = Object.values(c.engineProfiles).flatMap(profile =>
  profile.allowedModes.flatMap(mode => profile.allowedTopologies.map(topology => ({
    profile: profile.id, mode, topology,
    scope: profile.id === "aws-s3" ? "excluded-from-delivery" : "local",
  }))),
);
assert.equal(routes.length, 28);
assert.equal(routes.filter(route => route.scope === "local").length, 26);
const hosts = Object.fromEntries(Object.entries(c.projectionHostFiles()).map(([name, content]) =>
  [name, createHash("sha256").update(content).digest("hex")],
));
writeFileSync(process.argv[3], JSON.stringify({
  packageVersion: c.version,
  exports: inventory.exports,
  profiles: c.engineProfiles,
  routes,
  packagedHostFiles: hosts,
  scope: "Exhaustive exported surface; route declarations alone do not prove engine behavior",
}, null, 2) + "\n");
console.log(`Inventoried ${Object.keys(c).length} exports, ${routes.length} declared routes, and ${Object.keys(hosts).length} host files`);
