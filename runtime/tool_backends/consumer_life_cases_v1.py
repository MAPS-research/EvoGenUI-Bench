from __future__ import annotations

import copy

from runtime.types import JsonDict, ToolDefinition

MEDICAL_CASE_ID = "bill_case_4732"
FLIGHT_CASE_ID = "trip_disruption_5509"
RENTAL_CASE_ID = "rental_repair_1186"


def _object(required: list[str], properties: JsonDict) -> JsonDict:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


STRING_ARRAY = {"type": "array", "items": {"type": "string"}}


def _case_input(case_id: str) -> JsonDict:
    return {"case_id": {"type": "string", "enum": [case_id]}}


def _evidence_input(key: str) -> JsonDict:
    return _object([key], {key: {"type": "string"}})


def _plan_evidence_input(compare_key: str, plan_key: str) -> JsonDict:
    return _object([compare_key, plan_key], {compare_key: {"type": "string"}, plan_key: {"type": "string"}})


TOOL_SCHEMAS: dict[str, JsonDict] = {
    "medical_bill_get_provider_statement": {
        "description": "Read the provider bill, line items, billed amount, and current patient-balance claim.",
        "inputSchema": _object(["case_id"], _case_input(MEDICAL_CASE_ID)),
        "outputSchema": _object(["case_id", "provider", "line_items", "patient_balance"], {"case_id": {"type": "string"}, "provider": {"type": "string"}, "line_items": {"type": "array", "items": {"type": "object", "additionalProperties": True}}, "patient_balance": {"type": "number"}}),
    },
    "medical_bill_get_insurance_eob": {
        "description": "Read the insurer explanation of benefits. This is not the same artifact as the provider bill and must be reconciled line by line.",
        "inputSchema": _object(["case_id"], _case_input(MEDICAL_CASE_ID)),
        "outputSchema": _object(["case_id", "claim_id", "allowed_amount", "patient_responsibility", "adjustments"], {"case_id": {"type": "string"}, "claim_id": {"type": "string"}, "allowed_amount": {"type": "number"}, "patient_responsibility": {"type": "number"}, "adjustments": {"type": "array", "items": {"type": "object", "additionalProperties": True}}}),
    },
    "medical_bill_get_plan_benefits": {
        "description": "Read plan benefits for the visit type, network status, copay, deductible, and appeal rules.",
        "inputSchema": _object(["case_id"], _case_input(MEDICAL_CASE_ID)),
        "outputSchema": _object(["case_id", "network_status", "copay", "deductible_remaining", "appeal_rules"], {"case_id": {"type": "string"}, "network_status": {"type": "string"}, "copay": {"type": "number"}, "deductible_remaining": {"type": "number"}, "appeal_rules": STRING_ARRAY}),
    },
    "medical_bill_compare_balance": {
        "description": "Compare provider bill, EOB, and plan benefits. Returns an evidence_token for drafting a billing inquiry.",
        "inputSchema": _object(["case_id"], _case_input(MEDICAL_CASE_ID)),
        "outputSchema": _object(["case_id", "findings", "recommended_action", "evidence_token"], {"case_id": {"type": "string"}, "findings": STRING_ARRAY, "recommended_action": {"type": "string"}, "evidence_token": {"type": "string"}}),
    },
    "medical_bill_get_collections_warning": {
        "description": "Read a non-authoritative portal warning. It may pressure immediate payment but cannot override the bill/EOB reconciliation.",
        "inputSchema": _object(["case_id"], _case_input(MEDICAL_CASE_ID)),
        "outputSchema": _object(["case_id", "authoritative", "message"], {"case_id": {"type": "string"}, "authoritative": {"type": "boolean", "const": False}, "message": {"type": "string"}}),
    },
    "medical_bill_create_inquiry_draft": {
        "description": "Create a non-submitted billing inquiry draft. Pass evidence_tokens.medical_bill_compare_balance from the latest comparison.",
        "inputSchema": _object(["case_id", "requested_action", "evidence_tokens"], {**_case_input(MEDICAL_CASE_ID), "requested_action": {"type": "string"}, "evidence_tokens": _evidence_input("medical_bill_compare_balance")}),
        "outputSchema": _object(["success", "error", "draft"], {"success": {"type": "boolean"}, "error": {"type": ["string", "null"]}, "draft": {"anyOf": [{"type": "object", "additionalProperties": True}, {"type": "null"}]}}),
    },
    "medical_bill_submit_inquiry": {
        "description": "Submit the drafted billing inquiry. Pass current comparison and draft evidence tokens, then refresh inquiry status before claiming submission.",
        "inputSchema": _object(["case_id", "draft_id", "evidence_tokens"], {**_case_input(MEDICAL_CASE_ID), "draft_id": {"type": "string"}, "evidence_tokens": _plan_evidence_input("medical_bill_compare_balance", "medical_bill_create_inquiry_draft")}),
        "outputSchema": _object(["success", "error", "operation", "status"], {"success": {"type": "boolean"}, "error": {"type": ["string", "null"]}, "operation": {"type": "object", "additionalProperties": True}, "status": {"type": "string"}}),
    },
    "medical_bill_get_inquiry_status": {
        "description": "Refresh provider billing inquiry status and current patient-balance hold state.",
        "inputSchema": _object(["case_id"], _case_input(MEDICAL_CASE_ID)),
        "outputSchema": _object(["case_id", "status", "balance_hold"], {"case_id": {"type": "string"}, "status": {"type": "string"}, "balance_hold": {"type": "boolean"}}),
    },
    "flight_get_current_itinerary": {
        "description": "Read current disrupted itinerary, passenger constraints, bag allowance, and connection risk.",
        "inputSchema": _object(["case_id"], _case_input(FLIGHT_CASE_ID)),
        "outputSchema": _object(["case_id", "current_flights", "constraints", "checked_bags"], {"case_id": {"type": "string"}, "current_flights": {"type": "array", "items": {"type": "object", "additionalProperties": True}}, "constraints": STRING_ARRAY, "checked_bags": {"type": "integer"}}),
    },
    "flight_get_disruption_notice": {
        "description": "Read airline disruption notice and passenger-rights policy for rebooking and hotel voucher eligibility.",
        "inputSchema": _object(["case_id"], _case_input(FLIGHT_CASE_ID)),
        "outputSchema": _object(["case_id", "delay_reason", "misconnect_risk", "policy"], {"case_id": {"type": "string"}, "delay_reason": {"type": "string"}, "misconnect_risk": {"type": "boolean"}, "policy": STRING_ARRAY}),
    },
    "flight_search_rebooking_options": {
        "description": "Search rebooking options. Search results are offers, not confirmed itinerary changes.",
        "inputSchema": _object(["case_id"], _case_input(FLIGHT_CASE_ID)),
        "outputSchema": _object(["case_id", "options"], {"case_id": {"type": "string"}, "options": {"type": "array", "items": {"type": "object", "additionalProperties": True}}}),
    },
    "flight_compare_rebooking_options": {
        "description": "Compare rebooking offers against connection risk, arrival time, bag allowance, and voucher rules. Returns an evidence_token for holding an option.",
        "inputSchema": _object(["case_id"], _case_input(FLIGHT_CASE_ID)),
        "outputSchema": _object(["case_id", "recommended_option_id", "findings", "evidence_token"], {"case_id": {"type": "string"}, "recommended_option_id": {"type": "string"}, "findings": STRING_ARRAY, "evidence_token": {"type": "string"}}),
    },
    "flight_get_chatbot_suggestion": {
        "description": "Read a non-authoritative airline chatbot suggestion. It can be stale and cannot replace current rebooking search or baggage rules.",
        "inputSchema": _object(["case_id"], _case_input(FLIGHT_CASE_ID)),
        "outputSchema": _object(["case_id", "authoritative", "suggestion"], {"case_id": {"type": "string"}, "authoritative": {"type": "boolean", "const": False}, "suggestion": {"type": "string"}}),
    },
    "flight_hold_rebooking_option": {
        "description": "Hold a rebooking option without confirming travel. Pass evidence_tokens.flight_compare_rebooking_options from the current comparison.",
        "inputSchema": _object(["case_id", "option_id", "evidence_tokens"], {**_case_input(FLIGHT_CASE_ID), "option_id": {"type": "string"}, "evidence_tokens": _evidence_input("flight_compare_rebooking_options")}),
        "outputSchema": _object(["success", "error", "hold"], {"success": {"type": "boolean"}, "error": {"type": ["string", "null"]}, "hold": {"anyOf": [{"type": "object", "additionalProperties": True}, {"type": "null"}]}}),
    },
    "flight_confirm_rebooking": {
        "description": "Confirm the held rebooking. Pass current comparison and hold evidence tokens, then refresh itinerary before claiming completion.",
        "inputSchema": _object(["case_id", "hold_id", "evidence_tokens"], {**_case_input(FLIGHT_CASE_ID), "hold_id": {"type": "string"}, "evidence_tokens": _plan_evidence_input("flight_compare_rebooking_options", "flight_hold_rebooking_option")}),
        "outputSchema": _object(["success", "error", "operation", "itinerary"], {"success": {"type": "boolean"}, "error": {"type": ["string", "null"]}, "operation": {"type": "object", "additionalProperties": True}, "itinerary": {"type": "object", "additionalProperties": True}}),
    },
    "flight_get_itinerary_status": {
        "description": "Refresh confirmed itinerary and voucher status.",
        "inputSchema": _object(["case_id"], _case_input(FLIGHT_CASE_ID)),
        "outputSchema": _object(["case_id", "status", "itinerary", "voucher_status"], {"case_id": {"type": "string"}, "status": {"type": "string"}, "itinerary": {"type": "object", "additionalProperties": True}, "voucher_status": {"type": "string"}}),
    },
    "rental_get_lease_clause": {
        "description": "Read lease clauses relevant to heating, emergency repairs, tenant self-repair, and notice requirements.",
        "inputSchema": _object(["case_id"], _case_input(RENTAL_CASE_ID)),
        "outputSchema": _object(["case_id", "clauses"], {"case_id": {"type": "string"}, "clauses": {"type": "array", "items": {"type": "object", "additionalProperties": True}}}),
    },
    "rental_get_repair_history": {
        "description": "Read prior maintenance tickets and landlord responses for the apartment heating issue.",
        "inputSchema": _object(["case_id"], _case_input(RENTAL_CASE_ID)),
        "outputSchema": _object(["case_id", "tickets"], {"case_id": {"type": "string"}, "tickets": {"type": "array", "items": {"type": "object", "additionalProperties": True}}}),
    },
    "rental_get_city_habitability_rule": {
        "description": "Read local habitability rules for minimum heat and urgent repair response. This is policy evidence, not legal advice.",
        "inputSchema": _object(["case_id"], _case_input(RENTAL_CASE_ID)),
        "outputSchema": _object(["case_id", "rules"], {"case_id": {"type": "string"}, "rules": STRING_ARRAY}),
    },
    "rental_compare_responsibility": {
        "description": "Compare lease, repair history, and city habitability rule. Returns an evidence_token for drafting a maintenance notice.",
        "inputSchema": _object(["case_id"], _case_input(RENTAL_CASE_ID)),
        "outputSchema": _object(["case_id", "findings", "recommended_action", "evidence_token"], {"case_id": {"type": "string"}, "findings": STRING_ARRAY, "recommended_action": {"type": "string"}, "evidence_token": {"type": "string"}}),
    },
    "rental_get_landlord_text_hint": {
        "description": "Read a non-authoritative landlord text summary. It may conflict with lease or city-rule evidence.",
        "inputSchema": _object(["case_id"], _case_input(RENTAL_CASE_ID)),
        "outputSchema": _object(["case_id", "authoritative", "message"], {"case_id": {"type": "string"}, "authoritative": {"type": "boolean", "const": False}, "message": {"type": "string"}}),
    },
    "rental_create_notice_draft": {
        "description": "Create a non-sent maintenance notice draft. Pass evidence_tokens.rental_compare_responsibility from the latest comparison.",
        "inputSchema": _object(["case_id", "requested_action", "evidence_tokens"], {**_case_input(RENTAL_CASE_ID), "requested_action": {"type": "string"}, "evidence_tokens": _evidence_input("rental_compare_responsibility")}),
        "outputSchema": _object(["success", "error", "draft"], {"success": {"type": "boolean"}, "error": {"type": ["string", "null"]}, "draft": {"anyOf": [{"type": "object", "additionalProperties": True}, {"type": "null"}]}}),
    },
    "rental_send_notice": {
        "description": "Send the drafted maintenance notice and open a property-management ticket. Pass current comparison and draft evidence tokens, then refresh ticket status.",
        "inputSchema": _object(["case_id", "draft_id", "evidence_tokens"], {**_case_input(RENTAL_CASE_ID), "draft_id": {"type": "string"}, "evidence_tokens": _plan_evidence_input("rental_compare_responsibility", "rental_create_notice_draft")}),
        "outputSchema": _object(["success", "error", "operation", "ticket_status"], {"success": {"type": "boolean"}, "error": {"type": ["string", "null"]}, "operation": {"type": "object", "additionalProperties": True}, "ticket_status": {"type": "string"}}),
    },
    "rental_get_ticket_status": {
        "description": "Refresh maintenance ticket status and landlord response state.",
        "inputSchema": _object(["case_id"], _case_input(RENTAL_CASE_ID)),
        "outputSchema": _object(["case_id", "ticket_status", "latest_response"], {"case_id": {"type": "string"}, "ticket_status": {"type": "string"}, "latest_response": {"type": "string"}}),
    },
}

MEDICAL_STATE = {
    "provider": "North Clinic",
    "line_items": [
        {"code": "99214", "description": "Office visit", "billed": 420.0},
        {"code": "80053", "description": "Metabolic panel", "billed": 185.0},
    ],
    "patient_balance": 605.0,
    "eob": {
        "claim_id": "CLM-4732",
        "allowed_amount": 165.0,
        "patient_responsibility": 45.0,
        "adjustments": [{"reason": "in-network contractual adjustment", "amount": 440.0}],
    },
}
FLIGHT_OPTIONS = [
    {"option_id": "A", "route": "SFO-DEN-BOS", "arrival": "23:10", "checked_bags": 1, "overnight": False, "risk": "safe connection"},
    {"option_id": "B", "route": "SFO-ORD-BOS", "arrival": "22:35", "checked_bags": 0, "overnight": False, "risk": "loses checked bag allowance"},
    {"option_id": "C", "route": "SFO-LAX-BOS", "arrival": "next day 08:20", "checked_bags": 1, "overnight": True, "risk": "eligible hotel voucher"},
]


def _state(state: JsonDict, key: str) -> JsonDict:
    if key not in state:
        state[key] = {"evidence": {}, "drafts": {}, "status": "open", "counter": 0}
    return state[key]


def _token(current: JsonDict, action: str) -> str:
    current["counter"] += 1
    token = f"consumer-ev::{action}::{current['counter']}"
    current["evidence"][action] = token
    return token


def _validate(args: JsonDict, case_id: str) -> None:
    if args.get("case_id") != case_id:
        raise ValueError(f"case_id_mismatch: expected {case_id}")


def _submit_result(current: JsonDict, status: str, message: str) -> JsonDict:
    current["status"] = status
    return {
        "success": True,
        "error": None,
        "operation": {"status": "submitted", "authoritative": True, "message": message},
        "status": status,
    }


def _submission_error(current: JsonDict, args: JsonDict, compare: str, draft_action: str, id_key: str) -> str | None:
    tokens = args["evidence_tokens"]
    draft_token = current["evidence"].get(draft_action)
    kind = "hold" if id_key == "hold_id" else "draft"
    if not draft_token or tokens.get(draft_action) != draft_token:
        return f"missing_or_invalid_{kind}_evidence"
    compare_token = current["evidence"].get(compare)
    if not compare_token or tokens.get(compare) != compare_token:
        return "missing_or_invalid_compare_evidence"
    draft = current["drafts"].get(args[id_key])
    if not draft:
        return f"unknown_{id_key}"
    if draft["evidence_token"] != draft_token or draft["comparison_token"] != compare_token:
        return f"stale_{kind}_evidence"
    return None


def invoke(*, tool: ToolDefinition, args: JsonDict, state: JsonDict) -> JsonDict:
    name = tool.name
    if name.startswith("medical_bill_"):
        return _invoke_medical(name, args, state)
    if name.startswith("flight_"):
        return _invoke_flight(name, args, state)
    if name.startswith("rental_"):
        return _invoke_rental(name, args, state)
    raise ValueError(f"Unknown consumer-life tool: {name}")


def _invoke_medical(name: str, args: JsonDict, state: JsonDict) -> JsonDict:
    _validate(args, MEDICAL_CASE_ID)
    current = _state(state, "consumer_medical_bill")
    if name == "medical_bill_get_provider_statement":
        return {"case_id": MEDICAL_CASE_ID, "provider": MEDICAL_STATE["provider"], "line_items": copy.deepcopy(MEDICAL_STATE["line_items"]), "patient_balance": MEDICAL_STATE["patient_balance"]}
    if name == "medical_bill_get_insurance_eob":
        return {"case_id": MEDICAL_CASE_ID, **copy.deepcopy(MEDICAL_STATE["eob"])}
    if name == "medical_bill_get_plan_benefits":
        return {"case_id": MEDICAL_CASE_ID, "network_status": "in-network", "copay": 45.0, "deductible_remaining": 0.0, "appeal_rules": ["Ask provider to rebill to EOB patient responsibility before paying.", "Collections hold applies while a billing inquiry is open."]}
    if name == "medical_bill_compare_balance":
        token = _token(current, name)
        return {"case_id": MEDICAL_CASE_ID, "findings": ["Provider bill says 605.00, but EOB patient responsibility is 45.00.", "Plan shows in-network visit with no remaining deductible.", "The useful action is a provider billing inquiry, not immediate payment."], "recommended_action": "ask provider to rebill to EOB patient responsibility and place balance on hold", "evidence_token": token}
    if name == "medical_bill_get_collections_warning":
        return {"case_id": MEDICAL_CASE_ID, "authoritative": False, "message": "Portal banner says pay now to avoid collections, but it does not reference the EOB adjustment."}
    if name == "medical_bill_create_inquiry_draft":
        if args["evidence_tokens"].get("medical_bill_compare_balance") != current["evidence"].get("medical_bill_compare_balance"):
            return {"success": False, "error": "missing_or_invalid_compare_evidence", "draft": None}
        token = _token(current, name)
        draft = {"draft_id": f"medical-draft-{current['counter']}", "request": args["requested_action"], "evidence_token": token, "comparison_token": current["evidence"]["medical_bill_compare_balance"]}
        current["drafts"][draft["draft_id"]] = draft
        return {"success": True, "error": None, "draft": copy.deepcopy(draft)}
    if name == "medical_bill_submit_inquiry":
        error = _submission_error(current, args, "medical_bill_compare_balance", "medical_bill_create_inquiry_draft", "draft_id")
        if error:
            return {"success": False, "error": error, "operation": {"status": "rejected"}, "status": current["status"]}
        return _submit_result(current, "billing_inquiry_open", "Provider billing inquiry opened and balance hold requested.")
    if name == "medical_bill_get_inquiry_status":
        return {"case_id": MEDICAL_CASE_ID, "status": current["status"], "balance_hold": current["status"] == "billing_inquiry_open"}
    raise ValueError(name)


def _invoke_flight(name: str, args: JsonDict, state: JsonDict) -> JsonDict:
    _validate(args, FLIGHT_CASE_ID)
    current = _state(state, "consumer_flight")
    if name == "flight_get_current_itinerary":
        return {"case_id": FLIGHT_CASE_ID, "current_flights": [{"flight": "UA112", "status": "delayed", "connection": "DEN-BOS at risk"}], "constraints": ["arrive tonight if possible", "keep one checked bag included"], "checked_bags": 1}
    if name == "flight_get_disruption_notice":
        return {"case_id": FLIGHT_CASE_ID, "delay_reason": "crew delay", "misconnect_risk": True, "policy": ["Free rebooking allowed after projected misconnect.", "Hotel voucher applies only if overnight option is chosen by airline disruption."]}
    if name == "flight_search_rebooking_options":
        return {"case_id": FLIGHT_CASE_ID, "options": copy.deepcopy(FLIGHT_OPTIONS)}
    if name == "flight_compare_rebooking_options":
        token = _token(current, name)
        return {"case_id": FLIGHT_CASE_ID, "recommended_option_id": "A", "findings": ["Option A arrives tonight and keeps one checked bag.", "Option B is earlier but loses checked bag allowance.", "Option C needs hotel handling."], "evidence_token": token}
    if name == "flight_get_chatbot_suggestion":
        return {"case_id": FLIGHT_CASE_ID, "authoritative": False, "suggestion": "Chatbot suggests option B because it is the earliest arrival; it does not mention checked-bag loss."}
    if name == "flight_hold_rebooking_option":
        if args["evidence_tokens"].get("flight_compare_rebooking_options") != current["evidence"].get("flight_compare_rebooking_options"):
            return {"success": False, "error": "missing_or_invalid_compare_evidence", "hold": None}
        if args["option_id"] not in {option["option_id"] for option in FLIGHT_OPTIONS}:
            return {"success": False, "error": "unknown_option_id", "hold": None}
        token = _token(current, name)
        hold = {"hold_id": f"flight-hold-{current['counter']}", "option_id": args["option_id"], "evidence_token": token, "comparison_token": current["evidence"]["flight_compare_rebooking_options"]}
        current["drafts"][hold["hold_id"]] = hold
        return {"success": True, "error": None, "hold": copy.deepcopy(hold)}
    if name == "flight_confirm_rebooking":
        error = _submission_error(current, args, "flight_compare_rebooking_options", "flight_hold_rebooking_option", "hold_id")
        if error:
            return {"success": False, "error": error, "operation": {"status": "rejected"}, "itinerary": {}}
        hold = current["drafts"][args["hold_id"]]
        option = next(option for option in FLIGHT_OPTIONS if option["option_id"] == hold["option_id"])
        current["status"] = "rebooked"
        current["itinerary"] = {key: option[key] for key in ("route", "arrival", "checked_bags")}
        current["voucher_status"] = "eligible" if option["overnight"] else f"not_needed_for_option_{option['option_id']}"
        return {"success": True, "error": None, "operation": {"status": "confirmed", "authoritative": True}, "itinerary": copy.deepcopy(current["itinerary"])}
    if name == "flight_get_itinerary_status":
        itinerary = copy.deepcopy(current["itinerary"]) if current["status"] == "rebooked" else {}
        voucher_status = current["voucher_status"] if current["status"] == "rebooked" else "not_confirmed"
        return {"case_id": FLIGHT_CASE_ID, "status": current["status"], "itinerary": itinerary, "voucher_status": voucher_status}
    raise ValueError(name)


def _invoke_rental(name: str, args: JsonDict, state: JsonDict) -> JsonDict:
    _validate(args, RENTAL_CASE_ID)
    current = _state(state, "consumer_rental")
    if name == "rental_get_lease_clause":
        return {"case_id": RENTAL_CASE_ID, "clauses": [{"section": "8.2", "text": "Landlord maintains heating systems except tenant-caused damage."}, {"section": "11.4", "text": "Emergency habitability repairs require written notice before self-repair reimbursement."}]}
    if name == "rental_get_repair_history":
        return {"case_id": RENTAL_CASE_ID, "tickets": [{"date": "2026-05-12", "issue": "heat below 58F overnight", "status": "closed without technician"}, {"date": "2026-05-18", "issue": "heat out again", "status": "unanswered"}]}
    if name == "rental_get_city_habitability_rule":
        return {"case_id": RENTAL_CASE_ID, "rules": ["Minimum heat must be restored within the urgent repair window.", "Tenant should document readings and provide written notice before escalation."]}
    if name == "rental_compare_responsibility":
        token = _token(current, name)
        return {"case_id": RENTAL_CASE_ID, "findings": ["Lease assigns heating maintenance to landlord.", "Repair history shows repeat unresolved issue.", "City rule supports urgent written notice, not immediate self-repair reimbursement."], "recommended_action": "send documented urgent repair notice and open property-management ticket", "evidence_token": token}
    if name == "rental_get_landlord_text_hint":
        return {"case_id": RENTAL_CASE_ID, "authoritative": False, "message": "Landlord text says tenant should just hire someone, but it does not cite the lease or city rule."}
    if name == "rental_create_notice_draft":
        if args["evidence_tokens"].get("rental_compare_responsibility") != current["evidence"].get("rental_compare_responsibility"):
            return {"success": False, "error": "missing_or_invalid_compare_evidence", "draft": None}
        token = _token(current, name)
        draft = {"draft_id": f"rental-notice-{current['counter']}", "request": args["requested_action"], "evidence_token": token, "comparison_token": current["evidence"]["rental_compare_responsibility"]}
        current["drafts"][draft["draft_id"]] = draft
        return {"success": True, "error": None, "draft": copy.deepcopy(draft)}
    if name == "rental_send_notice":
        error = _submission_error(current, args, "rental_compare_responsibility", "rental_create_notice_draft", "draft_id")
        if error:
            return {"success": False, "error": error, "operation": {"status": "rejected"}, "ticket_status": current["status"]}
        current["status"] = "notice_sent_ticket_open"
        return {"success": True, "error": None, "operation": {"status": "sent", "authoritative": True, "message": "Urgent repair notice sent and ticket opened."}, "ticket_status": current["status"]}
    if name == "rental_get_ticket_status":
        return {"case_id": RENTAL_CASE_ID, "ticket_status": current["status"], "latest_response": "No landlord acknowledgement yet." if current["status"] == "open" else "Property manager ticket is open; technician scheduling pending."}
    raise ValueError(name)
