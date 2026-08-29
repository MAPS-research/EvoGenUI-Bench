from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from runner.evaluation.eval_runner import evaluation_failure_mode_from_payload
from runner.evaluation.scoring import breakdown_from_stage_results
from runner.orchestration.turn_bundle import (
    load_turn_bundle,
    load_turn_bundle_from_relative_path,
    read_stage_payload,
)
from runner.tools.token_usage import merge_component_token_usage, summarize_component_token_usage
from runtime.types import EvaluationResult, JsonDict

_INFRA_FAILURE_REASONS = {"generation", "build", "execute", "runtime", "evaluate"}


def load_json_object(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def evaluation_result_from_payload(payload: dict[str, object]) -> EvaluationResult:
    details = payload.get("details", {})
    if not isinstance(details, dict):
        raise ValueError("evaluation report details must be an object")
    dimensions = payload.get("dimensions", {})
    if not isinstance(dimensions, dict):
        raise ValueError("evaluation report dimensions must be an object")
    return EvaluationResult(
        task_id=str(payload.get("task_id", "")),
        turn=int(payload.get("turn", 0)),
        generation_pass=bool(payload.get("generation_pass", False)),
        build_pass=bool(payload.get("build_pass", False)),
        actor_pass=bool(payload.get("actor_pass", False)),
        evaluator_pass=bool(payload.get("evaluator_pass", False)),
        evaluator_score=float(payload.get("evaluator_score", 0.0)),
        evaluator_summary=str(payload.get("evaluator_summary", "")),
        dimensions=dimensions,
        official_pass=bool(payload.get("official_pass", False)),
        failure_reason=(
            None if payload.get("failure_reason") is None else str(payload.get("failure_reason"))
        ),
        details=details,
        failure_bucket=(
            None if payload.get("failure_bucket") is None else str(payload.get("failure_bucket"))
        ),
    )


def snapshot_from_result(result: EvaluationResult, *, output_root: Path) -> JsonDict | None:
    snapshot = result.details.get("current_turn_snapshot")
    if isinstance(snapshot, dict):
        return snapshot
    bundle_path = result.details.get("turn_bundle")
    if not isinstance(bundle_path, str) or not bundle_path:
        return None
    try:
        bundle = load_turn_bundle_from_relative_path(
            output_root=output_root,
            relative_root=bundle_path,
        )
    except (FileNotFoundError, ValueError):
        return None
    snapshot_path = bundle.execution_dir / "snapshot.json"
    if not snapshot_path.exists():
        return None
    return load_json_object(snapshot_path)


def latest_bundle_snapshot(task_id: str, *, turn: int, output_root: Path) -> JsonDict | None:
    try:
        bundle = load_turn_bundle(
            output_root=output_root,
            task_id=task_id,
            turn=turn,
            required_stage="execution",
        )
    except FileNotFoundError:
        return None
    return read_stage_payload(bundle, "execution", "snapshot.json")


def load_saved_turn_snapshot(task_id: str, *, turn: int, output_root: Path) -> JsonDict | None:
    report_path = output_root / "reports" / task_id / f"turn-{turn}.json"
    if report_path.exists():
        result = evaluation_result_from_payload(load_json_object(report_path))
        return snapshot_from_result(result, output_root=output_root)
    return latest_bundle_snapshot(task_id, turn=turn, output_root=output_root)


def required_previous_turn_snapshots(
    task_id: str,
    *,
    turn: int,
    output_root: Path,
    run_id: str | None = None,
) -> list[JsonDict]:
    snapshots: list[JsonDict] = []
    for previous_turn in range(1, turn):
        if run_id is not None:
            bundle = load_turn_bundle(
                output_root=output_root,
                task_id=task_id,
                turn=previous_turn,
                run_id=run_id,
                required_stage="execution",
            )
            snapshot = read_stage_payload(bundle, "execution", "snapshot.json")
        else:
            snapshot = load_saved_turn_snapshot(
                task_id,
                turn=previous_turn,
                output_root=output_root,
            )
        if snapshot is None:
            raise FileNotFoundError(
                f"Missing prior execution snapshot for task '{task_id}' turn {previous_turn} under {output_root}"
            )
        snapshots.append(snapshot)
    return snapshots


def stage_conversation_summary(
    task_id: str,
    *,
    stage: str,
    provider: str | None,
    turns_requested: list[int],
    results: list[dict[str, object]],
    turns_skipped: list[dict[str, object]],
    turn_failures: list[dict[str, object]],
) -> dict[str, object]:
    stage_turn_failures = list(turn_failures)
    if stage == "evaluate":
        for item in results:
            if bool(item.get("official_pass", False)):
                continue
            stage_turn_failures.append(
                {
                    "turn": item.get("turn"),
                    "failure_reason": item.get("failure_reason"),
                    "failure_bucket": item.get("failure_bucket"),
                    "failure_mode": evaluation_failure_mode_from_payload(item),
                    "official_pass": False,
                }
            )

    failure_buckets = Counter(
        str(item.get("failure_bucket", "unknown"))
        for item in stage_turn_failures
        if item.get("failure_bucket")
    )
    failure_reasons = Counter(
        str(item.get("failure_reason", "unknown"))
        for item in stage_turn_failures
        if item.get("failure_reason")
    )
    failure_modes = Counter(
        str(item.get("failure_mode", "unknown"))
        for item in stage_turn_failures
        if item.get("failure_mode")
    )
    turns_breakdown = breakdown_from_stage_results(
        turns_requested=turns_requested,
        results=results,
        turns_skipped=turns_skipped,
    )
    blocked = any(item.get("failure_bucket") == "blocked" for item in turn_failures)
    infra_failure_count = sum(
        1
        for item in turn_failures
        if item.get("failure_reason") in _INFRA_FAILURE_REASONS
        or item.get("failure_bucket") == "blocked"
    )
    judge_failed_turn_count = sum(
        1 for item in results if not bool(item.get("official_pass", False))
    )
    evaluation_complete = len(results) == len(turns_requested) and infra_failure_count == 0
    payload = {
        "task_id": task_id,
        "stage": stage,
        "provider": provider,
        "turns_requested": turns_requested,
        "turns_completed": [
            int(item.get("turn", 0)) for item in results if item.get("turn") is not None
        ],
        "turns_skipped": turns_skipped,
        "turns_breakdown": turns_breakdown,
        "turn_failures": stage_turn_failures,
        "completed": len(turn_failures) == 0 and len(results) == len(turns_requested),
        "blocked": blocked,
        "evaluation_complete": evaluation_complete,
        "infra_failed": infra_failure_count > 0,
        "infra_failure_count": infra_failure_count,
        "judge_failed_turn_count": judge_failed_turn_count,
        "completion_reason": _stage_completion_reason(
            stage=stage,
            result_count=len(results),
            turns_requested=turns_requested,
            blocked=blocked,
            infra_failure_count=infra_failure_count,
            judge_failed_turn_count=judge_failed_turn_count,
        ),
        "failure_buckets": dict(sorted(failure_buckets.items())),
        "failure_reasons": dict(sorted(failure_reasons.items())),
        "failure_modes": dict(sorted(failure_modes.items())),
        "token_usage": summarize_component_token_usage(
            merge_component_token_usage([result.get("token_usage", {}) for result in results])
        ),
        "results": results,
    }
    if stage == "evaluate":
        strict_pass = payload["completed"] and all(
            bool(item.get("official_pass", False)) for item in results
        )
        payload["official_pass"] = strict_pass
        payload["strict_pass"] = strict_pass
    return payload


def _evaluation_completion_reason(
    *,
    turn_count: int,
    turns_requested: list[int],
    infra_failure_count: int,
    judge_failed_count: int,
    blocked: bool,
) -> str:
    if blocked:
        return "blocked"
    if infra_failure_count > 0:
        return "infra_failed"
    if turn_count != len(turns_requested):
        return "incomplete"
    if judge_failed_count > 0:
        return "judge_failed"
    return "passed"


def _stage_completion_reason(
    *,
    stage: str,
    result_count: int,
    turns_requested: list[int],
    blocked: bool,
    infra_failure_count: int,
    judge_failed_turn_count: int,
) -> str:
    if stage != "evaluate":
        if blocked:
            return "blocked"
        if infra_failure_count > 0:
            return "infra_failed"
        if result_count != len(turns_requested):
            return "incomplete"
        return "completed"
    return _evaluation_completion_reason(
        turn_count=result_count,
        turns_requested=turns_requested,
        infra_failure_count=infra_failure_count,
        judge_failed_count=judge_failed_turn_count,
        blocked=blocked,
    )
