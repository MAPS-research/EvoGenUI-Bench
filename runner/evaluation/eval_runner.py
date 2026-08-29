from __future__ import annotations

import copy

from runner.evaluation.dimension_judge import run_dimension_judges
from runner.execution.execution_session import run_actor_and_capture_snapshot
from runner.execution.multiturn_state import (
    actor_snapshot_from_result,
    build_turn_diffs,
    compact_turn_history,
)
from runner.tools.experiment_config import BlindActorRuntimeConfig, ComponentRuntimeConfig
from runner.tools.token_usage import summarize_component_token_usage
from runtime.types import BuildArtifacts, EvaluationResult, JsonDict, PreviewHandle, TaskDefinition

INFRA_FAILURE_REASONS = {"generation", "build", "execute", "runtime", "evaluate"}


def evaluation_failure_mode_from_payload(result: JsonDict) -> str:
    if bool(result.get("official_pass", False)):
        return "passed"

    failure_reason = result.get("failure_reason")
    failure_bucket = result.get("failure_bucket")
    if failure_bucket == "blocked" or failure_reason == "blocked":
        return "blocked"
    if failure_reason in INFRA_FAILURE_REASONS:
        return "infra_failure"
    if failure_bucket == "inconclusive":
        return "inconclusive"
    if failure_reason == "evaluator" or failure_bucket == "quality":
        return "real_ui_failure"
    return "unknown_failure"


def evaluate_task(
    task: TaskDefinition,
    preview: PreviewHandle,
    artifacts: BuildArtifacts,
    *,
    previous_turns: list[JsonDict],
    blind_actor_config: BlindActorRuntimeConfig,
    evaluator_config: ComponentRuntimeConfig,
) -> EvaluationResult:
    actor_result, current_snapshot = run_actor_and_capture_snapshot(
        task,
        preview,
        artifacts,
        blind_actor_config=blind_actor_config,
    )
    return evaluate_snapshot(
        task,
        actor_result,
        previous_turns=previous_turns,
        current_snapshot=current_snapshot,
        evaluator_config=evaluator_config,
    )


def _sanitize_evaluation_inputs(
    current_turn: JsonDict,
    turn_diffs: JsonDict,
    *,
    include_source_code: bool,
) -> tuple[JsonDict, JsonDict]:
    if include_source_code:
        return current_turn, turn_diffs
    sanitized_turn = {k: v for k, v in current_turn.items() if k != "generated_files"}
    sanitized_diffs = {k: v for k, v in turn_diffs.items() if k != "code_diffs"}
    return sanitized_turn, sanitized_diffs


def _current_snapshot_with_raw_actor(
    current_snapshot: JsonDict, actor_result: JsonDict
) -> JsonDict:
    snapshot = copy.deepcopy(current_snapshot)
    snapshot["actor"] = actor_snapshot_from_result(actor_result)
    return snapshot


def evaluate_snapshot(
    task: TaskDefinition,
    actor_result: JsonDict,
    *,
    previous_turns: list[JsonDict],
    current_snapshot: JsonDict,
    evaluator_config: ComponentRuntimeConfig,
) -> EvaluationResult:
    current_snapshot = _current_snapshot_with_raw_actor(current_snapshot, actor_result)
    compact_previous_turns = compact_turn_history(previous_turns)
    turn_diffs = build_turn_diffs(previous_turns[-1] if previous_turns else None, current_snapshot)
    sanitized_snapshot, sanitized_diffs = _sanitize_evaluation_inputs(
        current_snapshot,
        turn_diffs,
        include_source_code=evaluator_config.include_source_code,
    )
    dimension_result = _evaluate_dimensions(
        task,
        actor_result,
        previous_turns=compact_previous_turns,
        current_turn=sanitized_snapshot,
        turn_diffs=sanitized_diffs,
        runtime_config=evaluator_config,
    )
    actor_pass = actor_result.get("status") == "success"
    dimension_pass = bool(dimension_result.get("passed"))
    official_pass, failure_reason, failure_bucket = _evaluation_outcome(dimension_result)

    return EvaluationResult(
        task_id=task.task_id,
        turn=task.turn_index,
        generation_pass=True,
        build_pass=True,
        actor_pass=actor_pass,
        evaluator_pass=dimension_pass,
        evaluator_score=float(dimension_result.get("score", 1.0)),
        evaluator_summary=str(dimension_result.get("summary", "")),
        dimensions=dimension_result.get("dimensions", {}),
        official_pass=official_pass,
        failure_reason=failure_reason,
        details=_evaluation_details(
            actor_result,
            dimension_result,
            current_snapshot=current_snapshot,
            turn_diffs=turn_diffs,
            previous_turns=compact_previous_turns,
            official_pass=official_pass,
        ),
        failure_bucket=failure_bucket,
    )


def _evaluate_dimensions(
    task: TaskDefinition,
    actor_result: JsonDict,
    *,
    previous_turns: list[JsonDict],
    current_turn: JsonDict,
    turn_diffs: JsonDict,
    runtime_config: ComponentRuntimeConfig,
) -> JsonDict:
    return run_dimension_judges(
        task,
        actor_result,
        previous_turns=previous_turns,
        current_turn=current_turn,
        turn_diffs=turn_diffs,
        runtime_config=runtime_config,
    )


def _evaluation_outcome(dimension_result: JsonDict) -> tuple[bool, str | None, str | None]:
    dimension_pass = bool(dimension_result.get("passed"))
    if dimension_pass:
        return True, None, None
    return False, "evaluator", "quality"


def _evaluation_details(
    actor_result: JsonDict,
    dimension_result: JsonDict,
    *,
    current_snapshot: JsonDict,
    turn_diffs: JsonDict,
    previous_turns: list[JsonDict],
    official_pass: bool,
) -> JsonDict:
    return {
        "actor": actor_result,
        "evaluator": dimension_result,
        "actor_status_is_advisory": True,
        "current_turn_snapshot": current_snapshot,
        "turn_diffs": turn_diffs,
        "previous_turns": previous_turns,
        "official_pass": official_pass,
        "token_usage": summarize_component_token_usage(
            {
                "evaluator": dimension_result.get("token_usage", {}),
                "blind_actor": actor_result.get("token_usage", {}),
            }
        ),
    }
