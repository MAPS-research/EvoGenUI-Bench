from __future__ import annotations

from runner.evaluation.evidence_rules import (
    actor_attempted_primary_flow,
    actor_has_runtime_or_state_progress,
    actor_has_supported_verification,
    evaluator_visible_console_errors,
    interaction_errors_empty_or_infra_only,
    is_infra_only_actor_error,
)
from runner.tools.io_utils import truncate_text
from runtime.types import JsonDict

MAX_SOURCE_CHARS = 50000
MAX_TEXT_CHARS = 6000
MAX_TREE_CHARS = 10000


def actor_supervision_signal(actor_result: JsonDict) -> JsonDict:
    status = str(actor_result.get("status") or "unknown").strip().lower()
    evidence_summary = actor_result.get("evidence_summary")
    meaningful_action_count = 0
    if isinstance(evidence_summary, dict) and isinstance(
        evidence_summary.get("meaningful_action_count"), int
    ):
        meaningful_action_count = int(evidence_summary["meaningful_action_count"])
    steps = actor_result.get("steps")
    step_count = len(steps) if isinstance(steps, list) else 0
    primary_flow_attempted = actor_attempted_primary_flow(actor_result)
    state_or_runtime_progress = actor_has_runtime_or_state_progress(actor_result)
    supported_verification = actor_has_supported_verification(actor_result)
    infra_only_error = is_infra_only_actor_error(actor_result)
    interaction_errors_infra_only = interaction_errors_empty_or_infra_only(actor_result)
    actor_limited_status = status in {"stuck", "timeout", "error", "budget_exhausted"}
    if status == "success":
        coverage = "complete"
    elif actor_limited_status and infra_only_error:
        coverage = "blocked_by_infra"
    elif primary_flow_attempted or state_or_runtime_progress or supported_verification:
        coverage = "partial"
    else:
        coverage = "missed"
    return {
        "status": status,
        "coverage": coverage,
        "incomplete_due_to_actor": status != "success" and coverage != "complete",
        "primary_flow_attempted": primary_flow_attempted,
        "state_or_runtime_progress": state_or_runtime_progress,
        "supported_verification": supported_verification,
        "meaningful_action_count": meaningful_action_count,
        "step_count": step_count,
        "infra_only_error": infra_only_error,
        "interaction_errors_infra_only": interaction_errors_infra_only,
        "has_console_errors": bool(strings(actor_result.get("console_errors"))),
        "interaction_errors": strings(actor_result.get("interaction_errors")),
    }


def compact_turn_diffs(value: object) -> JsonDict:
    if not isinstance(value, dict):
        return {}
    return {
        "has_previous_turn": value.get("has_previous_turn"),
        "previous_turn": value.get("previous_turn"),
        "current_turn": value.get("current_turn"),
        "assistant_text_diff": truncate_text(str(value.get("assistant_text_diff", "")), 2000),
        "ui_text_diff": truncate_text(str(value.get("ui_text_diff", "")), 3000),
        "tool_call_diff": truncate_text(str(value.get("tool_call_diff", "")), 2000),
        "resource_read_diff": truncate_text(str(value.get("resource_read_diff", "")), 2000),
    }


def compact_actor(actor: JsonDict) -> JsonDict:
    evidence_summary = actor.get("evidence_summary")
    return {
        "status": actor.get("status"),
        "summary": actor.get("summary"),
        "evidence_summary": evidence_summary if isinstance(evidence_summary, dict) else {},
        "final_assessment": actor.get("final_assessment", {}),
        "steps": _compact_steps(actor.get("steps")),
        "state_diffs": _compact_state_diffs(actor.get("state_diffs")),
        "verification_checks": actor.get("verification_checks", []),
        "tool_logs": actor.get("tool_logs", []),
        "resource_logs": actor.get("resource_logs", []),
        "confirmation_events": actor.get("confirmation_events", []),
        "console_errors": evaluator_visible_console_errors(actor.get("console_errors")),
        "interaction_errors": strings(actor.get("interaction_errors")),
        "observations_tail": _compact_observations(actor.get("observations")),
    }


def compact_snapshot(snapshot: JsonDict) -> JsonDict:
    final_ui = snapshot.get("final_ui")
    if not isinstance(final_ui, dict):
        final_ui = {}
    return {
        "assistant_text": truncate_text(str(snapshot.get("assistant_text", "")), 3000),
        "generated_files": _compact_generated_files(snapshot.get("generated_files")),
        "final_ui": {
            "text": truncate_text(str(final_ui.get("text", "")), MAX_TEXT_CHARS),
            "dom_tree": truncate_text(str(final_ui.get("dom_tree", "")), MAX_TREE_CHARS),
            "ax_tree": truncate_text(str(final_ui.get("ax_tree", "")), MAX_TREE_CHARS),
            "elements": (
                final_ui.get("elements", [])[:120]
                if isinstance(final_ui.get("elements"), list)
                else []
            ),
        },
    }


def _compact_generated_files(value: object) -> JsonDict:
    if not isinstance(value, dict):
        return {}
    remaining = MAX_SOURCE_CHARS
    files: JsonDict = {}
    for path, contents in sorted(value.items()):
        if remaining <= 0:
            break
        text = str(contents)
        clipped = truncate_text(text, remaining)
        files[str(path)] = clipped
        remaining -= len(clipped)
    return files


def _compact_steps(value: object) -> list[JsonDict]:
    if not isinstance(value, list):
        return []
    steps: list[JsonDict] = []
    for item in value[-20:]:
        if not isinstance(item, dict):
            continue
        action = item.get("action") if isinstance(item.get("action"), dict) else {}
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        resolved_target = item.get("resolved_target")
        steps.append(
            {
                "step": item.get("step"),
                "action": {
                    "action": action.get("action"),
                    "text": action.get("text"),
                    "value": action.get("value"),
                    "status": action.get("status"),
                    "rationale": truncate_text(str(action.get("rationale", "")), 1000),
                },
                "target": resolved_target if isinstance(resolved_target, dict) else None,
                "result": {
                    "status": result.get("status"),
                    "state_changed": result.get("state_changed"),
                    "runtime_changed": result.get("runtime_changed"),
                    "progress_classification": result.get("progress_classification"),
                    "extracted_content": truncate_text(
                        str(result.get("extracted_content", "")), 1200
                    ),
                },
            }
        )
    return steps


def _compact_state_diffs(value: object) -> list[JsonDict]:
    if not isinstance(value, list):
        return []
    compacted: list[JsonDict] = []
    for item in value[-12:]:
        if not isinstance(item, dict):
            continue
        compacted.append(
            {
                "step": item.get("step"),
                "progress_classification": item.get("progress_classification"),
                "state_changed": item.get("state_changed"),
                "runtime_changed": item.get("runtime_changed"),
                "result_excerpt": truncate_text(str(item.get("result_excerpt", "")), 800),
                "visible_text_before_excerpt": truncate_text(
                    str(item.get("visible_text_before_excerpt", "")), 1000
                ),
                "visible_text_after_excerpt": truncate_text(
                    str(item.get("visible_text_after_excerpt", "")), 1000
                ),
                "runtime_log_delta_summary": truncate_text(
                    str(item.get("runtime_log_delta_summary", "")), 800
                ),
            }
        )
    return compacted


def _compact_observations(value: object) -> list[JsonDict]:
    if not isinstance(value, list):
        return []
    observations: list[JsonDict] = []
    for item in value[-4:]:
        if not isinstance(item, dict):
            continue
        observations.append(
            {
                "step": item.get("step"),
                "phase": item.get("phase"),
                "visible_text": truncate_text(str(item.get("visible_text", "")), 2500),
                "console_errors": evaluator_visible_console_errors(item.get("console_errors")),
            }
        )
    return observations


def strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def float_value(value: object) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value.strip())
        except ValueError:
            return 0.0
    return 0.0
