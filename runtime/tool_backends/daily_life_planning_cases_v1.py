from __future__ import annotations

import copy
from datetime import datetime, time, timedelta

from runtime.types import JsonDict, ToolDefinition

EXAM_CASE_ID = "exam_week_case_314"
BUDGET_CASE_ID = "family_budget_case_882"
CLAIM_CASE_ID = "home_water_claim_case_506"


def _object(required: list[str], properties: JsonDict) -> JsonDict:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _token_input(*keys: str) -> JsonDict:
    return _object(list(keys), {key: {"type": "string"} for key in keys})


STRING_ARRAY = {"type": "array", "items": {"type": "string"}}
OPEN_ARRAY = {"type": "array", "items": {"type": "object", "additionalProperties": True}}
OPEN_OBJECT = {"type": "object", "additionalProperties": True}
EXAM_INPUT = {"case_id": {"type": "string", "enum": [EXAM_CASE_ID]}}
BUDGET_INPUT = {"case_id": {"type": "string", "enum": [BUDGET_CASE_ID]}}
CLAIM_INPUT = {"case_id": {"type": "string", "enum": [CLAIM_CASE_ID]}}

EXAMS = [
    {
        "course_id": "STAT201",
        "name": "Statistics",
        "exam_time": "2026-05-22T09:00",
        "exam_end": "2026-05-22T11:00",
        "weight": "high",
    },
    {
        "course_id": "CHEM110",
        "name": "Chemistry",
        "exam_time": "2026-05-23T14:00",
        "exam_end": "2026-05-23T16:00",
        "weight": "medium",
    },
    {
        "course_id": "HIST230",
        "name": "History",
        "exam_time": "2026-05-24T11:00",
        "exam_end": "2026-05-24T13:00",
        "weight": "medium",
    },
]
DEADLINES = [
    {"item_id": "CHEM110_LAB", "course_id": "CHEM110", "due_time": "2026-05-21T18:00", "estimated_minutes": 90},
    {"item_id": "HIST230_OUTLINE", "course_id": "HIST230", "due_time": "2026-05-22T20:00", "estimated_minutes": 60},
]
LIBRARY_SEATS = [
    {"seat_id": "quiet_2f_a", "start": "2026-05-20T19:00", "end": "2026-05-20T21:00", "zone": "quiet", "available": True},
    {"seat_id": "quiet_2f_b", "start": "2026-05-21T19:00", "end": "2026-05-21T21:00", "zone": "quiet", "available": False},
    {"seat_id": "group_1f_c", "start": "2026-05-21T19:00", "end": "2026-05-21T21:00", "zone": "group", "available": True},
    {"seat_id": "quiet_3f_d", "start": "2026-05-22T18:00", "end": "2026-05-22T20:00", "zone": "quiet", "available": True},
]
REVIEW_SESSIONS = [
    {"session_id": "STAT201_REVIEW", "course_id": "STAT201", "start": "2026-05-20T17:00", "end": "2026-05-20T18:00"},
    {"session_id": "CHEM110_REVIEW", "course_id": "CHEM110", "start": "2026-05-21T16:00", "end": "2026-05-21T17:00"},
]

BUDGET_BALANCE = {"checking": 1800.0, "as_of": "2026-05-19"}
BUDGET_INCOME = [
    {"income_id": "paycheck_0524", "date": "2026-05-24", "amount": 2100.0},
]
BUDGET_BILLS = [
    {"bill_id": "rent", "date": "2026-05-20", "amount": 950.0, "autopay": True, "category": "housing"},
    {"bill_id": "electric", "date": "2026-05-21", "amount": 210.0, "autopay": False, "category": "utility"},
    {"bill_id": "childcare", "date": "2026-05-22", "amount": 420.0, "autopay": False, "category": "care"},
    {"bill_id": "streaming_plus", "date": "2026-05-23", "amount": 28.0, "autopay": True, "category": "subscription"},
    {"bill_id": "groceries", "date": "2026-05-23", "amount": 180.0, "autopay": False, "category": "food"},
]
BUDGET_OPTIONS = [
    {"action_id": "delay_childcare", "bill_id": "childcare", "new_date": "2026-05-25", "fee": 15.0, "allowed": True},
    {"action_id": "cancel_streaming_plus", "bill_id": "streaming_plus", "saves": 28.0, "allowed": True, "effective_date": "2026-05-23"},
    {"action_id": "delay_electric", "bill_id": "electric", "new_date": "2026-05-27", "fee": 35.0, "allowed": False},
]
CLAIM_POLICY = {
    "policy_id": "renters_policy_2026",
    "deductible": 500.0,
    "covered_incident_types": ["sudden_pipe_leak"],
    "coverage_limits": {"personal_property": 5000.0, "temporary_housing": 1200.0, "water_mitigation": 3500.0},
    "exclusions": ["long_term_seepage", "cosmetic_upgrade", "pre_existing_mold"],
}
CLAIM_REQUIREMENTS = [
    {"requirement_id": "incident_photo", "label": "Photos of sudden water damage", "required": True},
    {"requirement_id": "vendor_invoice", "label": "Plumber invoice or source-of-loss document", "required": True},
    {"requirement_id": "itemized_estimate", "label": "Itemized drying or repair estimate", "required": True},
    {"requirement_id": "temporary_housing_receipt", "label": "Temporary housing receipt if claimed", "required": False},
]
CLAIM_DOCUMENTS = [
    {"document_id": "photo_child_room_wall", "doc_type": "incident_photo", "room": "child_room", "date": "2026-05-18"},
    {"document_id": "photo_kitchen_ceiling", "doc_type": "incident_photo", "room": "kitchen", "date": "2026-05-18"},
    {"document_id": "plumber_invoice_pipeflow", "doc_type": "vendor_invoice", "amount": 620.0, "cause": "sudden_pipe_leak"},
    {"document_id": "hotel_receipt_one_night", "doc_type": "temporary_housing_receipt", "amount": 180.0},
    {"document_id": "old_paint_quote", "doc_type": "cosmetic_quote", "amount": 520.0},
]
CLAIM_ESTIMATES = [
    {"estimate_id": "drywall_child_room", "room": "child_room", "category": "water_mitigation", "amount": 1450.0, "covered": True},
    {"estimate_id": "floor_drying_hall", "room": "hallway", "category": "water_mitigation", "amount": 780.0, "covered": True},
    {"estimate_id": "paint_upgrade", "room": "child_room", "category": "cosmetic_upgrade", "amount": 520.0, "covered": False},
]
CLAIM_ITEMS = [
    {"item_id": "area_rug", "room": "child_room", "amount": 180.0, "covered": True},
    {"item_id": "toy_storage_bin", "room": "child_room", "amount": 95.0, "covered": True},
    {"item_id": "designer_wallpaper", "room": "child_room", "amount": 420.0, "covered": False},
]

TOOL_SCHEMAS: dict[str, JsonDict] = {
    "exam_get_exam_calendar": {
        "description": "Read authoritative exam times and course weights for the exam week.",
        "inputSchema": _object(["case_id"], EXAM_INPUT),
        "outputSchema": _object(["case_id", "exams"], {"case_id": {"type": "string"}, "exams": OPEN_ARRAY}),
    },
    "exam_get_deadlines": {
        "description": "Read assignment deadlines and estimated completion time during exam week.",
        "inputSchema": _object(["case_id"], EXAM_INPUT),
        "outputSchema": _object(["case_id", "deadlines"], {"case_id": {"type": "string"}, "deadlines": OPEN_ARRAY}),
    },
    "exam_get_library_seat_availability": {
        "description": "Read current library seat availability by time and zone. User study preferences are not stored here.",
        "inputSchema": _object(["case_id"], EXAM_INPUT),
        "outputSchema": _object(["case_id", "seats"], {"case_id": {"type": "string"}, "seats": OPEN_ARRAY}),
    },
    "exam_get_review_sessions": {
        "description": "Read official review sessions that can be included in the study schedule.",
        "inputSchema": _object(["case_id"], EXAM_INPUT),
        "outputSchema": _object(["case_id", "sessions"], {"case_id": {"type": "string"}, "sessions": OPEN_ARRAY}),
    },
    "exam_validate_study_schedule": {
        "description": "Validate a proposed study schedule against exam/deadline conflicts, user-provided session limits, required course coverage, and requested quiet-seat holds.",
        "inputSchema": _object(
            ["case_id", "blocks", "required_course_ids", "max_block_minutes", "no_study_after_hour"],
            {
                **EXAM_INPUT,
                "blocks": OPEN_ARRAY,
                "required_course_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True},
                "max_block_minutes": {"type": "integer", "minimum": 1},
                "no_study_after_hour": {"type": "integer", "minimum": 0, "maximum": 24},
            },
        ),
        "outputSchema": _object(["case_id", "valid", "errors", "coverage", "evidence_token"], {"case_id": {"type": "string"}, "valid": {"type": "boolean"}, "errors": STRING_ARRAY, "coverage": OPEN_ARRAY, "evidence_token": {"type": "string"}}),
    },
    "exam_hold_library_seats": {
        "description": "Hold selected available library seats for a validated study schedule. Pass evidence_tokens.exam_validate_study_schedule.",
        "inputSchema": _object(["case_id", "seat_ids", "evidence_tokens"], {**EXAM_INPUT, "seat_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True}, "evidence_tokens": _token_input("exam_validate_study_schedule")}),
        "outputSchema": _object(["success", "error", "holds", "evidence_token"], {"success": {"type": "boolean"}, "error": {"type": ["string", "null"]}, "holds": OPEN_ARRAY, "evidence_token": {"type": ["string", "null"]}}),
    },
    "exam_save_study_plan": {
        "description": "Save the validated study plan only after holding the library seats used by the plan. Pass evidence_tokens.exam_validate_study_schedule and evidence_tokens.exam_hold_library_seats, then refresh plan status.",
        "inputSchema": _object(["case_id", "blocks", "evidence_tokens"], {**EXAM_INPUT, "blocks": OPEN_ARRAY, "evidence_tokens": _token_input("exam_validate_study_schedule", "exam_hold_library_seats")}),
        "outputSchema": _object(["success", "error", "saved_plan"], {"success": {"type": "boolean"}, "error": {"type": ["string", "null"]}, "saved_plan": {"anyOf": [OPEN_OBJECT, {"type": "null"}]}}),
    },
    "exam_get_study_plan_status": {
        "description": "Refresh saved study plan and library hold status.",
        "inputSchema": _object(["case_id"], EXAM_INPUT),
        "outputSchema": _object(["case_id", "saved_plan", "holds"], {"case_id": {"type": "string"}, "saved_plan": {"anyOf": [OPEN_OBJECT, {"type": "null"}]}, "holds": OPEN_ARRAY}),
    },
    "budget_get_current_balance": {
        "description": "Read current checking balance and as-of date.",
        "inputSchema": _object(["case_id"], BUDGET_INPUT),
        "outputSchema": _object(["case_id", "balance"], {"case_id": {"type": "string"}, "balance": OPEN_OBJECT}),
    },
    "budget_get_income_schedule": {
        "description": "Read expected income deposits by date.",
        "inputSchema": _object(["case_id"], BUDGET_INPUT),
        "outputSchema": _object(["case_id", "income"], {"case_id": {"type": "string"}, "income": OPEN_ARRAY}),
    },
    "budget_get_bills_and_autopay": {
        "description": "Read upcoming bills, due dates, amounts, categories, and autopay status. Autopay bills should not be manually scheduled again.",
        "inputSchema": _object(["case_id"], BUDGET_INPUT),
        "outputSchema": _object(["case_id", "bills"], {"case_id": {"type": "string"}, "bills": OPEN_ARRAY}),
    },
    "budget_get_flex_options": {
        "description": "Read external bill delay/cancel options, fees, and allowed status.",
        "inputSchema": _object(["case_id"], BUDGET_INPUT),
        "outputSchema": _object(["case_id", "options"], {"case_id": {"type": "string"}, "options": OPEN_ARRAY}),
    },
    "budget_simulate_cashflow": {
        "description": "Simulate daily cashflow from current balance, income, bills, planned manual payment dates, and selected user decisions. Minimum balance and savings goal come from the user request. Autopay bills are paid automatically and must not appear in payment_plan.",
        "inputSchema": _object(
            ["case_id", "payment_plan", "selected_option_ids", "minimum_balance"],
            {
                **BUDGET_INPUT,
                "payment_plan": {
                    "type": "array",
                    "items": _object(
                        ["bill_id", "pay_date"],
                        {"bill_id": {"type": "string"}, "pay_date": {"type": "string"}},
                    ),
                },
                "selected_option_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                "minimum_balance": {"type": "number"},
            },
        ),
        "outputSchema": _object(["case_id", "valid", "errors", "daily_balances", "lowest_balance", "evidence_token"], {"case_id": {"type": "string"}, "valid": {"type": "boolean"}, "errors": STRING_ARRAY, "daily_balances": OPEN_ARRAY, "lowest_balance": {"type": "number"}, "evidence_token": {"type": "string"}}),
    },
    "budget_schedule_manual_payments": {
        "description": "Schedule manual bill payments from a valid cashflow simulation. Pass evidence_tokens.budget_simulate_cashflow.",
        "inputSchema": _object(["case_id", "payment_plan", "evidence_tokens"], {**BUDGET_INPUT, "payment_plan": OPEN_ARRAY, "evidence_tokens": _token_input("budget_simulate_cashflow")}),
        "outputSchema": _object(["success", "error", "payment_status"], {"success": {"type": "boolean"}, "error": {"type": ["string", "null"]}, "payment_status": OPEN_ARRAY}),
    },
    "budget_apply_flex_options": {
        "description": "Apply allowed delay/cancel options from a valid cashflow simulation. Pass evidence_tokens.budget_simulate_cashflow.",
        "inputSchema": _object(["case_id", "selected_option_ids", "evidence_tokens"], {**BUDGET_INPUT, "selected_option_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True}, "evidence_tokens": _token_input("budget_simulate_cashflow")}),
        "outputSchema": _object(["success", "error", "option_status"], {"success": {"type": "boolean"}, "error": {"type": ["string", "null"]}, "option_status": OPEN_ARRAY}),
    },
    "budget_get_cashflow_status": {
        "description": "Refresh scheduled payments, applied options, and current cashflow status.",
        "inputSchema": _object(["case_id"], BUDGET_INPUT),
        "outputSchema": _object(["case_id", "payment_status", "option_status"], {"case_id": {"type": "string"}, "payment_status": OPEN_ARRAY, "option_status": OPEN_ARRAY}),
    },
    "claim_get_policy_coverage": {
        "description": "Read insurance policy coverage, deductible, limits, covered incident types, and exclusions.",
        "inputSchema": _object(["case_id"], CLAIM_INPUT),
        "outputSchema": _object(["case_id", "policy"], {"case_id": {"type": "string"}, "policy": OPEN_OBJECT}),
    },
    "claim_get_document_inventory": {
        "description": "Read available photos, invoices, receipts, and document metadata for the claim packet.",
        "inputSchema": _object(["case_id"], CLAIM_INPUT),
        "outputSchema": _object(["case_id", "documents"], {"case_id": {"type": "string"}, "documents": OPEN_ARRAY}),
    },
    "claim_get_vendor_estimates": {
        "description": "Read itemized vendor estimates and whether each scope is policy-covered or excluded.",
        "inputSchema": _object(["case_id"], CLAIM_INPUT),
        "outputSchema": _object(["case_id", "estimates", "items"], {"case_id": {"type": "string"}, "estimates": OPEN_ARRAY, "items": OPEN_ARRAY}),
    },
    "claim_get_submission_requirements": {
        "description": "Read insurer submission requirements. These are external packet requirements, not user preferences.",
        "inputSchema": _object(["case_id"], CLAIM_INPUT),
        "outputSchema": _object(["case_id", "requirements"], {"case_id": {"type": "string"}, "requirements": OPEN_ARRAY}),
    },
    "claim_validate_packet": {
        "description": "Validate a proposed claim packet against policy coverage, required documents, selected estimates/items, user-provided priority room, and user-provided max out-of-pocket tolerance.",
        "inputSchema": _object(
            ["case_id", "incident_type", "selected_document_ids", "selected_estimate_ids", "claimed_item_ids", "priority_room", "max_out_of_pocket"],
            {
                **CLAIM_INPUT,
                "incident_type": {"type": "string"},
                "selected_document_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True},
                "selected_estimate_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True},
                "claimed_item_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                "priority_room": {"type": "string"},
                "max_out_of_pocket": {"type": "number"},
            },
        ),
        "outputSchema": _object(["case_id", "valid", "errors", "missing_requirements", "payable_estimate", "out_of_pocket", "evidence_token"], {"case_id": {"type": "string"}, "valid": {"type": "boolean"}, "errors": STRING_ARRAY, "missing_requirements": STRING_ARRAY, "payable_estimate": {"type": "number"}, "out_of_pocket": {"type": "number"}, "evidence_token": {"type": "string"}}),
    },
    "claim_create_submission_draft": {
        "description": "Create a claim submission draft from a valid packet. Pass evidence_tokens.claim_validate_packet and refresh status before claiming submission.",
        "inputSchema": _object(["case_id", "selected_document_ids", "selected_estimate_ids", "claimed_item_ids", "evidence_tokens"], {**CLAIM_INPUT, "selected_document_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True}, "selected_estimate_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True}, "claimed_item_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True}, "evidence_tokens": _token_input("claim_validate_packet")}),
        "outputSchema": _object(["success", "error", "draft", "evidence_token"], {"success": {"type": "boolean"}, "error": {"type": ["string", "null"]}, "draft": {"anyOf": [OPEN_OBJECT, {"type": "null"}]}, "evidence_token": {"type": ["string", "null"]}}),
    },
    "claim_submit_packet": {
        "description": "Submit the current claim draft. Pass evidence_tokens.claim_validate_packet and evidence_tokens.claim_create_submission_draft, then refresh claim status.",
        "inputSchema": _object(["case_id", "draft_id", "evidence_tokens"], {**CLAIM_INPUT, "draft_id": {"type": "string"}, "evidence_tokens": _token_input("claim_validate_packet", "claim_create_submission_draft")}),
        "outputSchema": _object(["success", "error", "claim_status"], {"success": {"type": "boolean"}, "error": {"type": ["string", "null"]}, "claim_status": OPEN_OBJECT}),
    },
    "claim_get_claim_status": {
        "description": "Refresh submitted claim status, current draft, packet evidence, and insurer review state.",
        "inputSchema": _object(["case_id"], CLAIM_INPUT),
        "outputSchema": _object(["case_id", "draft", "claim_status"], {"case_id": {"type": "string"}, "draft": {"anyOf": [OPEN_OBJECT, {"type": "null"}]}, "claim_status": OPEN_OBJECT}),
    },
}


def _state(state: JsonDict) -> JsonDict:
    if "daily_life_planning_cases_v1" not in state:
        state["daily_life_planning_cases_v1"] = {
            "evidence": {},
            "exam_holds": [],
            "exam_saved_plan": None,
            "budget_payment_status": [],
            "budget_option_status": [],
            "claim_draft": None,
            "claim_status": {"status": "not_submitted"},
            "counter": 0,
        }
    return state["daily_life_planning_cases_v1"]


def _token(current: JsonDict, action: str) -> str:
    current["counter"] += 1
    token = f"planning-life-ev::{action}::{current['counter']}"
    current["evidence"][action] = token
    return token


def _check(args: JsonDict, expected: str) -> None:
    if args.get("case_id") != expected:
        raise ValueError(f"case_id_mismatch: expected {expected}")


def _overlap(left_start: str, left_end: str, right_start: str, right_end: str) -> bool:
    return left_start < right_end and left_end > right_start


def _date(value: str) -> str:
    return value.split("T")[0]


def invoke(*, tool: ToolDefinition, args: JsonDict, state: JsonDict) -> JsonDict:
    current = _state(state)
    name = tool.name

    if name.startswith("exam_"):
        _check(args, EXAM_CASE_ID)
        if name == "exam_get_exam_calendar":
            return {"case_id": EXAM_CASE_ID, "exams": copy.deepcopy(EXAMS)}
        if name == "exam_get_deadlines":
            return {"case_id": EXAM_CASE_ID, "deadlines": copy.deepcopy(DEADLINES)}
        if name == "exam_get_library_seat_availability":
            return {"case_id": EXAM_CASE_ID, "seats": copy.deepcopy(LIBRARY_SEATS)}
        if name == "exam_get_review_sessions":
            return {"case_id": EXAM_CASE_ID, "sessions": copy.deepcopy(REVIEW_SESSIONS)}
        if name == "exam_validate_study_schedule":
            blocks = args["blocks"]
            required = set(args["required_course_ids"])
            errors: list[str] = []
            covered = {block.get("course_id") for block in blocks if block.get("kind") in {"study", "review", "assignment"}}
            if not required.issubset(covered):
                errors.append("missing_required_course_coverage")
            seat_by_id = {seat["seat_id"]: seat for seat in LIBRARY_SEATS}
            intervals: list[tuple[datetime, datetime]] = []
            for block in blocks:
                start = block.get("start")
                end = block.get("end")
                if not isinstance(start, str) or not isinstance(end, str):
                    errors.append("bad_block_time")
                    continue
                try:
                    start_time, end_time = datetime.fromisoformat(start), datetime.fromisoformat(end)
                except ValueError:
                    errors.append("bad_block_time")
                    continue
                if start_time.tzinfo is not None or end_time.tzinfo is not None:
                    errors.append("block_times_must_be_local")
                    continue
                if end_time <= start_time:
                    errors.append("nonpositive_block_duration")
                if (end_time - start_time).total_seconds() / 60 > args["max_block_minutes"]:
                    errors.append(f"block_too_long:{block.get('label', block.get('course_id', 'unknown'))}")
                cutoff = datetime.combine(start_time.date(), time()) + timedelta(hours=args["no_study_after_hour"])
                if end_time > cutoff:
                    errors.append("study_after_user_limit")
                if any(start_time < prior_end and end_time > prior_start for prior_start, prior_end in intervals):
                    errors.append("overlapping_study_blocks")
                intervals.append((start_time, end_time))
                for exam in EXAMS:
                    if _overlap(start, end, exam["exam_time"], exam["exam_end"]):
                        errors.append(f"overlaps_exam:{exam['course_id']}")
                for deadline in DEADLINES:
                    if block.get("kind") == "assignment" and block.get("item_id") == deadline["item_id"] and end > deadline["due_time"]:
                        errors.append(f"assignment_after_deadline:{deadline['item_id']}")
                seat_id = block.get("seat_id")
                if seat_id:
                    seat = seat_by_id.get(seat_id)
                    if not seat:
                        errors.append(f"unknown_seat:{seat_id}")
                    elif not seat["available"]:
                        errors.append(f"seat_unavailable:{seat_id}")
                    elif seat["zone"] != "quiet":
                        errors.append(f"seat_not_quiet:{seat_id}")
                    elif seat["start"] != start or seat["end"] != end:
                        errors.append(f"seat_time_mismatch:{seat_id}")
            coverage = [{"course_id": course_id, "covered": course_id in covered} for course_id in sorted(required)]
            current["exam_last_valid"] = not errors
            current["exam_validated_blocks"] = copy.deepcopy(blocks)
            current["evidence"].pop("exam_hold_library_seats", None)
            current["exam_valid_seat_ids"] = sorted(
                {
                    block.get("seat_id")
                    for block in blocks
                    if block.get("seat_id") and not errors
                }
            )
            return {"case_id": EXAM_CASE_ID, "valid": not errors, "errors": errors, "coverage": coverage, "evidence_token": _token(current, name)}
        if name == "exam_hold_library_seats":
            if args["evidence_tokens"].get("exam_validate_study_schedule") != current["evidence"].get("exam_validate_study_schedule") or not current.get("exam_last_valid"):
                return {"success": False, "error": "missing_or_invalid_schedule_validation", "holds": copy.deepcopy(current["exam_holds"]), "evidence_token": None}
            valid_seats = set(current.get("exam_valid_seat_ids", []))
            requested = set(args["seat_ids"])
            if requested != valid_seats:
                return {"success": False, "error": "seat_hold_scope_mismatch", "holds": copy.deepcopy(current["exam_holds"]), "evidence_token": None}
            seat_by_id = {seat["seat_id"]: seat for seat in LIBRARY_SEATS}
            holds = [{"seat_id": seat_id, "status": "held", "zone": seat_by_id[seat_id]["zone"]} for seat_id in args["seat_ids"]]
            current["exam_holds"] = holds
            evidence_token = _token(current, name)
            return {"success": True, "error": None, "holds": copy.deepcopy(holds), "evidence_token": evidence_token}
        if name == "exam_save_study_plan":
            if args["evidence_tokens"].get("exam_validate_study_schedule") != current["evidence"].get("exam_validate_study_schedule") or not current.get("exam_last_valid"):
                return {"success": False, "error": "missing_or_invalid_schedule_validation", "saved_plan": current["exam_saved_plan"]}
            if args["evidence_tokens"].get("exam_hold_library_seats") != current["evidence"].get("exam_hold_library_seats"):
                return {"success": False, "error": "missing_or_invalid_seat_hold_evidence", "saved_plan": current["exam_saved_plan"]}
            if args["blocks"] != current["exam_validated_blocks"]:
                return {"success": False, "error": "study_plan_changed_after_validation", "saved_plan": copy.deepcopy(current["exam_saved_plan"])}
            current["exam_saved_plan"] = {"status": "saved", "blocks": copy.deepcopy(args["blocks"])}
            return {"success": True, "error": None, "saved_plan": copy.deepcopy(current["exam_saved_plan"])}
        if name == "exam_get_study_plan_status":
            return {"case_id": EXAM_CASE_ID, "saved_plan": copy.deepcopy(current["exam_saved_plan"]), "holds": copy.deepcopy(current["exam_holds"])}

    if name.startswith("budget_"):
        _check(args, BUDGET_CASE_ID)
        if name == "budget_get_current_balance":
            return {"case_id": BUDGET_CASE_ID, "balance": copy.deepcopy(BUDGET_BALANCE)}
        if name == "budget_get_income_schedule":
            return {"case_id": BUDGET_CASE_ID, "income": copy.deepcopy(BUDGET_INCOME)}
        if name == "budget_get_bills_and_autopay":
            return {"case_id": BUDGET_CASE_ID, "bills": copy.deepcopy(BUDGET_BILLS)}
        if name == "budget_get_flex_options":
            return {"case_id": BUDGET_CASE_ID, "options": copy.deepcopy(BUDGET_OPTIONS)}
        if name == "budget_simulate_cashflow":
            bill_by_id = {bill["bill_id"]: bill for bill in BUDGET_BILLS}
            option_by_id = {option["action_id"]: option for option in BUDGET_OPTIONS}
            payment_plan = list(args["payment_plan"])
            planned_bill_ids = [payment["bill_id"] for payment in payment_plan]
            manual = set(planned_bill_ids)
            options = set(args["selected_option_ids"])
            errors: list[str] = []
            if len(manual) != len(planned_bill_ids):
                errors.append("duplicate_manual_payment")
            for bill_id in manual:
                bill = bill_by_id.get(bill_id)
                if not bill:
                    errors.append(f"unknown_bill:{bill_id}")
                elif bill["autopay"]:
                    errors.append(f"manual_duplicate_autopay:{bill_id}")
            for option_id in options:
                option = option_by_id.get(option_id)
                if not option:
                    errors.append(f"unknown_option:{option_id}")
                elif not option["allowed"]:
                    errors.append(f"option_not_allowed:{option_id}")
            for bill in BUDGET_BILLS:
                canceled = any(
                    option.get("bill_id") == bill["bill_id"] and option["action_id"] in options and "cancel" in option["action_id"]
                    for option in BUDGET_OPTIONS
                )
                if not bill["autopay"] and not canceled and bill["bill_id"] not in manual:
                    errors.append(f"missing_manual_payment:{bill['bill_id']}")
            for option in BUDGET_OPTIONS:
                if option["action_id"] in options and option.get("new_date"):
                    planned_date = next(
                        (payment["pay_date"] for payment in payment_plan if payment["bill_id"] == option["bill_id"]),
                        None,
                    )
                    if planned_date != option["new_date"]:
                        errors.append(f"delay_payment_date_mismatch:{option['bill_id']}")
            balance = float(BUDGET_BALANCE["checking"])
            daily = []
            payment_by_bill = {payment["bill_id"]: payment["pay_date"] for payment in payment_plan}
            for day in ["2026-05-20", "2026-05-21", "2026-05-22", "2026-05-23", "2026-05-24", "2026-05-25"]:
                for bill in BUDGET_BILLS:
                    delayed = next((option for option in BUDGET_OPTIONS if option.get("bill_id") == bill["bill_id"] and option["action_id"] in options and option.get("new_date")), None)
                    canceled = any(option.get("bill_id") == bill["bill_id"] and option["action_id"] in options and "cancel" in option["action_id"] for option in BUDGET_OPTIONS)
                    bill_date = delayed["new_date"] if delayed else payment_by_bill.get(bill["bill_id"], bill["date"])
                    if canceled:
                        continue
                    if bill_date == day and (bill["autopay"] or bill["bill_id"] in manual):
                        balance -= float(bill["amount"])
                        if delayed:
                            balance -= float(delayed["fee"])
                for income in BUDGET_INCOME:
                    if income["date"] == day:
                        balance += float(income["amount"])
                daily.append({"date": day, "balance": round(balance, 2)})
            lowest = min(item["balance"] for item in daily)
            if lowest < float(args["minimum_balance"]):
                errors.append("below_minimum_balance")
            current["budget_last_valid"] = not errors
            current["budget_payment_plan"] = copy.deepcopy(payment_plan)
            current["budget_options"] = list(options)
            return {"case_id": BUDGET_CASE_ID, "valid": not errors, "errors": errors, "daily_balances": daily, "lowest_balance": lowest, "evidence_token": _token(current, name)}
        if name == "budget_schedule_manual_payments":
            if args["evidence_tokens"].get("budget_simulate_cashflow") != current["evidence"].get("budget_simulate_cashflow") or not current.get("budget_last_valid"):
                return {"success": False, "error": "missing_or_invalid_cashflow_simulation", "payment_status": copy.deepcopy(current["budget_payment_status"])}
            if args["payment_plan"] != current.get("budget_payment_plan"):
                return {"success": False, "error": "payment_plan_changed_after_simulation", "payment_status": copy.deepcopy(current["budget_payment_status"])}
            current["budget_payment_status"] = [
                {"bill_id": payment["bill_id"], "pay_date": payment["pay_date"], "status": "scheduled"}
                for payment in args["payment_plan"]
            ]
            return {"success": True, "error": None, "payment_status": copy.deepcopy(current["budget_payment_status"])}
        if name == "budget_apply_flex_options":
            if args["evidence_tokens"].get("budget_simulate_cashflow") != current["evidence"].get("budget_simulate_cashflow") or not current.get("budget_last_valid"):
                return {"success": False, "error": "missing_or_invalid_cashflow_simulation", "option_status": copy.deepcopy(current["budget_option_status"])}
            if sorted(args["selected_option_ids"]) != sorted(current["budget_options"]):
                return {"success": False, "error": "options_changed_after_simulation", "option_status": copy.deepcopy(current["budget_option_status"])}
            current["budget_option_status"] = [{"action_id": option_id, "status": "applied"} for option_id in args["selected_option_ids"]]
            return {"success": True, "error": None, "option_status": copy.deepcopy(current["budget_option_status"])}
        if name == "budget_get_cashflow_status":
            return {"case_id": BUDGET_CASE_ID, "payment_status": copy.deepcopy(current["budget_payment_status"]), "option_status": copy.deepcopy(current["budget_option_status"])}

    if name.startswith("claim_"):
        _check(args, CLAIM_CASE_ID)
        if name == "claim_get_policy_coverage":
            return {"case_id": CLAIM_CASE_ID, "policy": copy.deepcopy(CLAIM_POLICY)}
        if name == "claim_get_document_inventory":
            return {"case_id": CLAIM_CASE_ID, "documents": copy.deepcopy(CLAIM_DOCUMENTS)}
        if name == "claim_get_vendor_estimates":
            return {
                "case_id": CLAIM_CASE_ID,
                "estimates": copy.deepcopy(CLAIM_ESTIMATES),
                "items": copy.deepcopy(CLAIM_ITEMS),
            }
        if name == "claim_get_submission_requirements":
            return {"case_id": CLAIM_CASE_ID, "requirements": copy.deepcopy(CLAIM_REQUIREMENTS)}
        if name == "claim_validate_packet":
            document_by_id = {document["document_id"]: document for document in CLAIM_DOCUMENTS}
            estimate_by_id = {estimate["estimate_id"]: estimate for estimate in CLAIM_ESTIMATES}
            item_by_id = {item["item_id"]: item for item in CLAIM_ITEMS}
            selected_documents = [document_by_id.get(document_id) for document_id in args["selected_document_ids"]]
            selected_estimates = [estimate_by_id.get(estimate_id) for estimate_id in args["selected_estimate_ids"]]
            claimed_items = [item_by_id.get(item_id) for item_id in args["claimed_item_ids"]]
            errors: list[str] = []
            missing: list[str] = []
            if args["incident_type"] not in CLAIM_POLICY["covered_incident_types"]:
                errors.append(f"incident_not_covered:{args['incident_type']}")
            for document_id, document in zip(args["selected_document_ids"], selected_documents, strict=True):
                if document is None:
                    errors.append(f"unknown_document:{document_id}")
            for estimate_id, estimate in zip(args["selected_estimate_ids"], selected_estimates, strict=True):
                if estimate is None:
                    errors.append(f"unknown_estimate:{estimate_id}")
            for item_id, item in zip(args["claimed_item_ids"], claimed_items, strict=True):
                if item is None:
                    errors.append(f"unknown_item:{item_id}")
            doc_types = {document["doc_type"] for document in selected_documents if document}
            if any(estimate for estimate in selected_estimates):
                doc_types.add("itemized_estimate")
            for requirement in CLAIM_REQUIREMENTS:
                if requirement["required"] and requirement["requirement_id"] not in doc_types:
                    missing.append(requirement["requirement_id"])
            priority_photo = any(
                document
                and document["doc_type"] == "incident_photo"
                and document.get("room") == args["priority_room"]
                for document in selected_documents
            )
            if not priority_photo:
                errors.append(f"missing_priority_room_photo:{args['priority_room']}")
            covered_estimate_total = 0.0
            uncovered_total = 0.0
            for estimate in selected_estimates:
                if not estimate:
                    continue
                if estimate["covered"]:
                    covered_estimate_total += float(estimate["amount"])
                else:
                    uncovered_total += float(estimate["amount"])
                    errors.append(f"estimate_contains_excluded_scope:{estimate['estimate_id']}")
            covered_item_total = 0.0
            for item in claimed_items:
                if not item:
                    continue
                if item["covered"]:
                    covered_item_total += float(item["amount"])
                else:
                    uncovered_total += float(item["amount"])
                    errors.append(f"item_not_covered:{item['item_id']}")
            payable_before_deductible = covered_estimate_total + covered_item_total
            payable = max(0.0, payable_before_deductible - float(CLAIM_POLICY["deductible"]))
            out_of_pocket = float(CLAIM_POLICY["deductible"]) + uncovered_total
            if out_of_pocket > float(args["max_out_of_pocket"]):
                errors.append("out_of_pocket_above_user_limit")
            errors.extend(f"missing_required_document:{requirement}" for requirement in missing)
            current["claim_last_valid"] = not errors
            current["claim_packet"] = {
                "selected_document_ids": list(args["selected_document_ids"]),
                "selected_estimate_ids": list(args["selected_estimate_ids"]),
                "claimed_item_ids": list(args["claimed_item_ids"]),
                "payable_estimate": round(payable, 2),
                "out_of_pocket": round(out_of_pocket, 2),
            }
            return {
                "case_id": CLAIM_CASE_ID,
                "valid": not errors,
                "errors": errors,
                "missing_requirements": missing,
                "payable_estimate": round(payable, 2),
                "out_of_pocket": round(out_of_pocket, 2),
                "evidence_token": _token(current, name),
            }
        if name == "claim_create_submission_draft":
            if args["evidence_tokens"].get("claim_validate_packet") != current["evidence"].get("claim_validate_packet") or not current.get("claim_last_valid"):
                return {"success": False, "error": "missing_or_invalid_claim_validation", "draft": current["claim_draft"], "evidence_token": None}
            expected = current.get("claim_packet", {})
            if (
                args["selected_document_ids"] != expected.get("selected_document_ids")
                or args["selected_estimate_ids"] != expected.get("selected_estimate_ids")
                or args["claimed_item_ids"] != expected.get("claimed_item_ids")
            ):
                return {"success": False, "error": "packet_changed_after_validation", "draft": current["claim_draft"], "evidence_token": None}
            draft = {
                "draft_id": "claim-draft-506",
                "status": "draft_ready",
                **copy.deepcopy(expected),
            }
            current["claim_draft"] = draft
            evidence_token = _token(current, name)
            return {"success": True, "error": None, "draft": copy.deepcopy(draft), "evidence_token": evidence_token}
        if name == "claim_submit_packet":
            if args["evidence_tokens"].get("claim_validate_packet") != current["evidence"].get("claim_validate_packet") or not current.get("claim_last_valid"):
                return {"success": False, "error": "missing_or_invalid_claim_validation", "claim_status": copy.deepcopy(current["claim_status"])}
            if args["evidence_tokens"].get("claim_create_submission_draft") != current["evidence"].get("claim_create_submission_draft"):
                return {"success": False, "error": "missing_or_invalid_draft_evidence", "claim_status": copy.deepcopy(current["claim_status"])}
            if not current["claim_draft"] or args["draft_id"] != current["claim_draft"]["draft_id"]:
                return {"success": False, "error": "draft_id_mismatch", "claim_status": copy.deepcopy(current["claim_status"])}
            current["claim_status"] = {
                "status": "submitted",
                "claim_id": "CLM-506-WATER",
                "review_state": "adjuster_review",
                "payable_estimate": current["claim_draft"]["payable_estimate"],
            }
            return {"success": True, "error": None, "claim_status": copy.deepcopy(current["claim_status"])}
        if name == "claim_get_claim_status":
            return {
                "case_id": CLAIM_CASE_ID,
                "draft": copy.deepcopy(current["claim_draft"]),
                "claim_status": copy.deepcopy(current["claim_status"]),
            }

    raise ValueError(f"Unknown daily-life planning tool: {name}")
