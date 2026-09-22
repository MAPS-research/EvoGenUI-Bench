from __future__ import annotations

import copy

from runtime.types import JsonDict, ToolDefinition

CASE_ID = "fall_schedule_case_216"


def _object(required: list[str], properties: dict) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


STRING_ARRAY = {"type": "array", "items": {"type": "string"}}
COMMON_INPUT = {"case_id": {"type": "string", "enum": [CASE_ID]}}

DEGREE_AUDIT = {
    "student": "Maya Chen",
    "remaining_requirements": [
        {"requirement_id": "systems_core", "label": "Systems core", "needed": 1},
        {"requirement_id": "stats_elective", "label": "Statistics elective", "needed": 1},
        {"requirement_id": "writing_intensive", "label": "Writing intensive", "needed": 1},
    ],
}
SECTIONS = [
    {"section_id": "CS341-A", "course": "CS341 Operating Systems", "meets": ["Mon 10:00", "Wed 10:00"], "requirement": "systems_core", "seats": 2, "prereq": "CS240", "status": "open"},
    {"section_id": "CS341-B", "course": "CS341 Operating Systems", "meets": ["Tue 18:00", "Thu 18:00"], "requirement": "systems_core", "seats": 0, "prereq": "CS240", "status": "waitlist"},
    {"section_id": "STAT310-A", "course": "STAT310 Applied Regression", "meets": ["Mon 10:00", "Wed 10:00"], "requirement": "stats_elective", "seats": 4, "prereq": "STAT210", "status": "open"},
    {"section_id": "STAT330-B", "course": "STAT330 Bayesian Modeling", "meets": ["Tue 14:00", "Thu 14:00"], "requirement": "stats_elective", "seats": 1, "prereq": "STAT210", "status": "open"},
    {"section_id": "HUM220-A", "course": "HUM220 Technical Writing", "meets": ["Fri 09:00"], "requirement": "writing_intensive", "seats": 3, "prereq": None, "status": "open"},
    {"section_id": "BIO250-A", "course": "BIO250 Lab Methods", "meets": ["Tue 14:00", "Thu 14:00"], "requirement": "lab_science", "seats": 6, "prereq": "BIO101", "status": "open"},
]
COMPLETED = ["CS240", "STAT210", "BIO101"]
WORK_SCHEDULE = ["Tue 18:00", "Thu 18:00", "Fri 13:00"]

TOOL_SCHEMAS: dict[str, dict] = {
    "course_get_degree_audit": {
        "description": "Read remaining degree requirements. Requirement coverage must be computed from selected sections, not guessed from course titles.",
        "inputSchema": _object(["case_id"], COMMON_INPUT),
        "outputSchema": _object(["case_id", "student", "remaining_requirements"], {"case_id": {"type": "string"}, "student": {"type": "string"}, "remaining_requirements": {"type": "array", "items": {"type": "object", "additionalProperties": True}}}),
    },
    "course_search_sections": {
        "description": "Search available sections with meeting times, seats, prerequisites, and requirement tags.",
        "inputSchema": _object(["case_id"], COMMON_INPUT),
        "outputSchema": _object(["case_id", "sections"], {"case_id": {"type": "string"}, "sections": {"type": "array", "items": {"type": "object", "additionalProperties": True}}}),
    },
    "course_get_work_schedule": {
        "description": "Read fixed work commitments that should not conflict with selected sections.",
        "inputSchema": _object(["case_id"], COMMON_INPUT),
        "outputSchema": _object(["case_id", "busy_times"], {"case_id": {"type": "string"}, "busy_times": STRING_ARRAY}),
    },
    "course_check_prerequisites": {
        "description": "Check prerequisites for selected section ids. Returns an evidence_token for schedule validation.",
        "inputSchema": _object(["case_id", "section_ids"], {**COMMON_INPUT, "section_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True}}),
        "outputSchema": _object(["case_id", "passed", "missing", "evidence_token"], {"case_id": {"type": "string"}, "passed": {"type": "boolean"}, "missing": {"type": "array", "items": {"type": "object", "additionalProperties": True}}, "evidence_token": {"type": "string"}}),
    },
    "course_validate_schedule": {
        "description": "Validate selected sections against degree requirements, seat status, section conflicts, and work schedule. Pass evidence_tokens.course_check_prerequisites.",
        "inputSchema": _object(["case_id", "section_ids", "evidence_tokens"], {**COMMON_INPUT, "section_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True}, "evidence_tokens": _object(["course_check_prerequisites"], {"course_check_prerequisites": {"type": "string"}})}),
        "outputSchema": _object(["case_id", "valid", "coverage", "conflicts", "evidence_token"], {"case_id": {"type": "string"}, "valid": {"type": "boolean"}, "coverage": {"type": "array", "items": {"type": "object", "additionalProperties": True}}, "conflicts": STRING_ARRAY, "evidence_token": {"type": "string"}}),
    },
    "course_get_advisor_hint": {
        "description": "Read a non-authoritative advisor note. It may reference stale seat counts and cannot replace current section validation.",
        "inputSchema": _object(["case_id"], COMMON_INPUT),
        "outputSchema": _object(["case_id", "authoritative", "hint"], {"case_id": {"type": "string"}, "authoritative": {"type": "boolean", "const": False}, "hint": {"type": "string"}}),
    },
    "course_create_enrollment_cart": {
        "description": "Create a non-submitted enrollment cart. Pass current prerequisite and schedule-validation evidence tokens for the same selected section ids.",
        "inputSchema": _object(["case_id", "section_ids", "evidence_tokens"], {**COMMON_INPUT, "section_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True}, "evidence_tokens": _object(["course_check_prerequisites", "course_validate_schedule"], {"course_check_prerequisites": {"type": "string"}, "course_validate_schedule": {"type": "string"}})}),
        "outputSchema": _object(["success", "error", "cart"], {"success": {"type": "boolean"}, "error": {"type": ["string", "null"]}, "cart": {"anyOf": [{"type": "object", "additionalProperties": True}, {"type": "null"}]}}),
    },
    "course_submit_enrollment_cart": {
        "description": "Submit the staged enrollment cart. Pass prerequisite, validation, and cart evidence tokens, then refresh enrollment status.",
        "inputSchema": _object(["case_id", "cart_id", "evidence_tokens"], {**COMMON_INPUT, "cart_id": {"type": "string"}, "evidence_tokens": _object(["course_check_prerequisites", "course_validate_schedule", "course_create_enrollment_cart"], {"course_check_prerequisites": {"type": "string"}, "course_validate_schedule": {"type": "string"}, "course_create_enrollment_cart": {"type": "string"}})}),
        "outputSchema": _object(["success", "error", "operation", "enrollment_status"], {"success": {"type": "boolean"}, "error": {"type": ["string", "null"]}, "operation": {"type": "object", "additionalProperties": True}, "enrollment_status": {"type": "array", "items": {"type": "object", "additionalProperties": True}}}),
    },
    "course_get_enrollment_status": {
        "description": "Refresh enrolled/waitlisted status for submitted sections.",
        "inputSchema": _object(["case_id"], COMMON_INPUT),
        "outputSchema": _object(["case_id", "enrollment_status"], {"case_id": {"type": "string"}, "enrollment_status": {"type": "array", "items": {"type": "object", "additionalProperties": True}}}),
    },
}


def _state(state: dict) -> dict:
    if "course_schedule_planner_v1" not in state:
        state["course_schedule_planner_v1"] = {"evidence": {}, "carts": {}, "enrollment_status": [], "counter": 0}
    return state["course_schedule_planner_v1"]


def _check(args: dict) -> None:
    if args.get("case_id") != CASE_ID:
        raise ValueError(f"case_id_mismatch: expected {CASE_ID}")


def _token(current: dict, action: str) -> str:
    current["counter"] += 1
    token = f"course-ev::{action}::{current['counter']}"
    current["evidence"][action] = token
    return token


def _sections(section_ids: list[str]) -> list[dict]:
    by_id = {section["section_id"]: section for section in SECTIONS}
    return [by_id[section_id] for section_id in section_ids if section_id in by_id]


def invoke(*, tool: ToolDefinition, args: JsonDict, state: JsonDict) -> JsonDict:
    _check(args)
    current = _state(state)
    name = tool.name
    if name == "course_get_degree_audit":
        return {"case_id": CASE_ID, **copy.deepcopy(DEGREE_AUDIT)}
    if name == "course_search_sections":
        return {"case_id": CASE_ID, "sections": copy.deepcopy(SECTIONS)}
    if name == "course_get_work_schedule":
        return {"case_id": CASE_ID, "busy_times": copy.deepcopy(WORK_SCHEDULE)}
    if name == "course_check_prerequisites":
        missing = []
        for section in _sections(list(args["section_ids"])):
            prereq = section.get("prereq")
            if prereq and prereq not in COMPLETED:
                missing.append({"section_id": section["section_id"], "missing": prereq})
        token = _token(current, "course_check_prerequisites")
        return {"case_id": CASE_ID, "passed": not missing, "missing": missing, "evidence_token": token}
    if name == "course_validate_schedule":
        if args["evidence_tokens"].get("course_check_prerequisites") != current["evidence"].get("course_check_prerequisites"):
            raise ValueError("missing_or_invalid_prerequisite_evidence")
        selected = _sections(list(args["section_ids"]))
        conflicts = []
        seen_times: dict[str, str] = {}
        for section in selected:
            if section["status"] != "open":
                conflicts.append(f"not_open:{section['section_id']}")
            for meeting in section["meets"]:
                if meeting in WORK_SCHEDULE:
                    conflicts.append(f"work_conflict:{section['section_id']}:{meeting}")
                if meeting in seen_times:
                    conflicts.append(f"section_conflict:{seen_times[meeting]}:{section['section_id']}:{meeting}")
                seen_times[meeting] = section["section_id"]
        covered = {section["requirement"] for section in selected}
        coverage = [
            {"requirement_id": item["requirement_id"], "covered": item["requirement_id"] in covered}
            for item in DEGREE_AUDIT["remaining_requirements"]
        ]
        if not all(item["covered"] for item in coverage):
            conflicts.append("missing_required_coverage")
        token = _token(current, "course_validate_schedule")
        current["last_valid"] = not conflicts
        return {"case_id": CASE_ID, "valid": not conflicts, "coverage": coverage, "conflicts": conflicts, "evidence_token": token}
    if name == "course_get_advisor_hint":
        return {"case_id": CASE_ID, "authoritative": False, "hint": "Older advisor note suggested CS341-B, but that section now conflicts with work and is waitlisted."}
    if name == "course_create_enrollment_cart":
        tokens = args["evidence_tokens"]
        if tokens.get("course_check_prerequisites") != current["evidence"].get("course_check_prerequisites"):
            return {"success": False, "error": "missing_or_invalid_prerequisite_evidence", "cart": None}
        if tokens.get("course_validate_schedule") != current["evidence"].get("course_validate_schedule"):
            return {"success": False, "error": "missing_or_invalid_schedule_evidence", "cart": None}
        if not current.get("last_valid"):
            return {"success": False, "error": "schedule_has_conflicts", "cart": None}
        token = _token(current, "course_create_enrollment_cart")
        cart_id = f"enroll-cart-{current['counter']}"
        cart = {"cart_id": cart_id, "section_ids": list(args["section_ids"]), "evidence_token": token}
        current["carts"][cart_id] = cart
        return {"success": True, "error": None, "cart": cart}
    if name == "course_submit_enrollment_cart":
        tokens = args["evidence_tokens"]
        if tokens.get("course_check_prerequisites") != current["evidence"].get("course_check_prerequisites"):
            return {"success": False, "error": "missing_or_invalid_prerequisite_evidence", "operation": {"status": "rejected"}, "enrollment_status": copy.deepcopy(current["enrollment_status"])}
        if tokens.get("course_validate_schedule") != current["evidence"].get("course_validate_schedule"):
            return {"success": False, "error": "missing_or_invalid_schedule_evidence", "operation": {"status": "rejected"}, "enrollment_status": copy.deepcopy(current["enrollment_status"])}
        if tokens.get("course_create_enrollment_cart") != current["evidence"].get("course_create_enrollment_cart"):
            return {"success": False, "error": "missing_or_invalid_cart_evidence", "operation": {"status": "rejected"}, "enrollment_status": copy.deepcopy(current["enrollment_status"])}
        cart = current["carts"].get(args["cart_id"])
        if not cart:
            return {"success": False, "error": "unknown_cart_id", "operation": {"status": "rejected"}, "enrollment_status": copy.deepcopy(current["enrollment_status"])}
        current["enrollment_status"] = [{"section_id": section_id, "status": "enrolled"} for section_id in cart["section_ids"]]
        return {"success": True, "error": None, "operation": {"status": "submitted", "authoritative": True}, "enrollment_status": copy.deepcopy(current["enrollment_status"])}
    if name == "course_get_enrollment_status":
        return {"case_id": CASE_ID, "enrollment_status": copy.deepcopy(current["enrollment_status"])}
    raise ValueError(f"Unknown course schedule planner tool: {name}")
