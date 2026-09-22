from __future__ import annotations

from importlib import import_module

from runtime.types import JsonDict

FAMILY_MODULES = (
    "audit_reconciliation",
    "booking_reservation",
    "cancel_refund",
    "capacity_allocation",
    "identity_lookup_update",
    "payment_split",
    "policy_handoff",
    "record_update_revert",
    "return_exchange",
    "search_filter_select",
)


def load_specs() -> dict[str, JsonDict]:
    specs: dict[str, JsonDict] = {}
    for module_name in FAMILY_MODULES:
        module = import_module(f"{__name__}.{module_name}")
        module_specs = module.SPECS
        duplicates = set(specs) & set(module_specs)
        if duplicates:
            raise ValueError(f"duplicate tool-grounded hard50 specs: {sorted(duplicates)}")
        specs.update(module_specs)
    return specs
