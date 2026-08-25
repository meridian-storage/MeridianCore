<!-- SPDX-License-Identifier: Apache-2.0 -->

# Releasing

Releases are built from a protected `main` commit by GitHub Actions. Local
artifacts are verification inputs only and are never uploaded manually.

## Prepare

1. Update `src/meridian_storage/_version.py`, `pyproject.toml`,
   `compatibility.json`, the public API contract, and `CHANGELOG.md` together.
2. Run formatting, lint, strict typing, the full test suite, artifact
   verification, and two-build reproducibility comparison.
3. Merge the reviewed green pull request without bypassing branch protection.
4. Create an annotated `vMAJOR.MINOR.PATCH` tag at that `main` commit and push
   it.

## Automated release

The release workflow verifies that tag and package versions match, repeats all
quality gates, builds wheel and source distributions twice with a fixed
`SOURCE_DATE_EPOCH`, compares SHA-256 digests, verifies artifact contents,
generates an SPDX 2.3 SBOM, creates GitHub build-provenance attestations, and
publishes the artifacts to a GitHub release.

PyPI publishing is a separate job using OpenID Connect and the protected
`pypi` GitHub environment. It runs only when the repository variable
`PYPI_TRUSTED_PUBLISHING_ENABLED` is exactly `true`.

The first PyPI release requires the package owner to create or claim the
`meridian-storage-core` project, configure GitHub as a trusted publisher, and
enable that variable. Do not use or store a long-lived PyPI token and do not
bypass project ownership, MFA, trusted-publisher, environment, or repository
protection controls.

## Verify

Verify the GitHub release assets, attestation, and SBOM; install the released
wheel in a fresh Python 3.12+ environment; load all packaged contract documents;
and record the release URL, commit, CI run, artifact SHA-256 values, and clean
install result with the implementation task evidence.
