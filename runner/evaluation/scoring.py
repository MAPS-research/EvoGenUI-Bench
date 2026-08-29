from __future__ import annotations

from collections.abc import Mapping, Sequence

from runner.evaluation.reliability_metrics import generation_response_received
from runtime.types import EvaluationResult, JsonDict


def _dim_scores_from_dimensions(dimensions: object) -> dict[str, float] | None:
    if not isinstance(dimensions, dict) or not dimensions:
        return None
    scores: dict[str, float] = {}
    for name, value in dimensions.items():
        if not isinstance(value, dict):
            continue
        try:
            scores[str(name)] = float(value.get("score", 0.0))
        except (TypeError, ValueError):
            continue
    return scores or None


def breakdown_from_evaluation_results(
    *,
    turns_requested: Sequence[int],
    results: Sequence[EvaluationResult],
    generation_responses: Mapping[int, bool] | None = None,
) -> list[JsonDict]:
    by_turn: dict[int, EvaluationResult] = {result.turn: result for result in results}
    breakdown: list[JsonDict] = []
    for turn in turns_requested:
        result = by_turn.get(int(turn))
        if result is None:
            breakdown.append(
                {
                    "turn": int(turn),
                    "reached": False,
                    "fully_evaluated": False,
                    "generation_response_received": (
                        generation_responses.get(int(turn))
                        if generation_responses is not None
                        else None
                    ),
                    "official_pass": False,
                    "evaluator_score": None,
                    "dim_scores": None,
                    "failure_reason": "unreached",
                    "failure_bucket": "unreached",
                }
            )
            continue
        dim_scores = _dim_scores_from_dimensions(result.dimensions)
        fully_evaluated = bool(
            result.generation_pass and result.build_pass and dim_scores is not None
        )
        response_received = (
            generation_responses.get(int(turn))
            if generation_responses is not None and int(turn) in generation_responses
            else generation_response_received(result)
        )
        breakdown.append(
            {
                "turn": result.turn,
                "reached": True,
                "fully_evaluated": fully_evaluated,
                "generation_response_received": response_received,
                "official_pass": result.official_pass,
                "evaluator_score": float(result.evaluator_score),
                "dim_scores": dim_scores,
                "failure_reason": result.failure_reason,
                "failure_bucket": result.failure_bucket,
            }
        )
    return breakdown


def breakdown_from_stage_results(
    *,
    turns_requested: Sequence[int],
    results: Sequence[JsonDict],
    turns_skipped: Sequence[JsonDict] = (),
    generation_responses: Mapping[int, bool] | None = None,
) -> list[JsonDict]:
    by_turn: dict[int, JsonDict] = {}
    for result in results:
        turn_value = result.get("turn") if isinstance(result, dict) else None
        try:
            by_turn[int(turn_value)] = result  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
    skipped_reasons: dict[int, str] = {}
    for skip in turns_skipped:
        if not isinstance(skip, dict):
            continue
        turn_value = skip.get("turn")
        try:
            skipped_reasons[int(turn_value)] = str(skip.get("reason") or "skipped")
        except (TypeError, ValueError):
            continue
    breakdown: list[JsonDict] = []
    for turn in turns_requested:
        turn_int = int(turn)
        result = by_turn.get(turn_int)
        if result is None:
            breakdown.append(
                {
                    "turn": turn_int,
                    "reached": False,
                    "fully_evaluated": False,
                    "generation_response_received": (
                        generation_responses.get(turn_int)
                        if generation_responses is not None
                        else None
                    ),
                    "official_pass": False,
                    "evaluator_score": None,
                    "dim_scores": None,
                    "failure_reason": skipped_reasons.get(turn_int, "unreached"),
                    "failure_bucket": "skipped" if turn_int in skipped_reasons else "unreached",
                }
            )
            continue
        evaluator_score = result.get("evaluator_score")
        try:
            score_value = float(evaluator_score) if evaluator_score is not None else 0.0
        except (TypeError, ValueError):
            score_value = 0.0
        details = result.get("details") if isinstance(result, dict) else None
        dim_scores: dict[str, float] | None = None
        if isinstance(details, dict):
            evaluator = details.get("evaluator")
            if isinstance(evaluator, dict):
                dim_scores = _dim_scores_from_dimensions(evaluator.get("dimensions"))
        if dim_scores is None:
            dim_scores = _dim_scores_from_dimensions(result.get("dimensions"))
        fully_evaluated = dim_scores is not None
        response_received = (
            generation_responses.get(turn_int)
            if generation_responses is not None and turn_int in generation_responses
            else result.get("generation_response_received", True)
        )
        breakdown.append(
            {
                "turn": turn_int,
                "reached": True,
                "fully_evaluated": fully_evaluated,
                "generation_response_received": response_received,
                "official_pass": bool(result.get("official_pass", False)),
                "evaluator_score": score_value,
                "dim_scores": dim_scores,
                "failure_reason": result.get("failure_reason"),
                "failure_bucket": result.get("failure_bucket"),
            }
        )
    return breakdown
