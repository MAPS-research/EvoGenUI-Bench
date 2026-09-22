from __future__ import annotations

import copy

from runtime.types import JsonDict, ToolDefinition

LUGGAGE_CASE_ID = "carryon_packing_case_721"
ELECTRIC_CASE_ID = "home_electric_case_408"


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
LUGGAGE_INPUT = {"case_id": {"type": "string", "enum": [LUGGAGE_CASE_ID]}}
ELECTRIC_INPUT = {"case_id": {"type": "string", "enum": [ELECTRIC_CASE_ID]}}

TRIP_WEATHER = {
    "destination": "Seattle",
    "dates": "2026-06-12 to 2026-06-15",
    "conditions": ["rain", "cool evenings"],
    "low_f": 53,
    "high_f": 68,
}
AIRLINE_RULES = {
    "bag_type": "carry_on",
    "max_weight_lb": 22,
    "max_width_in": 14,
    "max_height_in": 22,
    "max_depth_in": 9,
}
LUGGAGE_ITEMS = [
    {"item_id": "rain_jacket", "label": "Rain jacket", "w": 8, "h": 6, "weight_lb": 1.1, "tags": ["rain"]},
    {"item_id": "work_shirt_1", "label": "Work shirt 1", "w": 6, "h": 5, "weight_lb": 0.5, "tags": ["work_outfit"]},
    {"item_id": "work_shirt_2", "label": "Work shirt 2", "w": 6, "h": 5, "weight_lb": 0.5, "tags": ["work_outfit"]},
    {"item_id": "trousers", "label": "Trousers", "w": 8, "h": 6, "weight_lb": 0.8, "tags": ["work_outfit"]},
    {"item_id": "laptop", "label": "Laptop", "w": 13, "h": 9, "weight_lb": 3.2, "tags": ["lithium_battery", "work"]},
    {"item_id": "meds", "label": "Medication pouch", "w": 4, "h": 3, "weight_lb": 0.4, "tags": ["must_carry"]},
    {"item_id": "travel_shampoo_90ml", "label": "Travel shampoo 90ml", "w": 2, "h": 5, "weight_lb": 0.3, "tags": ["liquid_90ml"]},
    {"item_id": "shampoo_250ml", "label": "Full-size shampoo 250ml", "w": 3, "h": 8, "weight_lb": 0.9, "tags": ["liquid_250ml"]},
    {"item_id": "boots", "label": "Heavy boots", "w": 10, "h": 8, "weight_lb": 4.8, "tags": ["heavy"]},
]
SECURITY_RULES = [
    "Carry-on liquids must be 100ml or smaller.",
    "Lithium battery electronics must stay in carry-on.",
    "Medication should remain accessible.",
]

CIRCUITS = {
    "study_a": {"max_watts": 1800, "outlets": ["desk_wall", "window_wall", "power_strip"]},
    "kitchen_b": {"max_watts": 1800, "outlets": ["kitchen_counter"]},
}
OUTLETS = [
    {"outlet_id": "desk_wall", "circuit": "study_a", "kind": "wall"},
    {"outlet_id": "window_wall", "circuit": "study_a", "kind": "wall"},
    {"outlet_id": "power_strip", "circuit": "study_a", "kind": "strip", "max_watts": 1250},
    {"outlet_id": "kitchen_counter", "circuit": "kitchen_b", "kind": "wall"},
]
DEVICES = [
    {"device_id": "space_heater", "label": "Space heater", "watts": 1500, "tags": ["heat", "no_strip"]},
    {"device_id": "laptop_charger", "label": "Laptop charger", "watts": 95, "tags": ["work"]},
    {"device_id": "monitor", "label": "Monitor", "watts": 65, "tags": ["work"]},
    {"device_id": "desk_lamp", "label": "Desk lamp", "watts": 20, "tags": ["lighting"]},
    {"device_id": "kettle", "label": "Electric kettle", "watts": 1500, "tags": ["kitchen"]},
]
ELECTRIC_RULES = [
    "Do not exceed circuit max watts.",
    "Space heaters must plug directly into a wall outlet, not a power strip.",
    "Power strip load must stay within its own rating.",
]

TOOL_SCHEMAS: dict[str, JsonDict] = {
    "luggage_get_trip_weather": {
        "description": "Read destination weather for the travel dates. Weather is external evidence for item coverage.",
        "inputSchema": _object(["case_id"], LUGGAGE_INPUT),
        "outputSchema": _object(["case_id", "weather"], {"case_id": {"type": "string"}, "weather": OPEN_OBJECT}),
    },
    "luggage_get_airline_bag_rules": {
        "description": "Read airline carry-on size and weight limits.",
        "inputSchema": _object(["case_id"], LUGGAGE_INPUT),
        "outputSchema": _object(["case_id", "rules"], {"case_id": {"type": "string"}, "rules": OPEN_OBJECT}),
    },
    "luggage_get_item_catalog": {
        "description": "Read item dimensions, weights, and rule-relevant tags for packing layout.",
        "inputSchema": _object(["case_id"], LUGGAGE_INPUT),
        "outputSchema": _object(["case_id", "items"], {"case_id": {"type": "string"}, "items": OPEN_ARRAY}),
    },
    "luggage_get_security_rules": {
        "description": "Read carry-on security rules for liquids, medication access, and battery items.",
        "inputSchema": _object(["case_id"], LUGGAGE_INPUT),
        "outputSchema": _object(["case_id", "rules"], {"case_id": {"type": "string"}, "rules": STRING_ARRAY}),
    },
    "luggage_validate_packing_layout": {
        "description": "Validate a user-requested carry-on packing layout against item dimensions, bag bounds, collisions, weather coverage, selected required items, liquid limits, and weight. Required items come from the user request.",
        "inputSchema": _object(
            ["case_id", "bag_type", "packed_item_ids", "required_item_ids", "placements"],
            {
                **LUGGAGE_INPUT,
                "bag_type": {"type": "string", "enum": ["carry_on"]},
                "packed_item_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True},
                "required_item_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                "placements": OPEN_OBJECT,
            },
        ),
        "outputSchema": _object(["case_id", "valid", "errors", "coverage", "total_weight_lb", "evidence_token"], {"case_id": {"type": "string"}, "valid": {"type": "boolean"}, "errors": STRING_ARRAY, "coverage": OPEN_ARRAY, "total_weight_lb": {"type": "number"}, "evidence_token": {"type": "string"}}),
    },
    "luggage_save_packing_plan": {
        "description": "Save a validated packing plan. Pass evidence_tokens.luggage_validate_packing_layout and refresh saved state before claiming completion.",
        "inputSchema": _object(["case_id", "packed_item_ids", "placements", "evidence_tokens"], {**LUGGAGE_INPUT, "packed_item_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True}, "placements": OPEN_OBJECT, "evidence_tokens": _token_input("luggage_validate_packing_layout")}),
        "outputSchema": _object(["success", "error", "saved_plan"], {"success": {"type": "boolean"}, "error": {"type": ["string", "null"]}, "saved_plan": {"anyOf": [OPEN_OBJECT, {"type": "null"}]}}),
    },
    "luggage_get_saved_packing_plan": {
        "description": "Refresh saved packing plan state.",
        "inputSchema": _object(["case_id"], LUGGAGE_INPUT),
        "outputSchema": _object(["case_id", "saved_plan"], {"case_id": {"type": "string"}, "saved_plan": {"anyOf": [OPEN_OBJECT, {"type": "null"}]}}),
    },
    "electric_get_circuit_map": {
        "description": "Read room outlets, circuit membership, circuit watt limits, and power-strip rating.",
        "inputSchema": _object(["case_id"], ELECTRIC_INPUT),
        "outputSchema": _object(["case_id", "circuits", "outlets"], {"case_id": {"type": "string"}, "circuits": OPEN_OBJECT, "outlets": OPEN_ARRAY}),
    },
    "electric_get_device_specs": {
        "description": "Read device wattage and safety tags.",
        "inputSchema": _object(["case_id"], ELECTRIC_INPUT),
        "outputSchema": _object(["case_id", "devices"], {"case_id": {"type": "string"}, "devices": OPEN_ARRAY}),
    },
    "electric_get_safety_rules": {
        "description": "Read household electrical safety rules for load and outlet placement.",
        "inputSchema": _object(["case_id"], ELECTRIC_INPUT),
        "outputSchema": _object(["case_id", "rules"], {"case_id": {"type": "string"}, "rules": STRING_ARRAY}),
    },
    "electric_validate_load_plan": {
        "description": "Validate a user-requested outlet assignment against circuit load, power-strip rating, no-strip devices, and required simultaneously running devices from the user request.",
        "inputSchema": _object(
            ["case_id", "outlet_assignments", "required_running_device_ids"],
            {
                **ELECTRIC_INPUT,
                "outlet_assignments": OPEN_OBJECT,
                "required_running_device_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            },
        ),
        "outputSchema": _object(["case_id", "valid", "errors", "load_by_circuit", "evidence_token"], {"case_id": {"type": "string"}, "valid": {"type": "boolean"}, "errors": STRING_ARRAY, "load_by_circuit": OPEN_OBJECT, "evidence_token": {"type": "string"}}),
    },
    "electric_save_load_plan": {
        "description": "Save a validated load plan. Pass evidence_tokens.electric_validate_load_plan and refresh saved state before claiming completion.",
        "inputSchema": _object(["case_id", "outlet_assignments", "evidence_tokens"], {**ELECTRIC_INPUT, "outlet_assignments": OPEN_OBJECT, "evidence_tokens": _token_input("electric_validate_load_plan")}),
        "outputSchema": _object(["success", "error", "saved_plan"], {"success": {"type": "boolean"}, "error": {"type": ["string", "null"]}, "saved_plan": {"anyOf": [OPEN_OBJECT, {"type": "null"}]}}),
    },
    "electric_get_saved_load_plan": {
        "description": "Refresh saved electrical load plan state.",
        "inputSchema": _object(["case_id"], ELECTRIC_INPUT),
        "outputSchema": _object(["case_id", "saved_plan"], {"case_id": {"type": "string"}, "saved_plan": {"anyOf": [OPEN_OBJECT, {"type": "null"}]}}),
    },
}


def _state(state: JsonDict) -> JsonDict:
    if "daily_life_home_cases_v1" not in state:
        state["daily_life_home_cases_v1"] = {"evidence": {}, "saved_luggage": None, "saved_electric": None, "counter": 0}
    return state["daily_life_home_cases_v1"]


def _token(current: JsonDict, action: str) -> str:
    current["counter"] += 1
    token = f"home-life-ev::{action}::{current['counter']}"
    current["evidence"][action] = token
    return token


def _check(args: JsonDict, expected: str) -> None:
    if args.get("case_id") != expected:
        raise ValueError(f"case_id_mismatch: expected {expected}")


def _luggage_rect(item_id: str, placement: JsonDict) -> tuple[float, float, float, float]:
    item = next(item for item in LUGGAGE_ITEMS if item["item_id"] == item_id)
    x = float(placement["x"])
    y = float(placement["y"])
    return x, y, x + float(item["w"]), y + float(item["h"])


def _overlap(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> bool:
    return left[0] < right[2] and left[2] > right[0] and left[1] < right[3] and left[3] > right[1]


def invoke(*, tool: ToolDefinition, args: JsonDict, state: JsonDict) -> JsonDict:
    current = _state(state)
    name = tool.name

    if name.startswith("luggage_"):
        _check(args, LUGGAGE_CASE_ID)
        if name == "luggage_get_trip_weather":
            return {"case_id": LUGGAGE_CASE_ID, "weather": copy.deepcopy(TRIP_WEATHER)}
        if name == "luggage_get_airline_bag_rules":
            return {"case_id": LUGGAGE_CASE_ID, "rules": copy.deepcopy(AIRLINE_RULES)}
        if name == "luggage_get_item_catalog":
            return {"case_id": LUGGAGE_CASE_ID, "items": copy.deepcopy(LUGGAGE_ITEMS)}
        if name == "luggage_get_security_rules":
            return {"case_id": LUGGAGE_CASE_ID, "rules": copy.deepcopy(SECURITY_RULES)}
        if name == "luggage_validate_packing_layout":
            item_by_id = {item["item_id"]: item for item in LUGGAGE_ITEMS}
            packed = list(args["packed_item_ids"])
            required = set(args["required_item_ids"])
            placements = args["placements"]
            errors: list[str] = []
            if not required.issubset(set(packed)):
                errors.append("missing_required_items")
            if set(packed) != set(placements):
                errors.append("placements_do_not_match_packed_items")
            total_weight = sum(float(item_by_id[item_id]["weight_lb"]) for item_id in packed if item_id in item_by_id)
            if total_weight > AIRLINE_RULES["max_weight_lb"]:
                errors.append("carry_on_overweight")
            rects = {}
            for item_id in packed:
                item = item_by_id.get(item_id)
                placement = placements.get(item_id)
                if not item or not isinstance(placement, dict) or "x" not in placement or "y" not in placement:
                    errors.append(f"bad_item_or_placement:{item_id}")
                    continue
                rect = _luggage_rect(item_id, placement)
                rects[item_id] = rect
                if rect[0] < 0 or rect[1] < 0 or rect[2] > AIRLINE_RULES["max_width_in"] or rect[3] > AIRLINE_RULES["max_height_in"]:
                    errors.append(f"item_out_of_bag:{item_id}")
                tags = set(item["tags"])
                if "liquid_250ml" in tags:
                    errors.append(f"liquid_over_100ml:{item_id}")
            for left_id, left_rect in rects.items():
                for right_id, right_rect in rects.items():
                    if left_id < right_id and _overlap(left_rect, right_rect):
                        errors.append(f"collision:{left_id}:{right_id}")
            if "rain" in TRIP_WEATHER["conditions"] and "rain_jacket" not in packed:
                errors.append("missing_rain_coverage")
            coverage = [
                {"requirement": "rain", "covered": "rain_jacket" in packed},
                {"requirement": "work_outfits", "covered": {"work_shirt_1", "work_shirt_2", "trousers"}.issubset(set(packed))},
                {"requirement": "must_carry_items", "covered": required.issubset(set(packed))},
            ]
            current["luggage_last_valid"] = not errors
            current["luggage_validated_plan"] = {
                "packed_item_ids": sorted(packed),
                "placements": copy.deepcopy(placements),
            }
            return {"case_id": LUGGAGE_CASE_ID, "valid": not errors, "errors": errors, "coverage": coverage, "total_weight_lb": round(total_weight, 2), "evidence_token": _token(current, name)}
        if name == "luggage_save_packing_plan":
            if args["evidence_tokens"].get("luggage_validate_packing_layout") != current["evidence"].get("luggage_validate_packing_layout") or not current.get("luggage_last_valid"):
                return {"success": False, "error": "missing_or_invalid_packing_validation", "saved_plan": current["saved_luggage"]}
            submitted = {"packed_item_ids": sorted(args["packed_item_ids"]), "placements": args["placements"]}
            if submitted != current["luggage_validated_plan"]:
                return {"success": False, "error": "packing_plan_changed_after_validation", "saved_plan": copy.deepcopy(current["saved_luggage"])}
            current["saved_luggage"] = {"status": "saved", "packed_item_ids": list(args["packed_item_ids"]), "placements": copy.deepcopy(args["placements"])}
            return {"success": True, "error": None, "saved_plan": copy.deepcopy(current["saved_luggage"])}
        if name == "luggage_get_saved_packing_plan":
            return {"case_id": LUGGAGE_CASE_ID, "saved_plan": copy.deepcopy(current["saved_luggage"])}

    if name.startswith("electric_"):
        _check(args, ELECTRIC_CASE_ID)
        if name == "electric_get_circuit_map":
            return {"case_id": ELECTRIC_CASE_ID, "circuits": copy.deepcopy(CIRCUITS), "outlets": copy.deepcopy(OUTLETS)}
        if name == "electric_get_device_specs":
            return {"case_id": ELECTRIC_CASE_ID, "devices": copy.deepcopy(DEVICES)}
        if name == "electric_get_safety_rules":
            return {"case_id": ELECTRIC_CASE_ID, "rules": copy.deepcopy(ELECTRIC_RULES)}
        if name == "electric_validate_load_plan":
            outlet_by_id = {outlet["outlet_id"]: outlet for outlet in OUTLETS}
            device_by_id = {device["device_id"]: device for device in DEVICES}
            assignments = args["outlet_assignments"]
            required = set(args["required_running_device_ids"])
            placed_devices = {device_id for device_ids in assignments.values() for device_id in device_ids}
            errors: list[str] = []
            if not required.issubset(placed_devices):
                errors.append("missing_required_running_devices")
            load_by_circuit = {circuit: 0 for circuit in CIRCUITS}
            load_by_outlet = {outlet["outlet_id"]: 0 for outlet in OUTLETS}
            for outlet_id, device_ids in assignments.items():
                outlet = outlet_by_id.get(outlet_id)
                if not outlet:
                    errors.append(f"unknown_outlet:{outlet_id}")
                    continue
                for device_id in device_ids:
                    device = device_by_id.get(device_id)
                    if not device:
                        errors.append(f"unknown_device:{device_id}")
                        continue
                    watts = int(device["watts"])
                    load_by_circuit[outlet["circuit"]] += watts
                    load_by_outlet[outlet_id] += watts
                    if "no_strip" in device["tags"] and outlet.get("kind") == "strip":
                        errors.append(f"device_cannot_use_power_strip:{device_id}")
            for circuit_id, watts in load_by_circuit.items():
                if watts > CIRCUITS[circuit_id]["max_watts"]:
                    errors.append(f"circuit_overload:{circuit_id}")
            for outlet in OUTLETS:
                if outlet.get("kind") == "strip" and load_by_outlet[outlet["outlet_id"]] > outlet["max_watts"]:
                    errors.append(f"power_strip_overload:{outlet['outlet_id']}")
            current["electric_last_valid"] = not errors
            current["electric_validated_assignments"] = {
                outlet: sorted(devices) for outlet, devices in assignments.items()
            }
            return {"case_id": ELECTRIC_CASE_ID, "valid": not errors, "errors": errors, "load_by_circuit": load_by_circuit, "evidence_token": _token(current, name)}
        if name == "electric_save_load_plan":
            if args["evidence_tokens"].get("electric_validate_load_plan") != current["evidence"].get("electric_validate_load_plan") or not current.get("electric_last_valid"):
                return {"success": False, "error": "missing_or_invalid_load_validation", "saved_plan": current["saved_electric"]}
            submitted = {outlet: sorted(devices) for outlet, devices in args["outlet_assignments"].items()}
            if submitted != current["electric_validated_assignments"]:
                return {"success": False, "error": "load_plan_changed_after_validation", "saved_plan": copy.deepcopy(current["saved_electric"])}
            current["saved_electric"] = {"status": "saved", "outlet_assignments": copy.deepcopy(args["outlet_assignments"])}
            return {"success": True, "error": None, "saved_plan": copy.deepcopy(current["saved_electric"])}
        if name == "electric_get_saved_load_plan":
            return {"case_id": ELECTRIC_CASE_ID, "saved_plan": copy.deepcopy(current["saved_electric"])}

    raise ValueError(f"Unknown daily-life home tool: {name}")
