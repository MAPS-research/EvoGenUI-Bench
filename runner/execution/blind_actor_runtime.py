from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

from pydantic import ValidationError

from runner.execution.actor_context import build_actor_stable_context_payload
from runner.execution.prompts import ACTOR_SYSTEM_PROMPT_EXTEND, ACTOR_TASK_PROMPT_TEMPLATE
from runner.tools.evaluation_inputs import benchmark_request
from runtime.types import JsonDict

_RUNTIME_DIR = Path(__file__).resolve().parent
DEFAULT_UPLOAD_MOCK_IMAGE = _RUNTIME_DIR / "mock_image.png"
_BROWSER_USE_SESSION_SEMAPHORE: threading.BoundedSemaphore | None = None
_DOWNLOAD_READ_DEFAULT_CHARS = 8000
_DOWNLOAD_READ_MAX_CHARS = 50000
_DOWNLOAD_READ_MAX_BYTES = 2 * 1024 * 1024

_GUIDANCE_STOP_WORDS = frozenset(
    [
        "about",
        "after",
        "before",
        "being",
        "clear",
        "each",
        "from",
        "into",
        "must",
        "that",
        "their",
        "then",
        "they",
        "this",
        "visible",
        "with",
        "within",
    ]
)


# ---------------------------------------------------------------------------
# Text / guidance utilities
# ---------------------------------------------------------------------------
def _normalize_text(value: object) -> str:
    return str(value or "").strip().lower()


def _has_pattern_match(text: str, patterns: list[str]) -> bool:
    normalized = _normalize_text(text)
    return any(pattern in normalized for pattern in patterns)


def _detect_visible_contradictions(
    *, current_request: str, observation: JsonDict | None
) -> list[str]:
    del current_request, observation
    return []


def _browser_use_max_parallel_sessions() -> int:
    raw = str(os.getenv("GENUI_BLIND_ACTOR_MAX_PARALLEL_SESSIONS", "2") or "2").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 2
    return max(value, 1)


def _browser_use_session_semaphore() -> threading.BoundedSemaphore:
    global _BROWSER_USE_SESSION_SEMAPHORE
    if _BROWSER_USE_SESSION_SEMAPHORE is None:
        _BROWSER_USE_SESSION_SEMAPHORE = threading.BoundedSemaphore(
            _browser_use_max_parallel_sessions()
        )
    return _BROWSER_USE_SESSION_SEMAPHORE


# ---------------------------------------------------------------------------
# JSON parse helpers
# ---------------------------------------------------------------------------
def _parse_actor_response_content(content: object, attempt: int = 1) -> JsonDict:
    raw = str(content or "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as first_error:
        # Try stripping markdown fences
        fenced = re.sub(r"^```json\s*", "", raw)
        fenced = re.sub(r"\s*```$", "", fenced).strip()
        if fenced != raw:
            try:
                return json.loads(fenced)
            except json.JSONDecodeError:
                pass
        # Try extracting first { ... } block
        match = re.search(r"\{[\s\S]*\}", raw)
        if match and match[0] != raw:
            try:
                return json.loads(match[0])
            except json.JSONDecodeError:
                pass
        if attempt <= 2:
            raise first_error
        raise first_error


# ---------------------------------------------------------------------------
# Token usage helpers
# ---------------------------------------------------------------------------
def _empty_token_usage() -> JsonDict:
    return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "calls": 0}


def _normalize_token_usage(value: object, default_calls: int = 0) -> JsonDict:
    if not isinstance(value, dict):
        return _empty_token_usage()

    def _to_int(raw: object) -> int:
        parsed = float(raw or 0)
        if not (float("-inf") < parsed < float("inf")) or parsed < 0:
            return 0
        return int(parsed)

    input_tokens = _to_int(value.get("input_tokens")) or _to_int(value.get("prompt_tokens"))
    output_tokens = _to_int(value.get("output_tokens")) or _to_int(value.get("completion_tokens"))
    total_tokens = _to_int(value.get("total_tokens")) or input_tokens + output_tokens
    cached_input_tokens = (
        _to_int(value.get("input_tokens_details", {}).get("cached_tokens"))
        or _to_int(value.get("prompt_tokens_details", {}).get("cached_tokens"))
        or _to_int(value.get("cached_input_tokens"))
    )
    reasoning_tokens = (
        _to_int(value.get("output_tokens_details", {}).get("reasoning_tokens"))
        or _to_int(value.get("completion_tokens_details", {}).get("reasoning_tokens"))
        or _to_int(value.get("reasoning_tokens"))
    )
    calls = _to_int(value.get("calls"))
    if calls == 0 and total_tokens > 0:
        calls = max(default_calls, 1)

    normalized: JsonDict = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "calls": calls,
    }
    if cached_input_tokens > 0:
        normalized["cached_input_tokens"] = cached_input_tokens
        normalized["uncached_input_tokens"] = max(input_tokens - cached_input_tokens, 0)
    if reasoning_tokens > 0:
        normalized["reasoning_tokens"] = reasoning_tokens
    return normalized


def _add_token_usage(left: JsonDict, right: JsonDict) -> JsonDict:
    nl = _normalize_token_usage(left)
    nr = _normalize_token_usage(right)
    merged: JsonDict = {
        "input_tokens": nl["input_tokens"] + nr["input_tokens"],
        "output_tokens": nl["output_tokens"] + nr["output_tokens"],
        "total_tokens": nl["total_tokens"] + nr["total_tokens"],
        "calls": nl["calls"] + nr["calls"],
    }
    cached = (nl.get("cached_input_tokens") or 0) + (nr.get("cached_input_tokens") or 0)
    if cached > 0:
        merged["cached_input_tokens"] = cached
        merged["uncached_input_tokens"] = max(merged["input_tokens"] - cached, 0)
    reasoning = (nl.get("reasoning_tokens") or 0) + (nr.get("reasoning_tokens") or 0)
    if reasoning > 0:
        merged["reasoning_tokens"] = reasoning
    return merged


def _validate_runtime_logs(payload: object) -> JsonDict:
    if not isinstance(payload, dict):
        raise ValueError("Runtime logs must be an object")

    required_keys = {
        "tool_logs": list,
        "resource_logs": list,
        "side_effect_logs": list,
        "scenarios": dict,
    }
    for key, expected_type in required_keys.items():
        if key not in payload:
            raise ValueError(f"Runtime logs missing required key: {key}")
        if not isinstance(payload[key], expected_type):
            raise ValueError(f"Runtime logs key '{key}' must be {expected_type.__name__}")
    confirmation_events = payload.get("confirmation_events", [])
    if not isinstance(confirmation_events, list):
        raise ValueError("Runtime logs key 'confirmation_events' must be list")

    return payload


def _observation_snapshot(observation: JsonDict, phase: str) -> JsonDict:
    return {
        "step": observation["step"],
        "phase": phase,
        "url": observation["url"],
        "visible_text": observation["finalText"],
        "dom_tree": observation["domTree"],
        "ax_tree": observation["axTree"],
        "elements": observation["elements"],
        "actionable_elements": observation.get("actionableElements", []),
        "affordance_facts": observation.get("affordanceFacts", {}),
        "screenshot": observation["screenshotPath"],
        "screenshot_mode": observation["screenshotMode"],
        "console_errors": observation["consoleErrors"],
        "runtime_logs": observation["runtimeLogs"],
    }


# ---------------------------------------------------------------------------
# Guidance / goal result tracking
# ---------------------------------------------------------------------------
def _compact_runtime_tool_calls(runtime_logs: JsonDict, *, limit: int = 5) -> list[JsonDict]:
    if not isinstance(runtime_logs, dict):
        return []
    tool_logs = runtime_logs.get("tool_logs")
    if not isinstance(tool_logs, list):
        return []
    recent = tool_logs[-limit:]
    result: list[JsonDict] = []
    for item in recent:
        if not isinstance(item, dict):
            continue
        result.append({"name": item.get("name"), "args": item.get("args")})
    return result


def _observation_from_probe(probe: JsonDict, runtime_logs: JsonDict) -> JsonDict:
    return {
        "url": probe.get("url"),
        "finalText": probe.get("text") or "",
        "elements": probe.get("elements") or [],
        "runtimeLogs": runtime_logs,
    }


def _diagnose_state_change(
    *,
    policy_state: EvidencePolicyState,
    probe: JsonDict,
    runtime_logs: JsonDict,
) -> JsonDict:
    current = _observation_from_probe(probe, runtime_logs)
    previous = policy_state.latest_observation
    current_state_hash = _state_hash(current)
    current_runtime_signature = _normalize_runtime_log_signature(runtime_logs)
    previous_state_hash = _state_hash(previous) if isinstance(previous, dict) else ""
    previous_runtime_signature = (
        _normalize_runtime_log_signature(previous.get("runtimeLogs", {}))
        if isinstance(previous, dict)
        else ""
    )
    visible_changed = current_state_hash != previous_state_hash
    runtime_changed = current_runtime_signature != previous_runtime_signature
    classification = _progress_classification(
        state_changed=visible_changed,
        runtime_changed=runtime_changed,
    )
    if classification == "runtime_only_changed":
        summary = (
            "Runtime state changed since the previous step, but the visible UI did not. "
            "This suggests a UI sync or rendering defect rather than a failed click."
        )
    elif classification == "visible_and_runtime_changed":
        summary = "Both runtime state and the visible UI changed since the previous step."
    elif classification == "visible_only_changed":
        summary = "The visible UI changed, but runtime logs did not materially change since the previous step."
    else:
        summary = "Neither the visible UI nor runtime logs changed since the previous step."
    return {
        "summary": summary,
        "classification": classification,
        "visible_changed": visible_changed,
        "runtime_changed": runtime_changed,
        "recent_tool_calls": _compact_runtime_tool_calls(runtime_logs),
    }


def _normalized_action_signature(action: JsonDict) -> str:
    if not isinstance(action, dict):
        return ""
    return json.dumps(
        {
            "action": action.get("action"),
            "index": action.get("index"),
            "ref": action.get("ref"),
            "value": action.get("value"),
            "text": action.get("text"),
            "key": action.get("key"),
            "direction": action.get("direction"),
            "x": action.get("x"),
            "y": action.get("y"),
            "x_pct": action.get("x_pct"),
            "y_pct": action.get("y_pct"),
            "source_index": action.get("source_index"),
            "target_index": action.get("target_index"),
            "source_ref": action.get("source_ref"),
            "target_ref": action.get("target_ref"),
            "target_x_pct": action.get("target_x_pct"),
            "target_y_pct": action.get("target_y_pct"),
        },
        sort_keys=True,
    )


def _normalize_runtime_log_signature(runtime_logs: JsonDict) -> str:
    if not isinstance(runtime_logs, dict):
        return ""
    tool_logs = runtime_logs["tool_logs"]
    resource_logs = runtime_logs["resource_logs"]
    side_effect_logs = runtime_logs["side_effect_logs"]
    confirmation_events = runtime_logs.get("confirmation_events", [])
    return json.dumps(
        {
            "tool_logs": [
                {"name": item.get("name"), "args": item.get("args")}
                for item in (tool_logs if isinstance(tool_logs, list) else [])
            ],
            "resource_logs": [
                {"uri": item.get("uri")}
                for item in (resource_logs if isinstance(resource_logs, list) else [])
            ],
            "side_effect_logs": (
                list(side_effect_logs) if isinstance(side_effect_logs, list) else []
            ),
            "confirmation_events": (
                list(confirmation_events) if isinstance(confirmation_events, list) else []
            ),
        },
        sort_keys=True,
    )


def _recent_duplicate_action(history: list[JsonDict], action: JsonDict) -> bool:
    signature = _normalized_action_signature(action)
    if not signature:
        return False
    for offset in range(len(history) - 1, -1, -1):
        step = history[offset]
        prior_action = step.get("action")
        if not isinstance(prior_action, dict):
            continue
        if _normalized_action_signature(prior_action) != signature:
            continue
        intervening = history[offset + 1 :]
        had_progress = any(
            isinstance(item.get("result"), dict)
            and (
                item["result"].get("state_changed") is True
                or item["result"].get("runtime_changed") is True
            )
            for item in intervening
        )
        return not had_progress
    return False


def _observation_elements(observation: JsonDict, key: str) -> list[JsonDict]:
    raw = observation.get(key)
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _observation_has_runtime_activity(observation: JsonDict | None) -> bool:
    if not isinstance(observation, dict):
        return False
    runtime_logs = observation.get("runtimeLogs")
    if not isinstance(runtime_logs, dict):
        return False
    for key in ("tool_logs", "resource_logs", "side_effect_logs", "confirmation_events"):
        entries = runtime_logs.get(key)
        if isinstance(entries, list) and entries:
            return True
    return False


def _progress_classification(*, state_changed: bool, runtime_changed: bool) -> str:
    if state_changed and runtime_changed:
        return "visible_and_runtime_changed"
    if state_changed:
        return "visible_only_changed"
    if runtime_changed:
        return "runtime_only_changed"
    return "no_visible_or_runtime_change"


def _recent_runtime_only_pattern(policy_state: EvidencePolicyState, *, window: int = 8) -> bool:
    recent = policy_state.progress_history[-window:]
    if not recent or "runtime_only_changed" not in recent:
        return False
    last_runtime_only = max(
        index for index, item in enumerate(recent) if item == "runtime_only_changed"
    )
    tail = recent[last_runtime_only + 1 :]
    return not any(item in ("visible_and_runtime_changed", "visible_only_changed") for item in tail)


def _interaction_errors_indicate_invalid_actor_output(errors: list[str]) -> bool:
    markers = (
        "agent_schema_mismatch",
        "validation errors for agentoutput",
        "extra inputs are not permitted",
    )
    for error in errors:
        normalized = _normalize_text(error)
        if any(marker in normalized for marker in markers):
            return True
    return False


def _is_verification_like_step(step: JsonDict) -> bool:
    if not isinstance(step, dict):
        return False
    action = step.get("action")
    if not isinstance(action, dict):
        return False
    action_name = _normalize_text(action.get("action"))
    return action_name in {
        "extract",
        "inspect_interaction_affordances",
        "read_current_visible_state",
        "list_downloaded_files",
        "read_downloaded_file",
        "read_runtime_logs",
        "find_text",
    }


def _step_target_label(step: JsonDict) -> str:
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


def _is_persist_submit_like_step(step: JsonDict) -> bool:
    if not isinstance(step, dict):
        return False
    action = step.get("action")
    if not isinstance(action, dict):
        return False
    action_name = _normalize_text(action.get("action"))
    if action_name not in {"click", "press"}:
        return False
    target = step.get("resolved_target")
    if isinstance(target, dict):
        role = _normalize_text(target.get("role"))
        if role and role not in {"button", "link"}:
            return False
    normalized = _normalize_text(_step_target_label(step))
    if not normalized or len(normalized) > 80:
        return False
    persist_phrases = {
        "submit",
        "save",
        "save updates",
        "confirm",
        "confirm order",
        "confirm booking",
        "update application",
        "update claim",
        "mark completed",
        "mark complete",
        "uncheck",
    }
    if normalized in persist_phrases:
        return True
    return any(normalized.startswith(f"{phrase} ") for phrase in persist_phrases)


def _is_query_refine_like_step(step: JsonDict) -> bool:
    if not isinstance(step, dict):
        return False
    action = step.get("action")
    if not isinstance(action, dict):
        return False
    action_name = _normalize_text(action.get("action"))
    if action_name not in {"click", "press"}:
        return False
    normalized = _normalize_text(_step_target_label(step))
    if not normalized or len(normalized) > 80:
        return False
    query_markers = (
        "apply filter",
        "apply filters",
        "search",
        "recompute",
        "shortlist",
        "sort",
        "compare",
    )
    return any(marker in normalized for marker in query_markers)


def _is_interactive_step(step: JsonDict) -> bool:
    if not isinstance(step, dict):
        return False
    action = step.get("action")
    if not isinstance(action, dict):
        return False
    action_name = _normalize_text(action.get("action"))
    return action_name not in {
        "",
        "navigate",
        "wait",
        "scroll",
        "finish",
        "extract",
        "inspect_interaction_affordances",
        "read_current_visible_state",
        "read_runtime_logs",
        "list_downloaded_files",
        "read_downloaded_file",
        "find_text",
        "get_runtime_logs",
        "list_upload_fixtures",
    }


def _is_meaningful_step(step: JsonDict) -> bool:
    if not isinstance(step, dict):
        return False
    action = step.get("action")
    result = step.get("result")
    if not isinstance(action, dict) or not isinstance(result, dict):
        return False
    action_name = _normalize_text(action.get("action"))
    if action_name in {"navigate", "wait", "scroll", "finish"}:
        return False
    if _is_verification_like_step(step):
        return True
    return result.get("state_changed") is True or result.get("runtime_changed") is True


def _meaningful_action_count(steps: list[JsonDict] | None) -> int:
    if not isinstance(steps, list):
        return 0
    return sum(1 for step in steps if _is_meaningful_step(step))


def _interactive_action_count(steps: list[JsonDict] | None) -> int:
    if not isinstance(steps, list):
        return 0
    return sum(1 for step in steps if _is_interactive_step(step))


def _verification_step_count(steps: list[JsonDict] | None) -> int:
    if not isinstance(steps, list):
        return 0
    return sum(1 for step in steps if _is_verification_like_step(step))


def _has_explicit_on_load_verification(steps: list[JsonDict] | None) -> bool:
    if not isinstance(steps, list):
        return False
    return any(_is_verification_like_step(step) for step in steps)


def _terminal_finish_text(steps: list[JsonDict] | None) -> str:
    if not isinstance(steps, list) or not steps:
        return ""
    action = steps[-1].get("action")
    if not isinstance(action, dict):
        return ""
    if _normalize_text(action.get("action")) != "finish":
        return ""
    return str(action.get("text") or "").strip()


def _has_substantive_finish_summary(steps: list[JsonDict] | None) -> bool:
    text = _terminal_finish_text(steps)
    if len(text) < 120:
        return False
    token_markers = ("1.", "2.", "3.", "evidence", "shows", "verified", "updated", "selected")
    normalized = _normalize_text(text)
    return any(marker in normalized for marker in token_markers)


def _observation_has_form_like_primary_flow(observation: JsonDict | None) -> bool:
    if not isinstance(observation, dict):
        return False
    elements = _observation_elements(observation, "actionableElements")
    if not elements:
        elements = _observation_elements(observation, "elements")
    if not elements:
        return False
    input_roles = {"textbox", "searchbox", "combobox", "spinbutton", "checkbox", "radio"}
    action_roles = {"button", "link"}
    has_input = any(
        _normalize_text(element.get("role")) in input_roles and not bool(element.get("disabled"))
        for element in elements
    )
    has_action = any(
        _normalize_text(element.get("role")) in action_roles and not bool(element.get("disabled"))
        for element in elements
    )
    return has_input and has_action


def _observation_has_forward_progress_action(
    observation: JsonDict | None, steps: list[JsonDict]
) -> bool:
    if not isinstance(observation, dict):
        return False
    last_label = _normalize_text(_step_target_label(steps[-1])) if steps else ""
    forward_markers = ("next", "continue", "proceed", "advance")
    elements = _observation_elements(observation, "actionableElements")
    if not elements:
        elements = _observation_elements(observation, "elements")
    for element in elements:
        role = _normalize_text(element.get("role"))
        if role not in {"button", "link"}:
            continue
        if bool(element.get("disabled")):
            continue
        label = _normalize_text(element.get("name"))
        if not label or label == last_label:
            continue
        if any(marker in label for marker in forward_markers):
            return True
    return False


def _observation_has_unresolved_atomic_affordance(observation: JsonDict | None) -> bool:
    if not isinstance(observation, dict):
        return False
    facts = observation.get("affordanceFacts")
    if not isinstance(facts, dict):
        facts = observation.get("affordance_facts")
    if isinstance(facts, dict):
        summary = facts.get("summary")
        if isinstance(summary, dict):
            try:
                if int(summary.get("unfilled_select_count") or 0) > 0:
                    return True
            except (TypeError, ValueError):
                pass
        selects = facts.get("selects")
        if isinstance(selects, list):
            for item in selects:
                if not isinstance(item, dict):
                    continue
                if bool(item.get("disabled")):
                    continue
                if bool(item.get("is_unfilled")):
                    return True
    elements = _observation_elements(observation, "elements")
    if not elements:
        elements = _observation_elements(observation, "actionableElements")
    input_roles = {"textbox", "searchbox", "combobox", "spinbutton", "checkbox", "radio"}
    for element in elements:
        if bool(element.get("disabled")):
            continue
        role = _normalize_text(element.get("role"))
        if role not in input_roles:
            continue
        value = _normalize_text(element.get("value"))
        checked = element.get("checked")
        if role in {"checkbox", "radio"}:
            if checked is None:
                return True
            continue
        if not value:
            return True
    return False


def _finish_success_allowed(
    *,
    current_request: str = "",
    action_count: int,
    observation: JsonDict | None = None,
    steps: list[JsonDict] | None = None,
) -> bool:
    del current_request
    if steps and _recent_submit_lacks_outcome_evidence(steps):
        return False
    if steps and not _recent_submit_has_canonical_follow_up(steps):
        return False
    interactive = _interactive_action_count(steps)
    meaningful = _meaningful_action_count(steps)
    if interactive > 0 and meaningful > 0:
        return True
    if (
        interactive == 0
        and _observation_has_form_like_primary_flow(observation)
        and not _has_substantive_finish_summary(steps)
    ):
        return False
    if action_count > 0 and (
        _has_explicit_on_load_verification(steps) or _has_substantive_finish_summary(steps)
    ):
        return True
    return bool(
        _observation_has_runtime_activity(observation) and _has_explicit_on_load_verification(steps)
    )


def _budget_exit_status(
    policy_state: EvidencePolicyState,
    *,
    steps: list[JsonDict] | None = None,
) -> str:
    return (
        "success"
        if _finish_success_allowed(
            current_request=policy_state.current_request,
            action_count=policy_state.action_count,
            observation=policy_state.latest_observation,
            steps=steps,
        )
        else "budget_exhausted"
    )


def _actor_outcome_from_status(status: str) -> str:
    if status == "success":
        return "success"
    if status in {"stuck", "error", "timeout", "budget_exhausted"}:
        return "failure"
    return "inconclusive"


def _actor_error_categories(errors: list[str]) -> list[str]:
    categories: set[str] = set()
    for error in errors:
        normalized = str(error or "").lower()
        if not normalized:
            continue
        if (
            "agent_schema_mismatch" in normalized
            or "validation errors for agentoutput" in normalized
            or "extra inputs are not permitted" in normalized
            or "unsupported browser-use action" in normalized
            or "field required" in normalized
        ):
            categories.add("actor_schema_mismatch")
        if "cdp request" in normalized or "dom_tree" in normalized or "ax_tree" in normalized:
            categories.add("browser_state_timeout")
        if "screenshot" in normalized and ("timed out" in normalized or "timeout" in normalized):
            categories.add("browser_state_timeout")
        if (
            "event bus" in normalized
            or "different loop" in normalized
            or "browser session cleanup" in normalized
            or "final recovery capture" in normalized
        ):
            categories.add("browser_cleanup_error")
        if "timed out" in normalized or "timeout" in normalized:
            categories.add("timeout")
        if "connection error" in normalized or "connection reset" in normalized:
            categories.add("connection_error")
        if _is_runtime_bridge_unavailable_error(normalized):
            categories.add("runtime_bridge_unavailable")
        elif "javascript evaluation failed" in normalized or "uncaught" in normalized:
            categories.add("console_error")
    return sorted(categories)


def _blank_final_ui(final_text: object, final_elements: object) -> bool:
    if final_elements is None:
        return False
    if str(final_text or "").strip():
        return False
    return not (isinstance(final_elements, list) and final_elements)


def _actor_termination_reason(
    *,
    final_status: str,
    browser_status: str,
    policy_state: EvidencePolicyState,
    reached_step_budget: bool,
    terminal_finish_status: str | None,
    interaction_errors: list[str],
    console_errors: list[str] | None = None,
    final_text: object = "",
    final_elements: object = None,
) -> str:
    if final_status == "success":
        return (
            "actor_reported_success"
            if terminal_finish_status == "success"
            else "evidence_sufficient"
        )
    categories = set(_actor_error_categories(interaction_errors + list(console_errors or [])))
    rationale = str(policy_state.stop_rationale or "").lower()
    if "actor_schema_mismatch" in categories:
        return "actor_schema_mismatch"
    if browser_status == "timeout":
        return "browser_use_timeout"
    if browser_status == "error":
        return "browser_use_error"
    if _blank_final_ui(final_text, final_elements):
        if "console_error" in categories or "runtime_bridge_unavailable" in categories:
            return "blank_ui_with_console_error"
        return "blank_ui_after_navigation"
    if reached_step_budget:
        return "step_budget_exhausted"
    if "submit/change action was attempted before any meaningful" in rationale:
        return "premature_submit"
    if "repeated the same action" in rationale:
        return "duplicate_action_no_progress"
    if "state and runtime logs stopped changing" in rationale:
        return "no_state_or_runtime_progress"
    if "runtime state changed, but the visible ui did not reflect" in rationale:
        return "runtime_visible_state_mismatch"
    if terminal_finish_status in {"stuck", "error"}:
        return f"actor_reported_{terminal_finish_status}"
    if final_status == "budget_exhausted":
        return "step_budget_exhausted"
    return f"{final_status}_unknown"


def _build_actor_diagnostics(
    *,
    final_status: str,
    browser_status: str,
    policy_state: EvidencePolicyState,
    reached_step_budget: bool,
    terminal_finish_status: str | None,
    interaction_errors: list[str],
    console_errors: list[str] | None = None,
    final_text: object = "",
    final_elements: object = None,
    schema_mismatch_count: int,
    schema_repair_count: int,
) -> JsonDict:
    combined_errors = interaction_errors + list(console_errors or [])
    categories = _actor_error_categories(combined_errors)
    blank_final_ui = _blank_final_ui(final_text, final_elements)
    if blank_final_ui:
        categories = sorted({*categories, "blank_final_ui"})
    termination_reason = _actor_termination_reason(
        final_status=final_status,
        browser_status=browser_status,
        policy_state=policy_state,
        reached_step_budget=reached_step_budget,
        terminal_finish_status=terminal_finish_status,
        interaction_errors=interaction_errors,
        console_errors=console_errors,
        final_text=final_text,
        final_elements=final_elements,
    )
    retry_recommended = final_status != "success" and (
        browser_status in {"timeout", "error"}
        or "timeout" in categories
        or "connection_error" in categories
        or "browser_state_timeout" in categories
        or "browser_cleanup_error" in categories
        or (blank_final_ui and "console_error" not in categories)
    )
    return {
        "browser_status": browser_status,
        "termination_reason": termination_reason,
        "terminal_finish_status": terminal_finish_status,
        "policy_stop_status": policy_state.stop_status,
        "policy_stop_rationale": policy_state.stop_rationale,
        "reached_step_budget": reached_step_budget,
        "action_count": policy_state.action_count,
        "stuck_count": policy_state.stuck_count,
        "schema_mismatch_count": schema_mismatch_count,
        "schema_repair_count": schema_repair_count,
        "interaction_error_count": len(interaction_errors),
        "console_error_count": len(console_errors or []),
        "blank_final_ui": blank_final_ui,
        "infra_error_categories": categories,
        "retry_recommended": retry_recommended,
    }


def _truncate_excerpt(value: object, *, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _result_excerpt(result: JsonDict | None, *, limit: int = 240) -> str:
    if not isinstance(result, dict):
        return ""
    return _truncate_excerpt(result.get("extracted_content") or "", limit=limit)


def _runtime_log_delta_summary(before: JsonDict | None, after: JsonDict | None) -> str:
    before_logs = before if isinstance(before, dict) else {}
    after_logs = after if isinstance(after, dict) else {}
    pieces: list[str] = []
    for key, label in (
        ("tool_logs", "tool"),
        ("resource_logs", "resource"),
        ("side_effect_logs", "side effect"),
        ("confirmation_events", "confirmation"),
    ):
        before_count = (
            len(before_logs.get(key, [])) if isinstance(before_logs.get(key), list) else 0
        )
        after_count = len(after_logs.get(key, [])) if isinstance(after_logs.get(key), list) else 0
        delta = after_count - before_count
        if delta > 0:
            pieces.append(f"{delta} new {label} log{'s' if delta != 1 else ''}")
    return ", ".join(pieces) if pieces else "No new runtime log entries."


def _step_range(start: int, end: int | None = None) -> list[int]:
    if end is None or end <= start:
        return [start, start]
    return [start, end]


def _verification_kind(step: JsonDict) -> str:
    action = step.get("action") if isinstance(step, dict) else {}
    action_name = _normalize_text(action.get("action") if isinstance(action, dict) else "")
    if action_name in {"read_current_visible_state", "find_text", "extract"}:
        return "visibility_check"
    if action_name in {"get_runtime_logs", "read_runtime_logs"}:
        return "runtime_check"
    if action_name in {"list_downloaded_files", "read_downloaded_file"}:
        return "download_check"
    if _is_persist_submit_like_step(step):
        return "submit_check"
    if _is_query_refine_like_step(step):
        return "query_check"
    if _is_mutation_like_step(step):
        return "mutation_check"
    if _is_selection_like_step(step):
        return "selection_check"
    return "on_load_check"


def _verification_target(step: JsonDict) -> str:
    target = step.get("resolved_target") if isinstance(step, dict) else None
    if isinstance(target, dict):
        name = str(target.get("name") or "").strip()
        role = str(target.get("role") or "").strip()
        if name and role:
            return f"{role}: {name}"
        if name:
            return name
    label = _step_target_label(step)
    if label:
        return label
    action = step.get("action") if isinstance(step, dict) else None
    if isinstance(action, dict):
        action_name = str(action.get("action") or "").strip()
        if action_name:
            return action_name
    return "page state"


def _step_observation(observations: list[JsonDict], step: int, phase: str) -> JsonDict | None:
    for item in observations:
        if item.get("step") == step and item.get("phase") == phase:
            return item
    return None


def _build_state_diffs(steps: list[JsonDict], observations: list[JsonDict]) -> list[JsonDict]:
    diffs: list[JsonDict] = []
    for step in steps:
        if not _is_meaningful_step(step):
            continue
        step_number = int(step.get("step") or 0)
        before = _step_observation(observations, step_number, "before_action")
        after = _step_observation(observations, step_number, "after_action")
        result = step.get("result") if isinstance(step.get("result"), dict) else {}
        action = step.get("action") if isinstance(step.get("action"), dict) else {}
        diffs.append(
            {
                "step": step_number,
                "action": {
                    "action": action.get("action"),
                    "index": action.get("index"),
                    "ref": action.get("ref"),
                    "text": action.get("text"),
                    "value": action.get("value"),
                    "status": action.get("status"),
                    "source_index": action.get("source_index"),
                    "target_index": action.get("target_index"),
                    "source_ref": action.get("source_ref"),
                    "target_ref": action.get("target_ref"),
                    "x_pct": action.get("x_pct"),
                    "y_pct": action.get("y_pct"),
                    "target_x_pct": action.get("target_x_pct"),
                    "target_y_pct": action.get("target_y_pct"),
                },
                "progress_classification": result.get("progress_classification", "unknown"),
                "state_changed": bool(result.get("state_changed") is True),
                "runtime_changed": bool(result.get("runtime_changed") is True),
                "visible_text_before_excerpt": _truncate_excerpt(
                    before.get("visible_text") if isinstance(before, dict) else ""
                ),
                "visible_text_after_excerpt": _truncate_excerpt(
                    after.get("visible_text") if isinstance(after, dict) else ""
                ),
                "result_excerpt": _result_excerpt(result),
                "runtime_log_delta_summary": _runtime_log_delta_summary(
                    before.get("runtime_logs") if isinstance(before, dict) else {},
                    after.get("runtime_logs") if isinstance(after, dict) else {},
                ),
            }
        )
    return diffs


_VISUAL_SURFACE_TERMS = (
    "canvas",
    "svg",
    "diagram",
    "chart",
    "graph",
    "map",
    "timeline",
    "grid",
    "flow",
    "path",
    "connector",
    "edge",
    "node",
    "overlay",
    "gauge",
    "readout",
    "simulation",
    "animation",
)

_VISUAL_ISSUE_TERMS = (
    "visual issue",
    "ui issue",
    "broken visual",
    "broken layout",
    "unreadable",
    "overlap",
    "clipped",
    "misaligned",
    "stale",
    "disconnected",
    "contradict",
    "warning persists",
    "impossible",
    "residual",
)

_NEGATED_VISUAL_ISSUE_RE = re.compile(
    r"\b(?:no|not|without|did not|do not|does not|cannot|can't)\b"
    r"[^.]{0,100}"
    r"\b(?:visual issue|ui issue|broken|unreadable|overlap|clipped|misaligned|stale|"
    r"disconnected|contradict|warning persists|impossible|residual|crowd|collid)\b",
    re.IGNORECASE,
)

_HYPOTHETICAL_VISUAL_ISSUE_PHRASES = (
    "if seen",
    "if observed",
    "if any",
    "unless a contradiction",
    "mention any visual issue",
    "mention any visual issues",
)


def _observation_has_visual_surface(observation: JsonDict | None) -> bool:
    if not isinstance(observation, dict):
        return False
    facts = observation.get("affordance_facts")
    if isinstance(facts, dict):
        summary = facts.get("summary")
        if isinstance(summary, dict):
            if _safe_positive_int(summary.get("canvas_svg_count")):
                return True
            if _safe_positive_int(summary.get("visual_target_count")):
                return True
        visual = facts.get("visual")
        if isinstance(visual, dict):
            regions = visual.get("regions")
            targets = visual.get("targets")
            if isinstance(regions, list) and regions:
                return True
            if isinstance(targets, list) and targets:
                return True
    visible_text = _normalize_text(observation.get("visible_text"))
    dom_tree = _normalize_text(observation.get("dom_tree"))
    ax_tree = _normalize_text(observation.get("ax_tree"))
    combined = f"{visible_text} {dom_tree} {ax_tree}"
    return any(term in combined for term in _VISUAL_SURFACE_TERMS)


def _safe_positive_int(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value > 0
    if isinstance(value, float):
        return value > 0
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip()) > 0
    return False


def _build_visual_process_screenshots(
    steps: list[JsonDict], observations: list[JsonDict], *, limit: int = 5
) -> list[JsonDict]:
    screenshots: list[JsonDict] = []
    seen: set[str] = set()
    for step in steps:
        if not _is_meaningful_step(step):
            continue
        step_number = int(step.get("step") or 0)
        after = _step_observation(observations, step_number, "after_action")
        if not _observation_has_visual_surface(after):
            continue
        screenshot = after.get("screenshot") if isinstance(after, dict) else None
        if not isinstance(screenshot, str) or not screenshot or screenshot in seen:
            continue
        action = step.get("action") if isinstance(step.get("action"), dict) else {}
        result = step.get("result") if isinstance(step.get("result"), dict) else {}
        screenshots.append(
            {
                "step": step_number,
                "phase": "after_action",
                "screenshot": screenshot,
                "action": {
                    "action": action.get("action"),
                    "index": action.get("index"),
                    "ref": action.get("ref"),
                    "text": action.get("text"),
                    "value": action.get("value"),
                },
                "reason": (
                    "process visual after meaningful interaction; "
                    f"progress={result.get('progress_classification', 'unknown')}"
                ),
                "visible_text_excerpt": _truncate_excerpt(after.get("visible_text", "")),
            }
        )
        seen.add(screenshot)
        if len(screenshots) >= limit:
            break
    return screenshots


def _first_visual_issue_sentence(text: str) -> str:
    sentences = [part.strip() for part in text.replace("\n", " ").split(".")]
    for sentence in sentences:
        lowered = sentence.lower()
        if _NEGATED_VISUAL_ISSUE_RE.search(lowered):
            continue
        if any(phrase in lowered for phrase in _HYPOTHETICAL_VISUAL_ISSUE_PHRASES):
            continue
        if any(term in lowered for term in _VISUAL_ISSUE_TERMS):
            return sentence[:500]
    return ""


def _iter_actor_model_observation_text(value: object) -> Iterator[str]:
    if isinstance(value, str):
        text = value.strip()
        if text:
            yield text
        return
    if isinstance(value, dict):
        for key in (
            "evaluation_previous_goal",
            "memory",
            "thinking",
            "next_goal",
        ):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                yield item.strip()
        return
    if isinstance(value, list):
        for item in value:
            yield from _iter_actor_model_observation_text(item)


def _visual_issue_sentences_from_actor_history(
    raw_history: JsonDict,
    *,
    limit: int = 3,
) -> list[str]:
    notes: list[str] = []
    seen: set[str] = set()
    for history_field in ("model_outputs", "model_thoughts"):
        for text in _iter_actor_model_observation_text(raw_history.get(history_field)):
            note = _first_visual_issue_sentence(text)
            if not note:
                continue
            key = note.lower()
            if key in seen:
                continue
            notes.append(note)
            seen.add(key)
            if len(notes) >= limit:
                return notes
    return notes


def _build_visual_quality_findings(
    *,
    finish_basis: str,
    visual_process_screenshots: list[JsonDict],
    actor_history_issue_notes: list[str] | None = None,
) -> list[JsonDict]:
    screenshot = ""
    if visual_process_screenshots:
        screenshot = str(visual_process_screenshots[-1].get("screenshot") or "")
    findings: list[JsonDict] = []
    seen: set[str] = set()

    def add_finding(note: str, *, source: str) -> None:
        key = note.lower()
        if not note or key in seen:
            return
        finding: JsonDict = {
            "severity": "suspicious",
            "source": source,
            "note": note[:500],
        }
        if screenshot:
            finding["screenshot"] = screenshot
        findings.append(finding)
        seen.add(key)

    add_finding(_first_visual_issue_sentence(finish_basis), source="actor_finish_basis")
    for note in actor_history_issue_notes or []:
        add_finding(note, source="actor_model_observation")
    return findings


def _merge_visual_issue_notes_into_finish_basis(
    finish_basis: str,
    actor_history_issue_notes: list[str],
) -> str:
    missing_notes = [
        note
        for note in actor_history_issue_notes
        if note and note.lower() not in finish_basis.lower()
    ]
    if not missing_notes:
        return finish_basis
    suffix = " ".join(f"VISUAL ISSUE: {note}." for note in missing_notes)
    if not finish_basis:
        return suffix
    return f"{finish_basis.rstrip()} {suffix}"


def _terminal_finish_step(steps: list[JsonDict]) -> JsonDict | None:
    for step in reversed(steps):
        action = step.get("action")
        if isinstance(action, dict) and action.get("action") == "finish":
            return step
    return None


def _verification_step_has_concrete_evidence(
    step: JsonDict,
    *,
    after: JsonDict | None,
) -> bool:
    result = step.get("result") if isinstance(step.get("result"), dict) else {}
    if result.get("state_changed") is True or result.get("runtime_changed") is True:
        return True
    if _result_excerpt(result):
        return True
    visible_excerpt = _truncate_excerpt(
        after.get("visible_text") if isinstance(after, dict) else ""
    )
    if visible_excerpt:
        return True
    return _observation_has_runtime_activity(
        {
            "runtimeLogs": after.get("runtime_logs", {}) if isinstance(after, dict) else {},
        }
    )


def _build_verification_checks(
    steps: list[JsonDict], observations: list[JsonDict]
) -> list[JsonDict]:
    checks: list[JsonDict] = []
    check_index = 1
    for step in steps:
        if not _is_meaningful_step(step):
            continue
        step_number = int(step.get("step") or 0)
        result = step.get("result") if isinstance(step.get("result"), dict) else {}
        after = _step_observation(observations, step_number, "after_action")
        status = (
            "supported"
            if _verification_step_has_concrete_evidence(step, after=after)
            else "unresolved"
        )
        evidence_parts = [
            f"progress={result.get('progress_classification', 'unknown')}",
            _runtime_log_delta_summary(
                (
                    _step_observation(observations, step_number, "before_action").get(
                        "runtime_logs"
                    )
                    if isinstance(
                        _step_observation(observations, step_number, "before_action"), dict
                    )
                    else {}
                ),
                after.get("runtime_logs") if isinstance(after, dict) else {},
            ),
        ]
        visible_excerpt = _truncate_excerpt(
            after.get("visible_text") if isinstance(after, dict) else ""
        )
        if visible_excerpt:
            evidence_parts.append(f"after='{visible_excerpt}'")
        result_excerpt = _result_excerpt(result, limit=400)
        if result_excerpt:
            evidence_parts.append(f"result='{result_excerpt}'")
        checks.append(
            {
                "check_id": f"check_{check_index}",
                "kind": _verification_kind(step),
                "target": _verification_target(step),
                "status": status,
                "step_range": _step_range(step_number),
                "evidence": "; ".join(part for part in evidence_parts if part),
            }
        )
        check_index += 1
    return checks


def _build_validation_requirement_checks(
    validation_contract: JsonDict | None,
    *,
    observations: list[JsonDict],
    final_text: str,
) -> list[JsonDict]:
    if not isinstance(validation_contract, dict):
        return []
    scenarios = validation_contract.get("validation_scenarios")
    if not isinstance(scenarios, list):
        return []

    final_observation = observations[-1] if observations else {}
    visible_excerpt = _truncate_excerpt(
        final_observation.get("visible_text") if isinstance(final_observation, dict) else ""
    )
    final_text_excerpt = _truncate_excerpt(final_text)
    runtime_logs = (
        final_observation.get("runtime_logs") if isinstance(final_observation, dict) else {}
    )
    tool_count = 0
    side_effect_count = 0
    if isinstance(runtime_logs, dict):
        tool_logs = runtime_logs.get("tool_logs")
        side_effect_logs = runtime_logs.get("side_effect_logs")
        if isinstance(tool_logs, list):
            tool_count = len(tool_logs)
        if isinstance(side_effect_logs, list):
            side_effect_count = len(side_effect_logs)

    checks: list[JsonDict] = []
    seen_ids: set[str] = set()
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            continue
        turn = scenario.get("turn")
        requirements = scenario.get("evidence_requirements")
        if not isinstance(requirements, list):
            continue
        for requirement in requirements:
            if not isinstance(requirement, dict):
                continue
            requirement_id = str(requirement.get("id") or "").strip()
            if not requirement_id:
                continue
            unique_id = requirement_id
            if unique_id in seen_ids:
                unique_id = f"{requirement_id}_turn_{turn}"
            seen_ids.add(unique_id)
            surface = str(requirement.get("surface") or "").strip()
            expectation = str(
                requirement.get("expect") or requirement.get("expectation") or ""
            ).strip()
            evidence_parts = [
                f"turn={turn}",
                f"surface={surface or 'unspecified'}",
                f"tool_logs={tool_count}",
                f"side_effect_logs={side_effect_count}",
            ]
            if visible_excerpt:
                evidence_parts.append(f"visible='{visible_excerpt}'")
            if final_text_excerpt and final_text_excerpt != visible_excerpt:
                evidence_parts.append(f"final_text='{final_text_excerpt}'")
            checks.append(
                {
                    "check_id": f"requirement_{unique_id}",
                    "kind": "validation_requirement_check",
                    "target": requirement_id,
                    "status": "unresolved",
                    "requirement_id": requirement_id,
                    "requirement_turn": turn,
                    "surface": surface,
                    "expectation": expectation,
                    "evidence": "; ".join(part for part in evidence_parts if part),
                }
            )
    return checks


def _final_assessment(
    *,
    final_status: str,
    final_text: str,
    steps: list[JsonDict],
    verification_checks: list[JsonDict],
    blocking_reason: str | None,
) -> JsonDict:
    supported_claims = [
        check.get("target") for check in verification_checks if check.get("status") == "supported"
    ]
    unresolved_claims = [
        check.get("target") for check in verification_checks if check.get("status") == "unresolved"
    ]
    refuted_claims: list[str] = []
    if final_status != "success" and final_text.strip():
        refuted_claims.append(_truncate_excerpt(final_text, limit=400))
    return {
        "supported_claims": supported_claims,
        "refuted_claims": refuted_claims,
        "unresolved_claims": unresolved_claims,
        "blocking_reason": blocking_reason,
    }


def _build_structured_actor_evidence(
    *,
    request: str,
    final_status: str,
    finish_basis: str,
    steps: list[JsonDict],
    observations: list[JsonDict],
    final_text: str,
    validation_contract: JsonDict | None = None,
) -> tuple[JsonDict, list[JsonDict], list[JsonDict], JsonDict]:
    verification_checks = _build_verification_checks(steps, observations)
    verification_checks.extend(
        _build_validation_requirement_checks(
            validation_contract,
            observations=observations,
            final_text=final_text,
        )
    )
    state_diffs = _build_state_diffs(steps, observations)
    final_assessment = _final_assessment(
        final_status=final_status,
        final_text=final_text,
        steps=steps,
        verification_checks=verification_checks,
        blocking_reason=finish_basis if final_status != "success" else None,
    )
    evidence_summary = {
        "request": benchmark_request(request),
        "actor_outcome": _actor_outcome_from_status(final_status),
        "meaningful_action_count": _meaningful_action_count(steps),
        "finish_basis": finish_basis,
    }
    return evidence_summary, verification_checks, state_diffs, final_assessment


def _normalize_finish_status(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if normalized in ("success", "stuck", "error") else None


def _create_auto_finish_action(status: str, rationale: str) -> JsonDict:
    return {
        "action": "finish",
        "index": None,
        "text": None,
        "value": None,
        "key": None,
        "direction": None,
        "amount": None,
        "seconds": None,
        "status": status,
        "rationale": rationale,
    }


def _history_has_terminal_finish_action(steps: list[JsonDict]) -> bool:
    if not steps:
        return False
    action = steps[-1].get("action")
    return bool(isinstance(action, dict) and action.get("action") == "finish")


def _history_terminal_finish_status(steps: list[JsonDict]) -> str | None:
    if not _history_has_terminal_finish_action(steps):
        return None
    action = steps[-1].get("action")
    if not isinstance(action, dict):
        return None
    return _normalize_finish_status(action.get("status"))


def _is_submit_like_step(step: JsonDict) -> bool:
    if not isinstance(step, dict):
        return False
    action = step.get("action")
    if not isinstance(action, dict):
        return False
    action_name = _normalize_text(action.get("action"))
    if action_name not in {"click", "press"}:
        return False
    pieces = [
        str(action.get("text") or ""),
        str(action.get("value") or ""),
    ]
    target = step.get("resolved_target")
    if isinstance(target, dict):
        pieces.append(str(target.get("name") or ""))
    text = _normalize_text(" ".join(pieces))
    markers = ("submit", "apply", "save", "confirm", "update")
    return any(marker in text for marker in markers)


def _is_mutation_like_step(step: JsonDict) -> bool:
    if not isinstance(step, dict):
        return False
    action = step.get("action")
    result = step.get("result")
    if not isinstance(action, dict) or not isinstance(result, dict):
        return False
    if result.get("state_changed") is not True and result.get("runtime_changed") is not True:
        return False
    action_name = _normalize_text(action.get("action"))
    if action_name in {"type", "input", "select", "select_dropdown", "switch", "press"}:
        return True
    target = step.get("resolved_target")
    if not isinstance(target, dict):
        return False
    return _normalize_text(target.get("role")) in {
        "textbox",
        "combobox",
        "spinbutton",
        "checkbox",
        "radio",
    }


def _is_selection_like_step(step: JsonDict) -> bool:
    if not isinstance(step, dict):
        return False
    action = step.get("action")
    result = step.get("result")
    target = step.get("resolved_target")
    if not isinstance(action, dict) or not isinstance(result, dict) or not isinstance(target, dict):
        return False
    if result.get("state_changed") is not True:
        return False
    action_name = _normalize_text(action.get("action"))
    if action_name not in {"click", "press"}:
        return False
    role = _normalize_text(target.get("role"))
    return role in {"button", "tab", "link", "option", "listitem", "row"}


def _submit_without_recent_mutation(steps: list[JsonDict]) -> bool:
    if not steps:
        return False
    submit_index = None
    for index in range(len(steps) - 1, -1, -1):
        if _is_submit_like_step(steps[index]):
            submit_index = index
            break
    if submit_index is None:
        return False
    recent_window = steps[max(0, submit_index - 3) : submit_index]
    return not any(_is_mutation_like_step(step) for step in recent_window)


def _step_has_submit_outcome_evidence(step: JsonDict) -> bool:
    result = step.get("result")
    if not isinstance(result, dict):
        return False
    if result.get("runtime_changed") is True:
        return True
    if result.get("state_changed") is not True:
        return False
    return not _is_selection_like_step(step)


def _recent_submit_has_canonical_follow_up(steps: list[JsonDict]) -> bool:
    if not steps:
        return True
    submit_index = None
    for index in range(len(steps) - 1, -1, -1):
        if _is_persist_submit_like_step(steps[index]):
            submit_index = index
            break
    if submit_index is None:
        return True

    submit_result = steps[submit_index].get("result")
    if isinstance(submit_result, dict) and submit_result.get("runtime_changed") is True:
        return True

    for later in steps[submit_index + 1 :]:
        if _is_verification_like_step(later) or _is_selection_like_step(later):
            return True
        result = later.get("result")
        if isinstance(result, dict) and (
            result.get("runtime_changed") is True or result.get("state_changed") is True
        ):
            return True
    return False


def _recent_submit_lacks_outcome_evidence(steps: list[JsonDict]) -> bool:
    if not steps:
        return False
    submit_index = None
    for index in range(len(steps) - 1, -1, -1):
        if _is_persist_submit_like_step(steps[index]):
            submit_index = index
            break
    if submit_index is None:
        return False
    if _step_has_submit_outcome_evidence(steps[submit_index]):
        return False
    for later in steps[submit_index + 1 :]:
        if _step_has_submit_outcome_evidence(later):
            return False
    return True


# ---------------------------------------------------------------------------
# browser-use integration
# ---------------------------------------------------------------------------
def _selector_map_element_name(node: Any) -> str:
    ax_node = getattr(node, "ax_node", None)
    ax_name = getattr(ax_node, "name", None)
    if isinstance(ax_name, str) and ax_name.strip():
        return re.sub(r"\s+", " ", ax_name).strip()

    attributes = getattr(node, "attributes", None)
    if isinstance(attributes, dict):
        for key in ("value", "aria-label", "title", "placeholder", "alt", "name"):
            value = attributes.get(key)
            if isinstance(value, str) and value.strip():
                return re.sub(r"\s+", " ", value).strip()

    getter = getattr(node, "get_meaningful_text_for_llm", None)
    if callable(getter):
        value = str(getter() or "").strip()
        if value:
            return re.sub(r"\s+", " ", value).strip()

    return ""


def _selector_map_element_role(node: Any) -> str:
    ax_node = getattr(node, "ax_node", None)
    role = getattr(ax_node, "role", None)
    if isinstance(role, str) and role.strip():
        return role.strip().lower()

    attributes = getattr(node, "attributes", None)
    if isinstance(attributes, dict):
        explicit = attributes.get("role")
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip().lower()

    tag_name = getattr(node, "tag_name", None)
    if isinstance(tag_name, str) and tag_name.strip():
        return tag_name.strip().lower()
    return ""


def _selector_map_summaries(selector_map: object) -> list[JsonDict]:
    if not isinstance(selector_map, dict):
        return []

    summaries: list[JsonDict] = []
    for raw_index, node in sorted(selector_map.items(), key=lambda item: int(item[0])):
        if not isinstance(raw_index, int):
            continue
        if node is None:
            continue
        summaries.append(
            {
                "index": raw_index,
                "role": _selector_map_element_role(node),
                "name": _selector_map_element_name(node),
                "tag_name": str(getattr(node, "tag_name", "") or ""),
                "xpath": str(getattr(node, "xpath", "") or ""),
            }
        )
    return summaries


def _resolve_actionable_target(
    observation: JsonDict | None, action_payload: JsonDict
) -> JsonDict | None:
    if not isinstance(observation, dict) or not isinstance(action_payload, dict):
        return None
    ref = action_payload.get("ref")
    if isinstance(ref, str) and ref.strip():
        target = _resolve_affordance_ref(observation, ref.strip())
        if target is not None:
            return target
    for key in ("source_ref", "target_ref"):
        nested_ref = action_payload.get(key)
        if isinstance(nested_ref, str) and nested_ref.strip():
            target = _resolve_affordance_ref(observation, nested_ref.strip())
            if target is not None:
                return target
    index = action_payload.get("index")
    if not isinstance(index, int):
        return None
    for element in _observation_elements(observation, "actionableElements"):
        if element.get("index") == index:
            return element
    return None


def _resolve_affordance_ref(observation: JsonDict, ref: str) -> JsonDict | None:
    facts = observation.get("affordanceFacts")
    if not isinstance(facts, dict):
        facts = observation.get("affordance_facts")
    if not isinstance(facts, dict):
        return None
    containers: list[object] = [facts.get("selects"), facts.get("buttons")]
    drag_drop = facts.get("drag_drop")
    if isinstance(drag_drop, dict):
        containers.extend([drag_drop.get("draggables"), drag_drop.get("drop_regions")])
    visual = facts.get("visual")
    if isinstance(visual, dict):
        containers.extend([visual.get("regions"), visual.get("targets")])
    for container in containers:
        if not isinstance(container, list):
            continue
        for item in container:
            if isinstance(item, dict) and item.get("ref") == ref:
                return item
    return None


@dataclass(slots=True)
class UploadFixtureSpec:
    name: str
    field_hint: str
    source_value: str
    description: str


@dataclass(slots=True)
class BrowserUseTaskSpec:
    prompt: str
    current_request: str
    max_steps: int
    max_time_seconds: int
    max_stuck_steps: int
    allow_submit_without_recent_mutation: bool
    allow_duplicate_action_no_progress: bool
    use_vision: bool
    step_timeout_seconds: int
    wait_between_actions_seconds: float
    upload_fixtures: dict[str, UploadFixtureSpec]
    validation_contract: JsonDict | None = None


@dataclass(slots=True)
class EvidencePolicyState:
    max_stuck_steps: int
    allow_submit_without_recent_mutation: bool = False
    allow_duplicate_action_no_progress: bool = False
    current_request: str = ""
    previous_state_hash: str = ""
    previous_runtime_signature: str = ""
    stuck_count: int = 0
    action_count: int = 0
    stop_status: str | None = None
    stop_rationale: str | None = None
    latest_observation: JsonDict | None = None
    progress_history: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ActorEvidenceBundle:
    raw_agent_history: JsonDict
    canonical_actor_result: JsonDict


class BrowserUseModelAdapter:
    def __init__(self, delegate: Any, *, fixture_names: set[str]):
        self._delegate = delegate
        self._fixture_names = fixture_names
        self.schema_mismatches: list[str] = []
        self.raw_payloads: list[str] = []
        self.schema_repairs: list[str] = []

    @property
    def model(self) -> str:
        return self._delegate.model

    @property
    def provider(self) -> str:
        return getattr(self._delegate, "provider", "unknown")

    @property
    def name(self) -> str:
        return getattr(self._delegate, "name", str(self.model))

    @property
    def model_name(self) -> str:
        return getattr(self._delegate, "model_name", str(self.model))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    async def ainvoke(
        self, messages: list[Any], output_format: type[Any] | None = None, **kwargs: Any
    ) -> Any:
        if output_format is None:
            return await self._delegate.ainvoke(messages, output_format=None, **kwargs)
        try:
            response = await self._delegate.ainvoke(messages, output_format=output_format, **kwargs)
            repaired_completion = _repair_implicit_scroll_direction(
                response.completion,
                output_format=output_format,
            )
            if repaired_completion is not response.completion:
                return response.__class__(
                    completion=repaired_completion,
                    usage=response.usage,
                    stop_reason=response.stop_reason,
                )
            return response
        except Exception as original_error:
            raw_completion = await self._delegate.ainvoke(messages, output_format=None, **kwargs)
            try:
                raw_payload = _parse_browser_use_raw_payload(raw_completion.completion)
                normalized_payload = _normalize_browser_use_output_payload(
                    raw_payload,
                    fixture_names=self._fixture_names,
                )
                parsed = output_format.model_validate(normalized_payload)
                parsed = _repair_implicit_scroll_direction(
                    parsed,
                    output_format=output_format,
                )
            except (
                ValueError,
                TypeError,
                ValidationError,
                json.JSONDecodeError,
            ) as normalize_error:
                compact_raw = str(raw_completion.completion or "").strip()
                if len(compact_raw) > 800:
                    compact_raw = compact_raw[:800].rstrip() + "...[truncated]"
                self.raw_payloads.append(compact_raw)
                repaired = await self._repair_schema_only_response(
                    messages=messages,
                    output_format=output_format,
                    raw_payload_text=compact_raw,
                    original_error=normalize_error,
                    **kwargs,
                )
                if repaired is not None:
                    return repaired
                message = f"agent_schema_mismatch: {normalize_error}. raw_payload={compact_raw}"
                self.schema_mismatches.append(message)
                raise original_error from normalize_error
            return raw_completion.__class__(
                completion=parsed,
                usage=raw_completion.usage,
                stop_reason=raw_completion.stop_reason,
            )

    async def _repair_schema_only_response(
        self,
        *,
        messages: list[Any],
        output_format: type[Any],
        raw_payload_text: str,
        original_error: Exception,
        **kwargs: Any,
    ) -> Any | None:
        from browser_use.llm.messages import UserMessage

        repair_prompt = _build_browser_use_schema_repair_prompt(
            raw_payload_text=raw_payload_text,
            original_error=original_error,
        )
        repair_messages = list(messages) + [UserMessage(content=repair_prompt)]
        try:
            repaired_completion = await self._delegate.ainvoke(
                repair_messages,
                output_format=None,
                **kwargs,
            )
            repaired_payload = _parse_browser_use_raw_payload(repaired_completion.completion)
            normalized_payload = _normalize_browser_use_output_payload(
                repaired_payload,
                fixture_names=self._fixture_names,
            )
            parsed = output_format.model_validate(normalized_payload)
            parsed = _repair_implicit_scroll_direction(parsed, output_format=output_format)
        except (
            ValueError,
            TypeError,
            ValidationError,
            json.JSONDecodeError,
        ):
            return None
        self.schema_repairs.append(raw_payload_text)
        return repaired_completion.__class__(
            completion=parsed,
            usage=repaired_completion.usage,
            stop_reason=repaired_completion.stop_reason,
        )


def _browser_use_llm(runtime_config: Any, *, fixture_names: set[str] | None = None):
    import httpx
    from browser_use.llm.anthropic.chat import ChatAnthropic
    from browser_use.llm.openai.chat import ChatOpenAI
    from browser_use.llm.openai.like import ChatOpenAILike

    from runner.tools.experiment_config import BlindActorRuntimeConfig

    if not isinstance(runtime_config, BlindActorRuntimeConfig):
        raise TypeError("runtime_config must be BlindActorRuntimeConfig")

    provider_config = runtime_config.provider_config
    common_kwargs = {
        "model": provider_config.model,
        "api_key": provider_config.api_key,
        "base_url": provider_config.base_url,
        "temperature": provider_config.temperature,
        "max_retries": provider_config.retry_policy.max_retries,
        "reasoning_effort": provider_config.reasoning_effort,
        "max_completion_tokens": provider_config.output_token_limit,
        "timeout": provider_config.timeout_policy.read_timeout_seconds,
    }
    if provider_config.extra_body:
        extra_body = dict(provider_config.extra_body)

        async def _inject_extra_body(request: httpx.Request) -> None:
            if request.method.upper() != "POST":
                return
            try:
                payload = json.loads(request.content.decode("utf-8"))
            except (AttributeError, json.JSONDecodeError, UnicodeDecodeError):
                return
            if not isinstance(payload, dict):
                return
            payload.update(extra_body)
            body = json.dumps(payload).encode("utf-8")
            request.headers["content-length"] = str(len(body))
            request.stream = httpx.ByteStream(body)

        common_kwargs["http_client"] = httpx.AsyncClient(
            event_hooks={"request": [_inject_extra_body]}
        )

    if provider_config.provider == "openai-compatible":
        if provider_config.base_url.strip().rstrip("/") == "https://api.openai.com/v1":
            llm = ChatOpenAI(**common_kwargs)
        else:
            llm = ChatOpenAILike(**common_kwargs)
    elif provider_config.provider == "anthropic":
        llm = ChatAnthropic(
            model=provider_config.model,
            api_key=provider_config.api_key,
            base_url=provider_config.base_url,
            temperature=provider_config.temperature,
            max_retries=provider_config.retry_policy.max_retries,
            max_tokens=provider_config.output_token_limit,
            timeout=provider_config.timeout_policy.read_timeout_seconds,
        )
    else:
        raise ValueError(
            f"Unsupported browser-use blind actor provider: {provider_config.provider}"
        )
    return BrowserUseModelAdapter(llm, fixture_names=fixture_names or set())


def _browser_use_token_usage(usage: object) -> JsonDict:
    if usage is None:
        return _empty_token_usage()
    if not hasattr(usage, "total_tokens"):
        return _empty_token_usage()
    prompt_tokens = int(getattr(usage, "total_prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(usage, "total_completion_tokens", 0) or 0)
    total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
    calls = int(getattr(usage, "entry_count", 0) or 0)
    payload: JsonDict = {
        "input_tokens": prompt_tokens,
        "output_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "calls": calls,
    }
    cached = int(getattr(usage, "total_prompt_cached_tokens", 0) or 0)
    if cached > 0:
        payload["cached_input_tokens"] = cached
        payload["uncached_input_tokens"] = max(prompt_tokens - cached, 0)
    return payload


def _slugify_filename(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-").lower()
    return normalized or "fixture"


def _compile_upload_fixtures(private_eval: JsonDict) -> dict[str, UploadFixtureSpec]:
    compiled: dict[str, UploadFixtureSpec] = {}

    def add_fixture(raw_name: str, payload: object) -> None:
        if not isinstance(payload, dict):
            raise ValueError(f"upload fixture {raw_name!r} must be an object")
        name = str(payload.get("name") or raw_name).strip()
        if not name:
            raise ValueError("upload fixture name must be non-empty")
        field_hint = str(payload.get("field_hint") or payload.get("target") or name).strip()
        source_value = str(
            payload.get("source_value") or payload.get("value") or payload.get("content") or ""
        ).strip()
        if not field_hint:
            raise ValueError(f"upload fixture {name!r} field_hint must be non-empty")
        if not source_value:
            raise ValueError(f"upload fixture {name!r} source_value must be non-empty")
        if name in compiled:
            raise ValueError(f"duplicate upload fixture name {name!r}")
        compiled[name] = UploadFixtureSpec(
            name=name,
            field_hint=field_hint,
            source_value=source_value,
            description=str(payload.get("description") or payload.get("reason") or field_hint),
        )

    def add_fixtures(value: object) -> None:
        if value is None:
            return
        if isinstance(value, dict):
            for name, payload in value.items():
                add_fixture(str(name), payload)
            return
        if isinstance(value, list):
            for index, payload in enumerate(value, start=1):
                if not isinstance(payload, dict):
                    raise ValueError(f"upload_fixtures[{index}] must be an object")
                add_fixture(str(payload.get("name") or f"fixture_{index}"), payload)
            return
        raise ValueError("upload_fixtures must be an object or array")

    add_fixtures(private_eval.get("upload_fixtures"))
    scenario_fixtures = private_eval.get("scenario_fixtures")
    if isinstance(scenario_fixtures, dict):
        for scenario_name, scenario in scenario_fixtures.items():
            if not isinstance(scenario, dict):
                continue
            try:
                add_fixtures(scenario.get("upload_fixtures"))
                initial_state = scenario.get("initial_state")
                if isinstance(initial_state, dict):
                    add_fixtures(initial_state.get("upload_fixtures"))
            except ValueError as exc:
                raise ValueError(
                    f"Invalid upload fixtures for scenario {scenario_name!r}: {exc}"
                ) from exc
    return compiled


def _browser_use_task_prompt(
    base_url: str,
    goal: JsonDict,
    extra_context: JsonDict | None,
    upload_fixtures: dict[str, UploadFixtureSpec],
) -> str:
    stable_context = build_actor_stable_context_payload(goal, extra_context)
    upload_note = (
        "Available upload fixtures: "
        + ", ".join(sorted(upload_fixtures))
        + ". Use list_upload_fixtures to discover them, then upload_fixture(index, fixture_name).\n"
        if upload_fixtures
        else "No upload fixtures are predeclared for this task.\n"
    )
    upload_note += (
        "If the app downloads a file and its contents are relevant evidence, use "
        "list_downloaded_files first, then read_downloaded_file(index). Do not use raw "
        "local file paths as general filesystem access.\n"
    )
    return ACTOR_TASK_PROMPT_TEMPLATE.format(
        base_url=base_url,
        upload_note=upload_note,
        stable_context=stable_context,
    )


def _compile_browser_use_task_spec(
    *,
    base_url: str,
    private_eval: JsonDict,
    runtime_config: Any,
) -> BrowserUseTaskSpec:
    from runner.tools.experiment_config import BlindActorRuntimeConfig

    if not isinstance(runtime_config, BlindActorRuntimeConfig):
        raise TypeError("runtime_config must be BlindActorRuntimeConfig")

    goal = private_eval.get("actor_goal", {})
    budget = private_eval.get("actor_budget", {})
    extra_context = private_eval.get("extra_context")
    current_request = str(
        goal.get("current_user_request") or goal.get("query") or goal.get("instructions") or ""
    )
    upload_fixtures = _compile_upload_fixtures(private_eval)
    max_steps = budget.get("max_steps", 24)
    max_time_seconds = budget.get("max_time_seconds", 300)
    max_stuck_steps = budget.get("max_stuck_steps", 4)
    if not isinstance(max_steps, int):
        max_steps = 24
    if not isinstance(max_time_seconds, int):
        max_time_seconds = 300
    if not isinstance(max_stuck_steps, int):
        max_stuck_steps = 4
    return BrowserUseTaskSpec(
        prompt=_browser_use_task_prompt(base_url, goal, extra_context, upload_fixtures),
        current_request=current_request,
        max_steps=max_steps,
        max_time_seconds=max_time_seconds,
        max_stuck_steps=max_stuck_steps,
        allow_submit_without_recent_mutation=bool(
            budget.get("allow_submit_without_recent_mutation")
        ),
        allow_duplicate_action_no_progress=bool(budget.get("allow_duplicate_action_no_progress")),
        use_vision=bool(runtime_config.browser_use.use_vision),
        step_timeout_seconds=min(
            runtime_config.browser_use.step_timeout_seconds,
            max(max_time_seconds, 1),
        ),
        wait_between_actions_seconds=float(runtime_config.browser_use.wait_between_actions_seconds),
        upload_fixtures=upload_fixtures,
        validation_contract=(
            extra_context.get("validation_contract")
            if isinstance(extra_context, dict)
            and isinstance(extra_context.get("validation_contract"), dict)
            else None
        ),
    )


def _parse_browser_use_raw_payload(content: object) -> JsonDict:
    parsed = _parse_actor_response_content(content, attempt=3)
    if not isinstance(parsed, dict):
        raise ValueError("browser-use raw model output must decode to a JSON object")
    return parsed


def _normalize_done_payload(params: JsonDict) -> JsonDict:
    text = params.get("text")
    if text is None and params.get("rationale") is not None:
        text = params.get("rationale")
    success = params.get("success")
    if success is None:
        status = _normalize_finish_status(params.get("status"))
        success = status != "error" and status != "stuck"
    return {
        "success": bool(success),
        "text": str(text or ""),
    }


_BROWSER_USE_ALLOWED_ACTIONS = frozenset(
    {
        "click",
        "click_at",
        "input",
        "navigate",
        "go_back",
        "wait",
        "switch",
        "close",
        "extract",
        "find_elements",
        "find_text",
        "scroll",
        "send_keys",
        "dropdown_options",
        "select_dropdown",
        "drag_and_drop",
        "drag_to_point",
        "get_runtime_logs",
        "inspect_interaction_affordances",
        "list_upload_fixtures",
        "list_downloaded_files",
        "move_mouse",
        "read_downloaded_file",
        "upload_fixture",
        "read_current_visible_state",
        "done",
    }
)

_BROWSER_USE_ACTION_ALIASES = {
    "search_page": "find_text",
    "search_text": "find_text",
    "search_in_page": "find_text",
    "search": "find_text",
    "evaluate": "read_current_visible_state",
    "screenshot": "read_current_visible_state",
    "take_screenshot": "read_current_visible_state",
    "read_page": "read_current_visible_state",
    "read_visible_state": "read_current_visible_state",
    "inspect_page": "read_current_visible_state",
    "inspect": "read_current_visible_state",
    "click_coordinates": "click_at",
    "click_point": "click_at",
    "click_position": "click_at",
    "click_xy": "click_at",
    "coordinate_click": "click_at",
    "hover": "move_mouse",
    "mouse_move": "move_mouse",
    "mousemove": "move_mouse",
    "drag": "drag_and_drop",
    "drag_drop": "drag_and_drop",
    "drag_to": "drag_to_point",
    "drag_point": "drag_to_point",
    "inspect_affordances": "inspect_interaction_affordances",
    "inspect_interactions": "inspect_interaction_affordances",
    "interaction_affordances": "inspect_interaction_affordances",
    "downloaded_files": "list_downloaded_files",
    "list_downloads": "list_downloaded_files",
    "read_file": "read_downloaded_file",
    "read_download": "read_downloaded_file",
    "read_downloaded": "read_downloaded_file",
    "upload_file": "upload_fixture",
    "upload": "upload_fixture",
    "select_option": "select_dropdown",
    "select_dropdown_option": "select_dropdown",
    "select": "select_dropdown",
    "choose_option": "select_dropdown",
    "choose": "select_dropdown",
    "fill": "input",
    "type_text": "input",
    "enter_text": "input",
    "input_text": "input",
    "press": "send_keys",
    "press_key": "send_keys",
    "send_key": "send_keys",
    "switch_tab": "switch",
    "close_tab": "close",
    "finish": "done",
    "complete": "done",
}


def _normalize_browser_use_action_name(value: object) -> str:
    action_name = str(value or "").strip().lower()
    return _BROWSER_USE_ACTION_ALIASES.get(action_name, action_name)


def _normalize_dropdown_label(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    return text


def _normalize_affordance_ref_value(value: object) -> str | None:
    if value is None:
        return None
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("_", "-")
    text = re.sub(r"^drop-region:", "drop:", text)
    text = re.sub(r"^dropzone:", "drop:", text)
    text = re.sub(r"^visual-region:", "visual-region:", text)
    text = re.sub(r"^visual-target:", "visual-target:", text)
    return text


def _ensure_allowed_browser_use_action(action_name: str) -> str:
    normalized_action = _normalize_browser_use_action_name(action_name)
    if normalized_action not in _BROWSER_USE_ALLOWED_ACTIONS:
        raise ValueError(
            "Unsupported browser-use action "
            f"'{action_name}'. Allowed actions: {', '.join(sorted(_BROWSER_USE_ALLOWED_ACTIONS))}"
        )
    return normalized_action


def _build_browser_use_schema_repair_prompt(
    *, raw_payload_text: str, original_error: Exception
) -> str:
    allowed_actions = ", ".join(sorted(_BROWSER_USE_ALLOWED_ACTIONS))
    return (
        "Your last response used an invalid browser action schema.\n"
        f"Validation error: {original_error}\n"
        f"Allowed action names exactly: {allowed_actions}\n"
        "Return ONLY a valid JSON object in the same browser-use output format.\n"
        "Do not explain anything. Do not invent unsupported actions. "
        "Map unsupported aliases to the closest allowed action.\n"
        "If the prior response tried to finish, use action done with {success, text}.\n"
        f"Previous invalid payload:\n{raw_payload_text}"
    )


def _coerce_browser_use_action_params(action_name: str, raw_params: object) -> JsonDict:
    if isinstance(raw_params, dict):
        return dict(raw_params)
    if action_name in {"click", "click_at", "dropdown_options"} and isinstance(raw_params, int):
        return {"index": raw_params}
    if action_name == "wait" and isinstance(raw_params, (int, float)):
        return {"seconds": raw_params}
    return {}


def _normalize_browser_use_action_dict(
    action_payload: JsonDict,
    *,
    fixture_names: set[str],
) -> JsonDict:
    if len(action_payload) == 1:
        action_name, raw_params = next(iter(action_payload.items()))
        params = _coerce_browser_use_action_params(str(action_name), raw_params)
    else:
        action_name = str(action_payload.get("action") or "").strip()
        params = {key: value for key, value in action_payload.items() if key != "action"}
    if not action_name:
        raise ValueError("Missing action name in browser-use payload")
    action_name = _ensure_allowed_browser_use_action(action_name)
    normalized_params: JsonDict = dict(params)
    if "element_index" in normalized_params and "index" not in normalized_params:
        normalized_params["index"] = normalized_params.pop("element_index")
    if "element_ref" in normalized_params and "ref" not in normalized_params:
        normalized_params["ref"] = normalized_params.pop("element_ref")
    if "reference" in normalized_params and "ref" not in normalized_params:
        normalized_params["ref"] = normalized_params.pop("reference")
    if "x_percent" in normalized_params and "x_pct" not in normalized_params:
        normalized_params["x_pct"] = normalized_params.pop("x_percent")
    if "y_percent" in normalized_params and "y_pct" not in normalized_params:
        normalized_params["y_pct"] = normalized_params.pop("y_percent")
    if "source_element_index" in normalized_params and "source_index" not in normalized_params:
        normalized_params["source_index"] = normalized_params.pop("source_element_index")
    if "target_element_index" in normalized_params and "target_index" not in normalized_params:
        normalized_params["target_index"] = normalized_params.pop("target_element_index")
    if "from_index" in normalized_params and "source_index" not in normalized_params:
        normalized_params["source_index"] = normalized_params.pop("from_index")
    if "to_index" in normalized_params and "target_index" not in normalized_params:
        normalized_params["target_index"] = normalized_params.pop("to_index")
    if "source_element_ref" in normalized_params and "source_ref" not in normalized_params:
        normalized_params["source_ref"] = normalized_params.pop("source_element_ref")
    if "target_element_ref" in normalized_params and "target_ref" not in normalized_params:
        normalized_params["target_ref"] = normalized_params.pop("target_element_ref")
    if "from_ref" in normalized_params and "source_ref" not in normalized_params:
        normalized_params["source_ref"] = normalized_params.pop("from_ref")
    if "to_ref" in normalized_params and "target_ref" not in normalized_params:
        normalized_params["target_ref"] = normalized_params.pop("to_ref")
    if "target_x_percent" in normalized_params and "target_x_pct" not in normalized_params:
        normalized_params["target_x_pct"] = normalized_params.pop("target_x_percent")
    if "target_y_percent" in normalized_params and "target_y_pct" not in normalized_params:
        normalized_params["target_y_pct"] = normalized_params.pop("target_y_percent")
    if "source_x_percent" in normalized_params and "source_x_pct" not in normalized_params:
        normalized_params["source_x_pct"] = normalized_params.pop("source_x_percent")
    if "source_y_percent" in normalized_params and "source_y_pct" not in normalized_params:
        normalized_params["source_y_pct"] = normalized_params.pop("source_y_percent")
    if "target_x" in normalized_params and "x" not in normalized_params:
        normalized_params["x"] = normalized_params.pop("target_x")
    if "target_y" in normalized_params and "y" not in normalized_params:
        normalized_params["y"] = normalized_params.pop("target_y")
    if isinstance(normalized_params.get("point"), list) and len(normalized_params["point"]) >= 2:
        if "x" not in normalized_params:
            normalized_params["x"] = normalized_params["point"][0]
        if "y" not in normalized_params:
            normalized_params["y"] = normalized_params["point"][1]
    target_point = normalized_params.get("target_point")
    if isinstance(target_point, dict):
        if "target_x" not in normalized_params and target_point.get("x") is not None:
            normalized_params["target_x"] = target_point.get("x")
        if "target_y" not in normalized_params and target_point.get("y") is not None:
            normalized_params["target_y"] = target_point.get("y")
    coordinates = normalized_params.get("coordinates")
    if isinstance(coordinates, dict):
        if "x" not in normalized_params and coordinates.get("x") is not None:
            normalized_params["x"] = coordinates.get("x")
        if "y" not in normalized_params and coordinates.get("y") is not None:
            normalized_params["y"] = coordinates.get("y")
    source_target = normalized_params.get("source")
    if isinstance(source_target, dict):
        if "source_ref" not in normalized_params and source_target.get("ref") is not None:
            normalized_params["source_ref"] = source_target.get("ref")
        if "source_index" not in normalized_params and source_target.get("index") is not None:
            normalized_params["source_index"] = source_target.get("index")
    target_target = normalized_params.get("target")
    if isinstance(target_target, dict):
        if "target_ref" not in normalized_params and target_target.get("ref") is not None:
            normalized_params["target_ref"] = target_target.get("ref")
        if "target_index" not in normalized_params and target_target.get("index") is not None:
            normalized_params["target_index"] = target_target.get("index")
    for ref_key in ("ref", "source_ref", "target_ref"):
        normalized_ref = _normalize_affordance_ref_value(normalized_params.get(ref_key))
        if normalized_ref is not None:
            normalized_params[ref_key] = normalized_ref
    if "file_path" in normalized_params and "path" not in normalized_params:
        normalized_params["path"] = normalized_params.pop("file_path")
    if (
        action_name == "wait"
        and "duration" in normalized_params
        and "seconds" not in normalized_params
    ):
        normalized_params["seconds"] = normalized_params.pop("duration")
    if (
        action_name == "find_text"
        and "query" in normalized_params
        and "pattern" not in normalized_params
    ):
        normalized_params["pattern"] = normalized_params.pop("query")
    if action_name == "read_downloaded_file":
        if "download_index" in normalized_params and "index" not in normalized_params:
            normalized_params["index"] = normalized_params.pop("download_index")
        if "file_index" in normalized_params and "index" not in normalized_params:
            normalized_params["index"] = normalized_params.pop("file_index")
        if "file_path" in normalized_params and "path" not in normalized_params:
            normalized_params["path"] = normalized_params.pop("file_path")
        if "download_path" in normalized_params and "path" not in normalized_params:
            normalized_params["path"] = normalized_params.pop("download_path")
        if "name" in normalized_params and "filename" not in normalized_params:
            normalized_params["filename"] = normalized_params.pop("name")
        if "file_name" in normalized_params and "filename" not in normalized_params:
            normalized_params["filename"] = normalized_params.pop("file_name")
        normalized_params = {
            key: normalized_params[key]
            for key in ("index", "filename", "path", "max_chars")
            if key in normalized_params
        }
    if action_name == "read_current_visible_state":
        normalized_params = {}
    if action_name == "select_dropdown":
        if "option_text" in normalized_params and "text" not in normalized_params:
            normalized_params["text"] = normalized_params.pop("option_text")
        if "option" in normalized_params and "text" not in normalized_params:
            normalized_params["text"] = normalized_params.pop("option")
        if isinstance(normalized_params.get("text"), str):
            normalized_params["text"] = _normalize_dropdown_label(normalized_params["text"])
        if isinstance(normalized_params.get("value"), str):
            normalized_params["value"] = _normalize_dropdown_label(normalized_params["value"])
    if action_name == "upload_fixture":
        fixture_name = normalized_params.get("fixture_name")
        if fixture_name is None:
            candidate = normalized_params.get("path")
            if isinstance(candidate, str) and candidate.strip():
                raw_name = candidate.strip()
                fixture_name = raw_name if raw_name in fixture_names else Path(raw_name).stem
        normalized_params = {
            "index": normalized_params.get("index"),
            "fixture_name": fixture_name,
        }
    if action_name == "done":
        normalized_params = _normalize_done_payload(normalized_params)
    if action_name in {"click_at", "move_mouse"}:
        normalized_params = {
            key: normalized_params[key]
            for key in ("index", "ref", "x_pct", "y_pct", "x", "y")
            if key in normalized_params
        }
    if action_name in {"drag_and_drop", "drag_to_point"}:
        normalized_params = {
            key: normalized_params[key]
            for key in (
                "source_index",
                "target_index",
                "source_ref",
                "target_ref",
                "ref",
                "source_x_pct",
                "source_y_pct",
                "target_x_pct",
                "target_y_pct",
                "target_x",
                "target_y",
                "point",
                "x",
                "y",
            )
            if key in normalized_params
        }
    return {action_name: normalized_params}


def _normalize_browser_use_output_payload(
    payload: JsonDict,
    *,
    fixture_names: set[str],
) -> JsonDict:
    flat_state: JsonDict = {
        "evaluation_previous_goal": "",
        "memory": "",
        "next_goal": "",
    }
    if isinstance(payload.get("current_state"), dict):
        current_state = payload.get("current_state", {})
        flat_state["evaluation_previous_goal"] = str(
            current_state.get("evaluation_previous_goal") or ""
        )
        flat_state["memory"] = str(current_state.get("memory") or "")
        flat_state["next_goal"] = str(current_state.get("next_goal") or "")
    for key in (
        "evaluation_previous_goal",
        "memory",
        "next_goal",
        "thinking",
        "current_plan_item",
        "plan_update",
    ):
        if key in payload and payload.get(key) is not None:
            flat_state[key] = payload.get(key)

    normalized_actions: list[JsonDict] = []
    raw_actions = payload.get("action")
    if isinstance(raw_actions, list):
        for item in raw_actions:
            if not isinstance(item, dict):
                raise ValueError("browser-use action list items must be objects")
            normalized_actions.append(
                _normalize_browser_use_action_dict(item, fixture_names=fixture_names)
            )
    elif isinstance(raw_actions, dict):
        normalized_actions.append(
            _normalize_browser_use_action_dict(raw_actions, fixture_names=fixture_names)
        )
    elif "action" in payload and isinstance(payload.get("action"), str):
        normalized_actions.append(
            _normalize_browser_use_action_dict(payload, fixture_names=fixture_names)
        )
    elif len(payload) == 1:
        normalized_actions.append(
            _normalize_browser_use_action_dict(payload, fixture_names=fixture_names)
        )
    else:
        raise ValueError("Could not normalize browser-use action payload")

    flat_state["action"] = normalized_actions
    return flat_state


def _infer_scroll_down_from_goal_text(*texts: object) -> bool | None:
    for value in texts:
        normalized = _normalize_text(value)
        if not normalized:
            continue
        if (
            "scroll up" in normalized
            or "scroll back up" in normalized
            or "back to the top" in normalized
            or "back to top" in normalized
            or "return to the top" in normalized
            or "go to the top" in normalized
            or "向上" in normalized
            or "上滑" in normalized
            or "回到顶部" in normalized
        ):
            return False
        if (
            "scroll down" in normalized
            or "scroll further down" in normalized
            or "go down" in normalized
            or "to the bottom" in normalized
            or "toward the bottom" in normalized
            or "向下" in normalized
            or "下滑" in normalized
            or "滚到底部" in normalized
        ):
            return True
    return None


def _repair_implicit_scroll_direction(parsed_output: Any, *, output_format: type[Any]) -> Any:
    model_dump = getattr(parsed_output, "model_dump", None)
    if not callable(model_dump):
        return parsed_output

    explicit_payload = parsed_output.model_dump(exclude_unset=True)
    full_payload = parsed_output.model_dump()
    explicit_actions = (
        explicit_payload.get("action") if isinstance(explicit_payload, dict) else None
    )
    full_actions = full_payload.get("action") if isinstance(full_payload, dict) else None
    if not isinstance(explicit_actions, list) or not isinstance(full_actions, list):
        return parsed_output

    inferred_down = _infer_scroll_down_from_goal_text(
        full_payload.get("next_goal"),
        full_payload.get("memory"),
        full_payload.get("evaluation_previous_goal"),
    )
    if inferred_down is None:
        return parsed_output

    changed = False
    for index, explicit_action in enumerate(explicit_actions):
        if index >= len(full_actions):
            break
        if not isinstance(explicit_action, dict) or not isinstance(full_actions[index], dict):
            continue
        explicit_scroll = explicit_action.get("scroll")
        full_scroll = full_actions[index].get("scroll")
        if not isinstance(explicit_scroll, dict) or not isinstance(full_scroll, dict):
            continue
        if "down" in explicit_scroll:
            continue
        full_scroll["down"] = inferred_down
        changed = True

    if not changed:
        return parsed_output
    return output_format.model_validate(full_payload)


async def _page_evaluate_json(page: Any, script: str) -> JsonDict:
    raw = await page.evaluate(script)
    if not isinstance(raw, str):
        raise ValueError("Expected browser-use page.evaluate() to return a string payload")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("Expected browser-use page.evaluate() payload to be a JSON object")
    return parsed


_AFFORDANCE_REF_INSTALL_SCRIPT = r"""() => {
    const clean = (value) => String(value ?? "").replace(/\s+/g, " ").trim();
    const isVisible = (el) => {
        if (!(el instanceof Element)) return false;
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.visibility !== "hidden" && style.display !== "none" && rect.width > 1 && rect.height > 1;
    };
    const labelFor = (el) => {
        if (!el) return "";
        const aria = clean(el.getAttribute?.("aria-label"));
        if (aria) return aria;
        const labelledBy = clean(el.getAttribute?.("aria-labelledby"));
        if (labelledBy) {
            const text = labelledBy.split(/\s+/).map((id) => clean(document.getElementById(id)?.textContent)).filter(Boolean).join(" ");
            if (text) return text;
        }
        if (el.labels && el.labels.length > 0) {
            const explicit = clean(Array.from(el.labels).map((label) => label.textContent).join(" "));
            if (explicit) return explicit;
        }
        const closestLabel = el.closest?.("label");
        if (closestLabel) {
            const nested = clean(closestLabel.textContent);
            if (nested) return nested;
        }
        return clean(el.textContent).slice(0, 120) || clean(el.getAttribute?.("placeholder")) || clean(el.getAttribute?.("value"));
    };
    const tag = (items, prefix) => items.filter(isVisible).forEach((el, index) => {
        if (!el.hasAttribute("data-genui-affordance-ref")) {
            el.setAttribute("data-genui-affordance-ref", `${prefix}:${index + 1}`);
        }
    });
    const areaOf = (el) => {
        const rect = el.getBoundingClientRect();
        return rect.width * rect.height;
    };
    const selectItems = Array.from(document.querySelectorAll("select"));
    const buttonItems = Array.from(document.querySelectorAll("button, [role='button'], input[type='button'], input[type='submit']"));
    const draggableItems = Array.from(document.querySelectorAll("[draggable='true']"));
    const markerFor = (el) => clean([
        labelFor(el),
        el.id,
        typeof el.className === "string" ? el.className : "",
        el.getAttribute?.("role"),
        el.getAttribute?.("aria-label"),
    ].join(" "));
    const allVisible = Array.from(document.querySelectorAll("*")).filter(isVisible);
    const dropScore = (el) => {
        const marker = markerFor(el);
        let score = 0;
        if (/drop[-_\s]?zone|droppable|sortable/i.test(marker)) score += 80;
        if (/timeline[-_\s]?lane|lane|timeline/i.test(marker)) score += 60;
        if (/canvas|board|game|target|region/i.test(marker)) score += 30;
        if (/grid/i.test(marker)) score += 5;
        if (/drag/i.test(marker)) score += 2;
        return score;
    };
    const dropItems = allVisible
        .map((el) => ({ el, score: dropScore(el), area: areaOf(el) }))
        .filter((item) => item.score > 0)
        .filter((item) => item.el !== document.body && item.el !== document.documentElement)
        .sort((left, right) => (right.score - left.score) || (left.area - right.area))
        .map((item) => item.el);
    const visualRegionItems = allVisible
        .filter((el) => /^(canvas|svg)$/i.test(el.tagName) || /canvas|grid|board|game|sprite|target|lane|timeline/i.test(markerFor(el)))
        .filter((el) => el !== document.body && el !== document.documentElement);
    const visualTargetItems = Array.from(document.querySelectorAll(".target, [data-target], [role='option'], svg text, svg [aria-label]"))
        .filter((el) => labelFor(el));
    tag(selectItems, "select");
    tag(buttonItems, "button");
    tag(draggableItems, "draggable");
    tag(dropItems, "drop");
    tag(visualRegionItems, "visual-region");
    tag(visualTargetItems, "visual-target");
    return JSON.stringify({
        select_count: selectItems.filter(isVisible).length,
        button_count: buttonItems.filter(isVisible).length,
        draggable_count: draggableItems.filter(isVisible).length,
        drop_region_count: dropItems.filter(isVisible).length,
        visual_region_count: visualRegionItems.filter(isVisible).length,
        visual_target_count: visualTargetItems.filter(isVisible).length
    });
}"""


async def _install_affordance_refs(page: Any) -> JsonDict:
    return await _page_evaluate_json(page, _AFFORDANCE_REF_INSTALL_SCRIPT)


def _clamped_unit(value: object, *, default: float = 0.5) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number > 1.0:
        number = number / 100.0
    return min(1.0, max(0.0, number))


async def _locator_from_browser_use_index(browser_session: Any, index: int) -> Any:
    selector_map = await browser_session.get_selector_map()
    node = selector_map.get(index)
    if node is None:
        raise ValueError(f"Could not find browser-use element index {index}")
    xpath = str(getattr(node, "xpath", "") or "").strip()
    if not xpath:
        raise ValueError(f"Browser-use element index {index} did not expose an xpath")
    page = await browser_session.must_get_current_page()
    return page.locator(f"xpath={xpath}").first()


async def _xpath_from_browser_use_index(browser_session: Any, index: int) -> str:
    selector_map = await browser_session.get_selector_map()
    node = selector_map.get(index)
    if node is None:
        raise ValueError(f"Could not find browser-use element index {index}")
    xpath = str(getattr(node, "xpath", "") or "").strip()
    if not xpath:
        raise ValueError(f"Browser-use element index {index} did not expose an xpath")
    return xpath


async def _pointer_target_spec(
    browser_session: Any,
    *,
    index: int | None = None,
    ref: str | None = None,
) -> JsonDict:
    if ref is not None and str(ref).strip():
        normalized_ref = _normalize_affordance_ref_value(ref)
        if normalized_ref is None:
            raise ValueError("Affordance ref must be non-empty")
        return {"ref": normalized_ref}
    if index is None:
        raise ValueError("Provide either index or ref")
    return {"xpath": await _xpath_from_browser_use_index(browser_session, int(index))}


async def _run_spatial_action(page: Any, payload: JsonDict) -> JsonDict:
    script = f"""(...args) => (async () => {{
        const payload = {json.dumps(payload, ensure_ascii=False)};
        const clean = (value) => String(value ?? "").replace(/\\s+/g, " ").trim();
        const isVisible = (el) => {{
            if (!(el instanceof Element)) return false;
            const style = window.getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return style.visibility !== "hidden" && style.display !== "none" && rect.width > 1 && rect.height > 1;
        }};
        const labelFor = (el) => {{
            if (!el) return "";
            const aria = clean(el.getAttribute?.("aria-label"));
            if (aria) return aria;
            const labelledBy = clean(el.getAttribute?.("aria-labelledby"));
            if (labelledBy) {{
                const text = labelledBy.split(/\\s+/).map((id) => clean(document.getElementById(id)?.textContent)).filter(Boolean).join(" ");
                if (text) return text;
            }}
            if (el.labels && el.labels.length > 0) {{
                const explicit = clean(Array.from(el.labels).map((label) => label.textContent).join(" "));
                if (explicit) return explicit;
            }}
            const closestLabel = el.closest?.("label");
            if (closestLabel) {{
                const nested = clean(closestLabel.textContent);
                if (nested) return nested;
            }}
            return clean(el.textContent).slice(0, 120) || clean(el.getAttribute?.("placeholder")) || clean(el.getAttribute?.("value"));
        }};
        const markerFor = (el) => clean([
            labelFor(el),
            el.id,
            typeof el.className === "string" ? el.className : "",
            el.getAttribute?.("role"),
            el.getAttribute?.("aria-label"),
        ].join(" "));
        const tag = (items, prefix) => items.filter(isVisible).forEach((el, index) => {{
            if (!el.hasAttribute("data-genui-affordance-ref")) {{
                el.setAttribute("data-genui-affordance-ref", `${{prefix}}:${{index + 1}}`);
            }}
        }});
        const allVisible = Array.from(document.querySelectorAll("*")).filter(isVisible);
        tag(Array.from(document.querySelectorAll("select")), "select");
        tag(Array.from(document.querySelectorAll("button, [role='button'], input[type='button'], input[type='submit']")), "button");
        tag(Array.from(document.querySelectorAll("[draggable='true']")), "draggable");
        tag(allVisible.filter((el) => /drop|drag|lane|timeline|canvas|board|target|region|grid|sortable/i.test(markerFor(el)) && el !== document.body && el !== document.documentElement), "drop");
        tag(allVisible.filter((el) => /^(canvas|svg)$/i.test(el.tagName) || /canvas|grid|board|game|sprite|target|lane|timeline/i.test(markerFor(el))), "visual-region");
        tag(Array.from(document.querySelectorAll(".target, [data-target], [role='option'], svg text, svg [aria-label]")).filter((el) => labelFor(el)), "visual-target");
        const byXpath = (xpath) => {{
            const result = document.evaluate("/" + xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
            return result.singleNodeValue;
        }};
        const resolve = (spec) => {{
            if (!spec) return null;
            if (spec.ref) return document.querySelector(`[data-genui-affordance-ref="${{CSS.escape(spec.ref)}}"]`);
            if (spec.xpath) return byXpath(spec.xpath);
            return null;
        }};
        const bbox = (el) => {{
            if (!(el instanceof Element) || !isVisible(el)) throw new Error("Target is not visible");
            const rect = el.getBoundingClientRect();
            return {{ x: rect.x, y: rect.y, width: rect.width, height: rect.height }};
        }};
        const unit = (value, fallback = 0.5) => {{
            let number = Number(value);
            if (!Number.isFinite(number)) number = fallback;
            if (number > 1) number = number / 100;
            return Math.max(0, Math.min(1, number));
        }};
        const pointIn = (rect, xPct, yPct) => [
            rect.x + rect.width * unit(xPct),
            rect.y + rect.height * unit(yPct),
        ];
        const mouse = (type, x, y, extra = {{}}) => new MouseEvent(type, {{
            bubbles: true,
            cancelable: true,
            view: window,
            clientX: x,
            clientY: y,
            ...extra,
        }});
        const clickAt = (x, y) => {{
            const target = document.elementFromPoint(x, y);
            if (!(target instanceof Element)) throw new Error("No element at requested click point");
            target.dispatchEvent(mouse("mousemove", x, y));
            target.dispatchEvent(mouse("mousedown", x, y, {{ buttons: 1 }}));
            target.dispatchEvent(mouse("mouseup", x, y));
            target.dispatchEvent(mouse("click", x, y));
            return {{ label: labelFor(target), tag: target.tagName.toLowerCase() }};
        }};
        const action = payload.action;
        if (action === "click_at" || action === "move_mouse") {{
            let x = payload.x;
            let y = payload.y;
            let target = null;
            let rect = null;
            if (!(Number.isFinite(Number(x)) && Number.isFinite(Number(y)))) {{
                target = resolve(payload.target);
                if (!(target instanceof Element)) throw new Error("Could not resolve target");
                rect = bbox(target);
                [x, y] = pointIn(rect, payload.x_pct, payload.y_pct);
            }}
            if (action === "move_mouse") {{
                const hoverTarget = document.elementFromPoint(x, y);
                if (hoverTarget instanceof Element) {{
                    hoverTarget.dispatchEvent(mouse("mousemove", x, y));
                    hoverTarget.dispatchEvent(mouse("mouseover", x, y));
                }}
            }} else {{
                clickAt(x, y);
            }}
            return JSON.stringify({{ action, x: Math.round(x), y: Math.round(y), target_label: target ? labelFor(target) : "" }});
        }}
        if (action === "drag_and_drop" || action === "drag_to_point") {{
            const source = resolve(payload.source);
            if (!(source instanceof Element)) throw new Error("Could not resolve drag source");
            const sourceRect = bbox(source);
            const [sourceX, sourceY] = pointIn(sourceRect, payload.source_x_pct, payload.source_y_pct);
            let target = null;
            let targetX = payload.x;
            let targetY = payload.y;
            if (!(Number.isFinite(Number(targetX)) && Number.isFinite(Number(targetY)))) {{
                target = resolve(payload.target);
                if (!(target instanceof Element)) throw new Error("Could not resolve drag target");
                const targetRect = bbox(target);
                [targetX, targetY] = pointIn(targetRect, payload.target_x_pct, payload.target_y_pct);
            }} else {{
                target = document.elementFromPoint(Number(targetX), Number(targetY));
            }}
            if (!(target instanceof Element)) throw new Error("No element at requested drag target point");
            const dataTransfer = new DataTransfer();
            source.dispatchEvent(mouse("mousemove", sourceX, sourceY));
            source.dispatchEvent(mouse("mousedown", sourceX, sourceY, {{ buttons: 1 }}));
            source.dispatchEvent(new DragEvent("dragstart", {{ bubbles: true, cancelable: true, clientX: sourceX, clientY: sourceY, dataTransfer }}));
            await new Promise((resolveDelay) => setTimeout(resolveDelay, 50));
            target.dispatchEvent(mouse("mousemove", Number(targetX), Number(targetY), {{ buttons: 1 }}));
            target.dispatchEvent(new DragEvent("dragenter", {{ bubbles: true, cancelable: true, clientX: Number(targetX), clientY: Number(targetY), dataTransfer }}));
            target.dispatchEvent(new DragEvent("dragover", {{ bubbles: true, cancelable: true, clientX: Number(targetX), clientY: Number(targetY), dataTransfer }}));
            await new Promise((resolveDelay) => setTimeout(resolveDelay, 50));
            target.dispatchEvent(new DragEvent("drop", {{ bubbles: true, cancelable: true, clientX: Number(targetX), clientY: Number(targetY), dataTransfer }}));
            source.dispatchEvent(new DragEvent("dragend", {{ bubbles: true, cancelable: true, clientX: Number(targetX), clientY: Number(targetY), dataTransfer }}));
            source.dispatchEvent(mouse("mouseup", Number(targetX), Number(targetY)));
            return JSON.stringify({{
                action,
                source_label: labelFor(source),
                target_label: labelFor(target),
                source: {{ x: Math.round(sourceX), y: Math.round(sourceY) }},
                target: {{ x: Math.round(Number(targetX)), y: Math.round(Number(targetY)) }},
            }});
        }}
        throw new Error(`Unknown spatial action: ${{action}}`);
    }})()"""
    return await _page_evaluate_json(page, script)


async def _locator_from_affordance_ref(page: Any, ref: str) -> Any:
    await _install_affordance_refs(page)
    normalized = str(ref or "").strip()
    if not re.match(
        r"^(select|button|draggable|drop|visual-region|visual-target):[1-9]\d*$", normalized
    ):
        raise ValueError(
            "Affordance ref must look like select:1, button:1, draggable:1, "
            "drop:1, visual-region:1, or visual-target:1"
        )
    locator = page.locator(f'[data-genui-affordance-ref="{normalized}"]').first()
    if await locator.count() < 1:
        raise ValueError(f"Could not resolve affordance ref {normalized}")
    return locator


async def _pointer_target_locator(
    *,
    page: Any,
    browser_session: Any,
    index: int | None = None,
    ref: str | None = None,
) -> Any:
    if ref is not None and str(ref).strip():
        return await _locator_from_affordance_ref(page, str(ref).strip())
    if index is None:
        raise ValueError("Provide either index or ref")
    return await _locator_from_browser_use_index(browser_session, int(index))


async def _locator_bbox(locator: Any) -> JsonDict:
    bbox = await locator.bounding_box()
    if not isinstance(bbox, dict):
        raise ValueError("Target element has no visible bounding box")
    return {
        "x": float(bbox["x"]),
        "y": float(bbox["y"]),
        "width": float(bbox["width"]),
        "height": float(bbox["height"]),
    }


def _point_in_bbox(
    bbox: JsonDict, *, x_pct: object = 0.5, y_pct: object = 0.5
) -> tuple[float, float]:
    px = _clamped_unit(x_pct)
    py = _clamped_unit(y_pct)
    return (
        float(bbox["x"]) + float(bbox["width"]) * px,
        float(bbox["y"]) + float(bbox["height"]) * py,
    )


async def _wait_for_browser_use_page_ready(page: Any) -> None:
    for _ in range(10):
        try:
            payload = await _page_evaluate_json(
                page,
                """() => JSON.stringify({
                    ready: (() => {
                        const body = document.body;
                        if (!body) return false;
                        const text = (body.innerText ?? "").trim();
                        if (text.length >= 20) return true;
                        const root = document.getElementById("root");
                        if (root && (root.textContent ?? "").trim().length >= 20) return true;
                        const interactiveCount = document.querySelectorAll(
                            "input, textarea, select, button, a[href], [role='button'], [role='link'], [role='textbox'], [role='combobox'], [role='checkbox'], [role='radio'], [role='option']",
                        ).length;
                        return interactiveCount > 0;
                    })()
                })""",
            )
        except Exception:
            payload = {"ready": False}
        if payload.get("ready") is True:
            return
        await asyncio.sleep(0.5)


async def _install_browser_use_console_collectors(page: Any) -> list[str]:
    payload = await _page_evaluate_json(
        page,
        """() => {
            const key = "__GENUI_ACTOR_CONSOLE_ERRORS__";
            if (!Array.isArray(window[key])) {
                window[key] = [];
                window.addEventListener("error", (event) => {
                    const message = event?.message || event?.error?.message || "Unknown window error";
                    window[key].push(String(message));
                });
                window.addEventListener("unhandledrejection", (event) => {
                    const reason = event?.reason;
                    const text =
                        typeof reason === "string"
                            ? reason
                            : reason?.message || "Unhandled promise rejection";
                    window[key].push(String(text));
                });
                const originalError = console.error.bind(console);
                console.error = (...args) => {
                    try {
                        window[key].push(
                            args
                                .map((arg) => {
                                    if (typeof arg === "string") return arg;
                                    try {
                                        return JSON.stringify(arg);
                                    } catch {
                                        return String(arg);
                                    }
                                })
                                .join(" "),
                        );
                    } catch {}
                    return originalError(...args);
                };
            }
            return JSON.stringify({ errors: window[key] });
        }""",
    )
    errors = payload.get("errors")
    return [str(item) for item in errors] if isinstance(errors, list) else []


async def _read_runtime_logs_async(page: Any) -> JsonDict:
    return _validate_runtime_logs(
        await _page_evaluate_json(
            page,
            """() => {
                const getter = window.__GENUI_GET_RUNTIME_LOGS__;
                if (typeof getter !== "function") {
                    throw new Error("Generated app did not expose __GENUI_GET_RUNTIME_LOGS__");
                }
                return JSON.stringify(getter());
            }""",
        )
    )


def _empty_browser_runtime_logs() -> JsonDict:
    return {
        "tool_logs": [],
        "resource_logs": [],
        "side_effect_logs": [],
        "confirmation_events": [],
        "scenarios": {},
    }


def _is_runtime_bridge_unavailable_error(error: object) -> bool:
    return "generated app did not expose __genui_get_runtime_logs__" in str(error or "").lower()


def _append_unique_error(errors: list[str], exc: object) -> None:
    text = str(exc)
    if text and text not in errors:
        errors.append(text)


async def _collect_interaction_affordance_facts(page: Any) -> JsonDict:
    await _install_affordance_refs(page)
    return await _page_evaluate_json(
        page,
        """() => {
            const clean = (value) => String(value ?? "").replace(/\\s+/g, " ").trim();
            const isVisible = (el) => {
                if (!(el instanceof Element)) return false;
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.visibility !== "hidden" && style.display !== "none" && rect.width > 1 && rect.height > 1;
            };
            const bbox = (el) => {
                const rect = el.getBoundingClientRect();
                return {
                    x: Math.round(rect.x),
                    y: Math.round(rect.y),
                    width: Math.round(rect.width),
                    height: Math.round(rect.height),
                    center_x: Math.round(rect.x + rect.width / 2),
                    center_y: Math.round(rect.y + rect.height / 2),
                };
            };
            const labelFor = (el) => {
                if (!el) return "";
                const aria = clean(el.getAttribute?.("aria-label"));
                if (aria) return aria;
                if (el.labels && el.labels.length > 0) {
                    const explicit = clean(Array.from(el.labels).map((label) => label.textContent).join(" "));
                    if (explicit) return explicit;
                }
                const labelledBy = clean(el.getAttribute?.("aria-labelledby"));
                if (labelledBy) {
                    const text = labelledBy.split(/\\s+/).map((id) => clean(document.getElementById(id)?.textContent)).filter(Boolean).join(" ");
                    if (text) return text;
                }
                const closestLabel = el.closest?.("label");
                if (closestLabel) {
                    const nested = clean(closestLabel.textContent);
                    if (nested) return nested;
                }
                return clean(el.textContent).slice(0, 120) || clean(el.getAttribute?.("placeholder")) || clean(el.getAttribute?.("value"));
            };
            const isEmptyOptionText = (value) => {
                const text = clean(value);
                return !text || /^(-+\\s*)?select(\\s*-+)?$/i.test(text) || /^choose/i.test(text);
            };
            const markerFor = (el) => clean([
                labelFor(el),
                el.id,
                typeof el.className === "string" ? el.className : "",
                el.getAttribute?.("role"),
                el.getAttribute?.("aria-label"),
            ].join(" "));
            const selects = Array.from(document.querySelectorAll("select"))
                .filter(isVisible)
                .map((select, index) => {
                    const selected = select.selectedOptions?.[0];
                    const selectedText = clean(selected?.textContent);
                    const nonEmptyOptions = Array.from(select.options).filter((option) => (
                        !option.disabled && !isEmptyOptionText(option.textContent) &&
                        (clean(option.value) || clean(option.textContent))
                    ));
                    return {
                        index: index + 1,
                        ref: select.getAttribute("data-genui-affordance-ref"),
                        label: labelFor(select),
                        disabled: select.disabled || select.getAttribute("aria-disabled") === "true",
                        value: clean(select.value),
                        selected_text: selectedText,
                        is_unfilled: !clean(select.value) || isEmptyOptionText(selectedText),
                        option_count: select.options.length,
                        non_empty_options: nonEmptyOptions.map((option) => ({
                            value: clean(option.value),
                            text: clean(option.textContent),
                        })).slice(0, 8),
                        bbox: bbox(select),
                    };
                });
            const buttons = Array.from(document.querySelectorAll("button, [role='button'], input[type='button'], input[type='submit']"))
                .filter(isVisible)
                .map((button, index) => ({
                    index: index + 1,
                    ref: button.getAttribute("data-genui-affordance-ref"),
                    label: labelFor(button),
                    disabled: Boolean(button.disabled) || button.getAttribute("aria-disabled") === "true",
                    bbox: bbox(button),
                }))
                .filter((item) => item.label || item.disabled)
                .slice(0, 30);
            const draggables = Array.from(document.querySelectorAll("[draggable='true']"))
                .filter(isVisible)
                .map((el, index) => ({ index: index + 1, ref: el.getAttribute("data-genui-affordance-ref"), label: labelFor(el), bbox: bbox(el) }))
                .slice(0, 20);
            const dropRegions = Array.from(document.querySelectorAll("*"))
                .filter(isVisible)
                .map((el, index) => ({ index: index + 1, ref: el.getAttribute("data-genui-affordance-ref"), label: labelFor(el), marker: markerFor(el), tag: el.tagName.toLowerCase(), bbox: bbox(el) }))
                .filter((item) => item.tag !== "body" && item.tag !== "html")
                .filter((item) => /drop|drag|lane|timeline|canvas|board|target|region|grid|sortable/i.test(item.marker))
                .slice(0, 20);
            const visualRegions = Array.from(document.querySelectorAll("*"))
                .filter(isVisible)
                .map((el, index) => ({
                    index: index + 1,
                    ref: el.getAttribute("data-genui-affordance-ref"),
                    tag: el.tagName.toLowerCase(),
                    label: labelFor(el) || clean(el.closest("section, article, main, div")?.textContent).slice(0, 120),
                    marker: markerFor(el),
                    text: clean(el.textContent).slice(0, 160),
                    child_count: el.children.length,
                    bbox: bbox(el),
                }))
                .filter((item) => item.tag !== "body" && item.tag !== "html")
                .filter((item) => /^(canvas|svg)$/.test(item.tag) || /canvas|grid|board|game|sprite|target|lane|timeline/i.test(item.marker))
                .slice(0, 20);
            const visualTargets = Array.from(document.querySelectorAll(".target, [data-target], [role='option'], svg text, svg [aria-label]"))
                .filter(isVisible)
                .map((el, index) => ({ index: index + 1, ref: el.getAttribute("data-genui-affordance-ref"), label: labelFor(el), tag: el.tagName.toLowerCase(), bbox: bbox(el) }))
                .filter((item) => item.label)
                .slice(0, 30);
            return JSON.stringify({
                summary: {
                    select_count: selects.length,
                    unfilled_select_count: selects.filter((item) => item.is_unfilled && !item.disabled).length,
                    enabled_button_count: buttons.filter((item) => !item.disabled).length,
                    disabled_button_count: buttons.filter((item) => item.disabled).length,
                    draggable_count: draggables.length,
                    drop_region_count: dropRegions.length,
                    canvas_svg_count: visualRegions.length,
                    visual_target_count: visualTargets.length,
                },
                selects,
                buttons,
                drag_drop: { draggables, drop_regions: dropRegions },
                visual: { regions: visualRegions, targets: visualTargets },
            });
        }""",
    )


def _format_interaction_affordance_context(facts: JsonDict) -> str:
    if not isinstance(facts, dict):
        return ""
    compact: JsonDict = {
        "summary": facts.get("summary", {}),
        "selects": [],
        "buttons": [],
    }
    for item in facts.get("selects", []) if isinstance(facts.get("selects"), list) else []:
        if not isinstance(item, dict):
            continue
        compact["selects"].append(
            {
                "ref": item.get("ref", ""),
                "label": item.get("label", ""),
                "disabled": bool(item.get("disabled")),
                "value": item.get("value", ""),
                "selected_text": item.get("selected_text", ""),
                "is_unfilled": bool(item.get("is_unfilled")),
                "options": (
                    item.get("non_empty_options", [])[:6]
                    if isinstance(item.get("non_empty_options"), list)
                    else []
                ),
            }
        )
    for item in facts.get("buttons", []) if isinstance(facts.get("buttons"), list) else []:
        if not isinstance(item, dict):
            continue
        compact["buttons"].append(
            {
                "ref": item.get("ref", ""),
                "label": item.get("label", ""),
                "disabled": bool(item.get("disabled")),
            }
        )
    drag_drop = facts.get("drag_drop", {})
    if isinstance(drag_drop, dict):
        compact["drag_drop"] = {
            "draggables": (
                drag_drop.get("draggables", [])[:8]
                if isinstance(drag_drop.get("draggables"), list)
                else []
            ),
            "drop_regions": (
                drag_drop.get("drop_regions", [])[:8]
                if isinstance(drag_drop.get("drop_regions"), list)
                else []
            ),
        }
    visual = facts.get("visual", {})
    if isinstance(visual, dict):
        compact["visual"] = {
            "regions": (
                visual.get("regions", [])[:8] if isinstance(visual.get("regions"), list) else []
            ),
            "targets": (
                visual.get("targets", [])[:12] if isinstance(visual.get("targets"), list) else []
            ),
        }
    return (
        "<interaction_affordances>\n"
        + json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
        + "\n</interaction_affordances>"
    )


async def _collect_browser_use_observation(
    browser_session: Any,
    artifact_dir: Path,
    step: int,
    history: list[JsonDict],
    phase: str,
) -> JsonDict:
    page = await browser_session.must_get_current_page()
    console_errors: list[str] = []
    try:
        await _wait_for_browser_use_page_ready(page)
    except Exception as exc:
        _append_unique_error(console_errors, exc)
    try:
        console_errors.extend(await _install_browser_use_console_collectors(page))
    except Exception as exc:
        _append_unique_error(console_errors, exc)
    screenshot_path = artifact_dir / f"step-{step:02d}-{phase}.png"

    page_state: JsonDict = {
        "text": "",
        "dom": "",
        "url": str(getattr(page, "url", "")),
        "elements": [],
    }
    try:
        page_state = await _page_evaluate_json(
            page,
            """() => {
                const candidates = Array.from(
                    document.querySelectorAll(
                        "input, textarea, select, button, a[href], [role='button'], [role='link'], [role='textbox'], [role='combobox'], [role='checkbox'], [role='radio'], [role='option']",
                    ),
                );
                const labelText = (element) => {
                    if ("labels" in element && element.labels?.length) {
                        return Array.from(element.labels)
                            .map((node) => node.textContent?.trim() ?? "")
                            .join(" ")
                            .trim();
                    }
                    if (element.getAttribute("aria-label")) {
                        return element.getAttribute("aria-label");
                    }
                    const labelledBy = element.getAttribute("aria-labelledby");
                    if (labelledBy) {
                        return labelledBy
                            .split(/\\s+/)
                            .map((id) => document.getElementById(id)?.textContent?.trim() ?? "")
                            .join(" ")
                            .trim();
                    }
                    return "";
                };
                const computeRole = (element) => {
                    const explicitRole = element.getAttribute("role");
                    if (explicitRole) return explicitRole;
                    const tagName = element.tagName.toLowerCase();
                    if (tagName === "button") return "button";
                    if (tagName === "a") return "link";
                    if (tagName === "select") {
                        return element instanceof HTMLSelectElement && (element.multiple || element.size > 1)
                            ? "listbox"
                            : "combobox";
                    }
                    if (tagName === "textarea") return "textbox";
                    if (tagName === "input") {
                        const type = (element.getAttribute("type") ?? "text").toLowerCase();
                        if (["text", "search", "email", "tel", "url", "date", "file"].includes(type)) return "textbox";
                        if (type === "number") return "spinbutton";
                        if (type === "checkbox") return "checkbox";
                        if (type === "radio") return "radio";
                        return "textbox";
                    }
                    return tagName;
                };
                const isVisible = (element) => {
                    const style = window.getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return (
                        style.display !== "none" &&
                        style.visibility !== "hidden" &&
                        style.opacity !== "0" &&
                        rect.width > 0 &&
                        rect.height > 0
                    );
                };
                const interactive = [];
                for (const element of candidates) {
                    if (!(element instanceof HTMLElement)) continue;
                    if (!isVisible(element)) continue;
                    const role = computeRole(element);
                    const name = (
                        labelText(element) ||
                        element.getAttribute("placeholder") ||
                        element.textContent ||
                        element.getAttribute("value") ||
                        ""
                    )
                        .replace(/\\s+/g, " ")
                        .trim();
                    if (!name && !["textbox", "combobox", "spinbutton"].includes(role)) continue;
                    interactive.push({
                        role,
                        name,
                        placeholder: element.getAttribute("placeholder") ?? "",
                        disabled:
                            element instanceof HTMLInputElement ||
                            element instanceof HTMLTextAreaElement ||
                            element instanceof HTMLSelectElement ||
                            element instanceof HTMLButtonElement
                                ? element.disabled
                                : element.getAttribute("aria-disabled") === "true",
                        value:
                            element instanceof HTMLInputElement ||
                            element instanceof HTMLTextAreaElement ||
                            element instanceof HTMLSelectElement
                                ? element.value
                                : "",
                        checked:
                            element instanceof HTMLInputElement &&
                            ["checkbox", "radio"].includes((element.getAttribute("type") ?? "").toLowerCase())
                                ? element.checked
                                : null,
                        ariaChecked: element.getAttribute("aria-checked"),
                        ariaPressed: element.getAttribute("aria-pressed"),
                        ariaSelected: element.getAttribute("aria-selected"),
                        ariaCurrent: element.getAttribute("aria-current"),
                        options:
                            element instanceof HTMLSelectElement
                                ? Array.from(element.options).map((option) => ({
                                    value: option.value ?? "",
                                    label: option.label ?? option.textContent ?? "",
                                    selected: option.selected,
                                }))
                                : [],
                    });
                }
                return JSON.stringify({
                    text: document.body?.innerText ?? "",
                    dom: document.body?.outerHTML ?? "",
                    url: window.location.href,
                    elements: interactive.map((element, index) => ({ index: index + 1, ...element })),
                });
            }""",
        )
    except Exception as exc:
        _append_unique_error(console_errors, exc)

    screenshot_path_text = str(screenshot_path)
    try:
        await browser_session.take_screenshot(
            path=screenshot_path_text, format="png", full_page=True
        )
    except Exception as exc:
        _append_unique_error(console_errors, exc)
        try:
            await page.screenshot(path=screenshot_path_text, full_page=True)
        except Exception as fallback_exc:
            _append_unique_error(console_errors, fallback_exc)
            screenshot_path_text = ""

    try:
        affordance_facts = await _collect_interaction_affordance_facts(page)
    except Exception as exc:
        _append_unique_error(console_errors, exc)
        affordance_facts = {}
    try:
        runtime_logs = await _read_runtime_logs_async(page)
    except Exception as exc:
        if not _is_runtime_bridge_unavailable_error(exc):
            _append_unique_error(console_errors, exc)
        runtime_logs = _empty_browser_runtime_logs()
    try:
        selector_map = await browser_session.get_selector_map()
    except Exception as exc:
        _append_unique_error(console_errors, exc)
        selector_map = {}
    try:
        ax_tree = await browser_session.get_state_as_text()
    except Exception as exc:
        _append_unique_error(console_errors, exc)
        ax_tree = ""

    return {
        "step": step,
        "phase": phase,
        "url": page_state["url"],
        "elements": page_state["elements"],
        "actionableElements": _selector_map_summaries(selector_map),
        "affordanceFacts": affordance_facts,
        "finalText": page_state["text"],
        "domTree": page_state["dom"],
        "screenshotPath": screenshot_path_text,
        "screenshotMode": "full_page",
        "axTree": ax_tree,
        "consoleErrors": console_errors,
        "runtimeLogs": runtime_logs,
        "history": history,
    }


async def _resolve_upload_input(page: Any, xpath: str) -> JsonDict:
    script = f"""() => {{
        const result = document.evaluate(
            {json.dumps("/" + xpath)},
            document,
            null,
            XPathResult.FIRST_ORDERED_NODE_TYPE,
            null,
        );
        let element = result.singleNodeValue;
        if (!(element instanceof HTMLElement)) {{
            return JSON.stringify({{ found: false, reason: "target element not found" }});
        }}
        let input = null;
        if (element instanceof HTMLInputElement && element.type === "file") {{
            input = element;
        }} else if (element instanceof HTMLLabelElement && element.control instanceof HTMLInputElement && element.control.type === "file") {{
            input = element.control;
        }} else {{
            input = element.querySelector?.('input[type="file"]') ?? null;
        }}
        if (!(input instanceof HTMLInputElement)) {{
            const allInputs = Array.from(document.querySelectorAll('input[type="file"]'));
            if (allInputs.length === 1) {{
                input = allInputs[0];
            }}
        }}
        if (!(input instanceof HTMLInputElement)) {{
            return JSON.stringify({{ found: false, reason: "no file input found" }});
        }}
        let marker = input.getAttribute("data-genui-upload-target");
        if (!marker) {{
            marker = "genui-upload-" + Math.random().toString(36).slice(2);
            input.setAttribute("data-genui-upload-target", marker);
        }}
        return JSON.stringify({{
            found: true,
            selector: `input[data-genui-upload-target="${{marker}}"]`,
            accept: input.getAttribute("accept") ?? "",
        }});
    }}"""
    return await _page_evaluate_json(page, script)


def _fixture_extension(accept: str) -> str:
    lowered = accept.lower()
    if "pdf" in lowered:
        return "pdf"
    if "json" in lowered:
        return "json"
    if "csv" in lowered:
        return "csv"
    if "markdown" in lowered or ".md" in lowered:
        return "md"
    if "image" in lowered or "png" in lowered or "jpg" in lowered or "jpeg" in lowered:
        return "png"
    return "txt"


def _fixture_bytes(spec: UploadFixtureSpec, extension: str) -> bytes:
    source_text = spec.source_value.strip()
    base_text = (
        f"Fixture: {spec.name}\n"
        f"Field hint: {spec.field_hint or spec.name}\n"
        "This deterministic upload artifact was generated by the GenUI benchmark actor.\n"
        f"\n{source_text}\n"
    )
    if extension == "png":
        return DEFAULT_UPLOAD_MOCK_IMAGE.read_bytes()
    if extension == "pdf":
        body = (
            "%PDF-1.4\n"
            "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
            "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
            "3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents 4 0 R >> endobj\n"
            f"4 0 obj << /Length {len(base_text) + 45} >> stream\nBT /F1 12 Tf 36 96 Td ({base_text.replace('(', '[').replace(')', ']')}) Tj ET\nendstream endobj\n"
            "xref\n0 5\n0000000000 65535 f \n"
            "0000000010 00000 n \n0000000060 00000 n \n0000000117 00000 n \n0000000207 00000 n \n"
            "trailer << /Root 1 0 R /Size 5 >>\nstartxref\n340\n%%EOF\n"
        )
        return body.encode("utf-8")
    if extension == "json":
        try:
            json.loads(source_text)
        except json.JSONDecodeError:
            pass
        else:
            return source_text.encode("utf-8")
        return json.dumps(
            {
                "fixture_name": spec.name,
                "field_hint": spec.field_hint,
                "summary": "Deterministic upload fixture for benchmark execution.",
                "content": source_text,
            },
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
    if extension == "csv":
        if "," in source_text or "\n" in source_text:
            return source_text.encode("utf-8")
        return f"field,value\nfixture_name,{spec.name}\nfield_hint,{spec.field_hint}\n".encode(
            "utf-8"
        )
    return source_text.encode("utf-8")


def _materialize_upload_fixture(
    artifact_dir: Path,
    spec: UploadFixtureSpec,
    *,
    accept: str,
) -> Path:
    extension = _fixture_extension(accept)
    fixture_dir = artifact_dir / "upload-fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    path = fixture_dir / f"{_slugify_filename(spec.name)}.{extension}"
    path.write_bytes(_fixture_bytes(spec, extension))
    return path


def _coerce_download_read_limit(max_chars: object) -> int:
    try:
        value = int(max_chars) if max_chars is not None else _DOWNLOAD_READ_DEFAULT_CHARS
    except (TypeError, ValueError):
        value = _DOWNLOAD_READ_DEFAULT_CHARS
    return min(_DOWNLOAD_READ_MAX_CHARS, max(1, value))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _downloaded_file_paths(browser_session: Any) -> list[Path]:
    raw_paths = getattr(browser_session, "downloaded_files", [])
    if callable(raw_paths):
        raw_paths = raw_paths()
    if not isinstance(raw_paths, list):
        return []
    paths: list[Path] = []
    for raw_path in raw_paths:
        try:
            path = Path(str(raw_path)).expanduser().resolve(strict=False)
        except (TypeError, ValueError, OSError):
            continue
        paths.append(path)
    return paths


def _downloaded_file_metadata(path: Path, *, index: int) -> JsonDict:
    exists = path.exists()
    size_bytes = path.stat().st_size if exists else None
    mime_type, _ = mimetypes.guess_type(path.name)
    return {
        "index": index,
        "filename": path.name,
        "size_bytes": size_bytes,
        "mime_type": mime_type or "application/octet-stream",
        "exists": exists,
    }


def _resolve_downloaded_file_path(
    downloaded_paths: list[Path],
    *,
    index: object = None,
    filename: object = None,
    path: object = None,
) -> tuple[int, Path]:
    if not downloaded_paths:
        raise ValueError("No downloaded files are available in this browser session")
    if index is not None:
        try:
            normalized_index = int(index)
        except (TypeError, ValueError) as exc:
            raise ValueError("Downloaded file index must be an integer") from exc
        if normalized_index < 0 or normalized_index >= len(downloaded_paths):
            raise ValueError(
                f"Downloaded file index {normalized_index} is out of range; "
                f"available indexes are 0..{len(downloaded_paths) - 1}"
            )
        return normalized_index, downloaded_paths[normalized_index]
    lookup_values = [str(value or "").strip() for value in (filename, path)]
    lookup_values = [value for value in lookup_values if value]
    for lookup in lookup_values:
        lookup_name = Path(lookup).name
        for candidate_index, candidate_path in enumerate(downloaded_paths):
            if lookup == str(candidate_path) or lookup_name == candidate_path.name:
                return candidate_index, candidate_path
    raise ValueError(
        "Provide a downloaded file index from list_downloaded_files. "
        "Raw paths are accepted only when they match a file downloaded in this browser session."
    )


def _read_downloaded_file_payload(
    downloaded_paths: list[Path],
    *,
    index: object = None,
    filename: object = None,
    path: object = None,
    max_chars: object = None,
) -> JsonDict:
    resolved_index, resolved_path = _resolve_downloaded_file_path(
        downloaded_paths,
        index=index,
        filename=filename,
        path=path,
    )
    resolved_path = resolved_path.resolve(strict=False)
    if not resolved_path.exists() or not resolved_path.is_file():
        raise ValueError(f"Downloaded file {resolved_index} no longer exists")
    size_bytes = resolved_path.stat().st_size
    read_chars = _coerce_download_read_limit(max_chars)
    read_bytes_limit = min(_DOWNLOAD_READ_MAX_BYTES, max(read_chars * 4, 4096))
    with resolved_path.open("rb") as handle:
        raw = handle.read(read_bytes_limit + 1)
    truncated_by_bytes = len(raw) > read_bytes_limit
    if truncated_by_bytes:
        raw = raw[:read_bytes_limit]
    mime_type, _ = mimetypes.guess_type(resolved_path.name)
    payload: JsonDict = {
        "index": resolved_index,
        "filename": resolved_path.name,
        "size_bytes": size_bytes,
        "mime_type": mime_type or "application/octet-stream",
        "sha256": _sha256_file(resolved_path),
    }
    if b"\x00" in raw:
        payload.update(
            {
                "is_text": False,
                "content": None,
                "truncated": truncated_by_bytes,
            }
        )
        return payload
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        payload.update(
            {
                "is_text": False,
                "content": None,
                "truncated": truncated_by_bytes,
            }
        )
        return payload
    truncated_by_chars = len(text) > read_chars
    if truncated_by_chars:
        text = text[:read_chars]
    json_valid = False
    if (mime_type and "json" in mime_type) or resolved_path.suffix.lower() == ".json":
        try:
            json.loads(text)
        except json.JSONDecodeError:
            json_valid = False
        else:
            json_valid = True
    payload.update(
        {
            "is_text": True,
            "content": text,
            "truncated": truncated_by_bytes or truncated_by_chars,
            "json_valid": json_valid,
        }
    )
    return payload


def _browser_use_action_payload(action_dump: object) -> JsonDict | None:
    if not isinstance(action_dump, dict) or len(action_dump) != 1:
        return None
    action_name, params = next(iter(action_dump.items()))
    if not isinstance(params, dict):
        params = {}
    mapped = {
        "click": "click",
        "click_at": "click_at",
        "move_mouse": "move_mouse",
        "drag_and_drop": "drag_and_drop",
        "drag_to_point": "drag_to_point",
        "input": "type",
        "select_dropdown": "select",
        "upload_fixture": "upload",
        "send_keys": "press",
        "scroll": "scroll",
        "wait": "wait",
        "go_back": "go_back",
        "done": "finish",
        "get_runtime_logs": "get_runtime_logs",
        "inspect_interaction_affordances": "inspect_interaction_affordances",
        "list_upload_fixtures": "list_upload_fixtures",
        "list_downloaded_files": "list_downloaded_files",
        "read_downloaded_file": "read_downloaded_file",
        "read_current_visible_state": "read_current_visible_state",
    }.get(action_name, action_name)
    payload: JsonDict = {
        "action": mapped,
        "index": params.get("index"),
        "ref": params.get("ref"),
        "text": params.get("text"),
        "value": params.get("value") or params.get("option") or params.get("fixture_name"),
        "key": params.get("keys"),
        "direction": "down" if params.get("down", True) else "up",
        "amount": params.get("amount"),
        "seconds": params.get("seconds"),
        "x": params.get("x"),
        "y": params.get("y"),
        "x_pct": params.get("x_pct"),
        "y_pct": params.get("y_pct"),
        "source_index": params.get("source_index"),
        "target_index": params.get("target_index"),
        "source_ref": params.get("source_ref"),
        "target_ref": params.get("target_ref"),
        "source_x_pct": params.get("source_x_pct"),
        "source_y_pct": params.get("source_y_pct"),
        "target_x_pct": params.get("target_x_pct"),
        "target_y_pct": params.get("target_y_pct"),
        "filename": params.get("filename"),
        "path": params.get("path"),
        "max_chars": params.get("max_chars"),
        "status": (
            "success"
            if action_name == "done" and params.get("success") is True
            else "stuck"
            if action_name == "done" and params.get("success") is False
            else None
        ),
    }
    return payload


def _browser_use_steps(
    history_dump: list[JsonDict], observations: list[JsonDict]
) -> list[JsonDict]:
    before_observations = {
        int(item["step"]): item
        for item in observations
        if item.get("phase") == "before_action" and isinstance(item.get("step"), int)
    }
    after_observations = {
        int(item["step"]): item
        for item in observations
        if item.get("phase") == "after_action" and isinstance(item.get("step"), int)
    }
    previous_state_hash = ""
    previous_runtime_signature = ""
    steps: list[JsonDict] = []
    for index, item in enumerate(history_dump, start=1):
        model_output = item.get("model_output") if isinstance(item, dict) else {}
        actions = model_output.get("action") if isinstance(model_output, dict) else []
        action_payload = (
            _browser_use_action_payload(actions[0])
            if isinstance(actions, list) and actions
            else None
        )
        result_entries = item.get("result") if isinstance(item, dict) else []
        errors = [
            str(result.get("error"))
            for result in (result_entries if isinstance(result_entries, list) else [])
            if isinstance(result, dict) and result.get("error")
        ]
        extracted_content_values = [
            str(result.get("extracted_content") or "").strip()
            for result in (result_entries if isinstance(result_entries, list) else [])
            if isinstance(result, dict) and str(result.get("extracted_content") or "").strip()
        ]
        after = after_observations.get(index)
        result_payload: JsonDict = {
            "duplicate_action": False,
            "status": "error" if errors else "executed",
        }
        if after is not None:
            current_state_hash = _state_hash(after)
            current_runtime_signature = _normalize_runtime_log_signature(after["runtimeLogs"])
            result_payload["state_changed"] = current_state_hash != previous_state_hash
            result_payload["runtime_changed"] = (
                current_runtime_signature != previous_runtime_signature
            )
            previous_state_hash = current_state_hash
            previous_runtime_signature = current_runtime_signature
        if errors:
            result_payload["error"] = errors[0]
        if extracted_content_values:
            result_payload["extracted_content"] = extracted_content_values[-1]
        step_payload: JsonDict = {"step": index, "result": result_payload}
        if action_payload is not None:
            step_payload["action"] = action_payload
            resolved_target = _resolve_actionable_target(
                before_observations.get(index), action_payload
            )
            if resolved_target is not None:
                step_payload["resolved_target"] = resolved_target
        steps.append(step_payload)
    return steps


def _action_count(steps: list[JsonDict]) -> int:
    count = 0
    for step in steps:
        action = step.get("action")
        if not isinstance(action, dict):
            continue
        if action.get("action") == "finish":
            continue
        count += 1
    return count


def _last_action_name(steps: list[JsonDict]) -> str:
    if not steps:
        return ""
    action = steps[-1].get("action")
    if not isinstance(action, dict):
        return ""
    return _normalize_text(action.get("action"))


def _is_passive_exploration_action(action_name: str) -> bool:
    return action_name in {
        "navigate",
        "wait",
        "scroll",
        "extract",
        "find_text",
        "inspect_interaction_affordances",
        "read_current_visible_state",
        "get_runtime_logs",
        "read_runtime_logs",
    }


def _effective_stuck_limit(policy_state: EvidencePolicyState, steps: list[JsonDict]) -> int:
    limit = policy_state.max_stuck_steps
    if _meaningful_action_count(steps) > 0:
        limit += 3
    if _verification_step_count(steps) > 0:
        limit += 1
    return limit


def _update_evidence_policy(
    *,
    policy_state: EvidencePolicyState,
    observation: JsonDict,
    steps: list[JsonDict],
) -> None:
    policy_state.action_count = _action_count(steps)
    policy_state.latest_observation = observation
    current_state_hash = _state_hash(observation)
    current_runtime_signature = _normalize_runtime_log_signature(observation["runtimeLogs"])
    computed_state_changed = current_state_hash != policy_state.previous_state_hash
    computed_runtime_changed = current_runtime_signature != policy_state.previous_runtime_signature
    state_changed = computed_state_changed
    runtime_changed = computed_runtime_changed
    last_result_payload = None
    if steps:
        last = steps[-1]
        if isinstance(last, dict):
            candidate_result = last.setdefault("result", {})
            if isinstance(candidate_result, dict):
                last_result_payload = candidate_result
                if isinstance(candidate_result.get("state_changed"), bool):
                    state_changed = candidate_result["state_changed"]
                if isinstance(candidate_result.get("runtime_changed"), bool):
                    runtime_changed = candidate_result["runtime_changed"]
    progress_classification = _progress_classification(
        state_changed=state_changed,
        runtime_changed=runtime_changed,
    )
    duplicate_action = False
    policy_state.progress_history.append(progress_classification)
    if len(policy_state.progress_history) > 8:
        policy_state.progress_history = policy_state.progress_history[-8:]
    if isinstance(last_result_payload, dict):
        last_result_payload["state_changed"] = state_changed
        last_result_payload["runtime_changed"] = runtime_changed
        last_result_payload["progress_classification"] = progress_classification
    if steps:
        last_action = steps[-1].get("action")
        if isinstance(last_action, dict):
            duplicate_action = _recent_duplicate_action(steps[:-1], last_action)
            if isinstance(steps[-1].get("result"), dict):
                steps[-1]["result"]["duplicate_action"] = duplicate_action
    if state_changed or runtime_changed:
        policy_state.stuck_count = 0
    else:
        policy_state.stuck_count += 1
    policy_state.previous_state_hash = current_state_hash
    policy_state.previous_runtime_signature = current_runtime_signature
    if not policy_state.allow_submit_without_recent_mutation and _submit_without_recent_mutation(
        steps
    ):
        policy_state.stop_status = "stuck"
        policy_state.stop_rationale = "Stopping because a submit/change action was attempted before any meaningful form or option change was made."
        return
    stuck_limit = _effective_stuck_limit(policy_state, steps)
    last_action_name = _last_action_name(steps)
    if (
        duplicate_action
        and not policy_state.allow_duplicate_action_no_progress
        and not state_changed
        and not runtime_changed
    ):
        has_unresolved_next_step = _observation_has_forward_progress_action(
            observation, steps
        ) or _observation_has_unresolved_atomic_affordance(observation)
        passive_after_progress = (
            _is_passive_exploration_action(last_action_name)
            and _meaningful_action_count(steps) > 0
            and policy_state.stuck_count < stuck_limit
        )
        if not (has_unresolved_next_step or passive_after_progress):
            policy_state.stop_status = "stuck"
            policy_state.stop_rationale = (
                "Stopping because the model repeated the same action without making progress."
            )
            if _recent_runtime_only_pattern(policy_state):
                policy_state.stop_rationale = "Stopping because runtime state changed in recent attempts, but the visible UI did not reflect the requested change."
            return
    if policy_state.stuck_count >= stuck_limit:
        policy_state.stop_status = "stuck"
        if _recent_runtime_only_pattern(policy_state):
            policy_state.stop_rationale = "Stopping because runtime state changed, but the visible UI did not reflect the requested change."
        else:
            policy_state.stop_rationale = (
                "Stopping because the page state and runtime logs stopped changing."
            )


def _browser_use_history_payload(history: Any, token_usage: JsonDict) -> JsonDict:
    if history is None:
        return {
            "history": [],
            "errors": [],
            "final_result": None,
            "is_done": False,
            "is_successful": None,
            "number_of_steps": 0,
            "usage": token_usage,
        }
    return {
        "history": _json_safe([item.model_dump() for item in history.history]),
        "urls": _json_safe(history.urls()),
        "screenshot_paths": _json_safe(history.screenshot_paths()),
        "action_names": _json_safe(history.action_names()),
        "extracted_content": _json_safe(history.extracted_content()),
        "model_outputs": _json_safe(history.model_outputs()),
        "model_thoughts": _json_safe(history.model_thoughts()),
        "action_results": _json_safe(history.action_results()),
        "errors": _json_safe(history.errors()),
        "final_result": _json_safe(history.final_result()),
        "is_done": history.is_done(),
        "is_successful": history.is_successful(),
        "number_of_steps": history.number_of_steps(),
        "total_duration_seconds": history.total_duration_seconds(),
        "usage": _json_safe(token_usage),
    }


def _resolve_browser_use_final_status(
    *,
    browser_status: str,
    policy_state: EvidencePolicyState,
    reached_step_budget: bool,
    steps: list[JsonDict] | None = None,
) -> str:
    terminal_finish_status = _history_terminal_finish_status(steps or [])
    if terminal_finish_status == "success":
        return (
            "success"
            if _finish_success_allowed(
                current_request=policy_state.current_request,
                action_count=policy_state.action_count,
                observation=policy_state.latest_observation,
                steps=steps,
            )
            else "stuck"
        )
    if policy_state.stop_status is not None:
        return policy_state.stop_status
    if terminal_finish_status in {"stuck", "error"}:
        return terminal_finish_status
    if browser_status == "success":
        return (
            "success"
            if _finish_success_allowed(
                current_request=policy_state.current_request,
                action_count=policy_state.action_count,
                observation=policy_state.latest_observation,
                steps=steps,
            )
            else "stuck"
        )
    if browser_status == "timeout" and policy_state.action_count > 0:
        return "stuck"
    if browser_status == "error":
        return browser_status
    if reached_step_budget:
        return _budget_exit_status(policy_state, steps=steps)
    return "stuck"


def _append_auto_finish_step(
    *,
    steps: list[JsonDict],
    status: str,
    rationale: str,
) -> None:
    if steps:
        action = steps[-1].get("action")
        if isinstance(action, dict) and action.get("action") == "finish":
            action["status"] = status
            if rationale:
                action["rationale"] = rationale
            return
    steps.append(
        {
            "step": len(steps) + 1,
            "action": _create_auto_finish_action(status, rationale),
            "result": {
                "duplicate_action": False,
                "state_changed": False,
                "runtime_changed": False,
                "status": status,
            },
        }
    )


async def _run_browser_use_session(
    *,
    base_url: str,
    private_eval: JsonDict,
    artifact_dir: Path,
    runtime_config: Any,
) -> ActorEvidenceBundle:
    session_gate = _browser_use_session_semaphore()
    await asyncio.to_thread(session_gate.acquire)
    try:
        return await _run_browser_use_session_locked(
            base_url=base_url,
            private_eval=private_eval,
            artifact_dir=artifact_dir,
            runtime_config=runtime_config,
        )
    finally:
        session_gate.release()


async def _run_browser_use_session_locked(
    *,
    base_url: str,
    private_eval: JsonDict,
    artifact_dir: Path,
    runtime_config: Any,
) -> ActorEvidenceBundle:
    os.environ.setdefault("BROWSER_USE_LOGGING_LEVEL", "result")
    from browser_use import Agent, BrowserSession, Tools
    from browser_use.agent.views import ActionResult
    from browser_use.llm.messages import UserMessage

    task_spec = _compile_browser_use_task_spec(
        base_url=base_url,
        private_eval=private_eval,
        runtime_config=runtime_config,
    )
    llm = _browser_use_llm(runtime_config, fixture_names=set(task_spec.upload_fixtures))

    class AffordanceAwareAgent(Agent):
        async def _prepare_context(self, step_info: Any | None = None) -> Any:
            browser_state_summary = await super()._prepare_context(step_info)
            try:
                page = await self.browser_session.must_get_current_page()
                context = _format_interaction_affordance_context(
                    await _collect_interaction_affordance_facts(page)
                )
                if context:
                    self._message_manager._add_context_message(UserMessage(content=context))
            except Exception:
                pass
            return browser_state_summary

    browser_session = BrowserSession(
        headless=True,
        viewport={"width": 1440, "height": 1200},
        allowed_domains=[urlparse(base_url).hostname or urlparse(base_url).netloc],
        wait_between_actions=task_spec.wait_between_actions_seconds,
    )
    tools = Tools(
        exclude_actions=[
            "search",
            "search_page",
            "write_file",
            "replace_file",
            "read_file",
            "save_as_pdf",
            "evaluate",
            "upload_file",
        ]
    )

    @tools.action(
        "Read the generated app runtime logs to verify tool calls, resource reads, and side effects."
    )
    async def get_runtime_logs(browser_session):
        page = await browser_session.must_get_current_page()
        runtime_logs = await _read_runtime_logs_async(page)
        return ActionResult(extracted_content=json.dumps(runtime_logs, ensure_ascii=False))

    @tools.action("List deterministic upload fixtures available for this benchmark task.")
    async def list_upload_fixtures(browser_session):
        del browser_session
        return ActionResult(
            extracted_content=json.dumps(
                [
                    {
                        "fixture_name": spec.name,
                        "field_hint": spec.field_hint,
                        "description": spec.description,
                    }
                    for spec in task_spec.upload_fixtures.values()
                ],
                ensure_ascii=False,
            )
        )

    @tools.action(
        "List files downloaded by the generated app during this browser session. Use this before read_downloaded_file."
    )
    async def list_downloaded_files(browser_session):
        files = [
            _downloaded_file_metadata(path, index=index)
            for index, path in enumerate(_downloaded_file_paths(browser_session))
        ]
        return ActionResult(extracted_content=json.dumps(files, ensure_ascii=False))

    @tools.action(
        "Read a text/JSON/CSV file downloaded by the generated app in this browser session. "
        "Use index from list_downloaded_files; raw filesystem paths outside session downloads are not allowed."
    )
    async def read_downloaded_file(
        browser_session,
        index: int | None = None,
        filename: str | None = None,
        path: str | None = None,
        max_chars: int = _DOWNLOAD_READ_DEFAULT_CHARS,
    ):
        payload = _read_downloaded_file_payload(
            _downloaded_file_paths(browser_session),
            index=index,
            filename=filename,
            path=path,
            max_chars=max_chars,
        )
        return ActionResult(extracted_content=json.dumps(payload, ensure_ascii=False))

    @tools.action(
        "Upload a deterministic benchmark fixture into the target file input. Use fixture_name from list_upload_fixtures."
    )
    async def upload_fixture(index: int, fixture_name: str, browser_session):
        selector_map = await browser_session.get_selector_map()
        node = selector_map.get(index)
        if node is None:
            raise ValueError(f"upload_fixture could not find browser-use element index {index}")
        spec = task_spec.upload_fixtures.get(fixture_name)
        if spec is None:
            available = ", ".join(sorted(task_spec.upload_fixtures))
            raise ValueError(
                f"Unknown upload fixture '{fixture_name}'. Available fixtures: {available}"
            )
        page = await browser_session.must_get_current_page()
        resolved = await _resolve_upload_input(page, node.xpath)
        if resolved.get("found") is not True:
            raise ValueError(
                f"upload_fixture could not resolve a file input: {resolved.get('reason') or 'unknown error'}"
            )
        fixture_path = _materialize_upload_fixture(
            artifact_dir,
            spec,
            accept=str(resolved.get("accept") or ""),
        )
        await page.locator(str(resolved["selector"])).set_input_files(str(fixture_path))
        return ActionResult(
            extracted_content=json.dumps(
                {
                    "fixture_name": spec.name,
                    "path": str(fixture_path),
                    "accept": str(resolved.get("accept") or ""),
                },
                ensure_ascii=False,
            )
        )

    @tools.action(
        "Inspect compact interaction affordances: forms, unfilled selects, drag/drop hints, visual targets, canvas/SVG regions, and disabled controls."
    )
    async def inspect_interaction_affordances(browser_session):
        page = await browser_session.must_get_current_page()
        await _install_affordance_refs(page)
        payload = await _page_evaluate_json(
            page,
            """() => {
                const clean = (value) => String(value ?? "").replace(/\\s+/g, " ").trim();
                const isVisible = (el) => {
                    if (!(el instanceof Element)) return false;
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.visibility !== "hidden" && style.display !== "none" && rect.width > 1 && rect.height > 1;
                };
                const bbox = (el) => {
                    const rect = el.getBoundingClientRect();
                    return {
                        x: Math.round(rect.x),
                        y: Math.round(rect.y),
                        width: Math.round(rect.width),
                        height: Math.round(rect.height),
                        center_x: Math.round(rect.x + rect.width / 2),
                        center_y: Math.round(rect.y + rect.height / 2),
                    };
                };
                const labelFor = (el) => {
                    if (!el) return "";
                    const aria = clean(el.getAttribute?.("aria-label"));
                    if (aria) return aria;
                    const labelledBy = clean(el.getAttribute?.("aria-labelledby"));
                    if (labelledBy) {
                        const text = labelledBy.split(/\\s+/).map((id) => clean(document.getElementById(id)?.textContent)).filter(Boolean).join(" ");
                        if (text) return text;
                    }
                    if (el.labels && el.labels.length > 0) {
                        const explicit = clean(Array.from(el.labels).map((label) => label.textContent).join(" "));
                        if (explicit) return explicit;
                    }
                    const closestLabel = el.closest?.("label");
                    if (closestLabel) {
                        const nested = clean(closestLabel.textContent);
                        if (nested) return nested;
                    }
                    const text = clean(el.textContent);
                    if (text) return text.slice(0, 160);
                    return clean(el.getAttribute?.("placeholder")) || clean(el.getAttribute?.("value"));
                };
                const optionPayload = (select) => Array.from(select.options)
                    .map((option, index) => ({
                        index,
                        value: clean(option.value),
                        text: clean(option.textContent),
                        selected: option.selected,
                        disabled: option.disabled,
                    }))
                    .slice(0, 12);
                const markerFor = (el) => clean([
                    labelFor(el),
                    el.id,
                    typeof el.className === "string" ? el.className : "",
                    el.getAttribute?.("role"),
                    el.getAttribute?.("aria-label"),
                ].join(" "));
                const selects = Array.from(document.querySelectorAll("select"))
                    .filter(isVisible)
                    .map((select, index) => {
                        const selected = select.selectedOptions?.[0];
                        const selectedText = clean(selected?.textContent);
                        const nonEmptyOptions = Array.from(select.options).filter((option) => (
                            !option.disabled && (clean(option.value) || clean(option.textContent)) &&
                            !/^(-+\\s*)?select(\\s*-+)?$/i.test(clean(option.textContent)) &&
                            !/^choose/i.test(clean(option.textContent))
                        ));
                        return {
                            index: index + 1,
                            ref: select.getAttribute("data-genui-affordance-ref"),
                            label: labelFor(select),
                            disabled: select.disabled || select.getAttribute("aria-disabled") === "true",
                            value: clean(select.value),
                            selected_text: selectedText,
                            is_unfilled: !clean(select.value) || /^(-+\\s*)?select(\\s*-+)?$/i.test(selectedText),
                            option_count: select.options.length,
                            non_empty_option_count: nonEmptyOptions.length,
                            options: optionPayload(select),
                            bbox: bbox(select),
                        };
                    });
                const buttons = Array.from(document.querySelectorAll("button, [role='button'], input[type='button'], input[type='submit']"))
                    .filter(isVisible)
                    .map((button, index) => ({
                        index: index + 1,
                        ref: button.getAttribute("data-genui-affordance-ref"),
                        label: labelFor(button),
                        disabled: Boolean(button.disabled) || button.getAttribute("aria-disabled") === "true",
                        bbox: bbox(button),
                    }))
                    .filter((item) => item.label || item.disabled)
                    .slice(0, 30);
                const draggables = Array.from(document.querySelectorAll("[draggable='true']"))
                    .filter(isVisible)
                    .map((el, index) => ({
                        index: index + 1,
                        ref: el.getAttribute("data-genui-affordance-ref"),
                        label: labelFor(el),
                        bbox: bbox(el),
                    }))
                    .slice(0, 20);
                const dropRegions = Array.from(document.querySelectorAll("*"))
                    .filter(isVisible)
                    .map((el, index) => ({
                        index: index + 1,
                        ref: el.getAttribute("data-genui-affordance-ref"),
                        label: labelFor(el),
                        marker: markerFor(el),
                        tag: el.tagName.toLowerCase(),
                        bbox: bbox(el),
                    }))
                    .filter((item) => item.tag !== "body" && item.tag !== "html")
                    .filter((item) => /drop|drag|lane|timeline|canvas|board|target|region|grid|sortable/i.test(item.marker))
                    .slice(0, 20);
                const visualRegions = Array.from(document.querySelectorAll("*"))
                    .filter(isVisible)
                    .map((el, index) => ({
                        index: index + 1,
                        ref: el.getAttribute("data-genui-affordance-ref"),
                        tag: el.tagName.toLowerCase(),
                        label: labelFor(el) || clean(el.closest("section, article, main, div")?.textContent).slice(0, 120),
                        marker: markerFor(el),
                        text: clean(el.textContent).slice(0, 160),
                        child_count: el.children.length,
                        bbox: bbox(el),
                    }))
                    .filter((item) => item.tag !== "body" && item.tag !== "html")
                    .filter((item) => /^(canvas|svg)$/.test(item.tag) || /canvas|grid|board|game|sprite|target|lane|timeline/i.test(item.marker))
                    .slice(0, 20);
                const visualTargets = Array.from(document.querySelectorAll(".target, [data-target], [role='option'], svg text, svg [aria-label]"))
                    .filter(isVisible)
                    .map((el, index) => ({
                        index: index + 1,
                        ref: el.getAttribute("data-genui-affordance-ref"),
                        label: labelFor(el),
                        tag: el.tagName.toLowerCase(),
                        bbox: bbox(el),
                    }))
                    .filter((item) => item.label)
                    .slice(0, 30);
                return JSON.stringify({
                    forms: [{
                        select_count: selects.length,
                        unfilled_select_count: selects.filter((item) => item.is_unfilled && !item.disabled).length,
                        enabled_button_count: buttons.filter((item) => !item.disabled).length,
                        disabled_button_count: buttons.filter((item) => item.disabled).length,
                    }],
                    selects,
                    buttons,
                    drag_drop: {
                        draggable_count: draggables.length,
                        drop_region_count: dropRegions.length,
                        draggables,
                        drop_regions: dropRegions,
                    },
                    visual: {
                        canvas_svg_count: visualRegions.length,
                        target_count: visualTargets.length,
                        regions: visualRegions,
                        targets: visualTargets,
                    },
                });
            }""",
        )
        return ActionResult(extracted_content=json.dumps(payload, ensure_ascii=False))

    @tools.action(
        "Click inside an element or visual target by relative coordinates. Use ref from inspect_interaction_affordances for canvas/SVG targets, grids, or non-accessible hotspots; use x_pct/y_pct from 0 to 1."
    )
    async def click_at(
        browser_session,
        index: int | None = None,
        ref: str | None = None,
        x_pct: float = 0.5,
        y_pct: float = 0.5,
        x: float | None = None,
        y: float | None = None,
    ):
        page = await browser_session.must_get_current_page()
        target_spec = None
        if x is not None and y is not None:
            payload = {
                "action": "click_at",
                "x": float(x),
                "y": float(y),
                "mode": "absolute",
            }
        else:
            target_spec = await _pointer_target_spec(browser_session, index=index, ref=ref)
            payload = {
                "action": "click_at",
                "target": target_spec,
                "x_pct": _clamped_unit(x_pct),
                "y_pct": _clamped_unit(y_pct),
            }
        result = await _run_spatial_action(page, payload)
        result.update({"index": index, "ref": ref, "target": target_spec})
        return ActionResult(extracted_content=json.dumps(result, ensure_ascii=False))

    @tools.action(
        "Move the mouse to an element-relative or absolute point. Use before click_at when crosshair, hover, or pointer-position UI matters."
    )
    async def move_mouse(
        browser_session,
        index: int | None = None,
        ref: str | None = None,
        x_pct: float = 0.5,
        y_pct: float = 0.5,
        x: float | None = None,
        y: float | None = None,
    ):
        page = await browser_session.must_get_current_page()
        target_spec = None
        if x is not None and y is not None:
            payload = {"action": "move_mouse", "x": float(x), "y": float(y), "mode": "absolute"}
        else:
            target_spec = await _pointer_target_spec(browser_session, index=index, ref=ref)
            payload = {
                "action": "move_mouse",
                "target": target_spec,
                "x_pct": _clamped_unit(x_pct),
                "y_pct": _clamped_unit(y_pct),
                "mode": "relative",
            }
        result = await _run_spatial_action(page, payload)
        result.update({"index": index, "ref": ref, "target": target_spec})
        return ActionResult(extracted_content=json.dumps(result, ensure_ascii=False))

    @tools.action(
        "Drag one element to another element or drop region. Use source_ref/target_ref from inspect_interaction_affordances for draggable cards, timeline lanes, boards, and drop zones."
    )
    async def drag_and_drop(
        browser_session,
        source_index: int | None = None,
        target_index: int | None = None,
        source_ref: str | None = None,
        target_ref: str | None = None,
        ref: str | None = None,
        source_x_pct: float = 0.5,
        source_y_pct: float = 0.5,
        target_x_pct: float = 0.5,
        target_y_pct: float = 0.5,
        target_x: float | None = None,
        target_y: float | None = None,
    ):
        page = await browser_session.must_get_current_page()
        if source_ref is None and ref is not None:
            source_ref = ref
        source_spec = await _pointer_target_spec(
            browser_session,
            index=source_index,
            ref=source_ref,
        )
        payload: JsonDict = {
            "action": "drag_and_drop",
            "source": source_spec,
            "source_x_pct": _clamped_unit(source_x_pct),
            "source_y_pct": _clamped_unit(source_y_pct),
            "target_x_pct": _clamped_unit(target_x_pct),
            "target_y_pct": _clamped_unit(target_y_pct),
        }
        target_spec = None
        if target_x is not None and target_y is not None:
            payload["x"] = float(target_x)
            payload["y"] = float(target_y)
        else:
            target_spec = await _pointer_target_spec(
                browser_session,
                index=target_index,
                ref=target_ref,
            )
            payload["target"] = target_spec
        result = await _run_spatial_action(page, payload)
        result.update(
            {
                "source_index": source_index,
                "target_index": target_index,
                "source_ref": source_ref,
                "target_ref": target_ref,
                "source": source_spec,
                "target": target_spec,
            }
        )
        return ActionResult(extracted_content=json.dumps(result, ensure_ascii=False))

    @tools.action(
        "Drag an element to a specific point. Use this for canvas boards, timeline insertion positions, sliders, or spatial controls when a destination element plus relative target point is needed."
    )
    async def drag_to_point(
        browser_session,
        source_index: int | None = None,
        source_ref: str | None = None,
        ref: str | None = None,
        target_index: int | None = None,
        target_ref: str | None = None,
        source_x_pct: float = 0.5,
        source_y_pct: float = 0.5,
        target_x_pct: float = 0.5,
        target_y_pct: float = 0.5,
        point: list[float] | None = None,
        x: float | None = None,
        y: float | None = None,
    ):
        page = await browser_session.must_get_current_page()
        if source_ref is None and ref is not None:
            source_ref = ref
        if point is not None and len(point) >= 2:
            x = point[0]
            y = point[1]
        source_spec = await _pointer_target_spec(
            browser_session,
            index=source_index,
            ref=source_ref,
        )
        payload: JsonDict = {
            "action": "drag_to_point",
            "source": source_spec,
            "source_x_pct": _clamped_unit(source_x_pct),
            "source_y_pct": _clamped_unit(source_y_pct),
            "target_x_pct": _clamped_unit(target_x_pct),
            "target_y_pct": _clamped_unit(target_y_pct),
        }
        target_spec = None
        if x is not None and y is not None:
            payload["x"] = float(x)
            payload["y"] = float(y)
        else:
            target_spec = await _pointer_target_spec(
                browser_session,
                index=target_index,
                ref=target_ref,
            )
            payload["target"] = target_spec
        result = await _run_spatial_action(page, payload)
        result.update(
            {
                "source_index": source_index,
                "source_ref": source_ref,
                "target_index": target_index,
                "target_ref": target_ref,
                "source": source_spec,
                "target": target_spec,
            }
        )
        return ActionResult(extracted_content=json.dumps(result, ensure_ascii=False))

    @tools.action(
        "Read a compact textual view of the current visible page state when visual evidence is ambiguous."
    )
    async def read_current_visible_state(browser_session):
        page = await browser_session.must_get_current_page()
        payload = await _page_evaluate_json(
            page,
            """() => {
                const clean = (value) => String(value ?? "").replace(/\\s+/g, " ").trim();
                const labelFor = (el) => {
                    if (!el) {
                        return "";
                    }
                    if (typeof el.getAttribute === "function") {
                        const ariaLabel = clean(el.getAttribute("aria-label"));
                        if (ariaLabel) {
                            return ariaLabel;
                        }
                    }
                    if (el.labels && el.labels.length > 0) {
                        const explicitLabel = clean(el.labels[0]?.textContent);
                        if (explicitLabel) {
                            return explicitLabel;
                        }
                    }
                    const closestLabel = typeof el.closest === "function" ? el.closest("label") : null;
                    if (closestLabel) {
                        const nestedLabel = clean(closestLabel.textContent);
                        if (nestedLabel) {
                            return nestedLabel;
                        }
                    }
                    const parentText = clean(el.parentElement?.textContent);
                    if (parentText) {
                        return parentText;
                    }
                    return clean(el.textContent) || clean(el.getAttribute?.("value")) || clean(el.getAttribute?.("placeholder"));
                };
                const controlState = (el) => {
                    const tag = el.tagName.toLowerCase();
                    const type = clean(el.getAttribute("type")).toLowerCase();
                    if (tag === "input" && (type === "checkbox" || type === "radio")) {
                        return el.checked ? "checked" : "unchecked";
                    }
                    if (tag === "select") {
                        return clean(el.selectedOptions?.[0]?.textContent) || clean(el.value);
                    }
                    if (el.hasAttribute("aria-pressed")) {
                        return clean(el.getAttribute("aria-pressed"));
                    }
                    if (el.hasAttribute("aria-selected")) {
                        return clean(el.getAttribute("aria-selected"));
                    }
                    if (el.hasAttribute("aria-current")) {
                        return clean(el.getAttribute("aria-current"));
                    }
                    if (clean(el.getAttribute("role")).toLowerCase() === "switch") {
                        return clean(el.getAttribute("aria-checked")) || clean(el.getAttribute("aria-pressed"));
                    }
                    return "";
                };
                return JSON.stringify({
                    url: window.location.href,
                    text: clean(document.body?.innerText).slice(0, 2000),
                    headings: Array.from(document.querySelectorAll("h1, h2, h3, [role='heading']"))
                        .map((el) => clean(el.textContent))
                        .filter(Boolean)
                        .slice(0, 12),
                    selected: Array.from(document.querySelectorAll(
                        "[aria-selected='true'], [aria-pressed='true'], [aria-current], input:checked, option:checked"
                    ))
                        .map((el) => ({
                            tag: el.tagName.toLowerCase(),
                            text: labelFor(el),
                        }))
                        .filter((item) => item.text)
                        .slice(0, 12),
                    control_states: Array.from(document.querySelectorAll(
                        "input[type='checkbox'], input[type='radio'], select, [role='switch'], [aria-pressed], [aria-selected], [aria-current]"
                    ))
                        .map((el) => ({
                            tag: el.tagName.toLowerCase(),
                            type: clean(el.getAttribute("type")).toLowerCase(),
                            role: clean(el.getAttribute("role")).toLowerCase(),
                            label: labelFor(el),
                            state: controlState(el),
                        }))
                        .filter((item) => item.label || item.state)
                        .slice(0, 20),
                    status_text: Array.from(document.querySelectorAll("[role='status'], [aria-live], progress, meter"))
                        .map((el) => clean(el.textContent || el.getAttribute("aria-label")))
                        .filter(Boolean)
                        .slice(0, 12),
                    interactive: Array.from(document.querySelectorAll(
                        "input, textarea, select, button, a[href]"
                    ))
                        .slice(0, 20)
                        .map((el) => ({
                            tag: el.tagName.toLowerCase(),
                            text: labelFor(el),
                        })),
                });
            }""",
        )
        return ActionResult(extracted_content=json.dumps(payload, ensure_ascii=False))

    history_records: list[JsonDict] = []
    console_errors: list[str] = []
    interaction_errors: list[str] = []
    observations: list[JsonDict] = []
    final_observation: JsonDict | None = None
    history = None
    policy_state = EvidencePolicyState(
        max_stuck_steps=task_spec.max_stuck_steps,
        allow_submit_without_recent_mutation=task_spec.allow_submit_without_recent_mutation,
        allow_duplicate_action_no_progress=task_spec.allow_duplicate_action_no_progress,
        current_request=task_spec.current_request,
    )

    async def _capture(step: int, phase: str) -> None:
        nonlocal final_observation
        observation = await _collect_browser_use_observation(
            browser_session,
            artifact_dir,
            step,
            history_records,
            phase,
        )
        observations.append(observation)
        final_observation = observation
        for error in observation.get("consoleErrors", []):
            text = str(error)
            if text and text not in console_errors:
                console_errors.append(text)

    async def _on_step_start(agent: Any) -> None:
        try:
            await _capture(int(agent.state.n_steps), "before_action")
        except Exception as exc:
            text = str(exc)
            if text and text not in console_errors:
                console_errors.append(text)

    async def _attempt_recovery_capture(phase: str) -> None:
        if (
            isinstance(final_observation, dict)
            and str(final_observation.get("screenshotPath") or "").strip()
        ):
            return
        step_number = len(history_records)
        if history is not None:
            try:
                step_number = max(step_number, len(history.history))
            except Exception:
                pass
        step_number = max(step_number, 1)
        for attempt in range(3):
            try:
                await asyncio.wait_for(_capture(step_number + attempt, phase), timeout=10)
                return
            except Exception as exc:
                text = str(exc)
                if text and text not in console_errors:
                    console_errors.append(text)
                await asyncio.sleep(0.5)

    async def _on_step_end(agent: Any) -> None:
        try:
            await _capture(len(agent.history.history), "after_action")
            history_records[:] = _browser_use_steps(
                [item.model_dump() for item in agent.history.history], observations
            )
            if final_observation is not None:
                _update_evidence_policy(
                    policy_state=policy_state,
                    observation=final_observation,
                    steps=history_records,
                )
        except Exception as exc:
            text = str(exc)
            if text and text not in console_errors:
                console_errors.append(text)

    async def _should_stop() -> bool:
        return policy_state.stop_status is not None

    actor = AffordanceAwareAgent(
        task=task_spec.prompt,
        llm=llm,
        browser_session=browser_session,
        tools=tools,
        use_vision=task_spec.use_vision,
        extend_system_message=ACTOR_SYSTEM_PROMPT_EXTEND,
        max_actions_per_step=1,
        step_timeout=task_spec.step_timeout_seconds,
        directly_open_url=True,
        include_recent_events=True,
        include_attributes=[
            "value",
            "disabled",
            "aria-disabled",
            "aria-selected",
            "aria-pressed",
            "aria-current",
            "aria-checked",
            "checked",
            "selected",
            "draggable",
            "role",
            "placeholder",
        ],
        use_judge=False,
        enable_planning=False,
        final_response_after_failure=True,
        register_should_stop_callback=_should_stop,
    )

    browser_status = "stuck"
    try:
        history = await asyncio.wait_for(
            actor.run(
                max_steps=task_spec.max_steps,
                on_step_start=_on_step_start,
                on_step_end=_on_step_end,
            ),
            timeout=task_spec.max_time_seconds,
        )
        if final_observation is None and history is not None and len(history.history) > 0:
            await _capture(len(history.history), "after_action")
        if history.is_done() and history.is_successful() is True:
            browser_status = "success"
        elif history.has_errors():
            browser_status = "error"
        else:
            browser_status = "stuck"
    except asyncio.TimeoutError:
        browser_status = "timeout"
        interaction_errors.append("browser-use actor timed out before completion")
    except Exception as exc:
        browser_status = "error"
        interaction_errors.append(str(exc))
    finally:
        try:
            await asyncio.wait_for(_attempt_recovery_capture("final_recovery"), timeout=15)
        except asyncio.TimeoutError:
            interaction_errors.append("final recovery capture timed out")
        except Exception as exc:
            interaction_errors.append(f"final recovery capture failed: {exc}")
        try:
            await asyncio.wait_for(browser_session.kill(), timeout=15)
        except asyncio.TimeoutError:
            interaction_errors.append("browser session cleanup timed out")
        except Exception as exc:
            interaction_errors.append(f"browser session cleanup failed: {exc}")

    if history is None:
        actor_token_usage = _empty_token_usage()
        history_dump: list[JsonDict] = []
    else:
        actor_token_usage = _browser_use_token_usage(history.usage)
        history_dump = [item.model_dump() for item in history.history]
        history_records = _browser_use_steps(history_dump, observations)
        interaction_errors.extend(error for error in history.errors() if error)
    interaction_errors.extend(llm.schema_mismatches)
    if final_observation is not None:
        _update_evidence_policy(
            policy_state=policy_state,
            observation=final_observation,
            steps=history_records,
        )
    reached_step_budget = len(history_dump) >= task_spec.max_steps
    recovered_to_terminal_finish = _history_has_terminal_finish_action(history_records)
    terminal_finish_status = _history_terminal_finish_status(history_records)
    if terminal_finish_status in {"stuck", "error"} and policy_state.stop_status is None:
        policy_state.stop_status = terminal_finish_status
    if recovered_to_terminal_finish and browser_status in {"error", "timeout"}:
        browser_status = "stuck"
    if (
        any("timed out" in err.lower() or "timeout" in err.lower() for err in interaction_errors)
        and not recovered_to_terminal_finish
    ):
        browser_status = "timeout"
    if (
        policy_state.stop_status is None
        and browser_status == "timeout"
        and policy_state.action_count == 0
    ):
        policy_state.stop_status = "error"
        policy_state.stop_rationale = "Stopping because the browser-use session timed out before any meaningful interaction occurred."
    if (
        policy_state.stop_status is None
        and _interaction_errors_indicate_invalid_actor_output(interaction_errors)
        and policy_state.action_count <= 1
    ):
        policy_state.stop_status = "error"
        policy_state.stop_rationale = "Stopping because the actor produced invalid action payloads before meaningful interaction."
    final_status = _resolve_browser_use_final_status(
        browser_status=browser_status,
        policy_state=policy_state,
        reached_step_budget=reached_step_budget,
        steps=history_records,
    )
    if final_status == "success" and terminal_finish_status == "success":
        policy_state.stop_rationale = None
    if policy_state.stop_rationale and final_status != "success":
        _append_auto_finish_step(
            steps=history_records,
            status=final_status,
            rationale=policy_state.stop_rationale,
        )
    elif final_status == "success" and browser_status != "success":
        _append_auto_finish_step(
            steps=history_records,
            status="success",
            rationale="Benchmark evidence is sufficient despite the browser-use agent not returning success.",
        )

    runtime_logs: JsonDict = {
        "tool_logs": [],
        "resource_logs": [],
        "side_effect_logs": [],
        "confirmation_events": [],
        "scenarios": {},
    }
    if final_observation is not None:
        runtime_logs = final_observation["runtimeLogs"]

    raw_history = _browser_use_history_payload(history, actor_token_usage)
    if llm.raw_payloads:
        raw_history["schema_adapter_raw_payloads"] = list(llm.raw_payloads)
    if llm.schema_mismatches:
        raw_history["schema_adapter_errors"] = list(llm.schema_mismatches)
    actor_history_issue_notes = _visual_issue_sentences_from_actor_history(raw_history)
    history_path = artifact_dir / "browser_use_history.json"
    history_path.write_text(
        json.dumps(raw_history, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    history_finish_basis = str(history.final_result() or "").strip() if history is not None else ""
    finish_basis = (
        history_finish_basis
        if final_status == "success" and history_finish_basis
        else policy_state.stop_rationale or history_finish_basis
    )
    if not finish_basis:
        finish_basis = f"Session ended with runtime status '{final_status}'."
    finish_basis = _merge_visual_issue_notes_into_finish_basis(
        finish_basis,
        actor_history_issue_notes,
    )
    terminal_step = _terminal_finish_step(history_records)
    if terminal_step is not None:
        action = terminal_step.get("action")
        if isinstance(action, dict):
            action["text"] = finish_basis
        result = terminal_step.get("result")
        if isinstance(result, dict):
            result["extracted_content"] = finish_basis
    final_text = final_observation.get("finalText") if isinstance(final_observation, dict) else ""
    observation_snapshots = [_observation_snapshot(item, item["phase"]) for item in observations]
    evidence_summary, verification_checks, state_diffs, final_assessment = (
        _build_structured_actor_evidence(
            request=task_spec.current_request,
            final_status=final_status,
            finish_basis=finish_basis,
            steps=history_records,
            observations=observation_snapshots,
            final_text=str(final_text or ""),
            validation_contract=task_spec.validation_contract,
        )
    )
    visual_process_screenshots = _build_visual_process_screenshots(
        history_records,
        observation_snapshots,
    )
    visual_quality_findings = _build_visual_quality_findings(
        finish_basis=finish_basis,
        visual_process_screenshots=visual_process_screenshots,
        actor_history_issue_notes=actor_history_issue_notes,
    )
    unique_interaction_errors = _unique_strings(interaction_errors)
    final_elements = (
        final_observation.get("elements", []) if isinstance(final_observation, dict) else []
    )
    actor_diagnostics = _build_actor_diagnostics(
        final_status=final_status,
        browser_status=browser_status,
        policy_state=policy_state,
        reached_step_budget=reached_step_budget,
        terminal_finish_status=terminal_finish_status,
        interaction_errors=unique_interaction_errors,
        console_errors=_unique_strings(console_errors),
        final_text=final_text,
        final_elements=final_elements,
        schema_mismatch_count=len(llm.schema_mismatches),
        schema_repair_count=len(llm.schema_repairs),
    )
    return ActorEvidenceBundle(
        raw_agent_history=raw_history,
        canonical_actor_result={
            "status": final_status,
            "finished": final_status == "success",
            "summary": (
                f"Blind actor finished with status '{final_status}' after {len(history_records)} steps."
            ),
            "steps": history_records,
            "observations": observation_snapshots,
            "visual_process_screenshots": visual_process_screenshots,
            "visual_quality_findings": visual_quality_findings,
            "final_url": (
                final_observation.get("url") if isinstance(final_observation, dict) else base_url
            ),
            "final_text": (
                final_observation.get("finalText") if isinstance(final_observation, dict) else ""
            ),
            "final_dom_tree": (
                final_observation.get("domTree") if isinstance(final_observation, dict) else ""
            ),
            "final_elements": (final_elements),
            "final_actionable_elements": (
                final_observation.get("actionableElements")
                if isinstance(final_observation, dict)
                else []
            ),
            "final_ax_tree": (
                final_observation.get("axTree") if isinstance(final_observation, dict) else None
            ),
            "final_screenshot": (
                final_observation.get("screenshotPath")
                if isinstance(final_observation, dict)
                else ""
            ),
            "console_errors": _unique_strings(console_errors),
            "interaction_errors": _unique_strings(interaction_errors),
            "tool_logs": runtime_logs["tool_logs"] if isinstance(runtime_logs, dict) else [],
            "resource_logs": (
                runtime_logs["resource_logs"] if isinstance(runtime_logs, dict) else []
            ),
            "side_effect_logs": (
                runtime_logs["side_effect_logs"] if isinstance(runtime_logs, dict) else []
            ),
            "confirmation_events": (
                runtime_logs.get("confirmation_events", [])
                if isinstance(runtime_logs, dict)
                else []
            ),
            "scenario_states": runtime_logs["scenarios"] if isinstance(runtime_logs, dict) else {},
            "diagnostics": actor_diagnostics,
            "evidence_summary": evidence_summary,
            "verification_checks": verification_checks,
            "state_diffs": state_diffs,
            "final_assessment": final_assessment,
            "token_usage": actor_token_usage,
        },
    )


# ---------------------------------------------------------------------------
# Main runtime entrypoint
# ---------------------------------------------------------------------------
def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for v in values:
        if v and v not in seen:
            seen.add(v)
            result.append(str(v))
    return result


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _json_safe(model_dump())
    dict_method = getattr(value, "dict", None)
    if callable(dict_method):
        return _json_safe(dict_method())
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        try:
            return str(isoformat())
        except Exception:
            pass
    return str(value)


def _state_hash(observation: JsonDict) -> str:
    elements = observation.get("elements", [])

    def control_state(element: JsonDict) -> JsonDict:
        options = element.get("options")
        return {
            "role": element.get("role"),
            "name": element.get("name"),
            "value": element.get("value"),
            "disabled": element.get("disabled"),
            "checked": element.get("checked"),
            "ariaChecked": element.get("ariaChecked"),
            "ariaPressed": element.get("ariaPressed"),
            "ariaSelected": element.get("ariaSelected"),
            "ariaCurrent": element.get("ariaCurrent"),
            "options": [
                {
                    "value": option.get("value"),
                    "label": option.get("label"),
                    "selected": option.get("selected"),
                }
                for option in (options if isinstance(options, list) else [])
                if isinstance(option, dict)
            ],
        }

    return json.dumps(
        {
            "url": observation.get("url"),
            "text": str(observation.get("finalText") or "")[:4000],
            "elements": [
                control_state(el)
                for el in (elements if isinstance(elements, list) else [])
                if isinstance(el, dict)
            ],
        },
        sort_keys=True,
    )


def run_blind_actor_session(
    *,
    base_url: str,
    private_eval: JsonDict,
    artifact_dir: Path,
    runtime_config: Any,
) -> JsonDict:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    bundle = asyncio.run(
        _run_browser_use_session(
            base_url=base_url,
            private_eval=private_eval,
            artifact_dir=artifact_dir,
            runtime_config=runtime_config,
        )
    )
    return bundle.canonical_actor_result
