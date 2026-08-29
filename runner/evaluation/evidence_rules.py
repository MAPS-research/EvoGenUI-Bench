from __future__ import annotations

from runtime.types import JsonDict

ACTOR_EVIDENCE_GAP = "actor_evidence_gap"
RUNTIME_BRIDGE_UNAVAILABLE_MARKER = "generated app did not expose __genui_get_runtime_logs__"

INFRA_ERROR_MARKERS = (
    "timed out",
    "timeout",
    "event handler",
    "invalid token",
    "json:",
    "value expected at position",
    "agent_schema_mismatch",
    "validation errors for agentoutput",
    "extra inputs are not permitted",
    "field required",
)

PASSIVE_ACTIONS = {
    "",
    "navigate",
    "wait",
    "scroll",
    "finish",
    "extract",
    "find_text",
    "inspect_interaction_affordances",
    "read_current_visible_state",
    "read_runtime_logs",
    "get_runtime_logs",
    "list_upload_fixtures",
}

PRIMARY_ACTION_LABEL_MARKERS = {
    "add",
    "analyze",
    "apply",
    "book",
    "calculate",
    "compare",
    "confirm",
    "create",
    "filter",
    "find",
    "generate",
    "import",
    "load",
    "plan",
    "recommend",
    "run",
    "save",
    "search",
    "send",
    "start",
    "submit",
    "update",
    "upload",
}


def _non_empty_strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def is_runtime_bridge_unavailable_error(value: object) -> bool:
    return RUNTIME_BRIDGE_UNAVAILABLE_MARKER in str(value or "").lower()


def evaluator_visible_console_errors(value: object) -> list[str]:
    return [item for item in _non_empty_strings(value) if not is_runtime_bridge_unavailable_error(item)]


def is_infra_only_actor_error(actor: JsonDict) -> bool:
    console_errors = actor.get("console_errors")
    if evaluator_visible_console_errors(console_errors):
        return False
    interaction_errors = [
        item.lower() for item in _non_empty_strings(actor.get("interaction_errors"))
    ]
    if not interaction_errors:
        return False
    return all(any(marker in item for marker in INFRA_ERROR_MARKERS) for item in interaction_errors)


def interaction_errors_empty_or_infra_only(actor: JsonDict) -> bool:
    interaction_errors = _non_empty_strings(actor.get("interaction_errors"))
    if not interaction_errors:
        return True
    return is_infra_only_actor_error(actor)


def actor_has_console_errors(actor: JsonDict) -> bool:
    return bool(evaluator_visible_console_errors(actor.get("console_errors")))


def actor_has_supported_verification(actor: JsonDict) -> bool:
    checks = actor.get("verification_checks")
    if not isinstance(checks, list):
        return False
    return any(isinstance(check, dict) and check.get("status") == "supported" for check in checks)


def actor_has_runtime_or_state_progress(actor: JsonDict) -> bool:
    state_diffs = actor.get("state_diffs")
    if not isinstance(state_diffs, list):
        return False
    return any(
        isinstance(item, dict)
        and (item.get("runtime_changed") is True or item.get("state_changed") is True)
        for item in state_diffs
    )


def actor_has_runtime_evidence(actor: JsonDict) -> bool:
    for key in ("tool_logs", "resource_logs", "side_effect_logs"):
        value = actor.get(key)
        if isinstance(value, list) and any(isinstance(item, dict) for item in value):
            return True
    return False


def actor_attempted_meaningful_action(actor: JsonDict) -> bool:
    steps = actor.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, dict):
                continue
            action = step.get("action")
            if not isinstance(action, dict):
                continue
            action_name = str(action.get("action") or "").strip().lower()
            if action_name not in PASSIVE_ACTIONS:
                return True
    evidence_summary = actor.get("evidence_summary")
    if not isinstance(evidence_summary, dict):
        return False
    count = evidence_summary.get("meaningful_action_count")
    return isinstance(count, int) and count > 0


def _step_label(step: object) -> str:
    if not isinstance(step, dict):
        return ""
    target = step.get("resolved_target")
    if isinstance(target, dict):
        name = str(target.get("name") or "").strip()
        if name:
            return name
    action = step.get("action")
    if isinstance(action, dict):
        for key in ("text", "value"):
            value = str(action.get(key) or "").strip()
            if value:
                return value
    return ""


def _has_primary_label_step(actor: JsonDict) -> bool:
    steps = actor.get("steps")
    if not isinstance(steps, list):
        return False
    for step in steps:
        if not isinstance(step, dict):
            continue
        action = step.get("action")
        if not isinstance(action, dict):
            continue
        action_name = str(action.get("action") or "").strip().lower()
        if action_name not in {"click", "press"}:
            continue
        label = _step_label(step).strip().lower()
        if not label or len(label) > 100:
            continue
        if any(marker in label for marker in PRIMARY_ACTION_LABEL_MARKERS):
            return True
    return False


def actor_attempted_primary_flow(actor: JsonDict) -> bool:
    return (
        actor_has_runtime_evidence(actor)
        or actor_has_supported_verification(actor)
        or actor_has_runtime_or_state_progress(actor)
        or _has_primary_label_step(actor)
    )
