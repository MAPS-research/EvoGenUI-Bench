from __future__ import annotations

from copy import deepcopy

from runtime.types import JsonDict


def benchmark_request(value: object) -> str:
    """Return the benchmark request verbatim when it is textual."""

    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def copy_actor_evidence_summary(value: object) -> JsonDict:
    return deepcopy(value) if isinstance(value, dict) else {}


def copy_validation_contract(value: object) -> JsonDict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("validation contract must be an object when present")
    return deepcopy(value)
