# SPDX-License-Identifier: Apache-2.0
"""Public testing utilities shipped by Meridian Core."""

from .adapter_conformance import (
    AdapterConformanceReport,
    AdapterConformanceTarget,
    run_adapter_conformance,
)

__all__ = [
    "AdapterConformanceReport",
    "AdapterConformanceTarget",
    "run_adapter_conformance",
]
