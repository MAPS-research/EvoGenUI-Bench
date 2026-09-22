from __future__ import annotations

import copy

from runtime.types import JsonDict, ToolDefinition

OUTING_CASE_ID = "family_outing_case_118"
GROCERY_CASE_ID = "grocery_dinner_case_604"
EVENT_CASE_ID = "block_party_case_337"
ROOM_CASE_ID = "studio_layout_case_229"
PARKING_CASE_ID = "parking_charge_case_512"


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

OUTING_INPUT = {"case_id": {"type": "string", "enum": [OUTING_CASE_ID]}}
GROCERY_INPUT = {"case_id": {"type": "string", "enum": [GROCERY_CASE_ID]}}
EVENT_INPUT = {"case_id": {"type": "string", "enum": [EVENT_CASE_ID]}}
ROOM_INPUT = {"case_id": {"type": "string", "enum": [ROOM_CASE_ID]}}
PARKING_INPUT = {"case_id": {"type": "string", "enum": [PARKING_CASE_ID]}}

OUTING_PLACES = [
    {"place_id": "riverside_cafe", "name": "Riverside Cafe", "walk_m": 180, "kid_friendly": True, "reservation_slots": ["11:30", "12:15"]},
    {"place_id": "hilltop_bbq", "name": "Hilltop BBQ", "walk_m": 120, "kid_friendly": True, "reservation_slots": ["11:30"]},
    {"place_id": "mall_noodles", "name": "Mall Noodles", "walk_m": 90, "kid_friendly": True, "reservation_slots": ["12:00"]},
]
OUTING_ACCESS = {
    "riverside_cafe": {"step_free": True, "restroom": True, "door_width_cm": 91},
    "hilltop_bbq": {"step_free": False, "restroom": True, "door_width_cm": 78},
    "mall_noodles": {"step_free": True, "restroom": True, "door_width_cm": 86},
}
OUTING_PARKING = {
    "riverside_cafe": {"available_spaces": 7, "distance_m": 180},
    "hilltop_bbq": {"available_spaces": 4, "distance_m": 260},
    "mall_noodles": {"available_spaces": 0, "distance_m": 90},
}
OUTING_WEATHER = {"11:30": {"condition": "clear", "rain_probability": 0.1}, "12:15": {"condition": "showers", "rain_probability": 0.7}}

GROCERY_RECIPE = {
    "dish": "tomato basil pasta for four",
    "needed": ["pasta", "tomato_sauce", "basil", "milk_alternative"],
    "optional": ["pesto_optional"],
}
GROCERY_PANTRY = [{"item_id": "olive_oil", "qty": "enough"}, {"item_id": "salt", "qty": "enough"}]
GROCERY_STOCK = [
    {"item_id": "semolina_pasta", "label": "Semolina pasta", "category": "pasta", "available": True, "allergens": ["wheat"], "price": 3.1},
    {"item_id": "tomato_sauce", "label": "Tomato sauce", "category": "tomato_sauce", "available": True, "allergens": [], "price": 4.2},
    {"item_id": "fresh_basil", "label": "Fresh basil", "category": "basil", "available": True, "allergens": [], "price": 2.0},
    {"item_id": "oat_milk", "label": "Oat milk", "category": "milk_alternative", "available": True, "allergens": [], "price": 4.5},
    {"item_id": "almond_pesto", "label": "Almond pesto", "category": "pesto_optional", "available": True, "allergens": ["tree_nuts"], "price": 5.8},
    {"item_id": "nut_free_pesto", "label": "Nut-free basil sauce", "category": "pesto_optional", "available": True, "allergens": [], "price": 6.3},
    {"item_id": "coupon_family_pasta", "label": "Family-size pasta coupon item", "category": "pasta", "available": False, "allergens": ["wheat"], "price": 2.5},
]
GROCERY_COUPONS = [{"coupon_id": "PASTA-FAMILY", "applies_to": "coupon_family_pasta", "requires_available_item": True}]

EVENT_SITE = {
    "zones": ["playground", "covered_pavilion", "grill_area", "stage_corner"],
    "constraints": ["food table needs certified volunteer", "music table needs power", "rain plan moves crafts under pavilion"],
}
EVENT_VOLUNTEERS = [
    {"person": "Sam", "available": ["10-12", "12-14"], "certifications": ["food_handler"]},
    {"person": "Priya", "available": ["10-12"], "certifications": []},
    {"person": "Nina", "available": ["12-14"], "certifications": ["first_aid"]},
    {"person": "Omar", "available": ["10-12", "12-14"], "certifications": ["sound_setup"]},
]
EVENT_SUPPLIES = [{"item": "folding_tables", "count": 4}, {"item": "canopy_tents", "count": 1}, {"item": "extension_cords", "count": 2}]
EVENT_WEATHER = {"rain_probability": 0.65, "wind_mph": 9, "recommended_plan": "covered_pavilion"}

ROOM_PLAN = {
    "width_ft": 15,
    "height_ft": 12,
    "door": {"x": 0, "y": 5, "clearance_ft": 3},
    "window": {"x": 12, "y": 0, "clearance_ft": 2},
}
ROOM_FURNITURE = [
    {"item_id": "sofa", "label": "Sofa", "w": 6, "h": 3},
    {"item_id": "desk", "label": "Desk", "w": 4, "h": 2},
    {"item_id": "crib", "label": "Crib", "w": 4, "h": 3},
    {"item_id": "bookshelf", "label": "Bookshelf", "w": 3, "h": 1},
]
ROOM_OUTLETS = [{"outlet_id": "north_wall", "x": 7, "y": 0}, {"outlet_id": "east_wall", "x": 15, "y": 8}]
ROOM_RULES = ["Door swing must stay clear.", "Crib must be at least 2 ft from the window.", "Desk should be within 5 ft of an outlet."]

PARKING_GARAGES = [
    {"garage_id": "civic_garage_ev", "name": "Civic Garage EV", "walk_m": 240, "base_price": 18},
    {"garage_id": "market_lot", "name": "Market Lot", "walk_m": 520, "base_price": 12},
    {"garage_id": "library_deck", "name": "Library Deck", "walk_m": 410, "base_price": 15},
]
PARKING_OCCUPANCY = {"civic_garage_ev": {"spaces": 9}, "market_lot": {"spaces": 22}, "library_deck": {"spaces": 5}}
PARKING_CHARGERS = {
    "civic_garage_ev": {"connector": "J1772", "ports_available": 3},
    "market_lot": {"connector": "CCS", "ports_available": 0},
    "library_deck": {"connector": "CCS", "ports_available": 2},
}
PARKING_SAFETY = {"civic_garage_ev": {"rating": "medium"}, "market_lot": {"rating": "low"}, "library_deck": {"rating": "high"}}


TOOL_SCHEMAS: dict[str, JsonDict] = {
    "outing_search_places": {
        "description": "Read candidate outing places with distance, kid-friendly flags, and reservation slots.",
        "inputSchema": _object(["case_id"], OUTING_INPUT),
        "outputSchema": _object(["case_id", "places"], {"case_id": {"type": "string"}, "places": OPEN_ARRAY}),
    },
    "outing_get_accessibility_detail": {
        "description": "Read verified accessibility details for a candidate place; this is authoritative over place marketing text.",
        "inputSchema": _object(["case_id", "place_id"], {**OUTING_INPUT, "place_id": {"type": "string"}}),
        "outputSchema": _object(["case_id", "place_id", "accessibility"], {"case_id": {"type": "string"}, "place_id": {"type": "string"}, "accessibility": OPEN_OBJECT}),
    },
    "outing_get_parking_live_status": {
        "description": "Read live parking availability and walking distance for a candidate place.",
        "inputSchema": _object(["case_id", "place_id"], {**OUTING_INPUT, "place_id": {"type": "string"}}),
        "outputSchema": _object(["case_id", "place_id", "parking"], {"case_id": {"type": "string"}, "place_id": {"type": "string"}, "parking": OPEN_OBJECT}),
    },
    "outing_get_weather_window": {
        "description": "Read weather by outing time; high rain probability should trigger a covered or lower-risk plan.",
        "inputSchema": _object(["case_id"], OUTING_INPUT),
        "outputSchema": _object(["case_id", "weather"], {"case_id": {"type": "string"}, "weather": OPEN_OBJECT}),
    },
    "outing_validate_route_plan": {
        "description": "Validate selected place and arrival time against user-provided accessibility, parking-walk, and rain-risk constraints plus live place evidence. User needs belong in these input fields, not in a profile-read tool. Returns an evidence_token for reservation hold.",
        "inputSchema": _object(
            [
                "case_id",
                "place_id",
                "arrival_time",
                "require_step_free",
                "require_restroom",
                "max_parking_walk_m",
                "max_rain_probability",
            ],
            {
                **OUTING_INPUT,
                "place_id": {"type": "string"},
                "arrival_time": {"type": "string"},
                "require_step_free": {"type": "boolean"},
                "require_restroom": {"type": "boolean"},
                "max_parking_walk_m": {"type": "integer", "minimum": 0},
                "max_rain_probability": {"type": "number", "minimum": 0, "maximum": 1},
            },
        ),
        "outputSchema": _object(["case_id", "valid", "errors", "evidence_token"], {"case_id": {"type": "string"}, "valid": {"type": "boolean"}, "errors": STRING_ARRAY, "evidence_token": {"type": "string"}}),
    },
    "outing_hold_reservation": {
        "description": "Create a temporary reservation hold. Pass evidence_tokens.outing_validate_route_plan for the selected place and time. This does not confirm the reservation.",
        "inputSchema": _object(["case_id", "place_id", "arrival_time", "evidence_tokens"], {**OUTING_INPUT, "place_id": {"type": "string"}, "arrival_time": {"type": "string"}, "evidence_tokens": _token_input("outing_validate_route_plan")}),
        "outputSchema": _object(["success", "error", "hold"], {"success": {"type": "boolean"}, "error": {"type": ["string", "null"]}, "hold": {"anyOf": [OPEN_OBJECT, {"type": "null"}]}}),
    },
    "outing_confirm_reservation": {
        "description": "Confirm the held reservation. Pass validation and hold evidence tokens, then refresh reservation status.",
        "inputSchema": _object(["case_id", "hold_id", "evidence_tokens"], {**OUTING_INPUT, "hold_id": {"type": "string"}, "evidence_tokens": _token_input("outing_validate_route_plan", "outing_hold_reservation")}),
        "outputSchema": _object(["success", "error", "operation", "reservation_status"], {"success": {"type": "boolean"}, "error": {"type": ["string", "null"]}, "operation": OPEN_OBJECT, "reservation_status": OPEN_OBJECT}),
    },
    "outing_get_reservation_status": {
        "description": "Refresh reservation status after a hold or confirmation attempt.",
        "inputSchema": _object(["case_id"], OUTING_INPUT),
        "outputSchema": _object(["case_id", "reservation_status"], {"case_id": {"type": "string"}, "reservation_status": OPEN_OBJECT}),
    },
    "grocery_get_recipe_plan": {
        "description": "Read dinner requirements, dietary restrictions, and ingredient categories.",
        "inputSchema": _object(["case_id"], GROCERY_INPUT),
        "outputSchema": _object(["case_id", "recipe"], {"case_id": {"type": "string"}, "recipe": OPEN_OBJECT}),
    },
    "grocery_get_pantry_inventory": {
        "description": "Read pantry items already at home so the shopping list does not duplicate them.",
        "inputSchema": _object(["case_id"], GROCERY_INPUT),
        "outputSchema": _object(["case_id", "pantry"], {"case_id": {"type": "string"}, "pantry": OPEN_ARRAY}),
    },
    "grocery_search_store_stock": {
        "description": "Read store stock, allergens, prices, and substitution categories.",
        "inputSchema": _object(["case_id"], GROCERY_INPUT),
        "outputSchema": _object(["case_id", "stock"], {"case_id": {"type": "string"}, "stock": OPEN_ARRAY}),
    },
    "grocery_get_coupon_rules": {
        "description": "Read coupon rules. Coupons are not authoritative availability and may apply only to unavailable or unsafe items.",
        "inputSchema": _object(["case_id"], GROCERY_INPUT),
        "outputSchema": _object(["case_id", "coupons"], {"case_id": {"type": "string"}, "coupons": OPEN_ARRAY}),
    },
    "grocery_validate_cart": {
        "description": "Validate selected store items against recipe coverage, availability, pantry duplicates, and user-provided allergen constraints from the conversation. Returns an evidence_token for pickup reservation.",
        "inputSchema": _object(
            ["case_id", "item_ids", "avoid_allergens"],
            {
                **GROCERY_INPUT,
                "item_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True},
                "avoid_allergens": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            },
        ),
        "outputSchema": _object(["case_id", "valid", "errors", "coverage", "evidence_token"], {"case_id": {"type": "string"}, "valid": {"type": "boolean"}, "errors": STRING_ARRAY, "coverage": OPEN_ARRAY, "evidence_token": {"type": "string"}}),
    },
    "grocery_reserve_pickup": {
        "description": "Reserve grocery pickup for a validated cart. Pass evidence_tokens.grocery_validate_cart. This mutates pickup status.",
        "inputSchema": _object(["case_id", "item_ids", "pickup_window", "evidence_tokens"], {**GROCERY_INPUT, "item_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True}, "pickup_window": {"type": "string"}, "evidence_tokens": _token_input("grocery_validate_cart")}),
        "outputSchema": _object(["success", "error", "pickup_status"], {"success": {"type": "boolean"}, "error": {"type": ["string", "null"]}, "pickup_status": OPEN_OBJECT}),
    },
    "grocery_get_pickup_status": {
        "description": "Refresh grocery pickup status.",
        "inputSchema": _object(["case_id"], GROCERY_INPUT),
        "outputSchema": _object(["case_id", "pickup_status"], {"case_id": {"type": "string"}, "pickup_status": OPEN_OBJECT}),
    },
    "event_get_site_map": {
        "description": "Read the block-party site map, zones, and placement constraints.",
        "inputSchema": _object(["case_id"], EVENT_INPUT),
        "outputSchema": _object(["case_id", "site"], {"case_id": {"type": "string"}, "site": OPEN_OBJECT}),
    },
    "event_get_volunteer_availability": {
        "description": "Read volunteer availability and certifications.",
        "inputSchema": _object(["case_id"], EVENT_INPUT),
        "outputSchema": _object(["case_id", "volunteers"], {"case_id": {"type": "string"}, "volunteers": OPEN_ARRAY}),
    },
    "event_get_supply_inventory": {
        "description": "Read available supplies for tables, weather cover, and powered stations.",
        "inputSchema": _object(["case_id"], EVENT_INPUT),
        "outputSchema": _object(["case_id", "supplies"], {"case_id": {"type": "string"}, "supplies": OPEN_ARRAY}),
    },
    "event_get_permit_rules": {
        "description": "Read event permit constraints. Food assignment and powered music placement must satisfy these rules.",
        "inputSchema": _object(["case_id"], EVENT_INPUT),
        "outputSchema": _object(["case_id", "rules"], {"case_id": {"type": "string"}, "rules": STRING_ARRAY}),
    },
    "event_get_weather_plan": {
        "description": "Read rain and wind risk for the event; this is authoritative for rain-plan validation.",
        "inputSchema": _object(["case_id"], EVENT_INPUT),
        "outputSchema": _object(["case_id", "weather"], {"case_id": {"type": "string"}, "weather": OPEN_OBJECT}),
    },
    "event_validate_assignment_plan": {
        "description": "Validate assignment roles, known volunteers and zones, certifications, power, and rain cover. Each assignment has person and zone; an optional time_slot must match that volunteer's published availability window. Returns an evidence_token for sending the same assignments.",
        "inputSchema": _object(["case_id", "assignments"], {**EVENT_INPUT, "assignments": OPEN_OBJECT}),
        "outputSchema": _object(["case_id", "valid", "errors", "evidence_token"], {"case_id": {"type": "string"}, "valid": {"type": "boolean"}, "errors": STRING_ARRAY, "evidence_token": {"type": "string"}}),
    },
    "event_send_assignments": {
        "description": "Send volunteer assignments. Pass evidence_tokens.event_validate_assignment_plan. This mutates assignment status.",
        "inputSchema": _object(["case_id", "assignments", "evidence_tokens"], {**EVENT_INPUT, "assignments": OPEN_OBJECT, "evidence_tokens": _token_input("event_validate_assignment_plan")}),
        "outputSchema": _object(["success", "error", "assignment_status"], {"success": {"type": "boolean"}, "error": {"type": ["string", "null"]}, "assignment_status": OPEN_OBJECT}),
    },
    "event_get_assignment_status": {
        "description": "Refresh sent assignment status.",
        "inputSchema": _object(["case_id"], EVENT_INPUT),
        "outputSchema": _object(["case_id", "assignment_status"], {"case_id": {"type": "string"}, "assignment_status": OPEN_OBJECT}),
    },
    "room_get_floor_plan": {
        "description": "Read room dimensions, door swing, and window location for an interactive floor-plan layout.",
        "inputSchema": _object(["case_id"], ROOM_INPUT),
        "outputSchema": _object(["case_id", "floor_plan"], {"case_id": {"type": "string"}, "floor_plan": OPEN_OBJECT}),
    },
    "room_get_furniture_catalog": {
        "description": "Read furniture dimensions for drag/drop placement.",
        "inputSchema": _object(["case_id"], ROOM_INPUT),
        "outputSchema": _object(["case_id", "furniture"], {"case_id": {"type": "string"}, "furniture": OPEN_ARRAY}),
    },
    "room_get_outlet_map": {
        "description": "Read outlet locations used for desk placement validation.",
        "inputSchema": _object(["case_id"], ROOM_INPUT),
        "outputSchema": _object(["case_id", "outlets"], {"case_id": {"type": "string"}, "outlets": OPEN_ARRAY}),
    },
    "room_get_safety_rules": {
        "description": "Read safety and clearance rules. The UI should validate the layout rather than relying on visual fit alone.",
        "inputSchema": _object(["case_id"], ROOM_INPUT),
        "outputSchema": _object(["case_id", "rules"], {"case_id": {"type": "string"}, "rules": STRING_ARRAY}),
    },
    "room_validate_layout": {
        "description": "Validate furniture placements against room bounds, collision, door clearance, crib/window safety, and desk/outlet distance. Returns an evidence_token for save.",
        "inputSchema": _object(["case_id", "placements"], {**ROOM_INPUT, "placements": OPEN_OBJECT}),
        "outputSchema": _object(["case_id", "valid", "errors", "evidence_token"], {"case_id": {"type": "string"}, "valid": {"type": "boolean"}, "errors": STRING_ARRAY, "evidence_token": {"type": "string"}}),
    },
    "room_save_layout": {
        "description": "Save a validated layout. Pass evidence_tokens.room_validate_layout. This mutates saved layout state.",
        "inputSchema": _object(["case_id", "placements", "evidence_tokens"], {**ROOM_INPUT, "placements": OPEN_OBJECT, "evidence_tokens": _token_input("room_validate_layout")}),
        "outputSchema": _object(["success", "error", "saved_layout"], {"success": {"type": "boolean"}, "error": {"type": ["string", "null"]}, "saved_layout": {"anyOf": [OPEN_OBJECT, {"type": "null"}]}}),
    },
    "room_get_saved_layout": {
        "description": "Refresh saved layout state.",
        "inputSchema": _object(["case_id"], ROOM_INPUT),
        "outputSchema": _object(["case_id", "saved_layout"], {"case_id": {"type": "string"}, "saved_layout": {"anyOf": [OPEN_OBJECT, {"type": "null"}]}}),
    },
    "parking_search_garages": {
        "description": "Read candidate garages with walking distance and base price.",
        "inputSchema": _object(["case_id"], PARKING_INPUT),
        "outputSchema": _object(["case_id", "garages"], {"case_id": {"type": "string"}, "garages": OPEN_ARRAY}),
    },
    "parking_get_live_occupancy": {
        "description": "Read live parking spaces for a garage.",
        "inputSchema": _object(["case_id", "garage_id"], {**PARKING_INPUT, "garage_id": {"type": "string"}}),
        "outputSchema": _object(["case_id", "garage_id", "occupancy"], {"case_id": {"type": "string"}, "garage_id": {"type": "string"}, "occupancy": OPEN_OBJECT}),
    },
    "parking_get_charger_status": {
        "description": "Read charger connector and port availability. Connector mismatch should invalidate EV charging plans.",
        "inputSchema": _object(["case_id", "garage_id"], {**PARKING_INPUT, "garage_id": {"type": "string"}}),
        "outputSchema": _object(["case_id", "garage_id", "charger"], {"case_id": {"type": "string"}, "garage_id": {"type": "string"}, "charger": OPEN_OBJECT}),
    },
    "parking_get_walking_safety": {
        "description": "Read late-night walking-route safety rating for a garage.",
        "inputSchema": _object(["case_id", "garage_id"], {**PARKING_INPUT, "garage_id": {"type": "string"}}),
        "outputSchema": _object(["case_id", "garage_id", "safety"], {"case_id": {"type": "string"}, "garage_id": {"type": "string"}, "safety": OPEN_OBJECT}),
    },
    "parking_validate_choice": {
        "description": "Validate selected garage against live spaces, charger connector and port availability, walking distance, and route safety. Vehicle connector and walk limit are user-provided constraints from the conversation. Returns an evidence_token for reservation.",
        "inputSchema": _object(
            ["case_id", "garage_id", "vehicle_connector", "max_walk_m", "require_charging"],
            {
                **PARKING_INPUT,
                "garage_id": {"type": "string"},
                "vehicle_connector": {"type": "string"},
                "max_walk_m": {"type": "integer", "minimum": 0},
                "require_charging": {"type": "boolean"},
            },
        ),
        "outputSchema": _object(["case_id", "valid", "errors", "evidence_token"], {"case_id": {"type": "string"}, "valid": {"type": "boolean"}, "errors": STRING_ARRAY, "evidence_token": {"type": "string"}}),
    },
    "parking_reserve_spot": {
        "description": "Reserve the selected parking/charging spot. Pass evidence_tokens.parking_validate_choice. This mutates reservation state.",
        "inputSchema": _object(["case_id", "garage_id", "evidence_tokens"], {**PARKING_INPUT, "garage_id": {"type": "string"}, "evidence_tokens": _token_input("parking_validate_choice")}),
        "outputSchema": _object(["success", "error", "reservation_status"], {"success": {"type": "boolean"}, "error": {"type": ["string", "null"]}, "reservation_status": OPEN_OBJECT}),
    },
    "parking_get_reservation_status": {
        "description": "Refresh parking reservation status.",
        "inputSchema": _object(["case_id"], PARKING_INPUT),
        "outputSchema": _object(["case_id", "reservation_status"], {"case_id": {"type": "string"}, "reservation_status": OPEN_OBJECT}),
    },
}


def _state(state: JsonDict) -> JsonDict:
    if "daily_life_interactive_cases_v1" not in state:
        state["daily_life_interactive_cases_v1"] = {
            "evidence": {},
            "holds": {},
            "outing_status": {},
            "pickup_status": {},
            "assignment_status": {},
            "saved_layout": None,
            "parking_status": {},
            "counter": 0,
        }
    return state["daily_life_interactive_cases_v1"]


def _token(current: JsonDict, action: str) -> str:
    current["counter"] += 1
    token = f"daily-life-ev::{action}::{current['counter']}"
    current["evidence"][action] = token
    return token


def _check_case(args: JsonDict, expected: str) -> None:
    if args.get("case_id") != expected:
        raise ValueError(f"case_id_mismatch: expected {expected}")


def _known(mapping: dict[str, JsonDict], key: str) -> JsonDict:
    if key not in mapping:
        raise ValueError(f"unknown_id:{key}")
    return mapping[key]


def _rect(place: JsonDict) -> tuple[float, float, float, float]:
    x = float(place["x"])
    y = float(place["y"])
    item = next(f for f in ROOM_FURNITURE if f["item_id"] == place["item_id"])
    return x, y, x + float(item["w"]), y + float(item["h"])


def _distance(a: JsonDict, b: JsonDict) -> float:
    return ((float(a["x"]) - float(b["x"])) ** 2 + (float(a["y"]) - float(b["y"])) ** 2) ** 0.5


def _validate_room(placements: JsonDict) -> list[str]:
    required = {item["item_id"] for item in ROOM_FURNITURE}
    errors: list[str] = []
    if set(placements) != required:
        errors.append("missing_or_extra_furniture")
        return errors
    rects: dict[str, tuple[float, float, float, float]] = {}
    for item_id, placement in placements.items():
        if not isinstance(placement, dict) or "x" not in placement or "y" not in placement:
            errors.append(f"bad_placement:{item_id}")
            continue
        rects[item_id] = _rect({"item_id": item_id, **placement})
        x1, y1, x2, y2 = rects[item_id]
        if x1 < 0 or y1 < 0 or x2 > ROOM_PLAN["width_ft"] or y2 > ROOM_PLAN["height_ft"]:
            errors.append(f"out_of_bounds:{item_id}")
    for left, left_rect in rects.items():
        for right, right_rect in rects.items():
            if left >= right:
                continue
            if left_rect[0] < right_rect[2] and left_rect[2] > right_rect[0] and left_rect[1] < right_rect[3] and left_rect[3] > right_rect[1]:
                errors.append(f"collision:{left}:{right}")
    door = ROOM_PLAN["door"]
    sofa = placements.get("sofa", {})
    if isinstance(sofa, dict) and float(sofa.get("x", 99)) < door["clearance_ft"] and abs(float(sofa.get("y", 99)) - door["y"]) < 3:
        errors.append("door_clearance_blocked:sofa")
    crib = placements.get("crib", {})
    if isinstance(crib, dict) and float(crib.get("y", 99)) < ROOM_PLAN["window"]["clearance_ft"]:
        errors.append("crib_too_close_to_window")
    desk = placements.get("desk", {})
    if isinstance(desk, dict) and min(_distance(desk, outlet) for outlet in ROOM_OUTLETS) > 5:
        errors.append("desk_too_far_from_outlet")
    return errors


def _validate_event_assignments(assignments: JsonDict) -> list[str]:
    required_roles = {"food_table", "music", "crafts", "first_aid"}
    volunteers = {person["person"]: person for person in EVENT_VOLUNTEERS}
    errors = []
    if set(assignments) != required_roles:
        errors.append("missing_or_extra_assignment_roles")
    certifications = {
        "food_table": ("food_handler", "food_table_needs_food_handler"),
        "music": ("sound_setup", "music_needs_powered_stage_corner"),
        "first_aid": ("first_aid", "first_aid_station_needs_certified_volunteer"),
    }
    for role, assignment in assignments.items():
        if not isinstance(assignment, dict):
            errors.append(f"bad_assignment:{role}")
            continue
        volunteer = volunteers.get(assignment.get("person"))
        if not volunteer:
            errors.append(f"unknown_volunteer:{role}")
        if assignment.get("zone") not in EVENT_SITE["zones"]:
            errors.append(f"unknown_zone:{role}")
        if volunteer and "time_slot" in assignment and assignment["time_slot"] not in volunteer["available"]:
            errors.append(f"volunteer_unavailable:{role}")
        if role in certifications:
            certification, error = certifications[role]
            if not volunteer or certification not in volunteer["certifications"]:
                errors.append(error)
        if role == "music" and assignment.get("zone") != "stage_corner":
            errors.append("music_needs_powered_stage_corner")
        if role == "crafts" and assignment.get("zone") != EVENT_WEATHER["recommended_plan"]:
            errors.append("rain_plan_needs_covered_crafts")
    return errors


def invoke(*, tool: ToolDefinition, args: JsonDict, state: JsonDict) -> JsonDict:
    current = _state(state)
    name = tool.name

    if name.startswith("outing_"):
        _check_case(args, OUTING_CASE_ID)
        if name == "outing_search_places":
            return {"case_id": OUTING_CASE_ID, "places": copy.deepcopy(OUTING_PLACES)}
        if name == "outing_get_accessibility_detail":
            return {"case_id": OUTING_CASE_ID, "place_id": args["place_id"], "accessibility": copy.deepcopy(_known(OUTING_ACCESS, args["place_id"]))}
        if name == "outing_get_parking_live_status":
            return {"case_id": OUTING_CASE_ID, "place_id": args["place_id"], "parking": copy.deepcopy(_known(OUTING_PARKING, args["place_id"]))}
        if name == "outing_get_weather_window":
            return {"case_id": OUTING_CASE_ID, "weather": copy.deepcopy(OUTING_WEATHER)}
        if name == "outing_validate_route_plan":
            place_id = args["place_id"]
            arrival = args["arrival_time"]
            access = _known(OUTING_ACCESS, place_id)
            parking = _known(OUTING_PARKING, place_id)
            weather = _known(OUTING_WEATHER, arrival)
            errors = []
            if args["require_step_free"] and not access["step_free"]:
                errors.append("step_free_entry_missing")
            if args["require_restroom"] and not access["restroom"]:
                errors.append("accessibility_constraint_failed")
            if parking["available_spaces"] <= 0:
                errors.append("parking_unavailable")
            if parking["distance_m"] > args["max_parking_walk_m"]:
                errors.append("parking_walk_too_far")
            if weather["rain_probability"] > args["max_rain_probability"]:
                errors.append("rain_window_too_risky")
            current["outing_last_valid"] = not errors
            current["outing_selection"] = {"place_id": place_id, "arrival_time": arrival}
            return {"case_id": OUTING_CASE_ID, "valid": not errors, "errors": errors, "evidence_token": _token(current, name)}
        if name == "outing_hold_reservation":
            tokens = args["evidence_tokens"]
            if tokens.get("outing_validate_route_plan") != current["evidence"].get("outing_validate_route_plan") or not current.get("outing_last_valid"):
                return {"success": False, "error": "missing_or_invalid_route_validation", "hold": None}
            selection = {"place_id": args["place_id"], "arrival_time": args["arrival_time"]}
            if selection != current["outing_selection"]:
                return {"success": False, "error": "route_changed_after_validation", "hold": None}
            token = _token(current, name)
            hold = {"hold_id": f"outing-hold-{current['counter']}", "place_id": args["place_id"], "arrival_time": args["arrival_time"], "status": "held", "evidence_token": token}
            current["holds"][hold["hold_id"]] = hold
            current["outing_status"] = {"status": "held", "hold_id": hold["hold_id"]}
            return {"success": True, "error": None, "hold": copy.deepcopy(hold)}
        if name == "outing_confirm_reservation":
            tokens = args["evidence_tokens"]
            if tokens.get("outing_validate_route_plan") != current["evidence"].get("outing_validate_route_plan") or not current.get("outing_last_valid"):
                return {"success": False, "error": "missing_or_invalid_route_validation", "operation": {"status": "rejected"}, "reservation_status": copy.deepcopy(current["outing_status"])}
            if tokens.get("outing_hold_reservation") != current["evidence"].get("outing_hold_reservation"):
                return {"success": False, "error": "missing_or_invalid_hold_evidence", "operation": {"status": "rejected"}, "reservation_status": copy.deepcopy(current["outing_status"])}
            hold = current["holds"].get(args["hold_id"])
            if not hold:
                return {"success": False, "error": "unknown_hold_id", "operation": {"status": "rejected"}, "reservation_status": copy.deepcopy(current["outing_status"])}
            if hold["evidence_token"] != tokens["outing_hold_reservation"] or any(
                hold[key] != current["outing_selection"][key] for key in ("place_id", "arrival_time")
            ):
                return {"success": False, "error": "hold_does_not_match_current_validation", "operation": {"status": "rejected"}, "reservation_status": copy.deepcopy(current["outing_status"])}
            current["outing_status"] = {"status": "confirmed", "hold_id": args["hold_id"], "place_id": hold["place_id"], "arrival_time": hold["arrival_time"]}
            return {"success": True, "error": None, "operation": {"status": "confirmed", "authoritative": True}, "reservation_status": copy.deepcopy(current["outing_status"])}
        if name == "outing_get_reservation_status":
            return {"case_id": OUTING_CASE_ID, "reservation_status": copy.deepcopy(current["outing_status"])}

    if name.startswith("grocery_"):
        _check_case(args, GROCERY_CASE_ID)
        if name == "grocery_get_recipe_plan":
            return {"case_id": GROCERY_CASE_ID, "recipe": copy.deepcopy(GROCERY_RECIPE)}
        if name == "grocery_get_pantry_inventory":
            return {"case_id": GROCERY_CASE_ID, "pantry": copy.deepcopy(GROCERY_PANTRY)}
        if name == "grocery_search_store_stock":
            return {"case_id": GROCERY_CASE_ID, "stock": copy.deepcopy(GROCERY_STOCK)}
        if name == "grocery_get_coupon_rules":
            return {"case_id": GROCERY_CASE_ID, "coupons": copy.deepcopy(GROCERY_COUPONS)}
        if name == "grocery_validate_cart":
            by_id = {item["item_id"]: item for item in GROCERY_STOCK}
            ids = list(args["item_ids"])
            avoid_allergens = set(args["avoid_allergens"])
            errors = []
            categories = set()
            for item_id in ids:
                item = by_id.get(item_id)
                if not item:
                    errors.append(f"unknown_item:{item_id}")
                    continue
                categories.add(item["category"])
                if not item["available"]:
                    errors.append(f"unavailable:{item_id}")
                for allergen in item["allergens"]:
                    if allergen in avoid_allergens:
                        errors.append(f"allergen_{allergen}:{item_id}")
            for category in GROCERY_RECIPE["needed"]:
                if category not in categories:
                    errors.append(f"missing_category:{category}")
            coverage = [{"category": category, "covered": category in categories} for category in GROCERY_RECIPE["needed"]]
            current["grocery_last_valid"] = not errors
            current["grocery_cart"] = ids
            return {"case_id": GROCERY_CASE_ID, "valid": not errors, "errors": errors, "coverage": coverage, "evidence_token": _token(current, name)}
        if name == "grocery_reserve_pickup":
            if args["evidence_tokens"].get("grocery_validate_cart") != current["evidence"].get("grocery_validate_cart") or not current.get("grocery_last_valid"):
                return {"success": False, "error": "missing_or_invalid_cart_validation", "pickup_status": copy.deepcopy(current["pickup_status"])}
            if sorted(args["item_ids"]) != sorted(current["grocery_cart"]):
                return {"success": False, "error": "cart_changed_after_validation", "pickup_status": copy.deepcopy(current["pickup_status"])}
            current["pickup_status"] = {"status": "reserved", "pickup_window": args["pickup_window"], "item_ids": list(args["item_ids"])}
            return {"success": True, "error": None, "pickup_status": copy.deepcopy(current["pickup_status"])}
        if name == "grocery_get_pickup_status":
            return {"case_id": GROCERY_CASE_ID, "pickup_status": copy.deepcopy(current["pickup_status"])}

    if name.startswith("event_"):
        _check_case(args, EVENT_CASE_ID)
        if name == "event_get_site_map":
            return {"case_id": EVENT_CASE_ID, "site": copy.deepcopy(EVENT_SITE)}
        if name == "event_get_volunteer_availability":
            return {"case_id": EVENT_CASE_ID, "volunteers": copy.deepcopy(EVENT_VOLUNTEERS)}
        if name == "event_get_supply_inventory":
            return {"case_id": EVENT_CASE_ID, "supplies": copy.deepcopy(EVENT_SUPPLIES)}
        if name == "event_get_permit_rules":
            return {"case_id": EVENT_CASE_ID, "rules": copy.deepcopy(EVENT_SITE["constraints"])}
        if name == "event_get_weather_plan":
            return {"case_id": EVENT_CASE_ID, "weather": copy.deepcopy(EVENT_WEATHER)}
        if name == "event_validate_assignment_plan":
            assignments = args["assignments"]
            errors = _validate_event_assignments(assignments)
            current["event_last_valid"] = not errors
            current["event_assignments"] = copy.deepcopy(assignments)
            return {"case_id": EVENT_CASE_ID, "valid": not errors, "errors": errors, "evidence_token": _token(current, name)}
        if name == "event_send_assignments":
            if args["evidence_tokens"].get("event_validate_assignment_plan") != current["evidence"].get("event_validate_assignment_plan") or not current.get("event_last_valid"):
                return {"success": False, "error": "missing_or_invalid_assignment_validation", "assignment_status": copy.deepcopy(current["assignment_status"])}
            if args["assignments"] != current["event_assignments"]:
                return {"success": False, "error": "assignments_changed_after_validation", "assignment_status": copy.deepcopy(current["assignment_status"])}
            current["assignment_status"] = {"status": "sent", "assignments": copy.deepcopy(args["assignments"])}
            return {"success": True, "error": None, "assignment_status": copy.deepcopy(current["assignment_status"])}
        if name == "event_get_assignment_status":
            return {"case_id": EVENT_CASE_ID, "assignment_status": copy.deepcopy(current["assignment_status"])}

    if name.startswith("room_"):
        _check_case(args, ROOM_CASE_ID)
        if name == "room_get_floor_plan":
            return {"case_id": ROOM_CASE_ID, "floor_plan": copy.deepcopy(ROOM_PLAN)}
        if name == "room_get_furniture_catalog":
            return {"case_id": ROOM_CASE_ID, "furniture": copy.deepcopy(ROOM_FURNITURE)}
        if name == "room_get_outlet_map":
            return {"case_id": ROOM_CASE_ID, "outlets": copy.deepcopy(ROOM_OUTLETS)}
        if name == "room_get_safety_rules":
            return {"case_id": ROOM_CASE_ID, "rules": copy.deepcopy(ROOM_RULES)}
        if name == "room_validate_layout":
            errors = _validate_room(args["placements"])
            current["room_last_valid"] = not errors
            current["room_placements"] = copy.deepcopy(args["placements"])
            return {"case_id": ROOM_CASE_ID, "valid": not errors, "errors": errors, "evidence_token": _token(current, name)}
        if name == "room_save_layout":
            if args["evidence_tokens"].get("room_validate_layout") != current["evidence"].get("room_validate_layout") or not current.get("room_last_valid"):
                return {"success": False, "error": "missing_or_invalid_layout_validation", "saved_layout": current["saved_layout"]}
            if args["placements"] != current["room_placements"]:
                return {"success": False, "error": "layout_changed_after_validation", "saved_layout": copy.deepcopy(current["saved_layout"])}
            current["saved_layout"] = {"status": "saved", "placements": copy.deepcopy(args["placements"])}
            return {"success": True, "error": None, "saved_layout": copy.deepcopy(current["saved_layout"])}
        if name == "room_get_saved_layout":
            return {"case_id": ROOM_CASE_ID, "saved_layout": copy.deepcopy(current["saved_layout"])}

    if name.startswith("parking_"):
        _check_case(args, PARKING_CASE_ID)
        if name == "parking_search_garages":
            return {"case_id": PARKING_CASE_ID, "garages": copy.deepcopy(PARKING_GARAGES)}
        if name == "parking_get_live_occupancy":
            return {"case_id": PARKING_CASE_ID, "garage_id": args["garage_id"], "occupancy": copy.deepcopy(_known(PARKING_OCCUPANCY, args["garage_id"]))}
        if name == "parking_get_charger_status":
            return {"case_id": PARKING_CASE_ID, "garage_id": args["garage_id"], "charger": copy.deepcopy(_known(PARKING_CHARGERS, args["garage_id"]))}
        if name == "parking_get_walking_safety":
            return {"case_id": PARKING_CASE_ID, "garage_id": args["garage_id"], "safety": copy.deepcopy(_known(PARKING_SAFETY, args["garage_id"]))}
        if name == "parking_validate_choice":
            garage_id = args["garage_id"]
            garage = next((item for item in PARKING_GARAGES if item["garage_id"] == garage_id), None)
            if not garage:
                raise ValueError(f"unknown_id:{garage_id}")
            occupancy = _known(PARKING_OCCUPANCY, garage_id)
            charger = _known(PARKING_CHARGERS, garage_id)
            safety = _known(PARKING_SAFETY, garage_id)
            errors = []
            if occupancy["spaces"] <= 0:
                errors.append("parking_full")
            if args["require_charging"] and charger["connector"] != args["vehicle_connector"]:
                errors.append("connector_mismatch")
            if args["require_charging"] and charger["ports_available"] <= 0:
                errors.append("charger_unavailable")
            if garage["walk_m"] > args["max_walk_m"]:
                errors.append("walk_too_far")
            if safety["rating"] == "low":
                errors.append("walking_route_low_safety")
            current["parking_last_valid"] = not errors
            current["parking_choice"] = garage_id
            current["parking_connector"] = args["vehicle_connector"]
            return {"case_id": PARKING_CASE_ID, "valid": not errors, "errors": errors, "evidence_token": _token(current, name)}
        if name == "parking_reserve_spot":
            if args["evidence_tokens"].get("parking_validate_choice") != current["evidence"].get("parking_validate_choice") or not current.get("parking_last_valid"):
                return {"success": False, "error": "missing_or_invalid_parking_validation", "reservation_status": copy.deepcopy(current["parking_status"])}
            if args["garage_id"] != current["parking_choice"]:
                return {"success": False, "error": "parking_choice_changed_after_validation", "reservation_status": copy.deepcopy(current["parking_status"])}
            current["parking_status"] = {"status": "reserved", "garage_id": args["garage_id"], "connector": current.get("parking_connector")}
            return {"success": True, "error": None, "reservation_status": copy.deepcopy(current["parking_status"])}
        if name == "parking_get_reservation_status":
            return {"case_id": PARKING_CASE_ID, "reservation_status": copy.deepcopy(current["parking_status"])}

    raise ValueError(f"Unknown daily-life interactive tool: {name}")
