from __future__ import annotations

import json

from runtime.types import JsonDict


def build_actor_stable_context_payload(goal: JsonDict, extra_context: JsonDict | None) -> str:
    payload: JsonDict = {"actor_goal": goal}
    if extra_context:
        available_tools = extra_context.get("available_tools")
        if isinstance(available_tools, list) and available_tools:
            payload["available_tool_details"] = available_tools
        available_resources = extra_context.get("available_resources")
        if isinstance(available_resources, list) and available_resources:
            payload["available_resource_details"] = available_resources
        validation_contract = extra_context.get("validation_contract")
        if isinstance(validation_contract, dict) and validation_contract:
            payload["validation_contract"] = validation_contract
        fixture_policy_hints = extra_context.get("fixture_policy_hints")
        if isinstance(fixture_policy_hints, list) and fixture_policy_hints:
            payload["fixture_policy_hints"] = fixture_policy_hints
    return json.dumps(payload, ensure_ascii=False)
