from __future__ import annotations

import json
from pathlib import Path

from runner.evaluation.eval_runner import evaluation_failure_mode_from_payload
from runner.tools.experiment_logging import utc_timestamp
from runtime.types import EvaluationResult


def evaluation_to_report_dict(result: EvaluationResult) -> dict[str, object]:
    report = {
        "written_at": utc_timestamp(),
        "task_id": result.task_id,
        "turn": result.turn,
        "generation_pass": result.generation_pass,
        "build_pass": result.build_pass,
        "actor_pass": result.actor_pass,
        "evaluator_pass": result.evaluator_pass,
        "evaluator_score": result.evaluator_score,
        "evaluator_summary": result.evaluator_summary,
        "official_pass": result.official_pass,
        "dimensions": result.dimensions,
        "failure_reason": result.failure_reason,
        "failure_bucket": result.failure_bucket,
        "details": result.details,
    }
    report["failure_mode"] = evaluation_failure_mode_from_payload(report)
    return report


def write_report(path: Path, result: EvaluationResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(evaluation_to_report_dict(result), ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def write_json_report(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    output = {"written_at": utc_timestamp(), **payload}
    path.write_text(json.dumps(output, ensure_ascii=True, indent=2), encoding="utf-8")
