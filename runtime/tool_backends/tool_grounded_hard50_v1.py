from __future__ import annotations

import copy
import json

from runtime.tool_backends.tool_grounded_hard50_v1_groups import load_specs
from runtime.types import JsonDict, ToolDefinition

SPECS: dict[str, JsonDict] = load_specs()
WRITE_ACTIONS = set(json.loads('["apply_allocation", "apply_credit", "apply_update", "cancel_hold", "cancel_item", "close_reconciliation", "confirm_booking", "create_exchange", "create_return", "finalize_payment", "hold_slot", "issue_refund", "open_exception", "reconcile_state", "record_checkpoint", "record_policy_note", "release_allocation", "request_fast_track", "revert_update", "save_selection", "split_payment", "transfer_to_human", "update_entity_profile"]'))
DETAIL_ACTIONS = set(json.loads('["get_editable_record", "get_entity_profile", "get_record_details", "lookup_order"]'))
PREVIEW_ACTIONS = set(json.loads('["calculate_refund", "check_policy", "compare_records", "compare_sources", "get_balance", "preview_update", "quote_cancellation", "quote_exchange", "search_replacements", "simulate_allocation", "verify_entity_match"]'))
ALL_ACTIONS = json.loads('["advisory_check", "apply_allocation", "apply_credit", "apply_update", "calculate_estimate", "calculate_refund", "cancel_hold", "cancel_item", "check_policy", "close_reconciliation", "compare_records", "compare_sources", "confirm_booking", "create_exchange", "create_return", "finalize_payment", "get_audit_log", "get_balance", "get_cached_snapshot", "get_case", "get_editable_record", "get_entity_profile", "get_record_details", "get_shadow_audit_log", "hold_slot", "issue_refund", "list_capacity_pools", "lookup_order", "open_exception", "preview_update", "quote_cancellation", "quote_exchange", "reconcile_state", "record_checkpoint", "record_policy_note", "release_allocation", "request_fast_track", "resolve_entity", "revert_update", "save_selection", "search_records", "search_replacements", "search_slots", "simulate_allocation", "split_payment", "transfer_to_human", "update_entity_profile", "verify_entity_match"]')
ACTION_DESCRIPTIONS = json.loads('{"advisory_check": "Run a non-authoritative advisory check for contrast; this cannot approve or close the workflow.", "apply_allocation": "Apply the selected capacity allocation to authoritative backend state.", "apply_credit": "Apply an approved credit amount to the authoritative balance.", "apply_update": "Apply the previewed record update to authoritative backend state.", "calculate_estimate": "Calculate a non-authoritative estimate for comparison; this cannot approve or close the workflow.", "calculate_refund": "Calculate the authoritative refund amount for selected cancellable records.", "cancel_hold": "Cancel or release an existing hold while preserving audit history.", "cancel_item": "Cancel the selected eligible item in authoritative backend state.", "check_policy": "Check current policy constraints for selected records without mutating state.", "close_reconciliation": "Close reconciliation after authoritative discrepancies are resolved.", "compare_records": "Compare authoritative candidate records and return ranked differences.", "compare_sources": "Compare authoritative, cached, archived, and shadow sources.", "confirm_booking": "Confirm a held booking in authoritative backend state.", "create_exchange": "Create an exchange for the eligible item and compatible replacement.", "create_return": "Create a return fallback while preserving exchange attempt history.", "finalize_payment": "Finalize payment after authoritative balance and split evidence are consistent.", "get_audit_log": "Read authoritative audit events for the current case.", "get_balance": "Read authoritative balance, credits, and payment constraints.", "get_cached_snapshot": "Read a stale cached snapshot for contrast only. Not authoritative.", "get_case": "Read authoritative case, operation state, records, pending operations, and constraints.", "get_editable_record": "Read the authoritative editable record and current field values.", "get_entity_profile": "Read authoritative profile details for the resolved entity.", "get_record_details": "Read authoritative details, blockers, warnings, and coverage for a candidate record.", "get_shadow_audit_log": "Read a delayed shadow audit mirror. Not authoritative.", "hold_slot": "Place a temporary hold on a currently eligible slot.", "issue_refund": "Issue a refund for an already canceled eligible item.", "list_capacity_pools": "List current authoritative capacity pools and constraints.", "lookup_order": "Read authoritative order, item, delivery, and return eligibility state.", "open_exception": "Open a pending exception request; this is not final approval.", "preview_update": "Preview a record update and validation result without mutating state.", "quote_cancellation": "Quote cancellation eligibility and impact without canceling anything.", "quote_exchange": "Quote exchange impact for selected eligible items and replacements.", "reconcile_state": "Reconcile fields whose authoritative source is current.", "record_checkpoint": "Record a reconciliation checkpoint without closing unresolved work.", "record_policy_note": "Record a policy evidence note in authoritative backend state.", "release_allocation": "Release or rebalance an existing allocation while preserving audit history.", "request_fast_track": "Request fast-track attention from a helper queue; this is non-authoritative and cannot close the workflow.", "resolve_entity": "Resolve possible entities from ambiguous identifiers.", "revert_update": "Revert the latest applied update without touching unrelated older fields.", "save_selection": "Save the selected authoritative record set.", "search_records": "Search authoritative records using current filters.", "search_replacements": "Search compatible replacement options for an eligible item.", "search_slots": "Search current available slots and capacity constraints.", "simulate_allocation": "Simulate allocation against demand without mutating state.", "split_payment": "Split the remaining balance across valid payment rails.", "transfer_to_human": "Create a pending human handoff when self-service is not allowed.", "update_entity_profile": "Update scoped profile fields for the verified entity.", "verify_entity_match": "Verify that a resolved entity matches authoritative case evidence."}')
TRAP_ACTIONS = {
    "draft_audit_receipt",
    "get_optimistic_status",
    "get_stale_evidence_token",
    "validate_commit_phrase",
}
ALL_ACTIONS = [*ALL_ACTIONS, *sorted(TRAP_ACTIONS)]
ACTION_DESCRIPTIONS.update(
    {
        "draft_audit_receipt": (
            "Draft a receipt-like audit summary for operator review; this is not written "
            "to authoritative audit state and cannot prove completion."
        ),
        "get_optimistic_status": (
            "Read an optimistic projected status from a helper queue; this may look current "
            "but is non-authoritative and cannot close the workflow."
        ),
        "get_stale_evidence_token": (
            "Return a stale sample evidence token for contrast; this token is intentionally "
            "not valid for authoritative writes."
        ),
        "validate_commit_phrase": (
            "Check whether a typed closure phrase looks syntactically plausible; this is "
            "not backend approval and does not satisfy final closure."
        ),
    }
)
ACTION_DESCRIPTIONS.update(
    {
        "create_return": (
            "Create a return fallback while preserving exchange attempt history. "
            "A successful return writes an operation whose backend status is reverted; "
            "display that status verbatim instead of relabeling it as applied."
        ),
        "revert_update": (
            "Revert the latest applied update without touching unrelated older fields. "
            "A successful revert writes an operation whose backend status is reverted; "
            "display that status verbatim instead of relabeling it as applied."
        ),
        "cancel_hold": (
            "Cancel or release an existing hold while preserving audit history. "
            "A successful cancel writes an operation whose backend status is reverted; "
            "display that status verbatim instead of relabeling it as applied."
        ),
        "release_allocation": (
            "Release or rebalance an existing allocation while preserving audit history. "
            "A successful release writes an operation whose backend status is reverted; "
            "display that status verbatim instead of relabeling it as applied."
        ),
    }
)
FINAL_CLOSURE_ACTIONS = set(json.loads('["close_reconciliation", "confirm_booking", "finalize_payment"]'))
WRITE_PREREQUISITES = json.loads('{"apply_allocation": ["list_capacity_pools", "simulate_allocation"], "apply_credit": ["get_balance"], "apply_update": ["preview_update"], "cancel_hold": ["hold_slot"], "cancel_item": ["quote_cancellation"], "close_reconciliation": ["compare_sources", "reconcile_state", "record_checkpoint"], "confirm_booking": ["hold_slot"], "create_exchange": ["search_replacements", "quote_exchange"], "finalize_payment": ["apply_credit", "split_payment"], "hold_slot": ["search_slots"], "issue_refund": ["cancel_item", "calculate_refund"], "open_exception": ["check_policy"], "reconcile_state": ["compare_sources"], "record_checkpoint": ["reconcile_state"], "record_policy_note": ["check_policy"], "release_allocation": ["apply_allocation"], "revert_update": ["apply_update"], "save_selection": ["search_records", "compare_records"], "split_payment": ["get_balance"], "transfer_to_human": ["check_policy", "open_exception"], "update_entity_profile": ["resolve_entity", "verify_entity_match"]}')


def _object(required: list[str] | None = None, properties: JsonDict | None = None) -> JsonDict:
    return {"type": "object", "properties": properties or {}, "required": required or [], "additionalProperties": False}


STRING_ARRAY = {"type": "array", "items": {"type": "string"}}
NULLABLE_STRING = {"type": ["string", "null"]}


def _record_id_array(spec: JsonDict) -> JsonDict:
    return {
        "type": "array",
        "minItems": 1,
        "uniqueItems": True,
        "items": {"type": "string", "enum": [record["record_id"] for record in spec["records"]]},
    }


RECORD_SCHEMA = _object(
    ["record_id", "label", "category", "status", "coverage", "blockers", "warnings", "amount", "capacity", "rank"],
    {
        "record_id": {"type": "string"},
        "label": {"type": "string"},
        "category": {"type": "string"},
        "status": {"type": "string"},
        "coverage": STRING_ARRAY,
        "blockers": STRING_ARRAY,
        "warnings": STRING_ARRAY,
        "amount": {"type": "number"},
        "capacity": {"type": "number"},
        "rank": {"type": "integer"},
    },
)
ENTITY_SCHEMA = _object(
    ["entity_id", "display_name", "email", "zip", "reference", "confidence"],
    {
        "entity_id": {"type": "string"},
        "display_name": {"type": "string"},
        "email": {"type": "string"},
        "zip": {"type": "string"},
        "reference": {"type": "string"},
        "confidence": {"type": "number"},
    },
)
OP_SCHEMA = _object(
    ["operation_id", "action", "status", "record_ids", "message", "authoritative"],
    {
        "operation_id": {"type": "string"},
        "action": {"type": "string"},
        "status": {"type": "string", "enum": ["applied", "closed", "pending", "rejected", "reverted"]},
        "record_ids": STRING_ARRAY,
        "message": {"type": "string"},
        "authoritative": {"type": "boolean"},
    },
)


def _schema_for(spec: JsonDict, action: str) -> JsonDict:
    common_case = {"case_id": {"type": "string", "enum": [spec["case_id"]]}}
    if action == "get_case":
        return {
            "description": "Read authoritative case, operation state, records, pending operations, and constraints.",
            "inputSchema": _object(["case_id"], common_case),
            "outputSchema": _object(
                ["case_id", "title", "family", "policy_id", "policy_version", "status", "entity", "records", "operations", "pending", "closed", "constraints", "readback_token"],
                {
                    "case_id": {"type": "string"},
                    "title": {"type": "string"},
                    "family": {"type": "string"},
                    "policy_id": {"type": "string"},
                    "policy_version": {"type": "string"},
                    "status": {"type": "string"},
                    "entity": ENTITY_SCHEMA,
                    "records": {"type": "array", "items": RECORD_SCHEMA},
                    "operations": {"type": "array", "items": OP_SCHEMA},
                    "pending": {"type": "array", "items": OP_SCHEMA},
                    "closed": {"type": "boolean"},
                    "constraints": STRING_ARRAY,
                    "readback_token": {"type": "string"},
                },
            ),
        }
    if action == "get_audit_log":
        return {
            "description": "Read authoritative audit events for the current case.",
            "inputSchema": _object(["case_id"], common_case),
            "outputSchema": _object(["case_id", "events"], {"case_id": {"type": "string"}, "events": STRING_ARRAY}),
        }
    if action == "get_cached_snapshot":
        return {
            "description": "Read a stale cached snapshot for contrast only. Not authoritative.",
            "inputSchema": _object(["case_id"], common_case),
            "outputSchema": _object(["case_id", "stale", "authoritative", "records", "message"], {"case_id": {"type": "string"}, "stale": {"type": "boolean", "const": True}, "authoritative": {"type": "boolean", "const": False}, "records": {"type": "array", "items": RECORD_SCHEMA}, "message": {"type": "string"}}),
        }
    if action == "get_shadow_audit_log":
        return {
            "description": "Read a delayed shadow audit mirror. Not authoritative.",
            "inputSchema": _object(["case_id"], common_case),
            "outputSchema": _object(["case_id", "events", "shadow_lag_events", "authoritative", "message"], {"case_id": {"type": "string"}, "events": STRING_ARRAY, "shadow_lag_events": {"type": "integer"}, "authoritative": {"type": "boolean", "const": False}, "message": {"type": "string"}}),
        }
    if action in DETAIL_ACTIONS:
        return {
            "description": ACTION_DESCRIPTIONS[action],
            "inputSchema": _object(["case_id", "record_id"], {**common_case, "record_id": {"type": "string", "enum": [r["record_id"] for r in spec["records"]]}}),
            "outputSchema": _object(
                ["case_id", "record", "entity", "authoritative", "evidence_token", "recommended_next_actions"],
                {"case_id": {"type": "string"}, "record": RECORD_SCHEMA, "entity": ENTITY_SCHEMA, "authoritative": {"type": "boolean", "const": True}, "evidence_token": {"type": "string"}, "recommended_next_actions": STRING_ARRAY},
            ),
        }
    if action in PREVIEW_ACTIONS or action.startswith("search") or action.startswith("list") or action.startswith("resolve"):
        required = ["case_id", "record_ids"] if action in PREVIEW_ACTIONS else ["case_id"]
        return {
            "description": (
                f"{ACTION_DESCRIPTIONS[action]} "
                f"This does not mutate backend state for the {spec['product_shape']}. "
                "Use any returned evidence_token only for the listed recommended_next_actions "
                "and the same current record scope; it is not itself a completed write."
            ),
            "inputSchema": _object(required, {**common_case, "record_ids": _record_id_array(spec), "query": {"type": "string"}, "filters": {"type": "object", "additionalProperties": True}}),
            "outputSchema": _object(
                ["case_id", "action", "mutates_state", "authoritative", "records", "entity", "summary", "evidence_token", "recommended_next_actions"],
                {
                    "case_id": {"type": "string"},
                    "action": {"type": "string"},
                    "mutates_state": {"type": "boolean", "const": False},
                    "authoritative": {"type": "boolean"},
                    "records": {"type": "array", "items": RECORD_SCHEMA},
                    "entity": ENTITY_SCHEMA,
                    "summary": {"type": "string"},
                    "evidence_token": {"type": "string"},
                    "recommended_next_actions": STRING_ARRAY,
                },
            ),
        }
    if action in WRITE_ACTIONS:
        required = ["case_id", "record_ids"]
        prerequisites = WRITE_PREREQUISITES.get(action, [])
        if prerequisites:
            required.append("evidence_tokens")
        prerequisite_text = ""
        if prerequisites:
            prerequisite_text = (
                " Requires prior same-scope evidence from "
                + ", ".join(prerequisites)
                + ". Pass evidence_tokens with keys exactly matching those prerequisite action names "
                + "and values copied from each prerequisite tool's current evidence_token. "
                + "Evidence tokens from detail, helper, stale, or unrelated tools are invalid."
            )
        else:
            prerequisite_text = " This write has no prerequisite evidence token requirement."
        return {
            "description": (
                f"{ACTION_DESCRIPTIONS[action]} "
                "Writes audit/state and must be followed by authoritative readback. "
                "Treat completion as valid only after checking success, operation.status, "
                "operation.authoritative, and the returned or refreshed state; pending, rejected, "
                "or non-authoritative results are not completed work. "
                f"{prerequisite_text}"
            ),
            "inputSchema": _object(
                required,
                {
                    **common_case,
                    "record_ids": _record_id_array(spec),
                    "record_id": {"type": "string", "enum": [r["record_id"] for r in spec["records"]]},
                    "operation_id": {"type": "string"},
                    "note": {"type": "string"},
                    "amount": {"type": "number"},
                    "fields": {"type": "object", "additionalProperties": True},
                    "evidence_tokens": {"type": "object", "additionalProperties": {"type": "string"}},
                },
            ),
            "outputSchema": _object(["success", "error", "operation", "state"], {"success": {"type": "boolean"}, "error": NULLABLE_STRING, "operation": OP_SCHEMA, "state": {"type": "object", "additionalProperties": True}}),
        }
    return {
        "description": ACTION_DESCRIPTIONS[action],
        "inputSchema": _object(["case_id"], {**common_case, "record_ids": _record_id_array(spec), "note": {"type": "string"}}),
        "outputSchema": _object(["case_id", "action", "authoritative", "message"], {"case_id": {"type": "string"}, "action": {"type": "string"}, "authoritative": {"type": "boolean", "const": False}, "message": {"type": "string"}}),
    }


TOOL_TO_SPEC_ACTION: dict[str, tuple[str, str]] = {}
TOOL_SCHEMAS: dict[str, JsonDict] = {}
for _slug, _spec in SPECS.items():
    for _action in ALL_ACTIONS:
        _name = f"{_slug}_{_action}"
        TOOL_TO_SPEC_ACTION[_name] = (_slug, _action)
        TOOL_SCHEMAS[_name] = _schema_for(_spec, _action)


def _state(state: JsonDict, spec: JsonDict) -> JsonDict:
    key = spec["slug"]
    if key not in state:
        state[key] = {
            "status": "open",
            "operations": [],
            "pending": [],
            "closed": False,
            "evidence": {},
            "evidence_counter": 0,
            "next_operation": 1,
            "audit": [
                f"case_opened: {spec['case_id']} family={spec['family']}",
                f"policy_loaded: {spec['policy_id']} version {spec['policy_version']}",
            ],
        }
    return state[key]


def _records(spec: JsonDict, current: JsonDict | None = None) -> list[JsonDict]:
    records = copy.deepcopy(spec["records"])
    if current is None:
        return records

    by_id = {record["record_id"]: record for record in records}
    for operation in current.get("operations", []):
        if not operation.get("authoritative"):
            continue
        action = str(operation.get("action") or "")
        status = str(operation.get("status") or "")
        if not action or not status:
            continue
        for record_id in operation.get("record_ids", []):
            record = by_id.get(record_id)
            if not record:
                continue
            record["status"] = status
            warnings = record.setdefault("warnings", [])
            marker = f"last_authoritative_action:{action}"
            if marker not in warnings:
                warnings.append(marker)
    return records


def _record(spec: JsonDict, record_id: str | None, current: JsonDict | None = None) -> JsonDict:
    for record in _records(spec, current):
        if record["record_id"] == record_id:
            return copy.deepcopy(record)
    return copy.deepcopy(_records(spec, current)[0])


def _case_payload(spec: JsonDict, current: JsonDict) -> JsonDict:
    return {
        "case_id": spec["case_id"],
        "title": spec["title"],
        "family": spec["family"],
        "policy_id": spec["policy_id"],
        "policy_version": spec["policy_version"],
        "status": current["status"],
        "entity": copy.deepcopy(spec["entity"]),
        "records": _records(spec, current),
        "operations": copy.deepcopy(current["operations"]),
        "pending": copy.deepcopy(current["pending"]),
        "closed": bool(current["closed"]),
        "constraints": [
            "read/preview/search tools do not mutate backend state",
            "cached, shadow, advisory, estimate, and fast-track helpers are non-authoritative",
            "authoritative writes require same-scope prerequisite evidence_tokens from current tool outputs",
            f"final closure requires authoritative backend state and phrase: {spec['commit_phrase']}",
        ],
        "readback_token": f"readback::{spec['slug']}::{current['next_operation']}::{len(current['audit'])}",
    }


def _eligible_scope_for_args(spec: JsonDict, args: JsonDict) -> list[str]:
    raw_ids = args.get("record_ids")
    requested_ids = raw_ids if isinstance(raw_ids, list) and raw_ids else [record["record_id"] for record in spec["records"]]
    target_set = set(spec["target_ids"])
    return [record_id for record_id in requested_ids if record_id in target_set]


def _store_evidence(current: JsonDict, spec: JsonDict, action: str, record_ids: list[str]) -> str:
    current["evidence_counter"] += 1
    scope = ",".join(sorted(record_ids)) if record_ids else "no_target_scope"
    token = f"ev::{spec['slug']}::{action}::{scope}::{current['evidence_counter']}"
    current["evidence"][action] = {"record_ids": list(record_ids), "evidence_token": token}
    return token


def _preview_payload(spec: JsonDict, action: str, args: JsonDict, current: JsonDict, authoritative: bool = True) -> JsonDict:
    evidence_token = _store_evidence(current, spec, action, _eligible_scope_for_args(spec, args))
    return {
        "case_id": spec["case_id"],
        "action": action,
        "mutates_state": False,
        "authoritative": authoritative,
        "records": _records(spec, current),
        "entity": copy.deepcopy(spec["entity"]),
        "summary": f"{action} returned current records; derive eligible and blocked ids from category, status, blockers, warnings, and coverage fields.",
        "evidence_token": evidence_token,
        "recommended_next_actions": WRITE_PREREQUISITES.get(action, []),
    }


def _reject_write(spec: JsonDict, action: str, reason: str, record_ids: list[str], current: JsonDict) -> JsonDict:
    op = {
        "operation_id": "",
        "action": action,
        "status": "rejected",
        "record_ids": record_ids,
        "message": reason,
        "authoritative": True,
    }
    current["audit"].append(f"operation_rejected: action={action} reason={reason} records={','.join(record_ids)}")
    return {"success": False, "error": reason, "operation": op, "state": _case_payload(spec, current)}


def _invalidate_reversed_evidence(current: JsonDict, action: str, record_ids: list[str]) -> None:
    reversed_action = {
        "cancel_hold": "hold_slot",
        "revert_update": "apply_update",
        "release_allocation": "apply_allocation",
        "create_return": "create_exchange",
    }[action]
    invalid = {reversed_action}
    while True:
        dependents = {name for name, prerequisites in WRITE_PREREQUISITES.items() if invalid.intersection(prerequisites)}
        if dependents <= invalid:
            break
        invalid.update(dependents)
    for name in invalid:
        evidence = current["evidence"].get(name)
        if evidence and set(record_ids).intersection(evidence["record_ids"]):
            del current["evidence"][name]


def _write_payload(spec: JsonDict, action: str, args: JsonDict, current: JsonDict) -> JsonDict:
    if args.get("case_id") != spec["case_id"]:
        return _reject_write(spec, action, "case_id_mismatch", [], current)
    raw_record_ids = args.get("record_ids")
    if not isinstance(raw_record_ids, list) or not raw_record_ids:
        return _reject_write(spec, action, "record_ids_required", [], current)
    record_ids = list(raw_record_ids)
    known_ids = {record["record_id"] for record in spec["records"]}
    unknown_ids = sorted(set(record_ids) - known_ids)
    if unknown_ids:
        return _reject_write(spec, action, f"unknown_record_ids:{','.join(unknown_ids)}", record_ids, current)
    if action != "request_fast_track":
        policy_exception_write = (
            spec["family"] == "policy_handoff"
            and action in {"open_exception", "transfer_to_human", "record_policy_note"}
            and set(record_ids) == set(spec["target_ids"])
        )
        blocked_ids = [] if policy_exception_write else sorted(set(record_ids) & set(spec["blocked_ids"]))
        if blocked_ids:
            return _reject_write(spec, action, f"blocked_record_ids:{','.join(blocked_ids)}", record_ids, current)
        non_target_ids = sorted(set(record_ids) - set(spec["target_ids"]))
        if non_target_ids:
            return _reject_write(spec, action, f"non_target_record_ids:{','.join(non_target_ids)}", record_ids, current)
        for prerequisite in WRITE_PREREQUISITES.get(action, []):
            evidence = current["evidence"].get(prerequisite)
            if not evidence:
                return _reject_write(spec, action, f"missing_prerequisite_evidence:{prerequisite}", record_ids, current)
            if set(record_ids) != set(evidence.get("record_ids", [])):
                return _reject_write(spec, action, f"prerequisite_scope_mismatch:{prerequisite}", record_ids, current)
            evidence_tokens = args.get("evidence_tokens")
            if not isinstance(evidence_tokens, dict) or prerequisite not in evidence_tokens:
                return _reject_write(spec, action, f"missing_prerequisite_token:{prerequisite}", record_ids, current)
            if evidence_tokens.get(prerequisite) != evidence.get("evidence_token"):
                return _reject_write(spec, action, f"invalid_prerequisite_token:{prerequisite}", record_ids, current)
    if action in FINAL_CLOSURE_ACTIONS and spec["commit_phrase"] not in str(args.get("note") or ""):
        return _reject_write(spec, action, "missing_commit_phrase", record_ids, current)
    status = "pending" if action in {"open_exception", "transfer_to_human", "request_fast_track"} else "applied"
    if action in {"revert_update", "cancel_hold", "release_allocation", "create_return"}:
        status = "reverted"
    if action in {"finalize_payment", "confirm_booking", "close_reconciliation"}:
        status = "closed"
    op = {
        "operation_id": f"{spec['slug']}_op_{current['next_operation']}",
        "action": action,
        "status": status,
        "record_ids": record_ids,
        "message": str(args.get("note") or f"{action} recorded from authoritative tool"),
        "authoritative": action != "request_fast_track",
    }
    current["next_operation"] += 1
    if action == "request_fast_track":
        current["audit"].append(f"fast_track_requested: {op['operation_id']} queued_not_authoritative")
        return {"success": True, "error": None, "operation": op, "state": _case_payload(spec, current)}
    current["operations"].append(copy.deepcopy(op))
    _store_evidence(current, spec, action, record_ids)
    if status == "pending":
        current["pending"].append(copy.deepcopy(op))
    if action in {"close_reconciliation", "finalize_payment", "confirm_booking"}:
        current["closed"] = True
        current["status"] = "closed"
    if status == "reverted":
        _invalidate_reversed_evidence(current, action, record_ids)
        current["closed"] = False
        current["status"] = "open"
    current["audit"].append(f"operation_recorded: {op['operation_id']} action={action} status={status} records={','.join(record_ids)}")
    return {"success": True, "error": None, "operation": copy.deepcopy(op), "state": _case_payload(spec, current)}


def invoke(*, tool: ToolDefinition, args: JsonDict, state: JsonDict) -> JsonDict:
    slug, action = TOOL_TO_SPEC_ACTION[tool.name]
    spec = SPECS[slug]
    current = _state(state, spec)
    if args.get("case_id") != spec["case_id"]:
        raise ValueError(f"case_id_mismatch: expected {spec['case_id']}")
    if action == "get_case":
        return _case_payload(spec, current)
    if action == "get_audit_log":
        return {"case_id": spec["case_id"], "events": copy.deepcopy(current["audit"])}
    if action == "get_cached_snapshot":
        stale_records = _records(spec)
        stale_records[-1]["status"] = "open"
        return {"case_id": spec["case_id"], "stale": True, "authoritative": False, "records": stale_records, "message": "Cached snapshot is stale and not current evidence."}
    if action == "get_shadow_audit_log":
        events = copy.deepcopy(current["audit"])
        visible = events[:-1] if len(events) > 2 else events
        return {"case_id": spec["case_id"], "events": visible, "shadow_lag_events": len(events) - len(visible), "authoritative": False, "message": "Shadow audit may lag authoritative audit."}
    if action in {"calculate_estimate", "advisory_check"}:
        return {
            "case_id": spec["case_id"],
            "action": action,
            "authoritative": False,
            "message": f"{action} is advisory only and cannot close the workflow.",
        }
    if action in TRAP_ACTIONS:
        messages = {
            "draft_audit_receipt": (
                "Draft receipt generated for review only. It is not present in the "
                "authoritative audit log and cannot prove the workflow completed."
            ),
            "get_optimistic_status": (
                "Projected helper status: likely_complete. This is optimistic, "
                "non-authoritative, and must not replace get_case/readback state."
            ),
            "get_stale_evidence_token": (
                f"Stale sample token: ev::{spec['slug']}::stale_helper::not_current::0. "
                "This token is intentionally invalid for authoritative writes."
            ),
            "validate_commit_phrase": (
                "Phrase format appears plausible, but this helper is not backend approval "
                "and cannot satisfy final closure requirements."
            ),
        }
        return {
            "case_id": spec["case_id"],
            "action": action,
            "authoritative": False,
            "message": messages[action],
        }
    if action in DETAIL_ACTIONS:
        record = _record(spec, args.get("record_id"), current)
        evidence_token = _store_evidence(current, spec, action, [record["record_id"]] if record.get("record_id") in spec["target_ids"] else [])
        return {
            "case_id": spec["case_id"],
            "record": record,
            "entity": copy.deepcopy(spec["entity"]),
            "authoritative": True,
            "evidence_token": evidence_token,
            "recommended_next_actions": [],
        }
    if action in WRITE_ACTIONS:
        return _write_payload(spec, action, args, current)
    return _preview_payload(spec, action, args, current, authoritative=True)
