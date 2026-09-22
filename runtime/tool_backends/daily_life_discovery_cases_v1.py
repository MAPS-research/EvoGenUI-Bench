from __future__ import annotations

import copy

from runtime.types import JsonDict, ToolDefinition

LOST_CASE_ID = "lost_keys_case_619"
PLANT_CASE_ID = "plant_rescue_case_274"


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
LOST_INPUT = {"case_id": {"type": "string", "enum": [LOST_CASE_ID]}}
PLANT_INPUT = {"case_id": {"type": "string", "enum": [PLANT_CASE_ID]}}

LOST_TRACKER_EVENTS = [
    {
        "event_id": "tracker_entry_0812",
        "source": "bluetooth_tag",
        "time": "2026-05-18T08:12",
        "zone_id": "entry_table",
        "confidence": 0.86,
        "stale": False,
    },
    {
        "event_id": "tracker_rideshare_0854",
        "source": "bluetooth_tag",
        "time": "2026-05-18T08:54",
        "zone_id": "rideshare_backseat",
        "confidence": 0.34,
        "stale": True,
    },
]
LOST_HOME_EVENTS = [
    {"event_id": "speaker_entry_0811", "source": "smart_speaker", "time": "2026-05-18T08:11", "zone_id": "entry_table", "signal": "metallic_jingle"},
    {"event_id": "laundry_door_0820", "source": "door_sensor", "time": "2026-05-18T08:20", "zone_id": "laundry_room", "signal": "opened_before_departure"},
    {"event_id": "robot_vacuum_1035", "source": "robot_vacuum", "time": "2026-05-18T10:35", "zone_id": "under_sofa", "signal": "small_object_bump"},
]
LOST_DAY_TIMELINE = [
    {"event_id": "calendar_school_dropoff", "source": "calendar", "time": "2026-05-18T08:30", "location": "home_exit"},
    {"event_id": "ride_receipt_0841", "source": "rideshare_receipt", "time": "2026-05-18T08:41", "location": "pickup_home"},
    {"event_id": "office_badge_0916", "source": "badge_log", "time": "2026-05-18T09:16", "location": "office_lobby"},
]
LOST_PHOTO_METADATA = [
    {"event_id": "photo_entry_bowl_0810", "source": "photo_metadata", "time": "2026-05-18T08:10", "zone_id": "entry_table", "visible_object": "red_keychain"},
    {"event_id": "photo_kitchen_0818", "source": "photo_metadata", "time": "2026-05-18T08:18", "zone_id": "kitchen_counter", "visible_object": "wallet_only"},
]
LOST_ZONES = {
    "entry_table": {"label": "Entry table and key bowl", "minutes": 10, "evidence": {"tracker_entry_0812", "speaker_entry_0811", "photo_entry_bowl_0810"}},
    "laundry_room": {"label": "Laundry room shelf and hamper edge", "minutes": 12, "evidence": {"laundry_door_0820"}},
    "under_sofa": {"label": "Living room sofa gap", "minutes": 14, "evidence": {"robot_vacuum_1035"}},
    "rideshare_backseat": {"label": "Rideshare back seat", "minutes": 45, "evidence": {"tracker_rideshare_0854", "ride_receipt_0841"}},
    "office_lobby": {"label": "Office lobby front desk", "minutes": 22, "evidence": {"office_badge_0916"}},
}

PLANT_SENSOR_READINGS = [
    {"signal_id": "soil_moisture_high", "sensor": "soil_moisture", "value": 84, "unit": "percent", "time": "2026-05-19T07:30"},
    {"signal_id": "low_light_window", "sensor": "light", "value": 95, "unit": "lux", "time": "2026-05-18T12:00"},
    {"signal_id": "cool_night_temp", "sensor": "temperature", "value": 58, "unit": "fahrenheit", "time": "2026-05-18T23:00"},
]
PLANT_CARE_HISTORY = [
    {"signal_id": "watered_three_days", "action": "watered", "date": "2026-05-17"},
    {"signal_id": "watered_two_days", "action": "watered", "date": "2026-05-18"},
    {"signal_id": "fertilized_last_week", "action": "fertilized", "date": "2026-05-12"},
]
PLANT_SPECIES_PROFILE = {
    "species": "calathea_orbifolia",
    "common_name": "Calathea orbifolia",
    "preferred_light": "bright_indirect",
    "soil_rule": "moist_not_soggy",
    "avoid_actions": ["direct_sun", "fertilize_now", "water_today", "cold_repot"],
    "safe_actions": ["pause_watering", "move_to_bright_indirect", "check_drainage", "wipe_leaves"],
}
PLANT_WEATHER_WINDOW = [
    {"date": "2026-05-19", "night_low_f": 51, "outdoor_repot_risk": "cold_stress"},
    {"date": "2026-05-20", "night_low_f": 54, "outdoor_repot_risk": "cold_stress"},
    {"date": "2026-05-21", "night_low_f": 61, "outdoor_repot_risk": "lower"},
]

TOOL_SCHEMAS: dict[str, JsonDict] = {
    "lost_get_tracker_events": {
        "description": "Read Bluetooth tracker events with confidence and stale flags for the missing item.",
        "inputSchema": _object(["case_id"], LOST_INPUT),
        "outputSchema": _object(["case_id", "events"], {"case_id": {"type": "string"}, "events": OPEN_ARRAY}),
    },
    "lost_get_home_device_events": {
        "description": "Read smart-home event evidence such as speaker, door, and robot vacuum logs.",
        "inputSchema": _object(["case_id"], LOST_INPUT),
        "outputSchema": _object(["case_id", "events"], {"case_id": {"type": "string"}, "events": OPEN_ARRAY}),
    },
    "lost_get_day_location_timeline": {
        "description": "Read external day timeline evidence from calendar, ride receipt, and badge logs.",
        "inputSchema": _object(["case_id"], LOST_INPUT),
        "outputSchema": _object(["case_id", "timeline"], {"case_id": {"type": "string"}, "timeline": OPEN_ARRAY}),
    },
    "lost_get_photo_metadata": {
        "description": "Read photo metadata and visible-object hints for the missing item. This is evidence, not a guarantee.",
        "inputSchema": _object(["case_id"], LOST_INPUT),
        "outputSchema": _object(["case_id", "photos"], {"case_id": {"type": "string"}, "photos": OPEN_ARRAY}),
    },
    "lost_validate_search_plan": {
        "description": "Validate a proposed search plan against evidence support, stale low-confidence signals, user-provided time limit, and user-provided first-search zone.",
        "inputSchema": _object(
            ["case_id", "selected_zone_ids", "evidence_event_ids", "required_first_zone_id", "max_search_minutes"],
            {
                **LOST_INPUT,
                "selected_zone_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True},
                "evidence_event_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True},
                "required_first_zone_id": {"type": "string"},
                "max_search_minutes": {"type": "integer", "minimum": 1},
            },
        ),
        "outputSchema": _object(["case_id", "valid", "errors", "ranked_zones", "total_minutes", "evidence_token"], {"case_id": {"type": "string"}, "valid": {"type": "boolean"}, "errors": STRING_ARRAY, "ranked_zones": OPEN_ARRAY, "total_minutes": {"type": "integer"}, "evidence_token": {"type": "string"}}),
    },
    "lost_save_search_plan": {
        "description": "Save a validated missing-item search plan. Pass evidence_tokens.lost_validate_search_plan and refresh saved status before claiming completion.",
        "inputSchema": _object(["case_id", "selected_zone_ids", "evidence_tokens"], {**LOST_INPUT, "selected_zone_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True}, "evidence_tokens": _token_input("lost_validate_search_plan")}),
        "outputSchema": _object(["success", "error", "saved_plan"], {"success": {"type": "boolean"}, "error": {"type": ["string", "null"]}, "saved_plan": {"anyOf": [OPEN_OBJECT, {"type": "null"}]}}),
    },
    "lost_get_search_plan_status": {
        "description": "Refresh saved missing-item search plan status.",
        "inputSchema": _object(["case_id"], LOST_INPUT),
        "outputSchema": _object(["case_id", "saved_plan"], {"case_id": {"type": "string"}, "saved_plan": {"anyOf": [OPEN_OBJECT, {"type": "null"}]}}),
    },
    "plant_get_sensor_readings": {
        "description": "Read current plant sensor readings such as soil moisture, light, and temperature.",
        "inputSchema": _object(["case_id"], PLANT_INPUT),
        "outputSchema": _object(["case_id", "readings"], {"case_id": {"type": "string"}, "readings": OPEN_ARRAY}),
    },
    "plant_get_care_history": {
        "description": "Read recent care history such as watering and fertilizer events.",
        "inputSchema": _object(["case_id"], PLANT_INPUT),
        "outputSchema": _object(["case_id", "history"], {"case_id": {"type": "string"}, "history": OPEN_ARRAY}),
    },
    "plant_get_species_profile": {
        "description": "Read species-specific care rules, safe actions, and actions to avoid.",
        "inputSchema": _object(["case_id"], PLANT_INPUT),
        "outputSchema": _object(["case_id", "profile"], {"case_id": {"type": "string"}, "profile": OPEN_OBJECT}),
    },
    "plant_get_weather_window": {
        "description": "Read local weather window for outdoor or repotting risk. Household constraints come from the user request, not this tool.",
        "inputSchema": _object(["case_id"], PLANT_INPUT),
        "outputSchema": _object(["case_id", "weather"], {"case_id": {"type": "string"}, "weather": OPEN_ARRAY}),
    },
    "plant_validate_rescue_plan": {
        "description": "Validate a proposed plant rescue plan against sensor evidence, care history, species rules, weather risk, and user-provided safety constraints.",
        "inputSchema": _object(
            ["case_id", "action_ids", "evidence_signal_ids", "avoid_action_ids", "recheck_days", "pet_safe_only"],
            {
                **PLANT_INPUT,
                "action_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True},
                "evidence_signal_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True},
                "avoid_action_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                "recheck_days": {"type": "integer", "minimum": 1},
                "pet_safe_only": {"type": "boolean"},
            },
        ),
        "outputSchema": _object(["case_id", "valid", "errors", "diagnosis", "evidence_token"], {"case_id": {"type": "string"}, "valid": {"type": "boolean"}, "errors": STRING_ARRAY, "diagnosis": OPEN_ARRAY, "evidence_token": {"type": "string"}}),
    },
    "plant_save_rescue_plan": {
        "description": "Save a validated plant rescue plan. Pass evidence_tokens.plant_validate_rescue_plan and refresh saved status before claiming completion.",
        "inputSchema": _object(["case_id", "action_ids", "evidence_tokens"], {**PLANT_INPUT, "action_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True}, "evidence_tokens": _token_input("plant_validate_rescue_plan")}),
        "outputSchema": _object(["success", "error", "saved_plan"], {"success": {"type": "boolean"}, "error": {"type": ["string", "null"]}, "saved_plan": {"anyOf": [OPEN_OBJECT, {"type": "null"}]}}),
    },
    "plant_get_rescue_plan_status": {
        "description": "Refresh saved plant rescue plan status.",
        "inputSchema": _object(["case_id"], PLANT_INPUT),
        "outputSchema": _object(["case_id", "saved_plan"], {"case_id": {"type": "string"}, "saved_plan": {"anyOf": [OPEN_OBJECT, {"type": "null"}]}}),
    },
}


def _state(state: JsonDict) -> JsonDict:
    if "daily_life_discovery_cases_v1" not in state:
        state["daily_life_discovery_cases_v1"] = {
            "evidence": {},
            "lost_last_valid": False,
            "lost_saved_plan": None,
            "plant_last_valid": False,
            "plant_saved_plan": None,
            "counter": 0,
        }
    return state["daily_life_discovery_cases_v1"]


def _token(current: JsonDict, action: str) -> str:
    current["counter"] += 1
    token = f"discovery-life-ev::{action}::{current['counter']}"
    current["evidence"][action] = token
    return token


def _check(args: JsonDict, expected: str) -> None:
    if args.get("case_id") != expected:
        raise ValueError(f"case_id_mismatch: expected {expected}")


def invoke(*, tool: ToolDefinition, args: JsonDict, state: JsonDict) -> JsonDict:
    current = _state(state)
    name = tool.name

    if name.startswith("lost_"):
        _check(args, LOST_CASE_ID)
        if name == "lost_get_tracker_events":
            return {"case_id": LOST_CASE_ID, "events": copy.deepcopy(LOST_TRACKER_EVENTS)}
        if name == "lost_get_home_device_events":
            return {"case_id": LOST_CASE_ID, "events": copy.deepcopy(LOST_HOME_EVENTS)}
        if name == "lost_get_day_location_timeline":
            return {"case_id": LOST_CASE_ID, "timeline": copy.deepcopy(LOST_DAY_TIMELINE)}
        if name == "lost_get_photo_metadata":
            return {"case_id": LOST_CASE_ID, "photos": copy.deepcopy(LOST_PHOTO_METADATA)}
        if name == "lost_validate_search_plan":
            errors: list[str] = []
            selected = list(args["selected_zone_ids"])
            evidence = set(args["evidence_event_ids"])
            if not selected or selected[0] != args["required_first_zone_id"]:
                errors.append("first_zone_does_not_match_user_request")
            total_minutes = 0
            ranked_zones = []
            for zone_id in selected:
                zone = LOST_ZONES.get(zone_id)
                if not zone:
                    errors.append(f"unknown_zone:{zone_id}")
                    continue
                total_minutes += int(zone["minutes"])
                support = sorted(evidence & set(zone["evidence"]))
                if not support:
                    errors.append(f"zone_without_supporting_evidence:{zone_id}")
                ranked_zones.append({"zone_id": zone_id, "label": zone["label"], "support": support, "minutes": zone["minutes"]})
            if total_minutes > int(args["max_search_minutes"]):
                errors.append("search_plan_exceeds_user_time_limit")
            if "rideshare_backseat" in selected and "tracker_rideshare_0854" in evidence and "tracker_entry_0812" not in evidence:
                errors.append("stale_low_confidence_tracker_used_without_home_baseline")
            if "entry_table" in selected and not {"tracker_entry_0812", "speaker_entry_0811"}.issubset(evidence):
                errors.append("entry_zone_missing_two_source_support")
            current["lost_last_valid"] = not errors
            current["lost_valid_zone_ids"] = selected
            return {
                "case_id": LOST_CASE_ID,
                "valid": not errors,
                "errors": errors,
                "ranked_zones": ranked_zones,
                "total_minutes": total_minutes,
                "evidence_token": _token(current, name),
            }
        if name == "lost_save_search_plan":
            if args["evidence_tokens"].get("lost_validate_search_plan") != current["evidence"].get("lost_validate_search_plan") or not current.get("lost_last_valid"):
                return {"success": False, "error": "missing_or_invalid_search_validation", "saved_plan": current["lost_saved_plan"]}
            if args["selected_zone_ids"] != current.get("lost_valid_zone_ids"):
                return {"success": False, "error": "search_plan_changed_after_validation", "saved_plan": current["lost_saved_plan"]}
            current["lost_saved_plan"] = {"status": "saved", "selected_zone_ids": list(args["selected_zone_ids"])}
            return {"success": True, "error": None, "saved_plan": copy.deepcopy(current["lost_saved_plan"])}
        if name == "lost_get_search_plan_status":
            return {"case_id": LOST_CASE_ID, "saved_plan": copy.deepcopy(current["lost_saved_plan"])}

    if name.startswith("plant_"):
        _check(args, PLANT_CASE_ID)
        if name == "plant_get_sensor_readings":
            return {"case_id": PLANT_CASE_ID, "readings": copy.deepcopy(PLANT_SENSOR_READINGS)}
        if name == "plant_get_care_history":
            return {"case_id": PLANT_CASE_ID, "history": copy.deepcopy(PLANT_CARE_HISTORY)}
        if name == "plant_get_species_profile":
            return {"case_id": PLANT_CASE_ID, "profile": copy.deepcopy(PLANT_SPECIES_PROFILE)}
        if name == "plant_get_weather_window":
            return {"case_id": PLANT_CASE_ID, "weather": copy.deepcopy(PLANT_WEATHER_WINDOW)}
        if name == "plant_validate_rescue_plan":
            actions = set(args["action_ids"])
            evidence = set(args["evidence_signal_ids"])
            avoid = set(args["avoid_action_ids"])
            errors: list[str] = []
            required_evidence = {"soil_moisture_high", "low_light_window", "watered_three_days"}
            if not required_evidence.issubset(evidence):
                errors.append("missing_core_overwatering_low_light_evidence")
            unsafe = actions & set(PLANT_SPECIES_PROFILE["avoid_actions"])
            if unsafe:
                errors.extend(f"unsafe_action_selected:{action}" for action in sorted(unsafe))
            missing_safe = {"pause_watering", "move_to_bright_indirect", "check_drainage"} - actions
            if missing_safe:
                errors.extend(f"missing_safe_action:{action}" for action in sorted(missing_safe))
            if "fertilize_now" not in avoid or "water_today" not in avoid:
                errors.append("avoid_list_missing_near_miss_actions")
            if args["pet_safe_only"] and "chemical_pesticide" in actions:
                errors.append("pet_safety_violation")
            if int(args["recheck_days"]) < 3:
                errors.append("recheck_window_too_short")
            diagnosis = [
                {"finding": "overwatering_pressure", "supported_by": ["soil_moisture_high", "watered_three_days"]},
                {"finding": "low_light_pressure", "supported_by": ["low_light_window"]},
                {"finding": "cold_repot_risk", "supported_by": ["cool_night_temp"]},
            ]
            current["plant_last_valid"] = not errors
            current["plant_valid_action_ids"] = list(args["action_ids"])
            return {
                "case_id": PLANT_CASE_ID,
                "valid": not errors,
                "errors": errors,
                "diagnosis": diagnosis,
                "evidence_token": _token(current, name),
            }
        if name == "plant_save_rescue_plan":
            if args["evidence_tokens"].get("plant_validate_rescue_plan") != current["evidence"].get("plant_validate_rescue_plan") or not current.get("plant_last_valid"):
                return {"success": False, "error": "missing_or_invalid_rescue_validation", "saved_plan": current["plant_saved_plan"]}
            if args["action_ids"] != current.get("plant_valid_action_ids"):
                return {"success": False, "error": "rescue_plan_changed_after_validation", "saved_plan": current["plant_saved_plan"]}
            current["plant_saved_plan"] = {"status": "saved", "action_ids": list(args["action_ids"])}
            return {"success": True, "error": None, "saved_plan": copy.deepcopy(current["plant_saved_plan"])}
        if name == "plant_get_rescue_plan_status":
            return {"case_id": PLANT_CASE_ID, "saved_plan": copy.deepcopy(current["plant_saved_plan"])}

    raise ValueError(f"Unknown daily-life discovery tool: {name}")
