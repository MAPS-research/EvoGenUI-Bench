from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Protocol

from runner.evaluation.evidence_rules import evaluator_visible_console_errors
from runner.evaluation.prompts import (
    DIMENSION_ALLOWED_FAILURE_TYPES,
    DIMENSION_EVIDENCE_POLICY,
    DIMENSION_FAILURE_TYPE_ALIASES,
    DIMENSION_RUBRICS,
    DIMENSION_SCORE_ANCHORS,
    DIMENSION_UNKNOWN_FAILURE_TYPE_FALLBACKS,
    PRESENTATION_SCORING_CAPS,
    SUITE_EVALUATION_PROFILES,
    dimension_judge_system_prompt,
)
from runner.generation.model_runner import ProviderResponseError
from runner.tools.evaluation_inputs import (
    benchmark_request,
    copy_actor_evidence_summary,
    copy_validation_contract,
)
from runner.tools.experiment_config import ComponentRuntimeConfig
from runner.tools.io_utils import truncate_text
from runner.tools.llm_client import (
    LlmInput,
    LlmRequest,
    call_llm,
    token_usage_for_exception,
)
from runner.tools.token_usage import (
    add_token_usage,
    empty_token_usage,
    normalize_token_usage,
)
from runtime.types import JsonDict, TaskDefinition

TURN_LEVEL_DIMENSIONS = ("Presentation", "Execution", "Alignment")
DIMENSIONS = TURN_LEVEL_DIMENSIONS
EXECUTION_DIMENSIONS = ("Execution",)
PRESENTATION_DIMENSIONS = ("Presentation",)
ALIGNMENT_DIMENSIONS = ("Alignment",)
DIMENSION_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("execution", EXECUTION_DIMENSIONS),
    ("presentation", PRESENTATION_DIMENSIONS),
    ("alignment", ALIGNMENT_DIMENSIONS),
)

SINGLE_DIMENSION_JUDGE_RESULT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "score": {"type": "number", "minimum": 1, "maximum": 5},
        "summary": {"type": "string"},
        "failure_types": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["score", "summary", "failure_types"],
    "additionalProperties": False,
}

SCREENSHOT_DIMENSIONS = {"Presentation"}

SCAFFOLD_BASE_CSS_CLASSES = frozenset(
    {
        "actions",
        "compareGrid",
        "error",
        "field",
        "formGrid",
        "grid",
        "itemCard",
        "itemList",
        "meta",
        "page",
        "panel",
        "primaryButton",
        "resultGrid",
        "secondaryButton",
        "shell",
    }
)
ALIGNMENT_SOURCE_CHAR_LIMIT = 30000
CODE_DIFF_PER_FILE_CHAR_LIMIT = 6000
CODE_DIFF_TOTAL_CHAR_LIMIT = 24000
TEXT_DIFF_CHAR_LIMIT = 4000
CURRENT_TURN_TEXT_CHAR_LIMIT = 4000
CURRENT_TURN_TREE_CHAR_LIMIT = 3000
CURRENT_TURN_ELEMENTS_LIMIT = 24
CURRENT_TURN_STEPS_LIMIT = 20
OBSERVATION_LIMIT = 8
OBSERVATION_TEXT_CHAR_LIMIT = 2000
OBSERVATION_TREE_CHAR_LIMIT = 2000
OBSERVATION_ELEMENTS_LIMIT = 24
BEHAVIORAL_OBSERVATION_LIMIT = 3
BEHAVIORAL_OBSERVATION_TEXT_CHAR_LIMIT = 1500
EVIDENCE_PACK_ACTION_LIMIT = 8
EVIDENCE_PACK_BEFORE_TEXT_LIMIT = 180
EVIDENCE_PACK_RUNTIME_CHANGE_TEXT_LIMIT = 180
EVIDENCE_PACK_DOM_LIMIT = 450
EVIDENCE_PACK_ELEMENTS_LIMIT = 6
EVIDENCE_PACK_RUNTIME_LOG_LIMIT = 10
EVIDENCE_PACK_STATE_LIMIT = 1600
EVIDENCE_PACK_TOOL_RESULT_LIMIT = 500
EVIDENCE_PACK_FINAL_UI_TEXT_LIMIT = 1200
EVIDENCE_PACK_REFERENCE_TEXT_LIMIT = 500
EVIDENCE_PACK_REFERENCE_CHECK_LIMIT = 260
EVIDENCE_PACK_PRIOR_REFERENCE_TEXT_LIMIT = 180
PAYLOAD_STATE_DIFF_TEXT_LIMIT = 220
KEY_ACTION_TERMS = (
    "submit",
    "save",
    "send",
    "apply",
    "filter",
    "run",
    "calculate",
    "confirm",
    "commit",
    "book",
    "update",
    "refresh",
    "validate",
    "search",
)
KEY_TOOL_TERMS = (
    "save",
    "send",
    "assign",
    "update",
    "create",
    "delete",
    "commit",
    "confirm",
    "submit",
    "write",
)


class DimensionJudgeBackend(Protocol):
    def judge_dimensions(
        self,
        dimensions: tuple[str, ...],
        shared_payload: JsonDict,
        config_payload: JsonDict,
        *,
        screenshot_path: Path | None = None,
        screenshot_paths: list[Path] | None = None,
    ) -> tuple[dict[str, JsonDict], JsonDict]: ...


def _score(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Dimension score must be numeric, got {value!r}") from exc
    if parsed < 1.0 or parsed > 5.0:
        raise ValueError(f"Dimension score must be between 1 and 5, got {parsed}")
    return parsed


def _failure_types(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Dimension failure_types must be an array")
    return [str(item).strip() for item in value if str(item).strip()]


def _unique_preserve_order(values: list[str]) -> list[str]:
    unique: list[str] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return unique


def _validated_failure_types(value: object, dimension: str) -> tuple[list[str], list[str]]:
    raw_failure_types = _failure_types(value)
    aliases = DIMENSION_FAILURE_TYPE_ALIASES.get(dimension, {})
    failure_types = [aliases.get(item, item) for item in raw_failure_types]
    allowed = set(DIMENSION_ALLOWED_FAILURE_TYPES[dimension])
    unknown = _unique_preserve_order([item for item in failure_types if item not in allowed])
    fallback = DIMENSION_UNKNOWN_FAILURE_TYPE_FALLBACKS[dimension]
    normalized = [item if item in allowed else fallback for item in failure_types]
    return _unique_preserve_order(normalized), unknown


def _require_dict(value: object, *, field: str) -> JsonDict:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


def _require_list(value: object, *, field: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    return value


def _require_string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def normalize_single_dimension_result(value: object, dimension: str) -> JsonDict:
    if dimension not in DIMENSIONS:
        raise ValueError(f"Unknown dimension: {dimension}")
    if not isinstance(value, dict):
        raise ValueError(f"{dimension} judge result must be a JSON object")
    summary = _require_string(value.get("summary"), field=f"{dimension} judge summary")
    score = _score(value.get("score"))
    failure_types, unsupported_failure_types = _validated_failure_types(
        value.get("failure_types"), dimension
    )
    if score < 4.0 and not failure_types:
        raise ValueError(
            f"{dimension} judge result with score < 4 must include at least one failure_type"
        )
    normalized = {
        "passed": score >= 4.0,
        "score": score,
        "summary": summary,
        "failure_types": failure_types,
    }
    if unsupported_failure_types:
        normalized["unsupported_failure_types"] = unsupported_failure_types
    token_usage = value.get("token_usage")
    normalized["token_usage"] = (
        normalize_token_usage({}) if token_usage is None else normalize_token_usage(token_usage)
    )
    return normalized


def aggregate_dimension_results(
    results: dict[str, JsonDict],
    *,
    token_usage: JsonDict | None = None,
    dimensions: tuple[str, ...] = TURN_LEVEL_DIMENSIONS,
) -> JsonDict:
    normalized = {
        dimension: normalize_single_dimension_result(results.get(dimension), dimension)
        for dimension in dimensions
    }
    if token_usage is None:
        aggregate_token_usage = empty_token_usage()
        for item in normalized.values():
            aggregate_token_usage = add_token_usage(
                aggregate_token_usage, item.get("token_usage", {})
            )
    else:
        aggregate_token_usage = normalize_token_usage(token_usage)
    score = sum(item["score"] for item in normalized.values()) / len(dimensions)
    failure_types = sorted(
        {
            failure_type
            for item in normalized.values()
            for failure_type in item.get("failure_types", [])
        }
    )
    passed = all(item["score"] >= 4.0 for item in normalized.values())
    summary = " ".join(
        f"{dimension}: {normalized[dimension]['summary']}" for dimension in dimensions
    )
    return {
        "passed": passed,
        "score": score,
        "summary": summary,
        "failure_types": failure_types,
        "token_usage": aggregate_token_usage,
        "dimensions": normalized,
    }


def _tail_list(value: object, count: int) -> list[object]:
    if not isinstance(value, list) or count <= 0:
        return []
    return value[-count:]


def _compact_observations(value: object) -> list[JsonDict]:
    observations_raw = _require_list(value, field="actor.observations")
    observations: list[JsonDict] = []
    for index, item in enumerate(observations_raw[:OBSERVATION_LIMIT], start=1):
        observation = _require_dict(item, field=f"actor.observations[{index}]")
        elements = observation.get("elements")
        if elements is None:
            elements = []
        elif not isinstance(elements, list):
            raise ValueError(f"actor.observations[{index}].elements must be an array")
        console_errors = observation.get("console_errors")
        if console_errors is not None and not isinstance(console_errors, list):
            raise ValueError(f"actor.observations[{index}].console_errors must be an array")
        runtime_logs = observation.get("runtime_logs")
        if runtime_logs is None:
            runtime_logs = {}
        elif not isinstance(runtime_logs, dict):
            raise ValueError(f"actor.observations[{index}].runtime_logs must be an object")
        observations.append(
            {
                "step": observation.get("step"),
                "phase": observation.get("phase"),
                "url": observation.get("url"),
                "visible_text": truncate_text(
                    str(observation.get("visible_text", "")), OBSERVATION_TEXT_CHAR_LIMIT
                ),
                "dom_tree": truncate_text(
                    str(observation.get("dom_tree", "")), OBSERVATION_TREE_CHAR_LIMIT
                ),
                "ax_tree": truncate_text(
                    str(observation.get("ax_tree", "")), OBSERVATION_TREE_CHAR_LIMIT
                ),
                "elements": elements[:OBSERVATION_ELEMENTS_LIMIT],
                "console_errors": evaluator_visible_console_errors(console_errors),
                "runtime_logs": runtime_logs,
            }
        )
    return observations


def _compact_behavioral_observations(value: object) -> list[JsonDict]:
    observations_raw = _require_list(value, field="actor.observations")
    observations: list[JsonDict] = []
    for offset, item in enumerate(
        _tail_list(observations_raw, BEHAVIORAL_OBSERVATION_LIMIT), start=1
    ):
        observation = _require_dict(item, field=f"actor.observations_tail[{offset}]")
        console_errors = observation.get("console_errors")
        if console_errors is not None and not isinstance(console_errors, list):
            raise ValueError(f"actor.observations_tail[{offset}].console_errors must be an array")
        runtime_logs = observation.get("runtime_logs")
        if runtime_logs is None:
            runtime_logs = {}
        elif not isinstance(runtime_logs, dict):
            raise ValueError(f"actor.observations_tail[{offset}].runtime_logs must be an object")
        observations.append(
            {
                "step": observation.get("step"),
                "phase": observation.get("phase"),
                "url": observation.get("url"),
                "visible_text": truncate_text(
                    str(observation.get("visible_text", "")), BEHAVIORAL_OBSERVATION_TEXT_CHAR_LIMIT
                ),
                "console_errors": evaluator_visible_console_errors(console_errors),
                "runtime_logs": runtime_logs,
            }
        )
    return observations


def _runtime_log_counts(runtime_logs: JsonDict, *, field: str) -> JsonDict:
    logs = _require_dict(runtime_logs, field=field)
    counts: JsonDict = {}
    for key in ("tool_logs", "resource_logs", "confirmation_events"):
        entries = logs.get(key)
        if isinstance(entries, list):
            counts[key] = len(entries)
            continue
        compact_count = logs.get(f"{key}_count")
        if isinstance(compact_count, int):
            counts[key] = compact_count
            continue
        raise ValueError(f"{field}.{key} must be an array or {key}_count must be an integer")
    return counts


def _runtime_log_entries(runtime_logs: JsonDict, *, key: str, field: str) -> list[object]:
    entries = runtime_logs.get(key)
    if isinstance(entries, list):
        return entries
    recent_entries = runtime_logs.get(f"recent_{key}")
    if isinstance(recent_entries, list):
        return recent_entries
    return []


def _runtime_log_signature(runtime_logs: JsonDict, *, field: str) -> str:
    logs = _require_dict(runtime_logs, field=field)
    counts = _runtime_log_counts(logs, field=field)
    tool_logs = _runtime_log_entries(logs, key="tool_logs", field=field)
    resource_logs = _runtime_log_entries(logs, key="resource_logs", field=field)
    confirmation_events = _require_list(
        _runtime_log_entries(logs, key="confirmation_events", field=field),
        field=f"{field}.confirmation_events",
    )
    return json.dumps(
        {
            "counts": counts,
            "tool_logs": [
                {
                    "name": _require_dict(item, field=f"{field}.tool_logs[]").get("name"),
                    "args": _require_dict(item, field=f"{field}.tool_logs[]").get("args"),
                    "error": _require_dict(item, field=f"{field}.tool_logs[]").get("error"),
                }
                for item in tool_logs
            ],
            "resource_logs": [
                {
                    "uri": _require_dict(item, field=f"{field}.resource_logs[]").get("uri"),
                    "error": _require_dict(item, field=f"{field}.resource_logs[]").get("error"),
                }
                for item in resource_logs
            ],
            "confirmation_events": confirmation_events,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _step_observation_by_phase(
    observations: list[JsonDict], *, step: int, phase: str
) -> JsonDict | None:
    for observation in observations:
        if observation.get("step") == step and observation.get("phase") == phase:
            return observation
    return None


def _compact_evidence_observation(
    observation: JsonDict, *, field: str, include_full_visible_text: bool
) -> JsonDict:
    item = _require_dict(observation, field=field)
    runtime_logs = _require_dict(item.get("runtime_logs"), field=f"{field}.runtime_logs")
    visible_text = str(item.get("visible_text", ""))
    return {
        "step": item.get("step"),
        "phase": item.get("phase"),
        "visible_text": (
            visible_text
            if include_full_visible_text
            else truncate_text(visible_text, EVIDENCE_PACK_FINAL_UI_TEXT_LIMIT)
        ),
        "dom_excerpt": truncate_text(str(item.get("dom_tree", "")), EVIDENCE_PACK_DOM_LIMIT),
        "elements": _compact_evidence_elements(
            _require_list(item.get("elements"), field=f"{field}.elements")
        ),
        "screenshot": item.get("screenshot"),
        "runtime_counts": _runtime_log_counts(runtime_logs, field=f"{field}.runtime_logs"),
    }


def _compact_before_evidence_observation(observation: JsonDict, *, field: str) -> JsonDict:
    item = _require_dict(observation, field=field)
    runtime_logs = _require_dict(item.get("runtime_logs"), field=f"{field}.runtime_logs")
    return {
        "step": item.get("step"),
        "phase": item.get("phase"),
        "visible_text": truncate_text(
            str(item.get("visible_text", "")), EVIDENCE_PACK_BEFORE_TEXT_LIMIT
        ),
        "runtime_counts": _runtime_log_counts(runtime_logs, field=f"{field}.runtime_logs"),
    }


def _compact_evidence_elements(value: list[object]) -> list[JsonDict]:
    elements: list[JsonDict] = []
    for index, item in enumerate(value[:EVIDENCE_PACK_ELEMENTS_LIMIT], start=1):
        element = _require_dict(item, field=f"evidence_pack.elements[{index}]")
        elements.append(
            {
                "index": element.get("index"),
                "role": element.get("role"),
                "name": truncate_text(str(element.get("name", "")), 120),
                "value": truncate_text(str(element.get("value", "")), 120),
                "disabled": element.get("disabled"),
                "checked": element.get("checked"),
                "ariaPressed": element.get("ariaPressed"),
                "ariaSelected": element.get("ariaSelected"),
            }
        )
    return elements


def _step_action_label(step: JsonDict) -> str:
    action = _require_dict(step.get("action"), field="actor.steps[].action")
    parts = [str(action.get("action") or "")]
    for key in ("text", "value"):
        value = action.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value)
    target = step.get("resolved_target")
    if isinstance(target, dict):
        for key in ("name", "role"):
            value = target.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value)
    return " ".join(parts).strip().lower()


def _is_key_action_step(step: JsonDict, state_diff_steps: set[int]) -> bool:
    step_number = int(step.get("step") or 0)
    action = _require_dict(step.get("action"), field=f"actor.steps[{step_number}].action")
    action_name = str(action.get("action") or "").strip().lower()
    if action_name in {"finish", "navigate", "wait", "scroll"}:
        return False
    if step_number in state_diff_steps:
        return True
    result = _require_dict(step.get("result"), field=f"actor.steps[{step_number}].result")
    if result.get("state_changed") is True or result.get("runtime_changed") is True:
        return True
    label = _step_action_label(step)
    return any(re.search(rf"\b{re.escape(term)}\b", label) for term in KEY_ACTION_TERMS)


def _compact_evidence_step(step: JsonDict, *, field: str) -> JsonDict:
    item = _require_dict(step, field=field)
    action = _require_dict(item.get("action"), field=f"{field}.action")
    result = _require_dict(item.get("result"), field=f"{field}.result")
    payload: JsonDict = {
        "step": item.get("step"),
        "action": {
            "action": action.get("action"),
            "index": action.get("index"),
            "text": action.get("text"),
            "value": action.get("value"),
            "status": action.get("status"),
        },
        "target": item.get("resolved_target")
        if isinstance(item.get("resolved_target"), dict)
        else None,
        "result": {
            "status": result.get("status"),
            "state_changed": result.get("state_changed"),
            "runtime_changed": result.get("runtime_changed"),
            "progress_classification": result.get("progress_classification"),
            "extracted_content": truncate_text(
                str(result.get("extracted_content", "")), EVIDENCE_PACK_TOOL_RESULT_LIMIT
            ),
        },
    }
    return payload


def _runtime_delta(before: JsonDict | None, after: JsonDict | None, *, field: str) -> JsonDict:
    if before is None or after is None:
        raise ValueError(f"{field} requires both before_action and after_action observations")
    before_logs = _require_dict(before.get("runtime_logs"), field=f"{field}.before.runtime_logs")
    after_logs = _require_dict(after.get("runtime_logs"), field=f"{field}.after.runtime_logs")
    before_counts = _runtime_log_counts(before_logs, field=f"{field}.before.runtime_logs")
    after_counts = _runtime_log_counts(after_logs, field=f"{field}.after.runtime_logs")
    return {
        key: int(after_counts[key]) - int(before_counts[key])
        for key in ("tool_logs", "resource_logs", "confirmation_events")
    }


def _compact_tool_logs(value: object) -> JsonDict:
    tool_logs = _require_list(value, field="actor.tool_logs")
    call_counts: dict[str, int] = {}
    calls: list[JsonDict] = []
    detailed_calls: list[JsonDict] = []
    for index, item in enumerate(tool_logs, start=1):
        log = _require_dict(item, field=f"actor.tool_logs[{index}]")
        name = str(log.get("name") or "")
        call_counts[name] = call_counts.get(name, 0) + 1
        calls.append(
            {
                "index": index,
                "name": log.get("name"),
                "args": _truncate_json_value(log.get("args"), 120),
                "error": log.get("error"),
            }
        )
        name_lower = name.lower()
        is_key_call = any(term in name_lower for term in KEY_TOOL_TERMS)
        is_tail_call = index > max(0, len(tool_logs) - 3)
        if (is_key_call or log.get("error") or is_tail_call) and len(detailed_calls) < 4:
            detailed_calls.append(
                {
                    "index": index,
                    "name": log.get("name"),
                    "args": _truncate_json_value(log.get("args"), 160),
                    "result": _truncate_json_value(log.get("result"), 180),
                    "error": log.get("error"),
                    "evidence": _truncate_json_value(log.get("evidence"), 180),
                }
            )
    if len(calls) > 30:
        calls.append(
            {
                "omitted_middle_calls": len(calls) - 30,
            }
        )
        calls = calls[:15] + calls[-16:]
    return {
        "total_calls": len(tool_logs),
        "call_counts": call_counts,
        "call_sequence": calls,
        "detailed_calls": detailed_calls,
    }


def _truncate_json_value(value: object, limit: int) -> object:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(text) <= limit:
        return value
    return {"truncated_json": truncate_text(text, limit)}


def _compact_scenario_states(value: object) -> JsonDict:
    if value is None:
        return {}
    states = _require_dict(value, field="actor.scenario_states")
    scenarios: JsonDict = {}
    for scenario, payload in states.items():
        scenario_payload = _require_dict(payload, field=f"actor.scenario_states.{scenario}")
        scenarios[str(scenario)] = {
            "initial_state": _truncate_json_value(
                scenario_payload.get("initial_state"), EVIDENCE_PACK_STATE_LIMIT
            ),
            "state": _truncate_json_value(scenario_payload.get("state"), EVIDENCE_PACK_STATE_LIMIT),
        }
    return scenarios


def _build_key_action_pack(
    *,
    steps: list[object],
    observations: list[JsonDict],
    state_diffs: list[object],
) -> list[JsonDict]:
    state_diff_steps = set()
    for index, item in enumerate(state_diffs, start=1):
        diff = _require_dict(item, field=f"actor.state_diffs[{index}]")
        if diff.get("state_changed") is True or diff.get("runtime_changed") is True:
            state_diff_steps.add(int(diff.get("step") or 0))
    key_actions: list[JsonDict] = []
    for index, raw_step in enumerate(steps, start=1):
        step = _require_dict(raw_step, field=f"actor.steps[{index}]")
        if "action" not in step or "result" not in step:
            continue
        if not _is_key_action_step(step, state_diff_steps):
            continue
        step_number = int(step.get("step") or 0)
        before = _step_observation_by_phase(observations, step=step_number, phase="before_action")
        after = _step_observation_by_phase(observations, step=step_number, phase="after_action")
        if before is None or after is None:
            raise ValueError(f"Key action step {step_number} is missing before/after observation")
        key_actions.append(
            {
                "step": _compact_evidence_step(step, field=f"actor.steps[{index}]"),
                "runtime_delta": _runtime_delta(
                    before, after, field=f"actor.key_actions[{step_number}]"
                ),
                "before": _compact_before_evidence_observation(
                    before, field=f"actor.observations.step_{step_number}.before_action"
                ),
                "after": _compact_evidence_observation(
                    after,
                    field=f"actor.observations.step_{step_number}.after_action",
                    include_full_visible_text=True,
                ),
            }
        )
        if len(key_actions) >= EVIDENCE_PACK_ACTION_LIMIT:
            break
    return key_actions


def _runtime_change_observation_pack(observations: list[JsonDict]) -> list[JsonDict]:
    selected: list[JsonDict] = []
    previous_signature = ""
    for index, observation in enumerate(observations, start=1):
        runtime_logs = _require_dict(
            observation.get("runtime_logs"), field=f"actor.observations[{index}].runtime_logs"
        )
        signature = _runtime_log_signature(
            runtime_logs, field=f"actor.observations[{index}].runtime_logs"
        )
        if signature != previous_signature:
            runtime_logs = _require_dict(
                observation.get("runtime_logs"),
                field=f"actor.observations[{index}].runtime_logs",
            )
            selected.append(
                {
                    "step": observation.get("step"),
                    "phase": observation.get("phase"),
                    "visible_text": truncate_text(
                        str(observation.get("visible_text", "")),
                        EVIDENCE_PACK_RUNTIME_CHANGE_TEXT_LIMIT,
                    ),
                    "runtime_counts": _runtime_log_counts(
                        runtime_logs, field=f"actor.observations[{index}].runtime_logs"
                    ),
                }
            )
        previous_signature = signature
    return selected[:4]


def _build_evidence_pack(actor: JsonDict, final_ui: JsonDict) -> JsonDict:
    observations_raw = _require_list(actor.get("observations"), field="actor.observations")
    observations: list[JsonDict] = []
    for index, item in enumerate(observations_raw, start=1):
        observation = _require_dict(item, field=f"actor.observations[{index}]")
        if any(key.startswith("omitted_") for key in observation):
            continue
        observations.append(observation)
    if not observations:
        raise ValueError("actor.observations must contain at least one observation")
    steps = _require_list(actor.get("steps"), field="actor.steps")
    state_diffs = _require_list(actor.get("state_diffs"), field="actor.state_diffs")
    final_observation = observations[-1]
    final_runtime_logs = _require_dict(
        final_observation.get("runtime_logs"), field="actor.final_observation.runtime_logs"
    )
    return {
        "compression": {
            "method": "event_centered_v1",
            "policy": (
                "Key after-action evidence is selected by action semantics, state/runtime deltas, "
                "and runtime-log changes. tool_logs and scenario_states are the primary "
                "tool-grounding evidence."
            ),
        },
        "final_state": {
            "ui": {
                "url": final_ui.get("url"),
                "text": truncate_text(
                    str(final_ui.get("text", "")), EVIDENCE_PACK_FINAL_UI_TEXT_LIMIT
                ),
                "elements": _compact_evidence_elements(
                    _require_list(final_ui.get("elements"), field="current_turn.final_ui.elements")
                ),
            },
            "observation": _compact_evidence_observation(
                final_observation,
                field="actor.observations.final",
                include_full_visible_text=False,
            ),
            "runtime_counts": _runtime_log_counts(
                final_runtime_logs, field="actor.final_observation.runtime_logs"
            ),
        },
        "key_actions": _build_key_action_pack(
            steps=steps,
            observations=observations,
            state_diffs=state_diffs,
        ),
        "runtime_change_observations": _runtime_change_observation_pack(observations),
        "runtime_grounding": {
            "tool_logs": _compact_tool_logs(actor.get("tool_logs")),
            "resource_logs_total": len(
                _require_list(actor.get("resource_logs"), field="actor.resource_logs")
            ),
            "confirmation_events_total": len(
                _require_list(actor.get("confirmation_events"), field="actor.confirmation_events")
            ),
            "scenario_states": _compact_scenario_states(actor.get("scenario_states")),
        },
        "actor_noise": {
            "status": actor.get("status"),
            "console_errors": evaluator_visible_console_errors(
                _require_list(actor.get("console_errors"), field="actor.console_errors")
            ),
            "interaction_errors": _require_list(
                actor.get("interaction_errors"), field="actor.interaction_errors"
            ),
        },
    }


def _compact_behavioral_steps(value: object) -> list[JsonDict]:
    steps_raw = _require_list(value, field="actor.steps")
    steps: list[JsonDict] = []
    for offset, item in enumerate(_tail_list(steps_raw, 4), start=1):
        step_item = _require_dict(item, field=f"actor.steps_tail[{offset}]")
        compact_step: JsonDict = {"step": step_item.get("step")}
        action = step_item.get("action")
        if isinstance(action, dict):
            compact_action: JsonDict = {"action": action.get("action")}
            for key in ("index", "text", "value", "status"):
                value = action.get(key)
                if value not in (None, ""):
                    compact_action[key] = value
            compact_step["action"] = compact_action
        else:
            compact_step["action"] = action
        steps.append(compact_step)
    return steps


def _compact_payload_state_diffs(value: object) -> list[JsonDict]:
    diffs = _require_list(value, field="actor.state_diffs")
    compact_diffs: list[JsonDict] = []
    for index, raw_item in enumerate(diffs[:EVIDENCE_PACK_ACTION_LIMIT], start=1):
        item = _require_dict(raw_item, field=f"actor.state_diffs[{index}]")
        compact: JsonDict = {
            "step": item.get("step"),
            "progress_classification": item.get("progress_classification"),
            "state_changed": item.get("state_changed"),
            "runtime_changed": item.get("runtime_changed"),
        }
        action = item.get("action")
        if isinstance(action, dict):
            compact_action = {
                key: action.get(key)
                for key in ("action", "index", "text", "value", "status")
                if action.get(key) not in (None, "")
            }
            if compact_action:
                compact["action"] = compact_action
        for source_key, target_key in (
            ("visible_text_before_excerpt", "before_text"),
            ("visible_text_after_excerpt", "after_text"),
            ("result_excerpt", "result"),
            ("runtime_log_delta_summary", "runtime_delta_summary"),
        ):
            field = item.get(source_key)
            if isinstance(field, str) and field.strip():
                compact[target_key] = truncate_text(field.strip(), PAYLOAD_STATE_DIFF_TEXT_LIMIT)
        compact_diffs.append(compact)
    return compact_diffs


def _compact_current_turn(current_turn: JsonDict) -> JsonDict:
    final_ui = _require_dict(current_turn.get("final_ui"), field="current_turn.final_ui")
    actor = _require_dict(current_turn.get("actor"), field="current_turn.actor")
    final_elements = _require_list(final_ui.get("elements"), field="current_turn.final_ui.elements")
    actor_steps = _require_list(actor.get("steps"), field="current_turn.actor.steps")
    actor_tool_logs = _require_list(actor.get("tool_logs"), field="current_turn.actor.tool_logs")
    actor_resource_logs = _require_list(
        actor.get("resource_logs"), field="current_turn.actor.resource_logs"
    )
    actor_confirmation_events = _require_list(
        actor.get("confirmation_events", []), field="current_turn.actor.confirmation_events"
    )
    actor_console_errors = evaluator_visible_console_errors(
        _require_list(actor.get("console_errors"), field="current_turn.actor.console_errors")
    )
    actor_interaction_errors = _require_list(
        actor.get("interaction_errors"), field="current_turn.actor.interaction_errors"
    )
    evidence_summary = copy_actor_evidence_summary(
        _require_dict(
            actor.get("evidence_summary", {}), field="current_turn.actor.evidence_summary"
        )
    )
    verification_checks = _require_list(
        actor.get("verification_checks", []), field="current_turn.actor.verification_checks"
    )
    state_diffs = _require_list(
        actor.get("state_diffs", []), field="current_turn.actor.state_diffs"
    )
    final_assessment = _require_dict(
        actor.get("final_assessment", {}), field="current_turn.actor.final_assessment"
    )
    diagnostics = _require_dict(
        actor.get("diagnostics", {}), field="current_turn.actor.diagnostics"
    )
    evidence_pack = _build_evidence_pack(actor, final_ui)
    result: JsonDict = {
        "task_id": current_turn.get("task_id"),
        "turn": current_turn.get("turn"),
        "user_request": benchmark_request(current_turn.get("user_request")),
        "assistant_text": truncate_text(
            str(current_turn.get("assistant_text", "")), CURRENT_TURN_TEXT_CHAR_LIMIT
        ),
        "final_ui": {
            "url": final_ui.get("url"),
            "text": truncate_text(str(final_ui.get("text", "")), CURRENT_TURN_TEXT_CHAR_LIMIT),
            "dom_tree": truncate_text(
                str(final_ui.get("dom_tree", "")), CURRENT_TURN_TREE_CHAR_LIMIT
            ),
            "ax_tree": truncate_text(
                str(final_ui.get("ax_tree", "")), CURRENT_TURN_TREE_CHAR_LIMIT
            ),
            "elements": final_elements[:CURRENT_TURN_ELEMENTS_LIMIT],
        },
        "actor": {
            "status": actor.get("status"),
            "summary": actor.get("summary"),
            "evidence_summary": evidence_summary,
            "verification_checks": verification_checks,
            "evidence_pack": evidence_pack,
            "state_diffs": state_diffs,
            "final_assessment": final_assessment,
            "visual_process_screenshots": _compact_visual_evidence(
                actor.get("visual_process_screenshots")
            ),
            "visual_quality_findings": _compact_visual_evidence(
                actor.get("visual_quality_findings")
            ),
            "steps": actor_steps[:CURRENT_TURN_STEPS_LIMIT],
            "observations": _compact_observations(actor.get("observations")),
            "tool_logs": actor_tool_logs,
            "resource_logs": actor_resource_logs,
            "confirmation_events": actor_confirmation_events,
            "scenario_states": _compact_scenario_states(actor.get("scenario_states")),
            "console_errors": actor_console_errors,
            "interaction_errors": actor_interaction_errors,
            "diagnostics": diagnostics,
        },
    }
    if "generated_files" in current_turn:
        result["generated_files"] = _compact_generated_files(
            current_turn.get("generated_files"), char_limit=ALIGNMENT_SOURCE_CHAR_LIMIT
        )
        result["static_visual_audit"] = _static_visual_audit(current_turn)
    return result


def _maybe_screenshot_path_from_current_turn(current_turn: JsonDict) -> Path | None:
    final_ui = current_turn.get("final_ui")
    if isinstance(final_ui, dict):
        screenshot = final_ui.get("screenshot")
        if isinstance(screenshot, str) and screenshot.strip():
            path = Path(screenshot)
            if path.exists():
                return path
    actor = current_turn.get("actor")
    if isinstance(actor, dict):
        screenshot = actor.get("final_screenshot")
        if isinstance(screenshot, str) and screenshot.strip():
            path = Path(screenshot)
            if path.exists():
                return path
    return None


def _raw_visible_text_for_audit(current_turn: JsonDict) -> str:
    parts: list[str] = []
    final_ui = current_turn.get("final_ui")
    if isinstance(final_ui, dict):
        parts.append(str(final_ui.get("text", "") or ""))
    actor = current_turn.get("actor")
    if isinstance(actor, dict):
        parts.append(str(actor.get("final_text", "") or ""))
        parts.append(str(actor.get("summary", "") or ""))
    return "\n".join(part for part in parts if part)


def _audit_element_role_counts(current_turn: JsonDict) -> JsonDict:
    final_ui = current_turn.get("final_ui")
    elements = final_ui.get("elements") if isinstance(final_ui, dict) else None
    if not isinstance(elements, list):
        actor = current_turn.get("actor")
        elements = actor.get("final_elements") if isinstance(actor, dict) else []
    counts: dict[str, int] = {}
    for element in elements if isinstance(elements, list) else []:
        if not isinstance(element, dict):
            continue
        role = str(element.get("role") or "unknown").strip().lower() or "unknown"
        counts[role] = counts.get(role, 0) + 1
    return dict(sorted(counts.items()))


def _source_files_for_visual_audit(current_turn: JsonDict) -> dict[str, str]:
    files = current_turn.get("generated_files")
    if not isinstance(files, dict):
        return {}
    return {
        str(path): str(contents)
        for path, contents in files.items()
        if isinstance(path, str) and isinstance(contents, str)
    }


def _css_class_tokens(value: str) -> set[str]:
    tokens: set[str] = set()
    for token in re.findall(r"[_a-zA-Z][\w-]*", value):
        if token in {"class", "className", "true", "false", "null", "undefined"}:
            continue
        tokens.add(token)
    return tokens


def _used_css_classes_from_source(path: str, contents: str) -> set[str]:
    if path.endswith(".css"):
        return set()
    used: set[str] = set()
    attr_pattern = re.compile(
        r"\bclass(?:Name)?\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|\{\s*`([^`]*)`\s*\})",
        flags=re.DOTALL,
    )
    for match in attr_pattern.finditer(contents):
        raw_value = next((group for group in match.groups() if group is not None), "")
        literal_value = re.sub(r"\$\{[^}]*\}", " ", raw_value)
        used.update(_css_class_tokens(literal_value))
    return used


def _defined_css_classes_from_source(path: str, contents: str) -> set[str]:
    if not path.endswith(".css"):
        return set()
    stripped = re.sub(r"/\*.*?\*/", "", contents, flags=re.DOTALL)
    return {
        token
        for token in re.findall(r"(?<![\w-])\.([_a-zA-Z][\w-]*)", stripped)
        if not token.replace("_", "").isdigit()
    }


def _css_class_coverage_audit(current_turn: JsonDict) -> JsonDict:
    files = _source_files_for_visual_audit(current_turn)
    if not files:
        return {"status": "unavailable", "reason": "No generated files available"}

    used: set[str] = set()
    defined: set[str] = set()
    for path, contents in files.items():
        used.update(_used_css_classes_from_source(path, contents))
        defined.update(_defined_css_classes_from_source(path, contents))
    defined.update(SCAFFOLD_BASE_CSS_CLASSES)

    undefined = sorted(used - defined)
    used_count = len(used)
    undefined_count = len(undefined)
    undefined_ratio = round(undefined_count / used_count, 4) if used_count else 0.0
    return {
        "status": "ok",
        "used_class_count": used_count,
        "defined_class_count": len(defined),
        "undefined_class_count": undefined_count,
        "undefined_ratio": undefined_ratio,
        "undefined_class_samples": undefined[:30],
    }


def _screenshot_density_audit(path: Path) -> JsonDict:
    try:
        from PIL import Image
    except Exception:
        return {
            "status": "unavailable",
            "reason": "Pillow is not available for screenshot audit",
        }
    try:
        image = Image.open(path).convert("RGB")
    except Exception as exc:
        return {
            "status": "unavailable",
            "reason": f"Could not open screenshot: {type(exc).__name__}",
        }

    width, height = image.size
    pixels = image.load()
    if width <= 80 or height <= 80:
        return {
            "status": "unavailable",
            "reason": "Screenshot too small for density audit",
            "size": [width, height],
        }

    samples: list[tuple[int, int, int]] = []
    for x in range(20, max(21, width - 40), max(1, width // 30)):
        for y in (20, height - 20):
            samples.append(pixels[x, y])
    for y in range(20, max(21, height - 20), max(1, height // 30)):
        for x in (20, width - 40):
            samples.append(pixels[x, y])
    if not samples:
        return {"status": "unavailable", "reason": "No screenshot samples available"}
    background = tuple(
        sorted(sample[channel] for sample in samples)[len(samples) // 2] for channel in range(3)
    )

    def is_ink(x: int, y: int) -> bool:
        pixel = pixels[x, y]
        distance = sum((pixel[channel] - background[channel]) ** 2 for channel in range(3))
        return distance**0.5 > 28

    step = 4
    regions = {
        "left": (20, 20, width // 2, height - 20),
        "right": (width // 2, 20, width - 40, height - 20),
        "top": (20, 20, width - 40, height // 2),
        "bottom": (20, height // 2, width - 40, height - 20),
        "all": (20, 20, width - 40, height - 20),
    }
    densities: JsonDict = {
        "status": "ok",
        "size": [width, height],
        "background_rgb": list(background),
    }
    for name, (x0, y0, x1, y1) in regions.items():
        total = 0
        ink = 0
        for y in range(y0, y1, step):
            for x in range(x0, x1, step):
                total += 1
                if is_ink(x, y):
                    ink += 1
        densities[f"{name}_ink_density"] = round(ink / total, 4) if total else 0.0

    occupied_tiles = 0
    total_tiles = 0
    for grid_y in range(8):
        for grid_x in range(12):
            x0 = 20 + grid_x * (width - 60) // 12
            x1 = 20 + (grid_x + 1) * (width - 60) // 12
            y0 = 20 + grid_y * (height - 40) // 8
            y1 = 20 + (grid_y + 1) * (height - 40) // 8
            total = 0
            ink = 0
            for y in range(y0, y1, step):
                for x in range(x0, x1, step):
                    total += 1
                    if is_ink(x, y):
                        ink += 1
            if total and ink / total > 0.01:
                occupied_tiles += 1
            total_tiles += 1
    densities["tile_occupancy_ratio"] = (
        round(occupied_tiles / total_tiles, 4) if total_tiles else 0.0
    )
    return densities


def _static_visual_audit(current_turn: JsonDict) -> JsonDict:
    visible_text = _raw_visible_text_for_audit(current_turn)
    role_counts = _audit_element_role_counts(current_turn)
    control_roles = {"button", "slider", "textbox", "checkbox", "combobox", "spinbutton"}
    control_count = sum(role_counts.get(role, 0) for role in control_roles)
    audit: JsonDict = {
        "status": "ok",
        "purpose": (
            "MiniAppBench-style static visual evidence for Presentation: this is not a score "
            "cap and must be interpreted with screenshot and task context."
        ),
        "element_role_counts": role_counts,
        "control_count": control_count,
        "css_class_coverage": _css_class_coverage_audit(current_turn),
        "findings": [],
    }

    findings: list[JsonDict] = []
    if re.search(r"\$[^\n$]{1,40}\$", visible_text):
        findings.append(
            {
                "id": "raw_math_tokens_visible",
                "severity": "suspicious",
                "evidence": "Visible text appears to contain raw TeX-style math delimiters.",
            }
        )

    css_coverage = audit["css_class_coverage"]
    if isinstance(css_coverage, dict) and css_coverage.get("status") == "ok":
        used_count = int(css_coverage.get("used_class_count", 0))
        undefined_count = int(css_coverage.get("undefined_class_count", 0))
        undefined_ratio = float(css_coverage.get("undefined_ratio", 0.0))
        if used_count >= 8 and undefined_count >= 8 and undefined_ratio >= 0.50:
            severity = (
                "severe"
                if used_count >= 12 and undefined_count >= 12 and undefined_ratio >= 0.75
                else "suspicious"
            )
            findings.append(
                {
                    "id": "undefined_css_classes",
                    "severity": severity,
                    "evidence": (
                        f"{undefined_count} of {used_count} CSS classes used in generated UI "
                        "source are not defined in generated CSS, so intended styling may not "
                        "actually render."
                    ),
                    "samples": css_coverage.get("undefined_class_samples", []),
                }
            )

    screenshot_path = _maybe_screenshot_path_from_current_turn(current_turn)
    if screenshot_path is None:
        audit["screenshot_density"] = {
            "status": "unavailable",
            "reason": "No materialized screenshot path available",
        }
    else:
        density = _screenshot_density_audit(screenshot_path)
        audit["screenshot_density"] = density
        if density.get("status") == "ok":
            all_density = float(density.get("all_ink_density", 0.0))
            left_density = float(density.get("left_ink_density", 0.0))
            right_density = float(density.get("right_ink_density", 0.0))
            tile_occupancy = float(density.get("tile_occupancy_ratio", 0.0))
            if all_density < 0.003:
                findings.append(
                    {
                        "id": "near_blank_screenshot",
                        "severity": "severe",
                        "evidence": "Screenshot has almost no visible foreground content.",
                    }
                )
            if all_density < 0.025 and tile_occupancy < 0.30:
                findings.append(
                    {
                        "id": "sparse_unfinished_layout",
                        "severity": "suspicious",
                        "evidence": (
                            "Screenshot foreground content is sparse and occupies few screen "
                            "regions, suggesting an unfinished or weakly composed layout."
                        ),
                    }
                )
            if left_density > 0.01 and right_density < 0.003:
                findings.append(
                    {
                        "id": "large_unused_right_side",
                        "severity": "suspicious",
                        "evidence": (
                            "Foreground content is concentrated on the left while the right "
                            "half is nearly empty."
                        ),
                    }
                )
            if control_count >= 8 and all_density < 0.035 and tile_occupancy < 0.40:
                findings.append(
                    {
                        "id": "control_heavy_sparse_layout",
                        "severity": "suspicious",
                        "evidence": (
                            "The UI exposes many controls while the visible layout remains sparse."
                        ),
                    }
                )

    audit["findings"] = findings
    return audit


def _behavioral_previous_turns(previous_turns: object) -> list[JsonDict]:
    if not isinstance(previous_turns, list):
        return []
    compacted: list[JsonDict] = []
    for turn in previous_turns[-3:]:
        if not isinstance(turn, dict):
            continue
        compacted.append(
            {
                "turn": turn.get("turn"),
                "user_request": benchmark_request(turn.get("user_request")),
                "final_ui_text": truncate_text(turn.get("final_ui_text", ""), 4000),
                "actor_status": turn.get("actor_status"),
            }
        )
    return compacted


def _compact_declared_tools(value: object) -> list[JsonDict]:
    if not isinstance(value, list):
        return []
    tools: list[JsonDict] = []
    for item in value[:20]:
        if not isinstance(item, dict):
            continue
        compact: JsonDict = {}
        for key in ("name", "description", "mode"):
            field = item.get(key)
            if field not in (None, ""):
                compact[key] = field
        if item.get("allowed") is False:
            compact["allowed"] = False
        if compact:
            tools.append(compact)
    return tools


def _compact_declared_resources(value: object) -> list[JsonDict]:
    if not isinstance(value, list):
        return []
    resources: list[JsonDict] = []
    for item in value[:20]:
        if not isinstance(item, dict):
            continue
        compact: JsonDict = {}
        for key in ("uri", "name", "mime_type", "description"):
            field = item.get(key)
            if field not in (None, ""):
                compact[key] = field
        if compact:
            resources.append(compact)
    return resources


def _compact_generated_files(value: object, *, char_limit: int) -> JsonDict:
    if not isinstance(value, dict):
        return {}
    files: JsonDict = {}
    remaining = char_limit
    for path, contents in sorted(value.items()):
        if remaining <= 0:
            break
        text = str(contents)
        clipped = truncate_text(text, remaining)
        files[str(path)] = clipped
        remaining -= len(clipped)
    return files


def _actor_evidence_summary(actor: JsonDict) -> JsonDict:
    return copy_actor_evidence_summary(actor.get("evidence_summary", {}))


def _compact_visual_evidence(value: object) -> list[JsonDict]:
    if not isinstance(value, list):
        return []
    compacted: list[JsonDict] = []
    for item in value[:6]:
        if not isinstance(item, dict):
            continue
        compact: JsonDict = {}
        for key in ("step", "phase", "severity", "source", "reason"):
            field = item.get(key)
            if field not in (None, ""):
                compact[key] = field
        note = item.get("note")
        if isinstance(note, str) and note.strip():
            compact["note"] = truncate_text(note.strip(), 800)
        excerpt = item.get("visible_text_excerpt")
        if isinstance(excerpt, str) and excerpt.strip():
            compact["visible_text_excerpt"] = truncate_text(excerpt.strip(), 800)
        action = item.get("action")
        if isinstance(action, dict):
            compact_action = {
                key: action.get(key)
                for key in ("action", "index", "ref", "text", "value")
                if action.get(key) not in (None, "")
            }
            if compact_action:
                compact["action"] = compact_action
        if compact:
            compacted.append(compact)
    return compacted


def _compact_validation_contract(value: object, *, current_turn: int) -> JsonDict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("task.private_eval['validation_contract'] must be an object when present")
    value = copy_validation_contract(value)
    if value is None:
        return None
    scenarios = value.get("validation_scenarios")
    if not isinstance(scenarios, list):
        raise ValueError("validation_contract.validation_scenarios must be an array")
    compact_scenarios: list[JsonDict] = []
    for index, scenario in enumerate(scenarios, start=1):
        if not isinstance(scenario, dict):
            raise ValueError(f"validation_contract.validation_scenarios[{index}] must be an object")
        compact: JsonDict = {}
        scenario_turn = scenario.get("turn")
        if not isinstance(scenario_turn, int) or scenario_turn < 1:
            raise ValueError(
                f"validation_contract.validation_scenarios[{index}].turn must be a positive integer"
            )
        if scenario_turn > current_turn:
            continue
        compact["turn"] = scenario_turn
        for key in ("name", "public_requirement_ref", "oracle", "fairness_risk"):
            field = scenario.get(key)
            if isinstance(field, str) and field.strip():
                compact[key] = field.strip()
        evidence_requirements = scenario.get("evidence_requirements")
        if isinstance(evidence_requirements, list):
            compact_requirements: list[JsonDict] = []
            for requirement in evidence_requirements:
                if not isinstance(requirement, dict):
                    continue
                compact_requirement: JsonDict = {}
                for key in ("id", "surface", "expect", "action", "evidence"):
                    field = requirement.get(key)
                    if isinstance(field, str) and field.strip():
                        compact_requirement[key] = field.strip()
                if {"id", "surface", "expect"}.issubset(compact_requirement):
                    compact_requirements.append(compact_requirement)
            if compact_requirements:
                compact["evidence_requirements"] = compact_requirements
        if compact:
            compact_scenarios.append(compact)
    if not compact_scenarios:
        return None
    return {"validation_scenarios": compact_scenarios}


def _judge_reference_from_validation_contract(
    validation_contract: JsonDict | None, *, current_turn: int | None = None
) -> JsonDict | None:
    if not isinstance(validation_contract, dict):
        return None
    scenarios = validation_contract.get("validation_scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        return None
    scenario_references: list[JsonDict] = []
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            continue
        reference: JsonDict = {
            "id": str(scenario.get("name") or f"scenario_{len(scenario_references) + 1}"),
            "turn": scenario.get("turn"),
            "public_requirement": scenario.get("public_requirement_ref"),
        }
        scenario_turn = scenario.get("turn")
        is_current_turn = current_turn is None or scenario_turn == current_turn
        expected_behavior = scenario.get("oracle")
        if isinstance(expected_behavior, str) and expected_behavior.strip():
            expected_limit = (
                EVIDENCE_PACK_REFERENCE_TEXT_LIMIT
                if is_current_turn
                else EVIDENCE_PACK_PRIOR_REFERENCE_TEXT_LIMIT
            )
            reference["expected_behavior"] = truncate_text(
                expected_behavior.strip(), expected_limit
            )
        evidence_requirements = scenario.get("evidence_requirements")
        if is_current_turn and isinstance(evidence_requirements, list):
            checks: list[JsonDict] = []
            for requirement in evidence_requirements:
                if not isinstance(requirement, dict):
                    continue
                check = {
                    key: truncate_text(
                        requirement[key].strip(), EVIDENCE_PACK_REFERENCE_CHECK_LIMIT
                    )
                    for key in ("id", "surface", "expect", "action", "evidence")
                    if isinstance(requirement.get(key), str) and requirement.get(key).strip()
                }
                if {"id", "surface", "expect"}.issubset(check):
                    checks.append(check)
            if checks:
                reference["evidence_checks"] = checks
        scenario_references.append(reference)
    if not scenario_references:
        return None
    return {
        "reference_type": "task_evaluation_reference",
        "source": "validation_contract",
        "scenario_references": scenario_references,
    }


def _behavioral_task_payload(base: JsonDict, runtime_context: JsonDict) -> JsonDict:
    task = _require_dict(base.get("task"), field="task")
    turn = runtime_context.get("turn", task.get("turn"))
    payload: JsonDict = {
        "task_id": task.get("task_id"),
        "title": task.get("title"),
        "suite": task.get("suite"),
        "turn": turn,
        "current_user_request": benchmark_request(runtime_context.get("current_user_request", "")),
        "tools": runtime_context.get("tools", task.get("tools", [])),
        "resources": runtime_context.get("resources", task.get("resources", [])),
    }
    validation_contract = runtime_context.get("validation_contract")
    if isinstance(validation_contract, dict) and validation_contract:
        current_turn_int = (
            int(turn) if isinstance(turn, (int, str)) and str(turn).isdigit() else None
        )
        judge_reference = _judge_reference_from_validation_contract(
            validation_contract, current_turn=current_turn_int
        )
        if judge_reference:
            payload["evaluation_reference"] = judge_reference
    return payload


def _behavioral_shared_payload(base: JsonDict) -> JsonDict:
    current_turn = base.get("current_turn", {})
    final_ui = current_turn.get("final_ui", {}) if isinstance(current_turn, dict) else {}
    actor = current_turn.get("actor", {}) if isinstance(current_turn, dict) else {}
    evidence_pack = _require_dict(
        actor.get("evidence_pack"), field="current_turn.actor.evidence_pack"
    )
    runtime_grounding = _require_dict(
        evidence_pack.get("runtime_grounding"),
        field="current_turn.actor.evidence_pack.runtime_grounding",
    )
    runtime_context = base.get("task_runtime_context", {})
    runtime_context = runtime_context if isinstance(runtime_context, dict) else {}
    task_payload = _behavioral_task_payload(base, runtime_context)
    payload_current_turn: JsonDict = {
        "user_request": benchmark_request(current_turn.get("user_request")),
        "assistant_text": current_turn.get("assistant_text", ""),
        "final_ui": {
            "url": final_ui.get("url"),
            "text": truncate_text(str(final_ui.get("text", "")), EVIDENCE_PACK_FINAL_UI_TEXT_LIMIT),
            "elements": _compact_evidence_elements(
                _require_list(final_ui.get("elements"), field="current_turn.final_ui.elements")
            ),
        },
        "actor": {
            "status": actor.get("status"),
            "summary": actor.get("summary"),
            "evidence_summary": _actor_evidence_summary(actor),
            "evidence_pack": evidence_pack,
            "verification_checks": {
                "source": "see evidence_pack.key_actions and task.evaluation_reference",
                "total": len(
                    _require_list(
                        actor.get("verification_checks"), field="actor.verification_checks"
                    )
                ),
            },
            "state_diffs": _compact_payload_state_diffs(actor.get("state_diffs", [])),
            "final_assessment": actor.get("final_assessment", {}),
            "visual_process_screenshots": actor.get("visual_process_screenshots", []),
            "visual_quality_findings": actor.get("visual_quality_findings", []),
            "steps": _compact_behavioral_steps(actor.get("steps", [])),
            "observations": evidence_pack.get("runtime_change_observations"),
            "tool_logs": runtime_grounding.get("tool_logs"),
            "resource_logs": {
                "total": runtime_grounding.get("resource_logs_total"),
            },
            "confirmation_events": {
                "total": runtime_grounding.get("confirmation_events_total"),
            },
            "console_errors": actor.get("console_errors", []),
            "interaction_errors": actor.get("interaction_errors", []),
        },
    }
    if "generated_files" in current_turn:
        payload_current_turn["generated_files"] = _compact_generated_files(
            current_turn.get("generated_files"), char_limit=ALIGNMENT_SOURCE_CHAR_LIMIT
        )
    return {
        "task": task_payload,
        "previous_turns": _behavioral_previous_turns(base.get("previous_turns", [])),
        "current_turn": payload_current_turn,
    }


def _presentation_shared_payload(base: JsonDict) -> JsonDict:
    current_turn = base.get("current_turn", {})
    final_ui = current_turn.get("final_ui", {}) if isinstance(current_turn, dict) else {}
    actor = current_turn.get("actor", {}) if isinstance(current_turn, dict) else {}
    payload_current_turn: JsonDict = {
        "turn": current_turn.get("turn"),
        "user_request": benchmark_request(current_turn.get("user_request")),
        "assistant_text": current_turn.get("assistant_text", ""),
        "final_ui": {
            "url": final_ui.get("url"),
            "text": final_ui.get("text", ""),
            "elements": final_ui.get("elements", []),
        },
        "actor": {
            "status": actor.get("status"),
            "summary": actor.get("summary"),
            "evidence_summary": _actor_evidence_summary(actor),
            "final_assessment": actor.get("final_assessment", {}),
            "visual_process_screenshots": actor.get("visual_process_screenshots", []),
            "visual_quality_findings": actor.get("visual_quality_findings", []),
            "confirmation_events": actor.get("confirmation_events", []),
            "console_errors": actor.get("console_errors", []),
            "interaction_errors": actor.get("interaction_errors", []),
        },
    }
    if "static_visual_audit" in current_turn:
        payload_current_turn["static_visual_audit"] = current_turn["static_visual_audit"]
    return {
        "task": base["task"],
        "current_turn": payload_current_turn,
    }


def _alignment_shared_payload(base: JsonDict) -> JsonDict:
    current_turn = base.get("current_turn", {})
    final_ui = current_turn.get("final_ui", {}) if isinstance(current_turn, dict) else {}
    actor = current_turn.get("actor", {}) if isinstance(current_turn, dict) else {}
    evidence_pack = _require_dict(
        actor.get("evidence_pack"), field="current_turn.actor.evidence_pack"
    )
    runtime_grounding = _require_dict(
        evidence_pack.get("runtime_grounding"),
        field="current_turn.actor.evidence_pack.runtime_grounding",
    )
    payload_current_turn: JsonDict = {
        "turn": current_turn.get("turn"),
        "user_request": benchmark_request(current_turn.get("user_request")),
        "assistant_text": current_turn.get("assistant_text", ""),
        "final_ui": {
            "url": final_ui.get("url"),
            "text": truncate_text(str(final_ui.get("text", "")), EVIDENCE_PACK_FINAL_UI_TEXT_LIMIT),
            "ax_tree": truncate_text(str(final_ui.get("ax_tree", "")), EVIDENCE_PACK_DOM_LIMIT),
            "elements": _compact_evidence_elements(
                _require_list(final_ui.get("elements"), field="current_turn.final_ui.elements")
            ),
        },
        "actor": {
            "status": actor.get("status"),
            "summary": actor.get("summary"),
            "evidence_summary": _actor_evidence_summary(actor),
            "evidence_pack": evidence_pack,
            "verification_checks": {
                "source": "see evidence_pack.key_actions and task.evaluation_reference",
                "total": len(
                    _require_list(
                        actor.get("verification_checks"), field="actor.verification_checks"
                    )
                ),
            },
            "state_diffs": _compact_payload_state_diffs(actor.get("state_diffs", [])),
            "final_assessment": actor.get("final_assessment", {}),
            "visual_process_screenshots": actor.get("visual_process_screenshots", []),
            "visual_quality_findings": actor.get("visual_quality_findings", []),
            "steps": _compact_behavioral_steps(actor.get("steps", [])),
            "observations": evidence_pack.get("runtime_change_observations"),
            "tool_logs": runtime_grounding.get("tool_logs"),
            "resource_logs": {
                "total": runtime_grounding.get("resource_logs_total"),
            },
            "confirmation_events": {
                "total": runtime_grounding.get("confirmation_events_total"),
            },
            "console_errors": actor.get("console_errors", []),
            "interaction_errors": actor.get("interaction_errors", []),
        },
    }
    if "generated_files" in current_turn:
        payload_current_turn["generated_files"] = _compact_generated_files(
            current_turn.get("generated_files"), char_limit=ALIGNMENT_SOURCE_CHAR_LIMIT
        )
    turn_diffs_payload: JsonDict = {
        "has_previous_turn": base["turn_diffs"].get("has_previous_turn", False),
        "previous_turn": base["turn_diffs"].get("previous_turn"),
        "current_turn": base["turn_diffs"].get("current_turn"),
        "assistant_text_diff": base["turn_diffs"].get("assistant_text_diff", ""),
        "ui_text_diff": base["turn_diffs"].get("ui_text_diff", ""),
    }
    if "code_diffs" in base["turn_diffs"]:
        turn_diffs_payload["code_diffs"] = base["turn_diffs"].get("code_diffs", [])
    return {
        "task": base["task"],
        "current_turn": payload_current_turn,
        "turn_diffs": turn_diffs_payload,
    }


def _compact_turn_diffs(turn_diffs: JsonDict) -> JsonDict:
    code_diffs = turn_diffs.get("code_diffs", [])
    compact_code_diffs = []
    remaining_diff_chars = CODE_DIFF_TOTAL_CHAR_LIMIT
    omitted_code_diff_count = 0
    if isinstance(code_diffs, list):
        for item in code_diffs[:20]:
            if not isinstance(item, dict):
                continue
            if remaining_diff_chars <= 0:
                omitted_code_diff_count += 1
                continue
            diff_text = str(item.get("diff", ""))
            diff_limit = min(CODE_DIFF_PER_FILE_CHAR_LIMIT, remaining_diff_chars)
            compact_diff = truncate_text(diff_text, diff_limit)
            remaining_diff_chars -= min(len(diff_text), diff_limit)
            compact_code_diffs.append(
                {
                    "path": item.get("path"),
                    "status": item.get("status"),
                    "diff": compact_diff,
                }
            )
        omitted_code_diff_count += max(len(code_diffs) - 20, 0)
    result: JsonDict = {
        "has_previous_turn": turn_diffs.get("has_previous_turn", False),
        "previous_turn": turn_diffs.get("previous_turn"),
        "current_turn": turn_diffs.get("current_turn"),
        "assistant_text_diff": truncate_text(
            turn_diffs.get("assistant_text_diff", ""), TEXT_DIFF_CHAR_LIMIT
        ),
        "ui_text_diff": truncate_text(turn_diffs.get("ui_text_diff", ""), TEXT_DIFF_CHAR_LIMIT),
        "tool_call_diff": truncate_text(turn_diffs.get("tool_call_diff", ""), TEXT_DIFF_CHAR_LIMIT),
        "resource_read_diff": truncate_text(
            turn_diffs.get("resource_read_diff", ""), TEXT_DIFF_CHAR_LIMIT
        ),
    }
    if "code_diffs" in turn_diffs:
        result["code_diffs"] = compact_code_diffs
        result["omitted_code_diff_count"] = omitted_code_diff_count
    return result


def _base_payload(
    task: TaskDefinition,
    *,
    previous_turns: list[JsonDict],
    current_turn: JsonDict,
    turn_diffs: JsonDict,
) -> JsonDict:
    validation_contract = _compact_validation_contract(
        task.private_eval.get("validation_contract"),
        current_turn=task.turn_index,
    )
    task_payload: JsonDict = {
        "task_id": task.task_id,
        "title": task.title,
    }
    suite = _suite_name(task)
    if suite:
        task_payload["suite"] = suite
    task_runtime_context: JsonDict = {
        "turn": task.turn_index,
        "current_user_request": benchmark_request(task.user_prompt),
        "tools": _compact_declared_tools(task.public_task.get("tools", [])),
        "resources": _compact_declared_resources(task.public_task.get("resources", [])),
    }
    if validation_contract:
        task_payload["validation_contract"] = validation_contract
        task_runtime_context["validation_contract"] = validation_contract
    return {
        "task": task_payload,
        "task_runtime_context": task_runtime_context,
        "previous_turns": previous_turns,
        "current_turn": _compact_current_turn(current_turn),
        "turn_diffs": _compact_turn_diffs(turn_diffs),
    }


def _dimension_group_payload(dimensions: tuple[str, ...], base: JsonDict) -> JsonDict:
    if dimensions == EXECUTION_DIMENSIONS:
        return _behavioral_shared_payload(base)
    if dimensions == PRESENTATION_DIMENSIONS:
        return _presentation_shared_payload(base)
    if dimensions == ALIGNMENT_DIMENSIONS:
        return _alignment_shared_payload(base)
    raise ValueError(f"Unsupported dimension group: {dimensions!r}")


def _suite_name(task: TaskDefinition) -> str:
    metadata_suite = task.metadata.get("suite") if isinstance(task.metadata, dict) else None
    if isinstance(metadata_suite, str) and metadata_suite.strip():
        return metadata_suite.strip()
    public_suite = task.public_task.get("suite")
    if isinstance(public_suite, str) and public_suite.strip():
        return public_suite.strip()
    return ""


def _suite_profile(suite: str) -> JsonDict:
    profile = SUITE_EVALUATION_PROFILES.get(suite)
    return profile if isinstance(profile, dict) else {}


def _dimension_group_config_payload(dimensions: tuple[str, ...], *, suite: str = "") -> JsonDict:
    return {
        "requested_dimensions": list(dimensions),
        "suite": suite,
        "suite_profile": _suite_profile(suite),
        "dimensions": {
            dimension: {
                "rubric": DIMENSION_RUBRICS[dimension],
                "score_anchors": DIMENSION_SCORE_ANCHORS[dimension],
                "allowed_failure_types": list(DIMENSION_ALLOWED_FAILURE_TYPES[dimension]),
                **(
                    {"scoring_caps": list(PRESENTATION_SCORING_CAPS)}
                    if dimension == "Presentation"
                    else {}
                ),
            }
            for dimension in dimensions
        },
    }


def _required_screenshot_path(actor_result: JsonDict) -> Path:
    screenshot_path = actor_result.get("final_screenshot")
    if not isinstance(screenshot_path, str) or not screenshot_path:
        raise ValueError("Actor did not produce a final screenshot")
    image_path = Path(screenshot_path)
    if not image_path.exists():
        raise FileNotFoundError(f"Actor screenshot does not exist: {image_path}")
    return image_path


def _actor_process_screenshot_paths(actor_result: JsonDict, *, limit: int = 6) -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()
    for field in ("visual_quality_findings", "visual_process_screenshots"):
        value = actor_result.get(field)
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            screenshot = item.get("screenshot")
            if not isinstance(screenshot, str) or not screenshot:
                continue
            path = Path(screenshot)
            if not path.exists() or path in seen:
                continue
            paths.append(path)
            seen.add(path)
            if len(paths) >= limit:
                return paths
    return paths


def _screenshot_content(screenshot_path: Path) -> JsonDict:
    encoded = base64.b64encode(screenshot_path.read_bytes()).decode("utf-8")
    suffix = screenshot_path.suffix.lower()
    mime_type = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
    }


def _single_dimension_response_schema(dimension: str) -> JsonDict:
    return {
        **SINGLE_DIMENSION_JUDGE_RESULT_SCHEMA,
        "properties": {
            **SINGLE_DIMENSION_JUDGE_RESULT_SCHEMA["properties"],
            "failure_types": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": list(DIMENSION_ALLOWED_FAILURE_TYPES[dimension]),
                },
            },
        },
    }


def _dimensions_response_schema(dimensions: tuple[str, ...]) -> JsonDict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            dimension: _single_dimension_response_schema(dimension) for dimension in dimensions
        },
        "required": list(dimensions),
    }


def _dimension_response_payload(response: object, dimensions: tuple[str, ...]) -> JsonDict:
    parsed = response.parsed_json  # type: ignore[attr-defined]
    if not isinstance(parsed, dict):
        content_text = response.content_text  # type: ignore[attr-defined]
        if isinstance(content_text, str) and content_text.strip():
            fallback = _json_object_from_text(content_text)
            if isinstance(fallback, dict):
                parsed = fallback
    if not isinstance(parsed, dict):
        raise ValueError("Dimension judge response must be a JSON object")

    if len(dimensions) == 1 and all(
        key in parsed for key in SINGLE_DIMENSION_JUDGE_RESULT_SCHEMA["required"]
    ):
        return {dimensions[0]: parsed}
    return parsed


def _json_object_from_text(value: str) -> object | None:
    decoder = json.JSONDecoder()
    text = value.strip()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            parsed, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        return parsed
    return None


class OpenAICompatibleDimensionJudgeBackend:
    def __init__(self, config: ComponentRuntimeConfig) -> None:
        self.config = config

    def _request_inputs(
        self,
        shared_payload: JsonDict,
        config_payload: JsonDict,
        *,
        screenshot_path: Path | None = None,
        screenshot_paths: list[Path] | None = None,
    ) -> list[LlmInput]:
        content: list[LlmInput] = [
            LlmInput(
                type="text",
                text=json.dumps(shared_payload, ensure_ascii=False, separators=(",", ":")),
            ),
            LlmInput(
                type="text",
                text=json.dumps(config_payload, ensure_ascii=False, separators=(",", ":")),
            ),
        ]
        image_paths: list[Path] = []
        if screenshot_path is not None:
            image_paths.append(screenshot_path)
        if screenshot_paths:
            image_paths.extend(screenshot_paths)
        for image_path in image_paths:
            suffix = image_path.suffix.lower()
            mime_type = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
            content.append(
                LlmInput(
                    type="image",
                    image_bytes=image_path.read_bytes(),
                    mime_type=mime_type,
                )
            )
        return content

    def judge_dimensions(
        self,
        dimensions: tuple[str, ...],
        shared_payload: JsonDict,
        config_payload: JsonDict,
        *,
        screenshot_path: Path | None = None,
        screenshot_paths: list[Path] | None = None,
    ) -> tuple[dict[str, JsonDict], JsonDict]:
        content = self._request_inputs(
            shared_payload,
            config_payload,
            screenshot_path=screenshot_path,
            screenshot_paths=screenshot_paths,
        )
        response = call_llm(
            LlmRequest(
                provider_config=self.config.provider_config,
                system_prompt=dimension_judge_system_prompt(
                    evidence_policy=DIMENSION_EVIDENCE_POLICY,
                ),
                inputs=content,
                response_mode="json_schema",
                schema_name=f"genui_{'_'.join(dimension.lower() for dimension in dimensions)}_judge_result",
                response_schema=_dimensions_response_schema(dimensions),
                component="evaluator",
            )
        )
        try:
            parsed = _dimension_response_payload(response, dimensions)
            result = {
                dimension: normalize_single_dimension_result(parsed.get(dimension), dimension)
                for dimension in dimensions
            }
        except Exception as exc:
            raise ProviderResponseError(
                (
                    "Provider returned an unusable dimension judge response for "
                    f"{', '.join(dimensions)}: {exc}"
                ),
                endpoint=response.endpoint_family,
                usage=response.usage,
            ) from exc
        return result, response.token_usage


def create_dimension_judge_backend(runtime_config: ComponentRuntimeConfig) -> DimensionJudgeBackend:
    if runtime_config is None:
        raise ValueError("runtime_config is required for dimension judging")
    return OpenAICompatibleDimensionJudgeBackend(runtime_config)


def run_dimension_judges(
    task: TaskDefinition,
    actor_result: JsonDict,
    *,
    previous_turns: list[JsonDict],
    current_turn: JsonDict,
    turn_diffs: JsonDict,
    runtime_config: ComponentRuntimeConfig,
) -> JsonDict:
    if runtime_config is None:
        raise ValueError("runtime_config is required for dimension judging")
    backend = create_dimension_judge_backend(runtime_config)
    current_turn_for_judge = current_turn
    turn_diffs_for_judge = turn_diffs
    if not runtime_config.include_source_code:
        current_turn_for_judge = {k: v for k, v in current_turn.items() if k != "generated_files"}
        turn_diffs_for_judge = {k: v for k, v in turn_diffs.items() if k != "code_diffs"}
    base = _base_payload(
        task,
        previous_turns=previous_turns,
        current_turn=current_turn_for_judge,
        turn_diffs=turn_diffs_for_judge,
    )
    suite = _suite_name(task)
    results: dict[str, JsonDict] = {}
    total_token_usage = empty_token_usage()
    screenshot_path: Path | None = None
    process_screenshot_paths: list[Path] | None = None
    for _, dimensions in DIMENSION_GROUPS:
        try:
            needs_screenshot = runtime_config.use_screenshot and any(
                dimension in SCREENSHOT_DIMENSIONS for dimension in dimensions
            )
            if needs_screenshot and screenshot_path is None:
                screenshot_path = _required_screenshot_path(actor_result)
                process_screenshot_paths = _actor_process_screenshot_paths(actor_result)
            group_results, group_usage = backend.judge_dimensions(
                dimensions,
                _dimension_group_payload(dimensions, base),
                _dimension_group_config_payload(dimensions, suite=suite),
                screenshot_path=screenshot_path if needs_screenshot else None,
                screenshot_paths=process_screenshot_paths if needs_screenshot else None,
            )
            results.update(group_results)
            total_token_usage = add_token_usage(total_token_usage, group_usage)
        except Exception as exc:
            partial_usage = add_token_usage(total_token_usage, token_usage_for_exception(exc))
            if partial_usage.get("total_tokens", 0) > 0:
                raise ProviderResponseError(
                    f"Dimension judge failed after partial provider usage: {exc}",
                    endpoint="chat/completions",
                    usage=partial_usage,
                ) from exc
            raise
    return aggregate_dimension_results(results, token_usage=total_token_usage)
