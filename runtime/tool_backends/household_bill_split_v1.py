from __future__ import annotations

import copy

from runtime.types import JsonDict, ToolDefinition

CASE_ID = "household_split_2026_05"
PARTICIPANTS = ["Alex", "Mina", "Theo"]


def _object(required: list[str], properties: JsonDict) -> JsonDict:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


STRING_ARRAY = {"type": "array", "items": {"type": "string"}}
COMMON_INPUT = {"case_id": {"type": "string", "enum": [CASE_ID]}}

EXPENSES = [
    {
        "expense_id": "rent_may",
        "label": "May rent",
        "amount": 3150.0,
        "paid_by": "Alex",
        "eligible_people": ["Alex", "Mina", "Theo"],
        "status": "posted",
        "split_basis": "presence_days",
    },
    {
        "expense_id": "internet_may",
        "label": "May internet",
        "amount": 84.0,
        "paid_by": "Mina",
        "eligible_people": ["Alex", "Mina", "Theo"],
        "status": "posted",
        "split_basis": "equal",
    },
    {
        "expense_id": "repair_filter",
        "label": "AC filter replacement",
        "amount": 96.0,
        "paid_by": "Theo",
        "eligible_people": ["Alex", "Mina"],
        "status": "posted",
        "split_basis": "eligible_people",
    },
    {
        "expense_id": "grocery_pending",
        "label": "Pending grocery authorization",
        "amount": 142.2,
        "paid_by": "Mina",
        "eligible_people": ["Alex", "Mina", "Theo"],
        "status": "pending",
        "split_basis": "excluded_until_posted",
    },
    {
        "expense_id": "internet_duplicate",
        "label": "Duplicate internet receipt upload",
        "amount": 84.0,
        "paid_by": "Mina",
        "eligible_people": ["Alex", "Mina", "Theo"],
        "status": "duplicate",
        "split_basis": "excluded_duplicate",
    },
]
PRESENCE_DAYS = {"Alex": 31, "Mina": 31, "Theo": 15}
PAYMENTS = [
    {"from": "Theo", "to": "Alex", "amount": 250.0, "status": "paid", "memo": "April catch-up"}
]

TOOL_SCHEMAS: dict[str, JsonDict] = {
    "household_get_members": {
        "description": "Read household members and the active monthly split policy.",
        "inputSchema": _object(["case_id"], COMMON_INPUT),
        "outputSchema": _object(["case_id", "members", "policy"], {"case_id": {"type": "string"}, "members": STRING_ARRAY, "policy": STRING_ARRAY}),
    },
    "household_list_expenses": {
        "description": "Read monthly expenses with status, payer, eligible participants, and split basis. Pending or duplicate expenses must not enter settlement.",
        "inputSchema": _object(["case_id"], COMMON_INPUT),
        "outputSchema": _object(["case_id", "expenses"], {"case_id": {"type": "string"}, "expenses": {"type": "array", "items": {"type": "object", "additionalProperties": True}}}),
    },
    "household_get_presence_calendar": {
        "description": "Read occupancy days used for presence-weighted shared expenses.",
        "inputSchema": _object(["case_id"], COMMON_INPUT),
        "outputSchema": _object(["case_id", "presence_days"], {"case_id": {"type": "string"}, "presence_days": {"type": "object", "additionalProperties": {"type": "integer"}}}),
    },
    "household_get_payment_history": {
        "description": "Read already-settled payments so the UI can avoid duplicate payment requests.",
        "inputSchema": _object(["case_id"], COMMON_INPUT),
        "outputSchema": _object(["case_id", "payments"], {"case_id": {"type": "string"}, "payments": {"type": "array", "items": {"type": "object", "additionalProperties": True}}}),
    },
    "household_validate_expense_selection": {
        "description": "Validate selected expense ids against posted status and duplicate/pending exclusions. Returns an evidence_token for split calculation.",
        "inputSchema": _object(["case_id", "expense_ids"], {**COMMON_INPUT, "expense_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True}}),
        "outputSchema": _object(["case_id", "valid", "errors", "included_expense_ids", "evidence_token"], {"case_id": {"type": "string"}, "valid": {"type": "boolean"}, "errors": STRING_ARRAY, "included_expense_ids": STRING_ARRAY, "evidence_token": {"type": "string"}}),
    },
    "household_calculate_split": {
        "description": "Calculate member balances and net payment requests from a validated expense selection and occupancy days. Pass evidence_tokens.household_validate_expense_selection.",
        "inputSchema": _object(["case_id", "expense_ids", "evidence_tokens"], {**COMMON_INPUT, "expense_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True}, "evidence_tokens": _object(["household_validate_expense_selection"], {"household_validate_expense_selection": {"type": "string"}})}),
        "outputSchema": _object(["case_id", "balances", "payment_requests", "evidence_token"], {"case_id": {"type": "string"}, "balances": {"type": "object", "additionalProperties": {"type": "number"}}, "payment_requests": {"type": "array", "items": {"type": "object", "additionalProperties": True}}, "evidence_token": {"type": "string"}}),
    },
    "household_get_ai_split_suggestion": {
        "description": "Read a non-authoritative generated split suggestion. It may include pending or duplicate expenses and cannot replace validation/calculation tools.",
        "inputSchema": _object(["case_id"], COMMON_INPUT),
        "outputSchema": _object(["case_id", "authoritative", "suggestion"], {"case_id": {"type": "string"}, "authoritative": {"type": "boolean", "const": False}, "suggestion": {"type": "string"}}),
    },
    "household_create_settlement_plan": {
        "description": "Create a non-sent settlement plan. Pass current validation and calculation evidence tokens for the same selected expense ids.",
        "inputSchema": _object(["case_id", "expense_ids", "evidence_tokens"], {**COMMON_INPUT, "expense_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True}, "evidence_tokens": _object(["household_validate_expense_selection", "household_calculate_split"], {"household_validate_expense_selection": {"type": "string"}, "household_calculate_split": {"type": "string"}})}),
        "outputSchema": _object(["success", "error", "plan"], {"success": {"type": "boolean"}, "error": {"type": ["string", "null"]}, "plan": {"anyOf": [{"type": "object", "additionalProperties": True}, {"type": "null"}]}}),
    },
    "household_send_payment_requests": {
        "description": "Send the staged payment requests. Pass validation, calculation, and plan evidence tokens, then refresh payment status.",
        "inputSchema": _object(["case_id", "plan_id", "evidence_tokens"], {**COMMON_INPUT, "plan_id": {"type": "string"}, "evidence_tokens": _object(["household_validate_expense_selection", "household_calculate_split", "household_create_settlement_plan"], {"household_validate_expense_selection": {"type": "string"}, "household_calculate_split": {"type": "string"}, "household_create_settlement_plan": {"type": "string"}})}),
        "outputSchema": _object(["success", "error", "operation", "payment_status"], {"success": {"type": "boolean"}, "error": {"type": ["string", "null"]}, "operation": {"type": "object", "additionalProperties": True}, "payment_status": {"type": "array", "items": {"type": "object", "additionalProperties": True}}}),
    },
    "household_get_payment_status": {
        "description": "Refresh sent payment-request statuses.",
        "inputSchema": _object(["case_id"], COMMON_INPUT),
        "outputSchema": _object(["case_id", "payment_status"], {"case_id": {"type": "string"}, "payment_status": {"type": "array", "items": {"type": "object", "additionalProperties": True}}}),
    },
}


def _state(state: JsonDict) -> JsonDict:
    if "household_bill_split_v1" not in state:
        state["household_bill_split_v1"] = {
            "evidence": {},
            "selection": [],
            "plans": {},
            "payment_status": [],
            "counter": 0,
        }
    return state["household_bill_split_v1"]


def _check(args: JsonDict) -> None:
    if args.get("case_id") != CASE_ID:
        raise ValueError(f"case_id_mismatch: expected {CASE_ID}")


def _token(current: JsonDict, action: str) -> str:
    current["counter"] += 1
    token = f"household-ev::{action}::{current['counter']}"
    current["evidence"][action] = token
    return token


def _posted_by_id() -> dict[str, JsonDict]:
    return {expense["expense_id"]: expense for expense in EXPENSES if expense["status"] == "posted"}


def _calculate(expense_ids: list[str]) -> tuple[dict[str, float], list[JsonDict]]:
    balances = {person: 0.0 for person in PARTICIPANTS}
    posted = _posted_by_id()
    for expense_id in expense_ids:
        expense = posted[expense_id]
        balances[expense["paid_by"]] += float(expense["amount"])
        if expense["split_basis"] == "presence_days":
            total_days = sum(PRESENCE_DAYS[p] for p in expense["eligible_people"])
            for person in expense["eligible_people"]:
                balances[person] -= expense["amount"] * PRESENCE_DAYS[person] / total_days
        else:
            share = expense["amount"] / len(expense["eligible_people"])
            for person in expense["eligible_people"]:
                balances[person] -= share
    rounded = {person: round(value, 2) for person, value in balances.items()}
    debtors = sorted([(p, -v) for p, v in rounded.items() if v < -0.01], key=lambda item: item[1], reverse=True)
    creditors = sorted([(p, v) for p, v in rounded.items() if v > 0.01], key=lambda item: item[1], reverse=True)
    requests: list[JsonDict] = []
    i = j = 0
    while i < len(debtors) and j < len(creditors):
        debtor, debt = debtors[i]
        creditor, credit = creditors[j]
        amount = round(min(debt, credit), 2)
        requests.append({"from": debtor, "to": creditor, "amount": amount, "status": "draft"})
        debtors[i] = (debtor, round(debt - amount, 2))
        creditors[j] = (creditor, round(credit - amount, 2))
        if debtors[i][1] <= 0.01:
            i += 1
        if creditors[j][1] <= 0.01:
            j += 1
    return rounded, requests


def invoke(*, tool: ToolDefinition, args: JsonDict, state: JsonDict) -> JsonDict:
    _check(args)
    current = _state(state)
    name = tool.name
    if name == "household_get_members":
        return {"case_id": CASE_ID, "members": PARTICIPANTS, "policy": ["Posted expenses only.", "Rent is weighted by occupancy days.", "Duplicate uploads and pending authorizations are excluded."]}
    if name == "household_list_expenses":
        return {"case_id": CASE_ID, "expenses": copy.deepcopy(EXPENSES)}
    if name == "household_get_presence_calendar":
        return {"case_id": CASE_ID, "presence_days": copy.deepcopy(PRESENCE_DAYS)}
    if name == "household_get_payment_history":
        return {"case_id": CASE_ID, "payments": copy.deepcopy(PAYMENTS)}
    if name == "household_validate_expense_selection":
        ids = list(args["expense_ids"])
        by_id = {expense["expense_id"]: expense for expense in EXPENSES}
        errors = []
        for expense_id in ids:
            expense = by_id.get(expense_id)
            if not expense:
                errors.append(f"unknown_expense:{expense_id}")
            elif expense["status"] != "posted":
                errors.append(f"excluded_{expense['status']}:{expense_id}")
        current["selection"] = ids
        token = _token(current, "household_validate_expense_selection")
        return {"case_id": CASE_ID, "valid": not errors, "errors": errors, "included_expense_ids": [i for i in ids if i in _posted_by_id()], "evidence_token": token}
    if name == "household_calculate_split":
        if args["evidence_tokens"].get("household_validate_expense_selection") != current["evidence"].get("household_validate_expense_selection"):
            raise ValueError("missing_or_invalid_selection_evidence")
        balances, requests = _calculate(list(args["expense_ids"]))
        token = _token(current, "household_calculate_split")
        current["calculation"] = {"balances": balances, "payment_requests": requests}
        return {"case_id": CASE_ID, "balances": balances, "payment_requests": requests, "evidence_token": token}
    if name == "household_get_ai_split_suggestion":
        return {"case_id": CASE_ID, "authoritative": False, "suggestion": "Split every receipt equally, including the pending grocery authorization and duplicate internet upload."}
    if name == "household_create_settlement_plan":
        tokens = args["evidence_tokens"]
        if tokens.get("household_validate_expense_selection") != current["evidence"].get("household_validate_expense_selection"):
            return {"success": False, "error": "missing_or_invalid_selection_evidence", "plan": None}
        if tokens.get("household_calculate_split") != current["evidence"].get("household_calculate_split"):
            return {"success": False, "error": "missing_or_invalid_calculation_evidence", "plan": None}
        plan_token = _token(current, "household_create_settlement_plan")
        plan_id = f"household-plan-{current['counter']}"
        plan = {"plan_id": plan_id, "expense_ids": list(args["expense_ids"]), "payment_requests": copy.deepcopy(current["calculation"]["payment_requests"]), "evidence_token": plan_token}
        current["plans"][plan_id] = plan
        return {"success": True, "error": None, "plan": plan}
    if name == "household_send_payment_requests":
        tokens = args["evidence_tokens"]
        if tokens.get("household_validate_expense_selection") != current["evidence"].get("household_validate_expense_selection"):
            return {"success": False, "error": "missing_or_invalid_selection_evidence", "operation": {"status": "rejected"}, "payment_status": copy.deepcopy(current["payment_status"])}
        if tokens.get("household_calculate_split") != current["evidence"].get("household_calculate_split"):
            return {"success": False, "error": "missing_or_invalid_calculation_evidence", "operation": {"status": "rejected"}, "payment_status": copy.deepcopy(current["payment_status"])}
        if tokens.get("household_create_settlement_plan") != current["evidence"].get("household_create_settlement_plan"):
            return {"success": False, "error": "missing_or_invalid_plan_evidence", "operation": {"status": "rejected"}, "payment_status": copy.deepcopy(current["payment_status"])}
        plan = current["plans"].get(args["plan_id"])
        if not plan:
            return {"success": False, "error": "unknown_plan_id", "operation": {"status": "rejected"}, "payment_status": copy.deepcopy(current["payment_status"])}
        current["payment_status"] = [{**request, "status": "sent"} for request in plan["payment_requests"]]
        return {"success": True, "error": None, "operation": {"status": "sent", "authoritative": True}, "payment_status": copy.deepcopy(current["payment_status"])}
    if name == "household_get_payment_status":
        return {"case_id": CASE_ID, "payment_status": copy.deepcopy(current["payment_status"])}
    raise ValueError(f"Unknown household split tool: {name}")
