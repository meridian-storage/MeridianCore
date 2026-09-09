# SPDX-License-Identifier: Apache-2.0
"""Independent release selection and retained negatives through public fixtures."""

import copy
import json
import sys
from pathlib import Path

from meridian_storage.runtime.config import RuntimeConfig


def main():
    consumer = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(consumer / "tests/integration/released-capabilities"))
    import test_public_contracts as public

    profiles = public.render({"command": "profiles"})
    inputs, expected, labels = [], [], []
    pins = dict(
        line.split("==")
        for line in (Path(__file__).parent / "requirements.txt").read_text().splitlines()
        if line and not line.startswith("#")
    )
    for profile_id, selected in public.manifests().items():
        profile = profiles[profile_id]
        manifest = public.core_manifest(selected["manifest"])
        cap = next(
            c
            for c in manifest.descriptor.capabilities
            if c.operation_contract in manifest.available_operation_contracts
        )
        for mode in profile["allowedModes"]:
            for topology in profile["allowedTopologies"]:
                base = public.spec(
                    profile,
                    selected,
                    mode,
                    topology,
                    cap.operation_contract,
                    cap.operation_versions[0],
                    cap.guarantees,
                    dict(cap.limits),
                )
                base["bindings"][0]["compatibilityPins"] = pins
                variants = {"selected-lock": copy.deepcopy(base)}
                library = copy.deepcopy(base)
                library["bindings"][0]["compatibilityPins"][selected["package"]] = "99.12.4"
                variants["independent-library-release"] = library
                if profile_id not in {"aws-s3", "s3-compatible", "oci-distribution"}:
                    engine = copy.deepcopy(base)
                    binding = engine["bindings"][0]
                    binding["engineVersion"] = "99.12.4"
                    binding["capabilityManifest"]["engineVersion"] = "99.12.4"
                    binding["requiredCapabilityFingerprint"] = public.core_manifest(
                        binding["capabilityManifest"]
                    ).fingerprint
                    variants["independent-server-release"] = engine
                for name, value in variants.items():
                    inputs.append(value)
                    expected.append(True)
                    labels.append([profile_id, mode, topology, name])
                for name in (
                    "operation",
                    "guarantee",
                    "package",
                    "topology",
                    "manifest-drift",
                    "tls",
                ):
                    invalid = copy.deepcopy(base)
                    binding = invalid["bindings"][0]
                    operation = invalid["resources"][0]["operations"][0]
                    if name == "operation":
                        operation["version"] = "99.0.0"
                    elif name == "guarantee":
                        operation["guarantees"] = ["not-advertised"]
                    elif name == "package":
                        binding["compatibilityPins"][selected["package"]] = "latest"
                    elif name == "topology":
                        binding["topology"] = "not-a-topology"
                    elif name == "manifest-drift":
                        binding["requiredCapabilityFingerprint"] = "sha256:" + "0" * 64
                    elif name == "tls":
                        binding["connection"]["tls"]["mode"] = "not-a-tls-policy"
                    inputs.append(invalid)
                    expected.append(False)
                    labels.append([profile_id, mode, topology, name])
                if profile_id in {"aws-s3", "s3-compatible", "oci-distribution"}:
                    invalid = copy.deepcopy(base)
                    invalid["bindings"][0]["engineVersion"] = "99.12.4"
                    inputs.append(invalid)
                    expected.append(False)
                    labels.append([profile_id, mode, topology, "protocol-contract"])
    results = public.render({"command": "batch", "specs": inputs})
    mismatches = []
    fingerprints = {}
    for label, value, want, result in zip(labels, inputs, expected, results, strict=True):
        if result["accepted"] != want:
            mismatches.append({"case": label, "expected": want, "actual": result})
        if result["accepted"]:
            config = RuntimeConfig.from_mapping(result["config"])
            assert RuntimeConfig.from_mapping(config.to_dict()).fingerprint == config.fingerprint
            binding = result["config"]["bindings"][0]
            assert (
                binding["extensions"]["org.meridian.constructs/package-lock.v1"]["packages"]
                == (value["bindings"][0]["compatibilityPins"])
            )
            assert binding["engineVersion"] == value["bindings"][0]["engineVersion"]
            key = tuple(label[:3])
            if key in fingerprints:
                assert config.fingerprint not in fingerprints[key]
            fingerprints.setdefault(key, set()).add(config.fingerprint)
    output = {
        "scope": "Contract metadata only; invented provenance does not claim engine compatibility",
        "cases": len(inputs),
        "accepted": sum(expected),
        "negative": len(expected) - sum(expected),
        "mismatches": mismatches,
        "labels": labels,
    }
    (consumer / "evidence/independent-selection.json").write_text(
        json.dumps(output, indent=2) + "\n"
    )
    assert not mismatches, mismatches[:5]
    print(json.dumps({key: value for key, value in output.items() if key != "labels"}))


if __name__ == "__main__":
    main()
