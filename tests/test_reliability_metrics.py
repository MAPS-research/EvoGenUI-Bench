from __future__ import annotations

import unittest

from runner.evaluation.reliability_metrics import (
    generation_response_received,
    paper_reliability_diagnostics,
    paper_reliability_metrics,
)
from runner.evaluation.scoring import (
    breakdown_from_evaluation_results,
    breakdown_from_stage_results,
)
from runtime.types import EvaluationResult


def _turn(
    turn: int,
    passed: bool,
    response_received: bool | None = True,
    *,
    provider_failure: bool = False,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "turn": turn,
        "official_pass": passed,
        "generation_response_received": response_received,
        "fully_evaluated": passed,
    }
    if provider_failure:
        payload["failure_reason"] = "generation"
        payload["failure_bucket"] = "infra"
    return payload


def _result(
    *,
    turn: int,
    generation_pass: bool,
    details: dict[str, object],
) -> EvaluationResult:
    return EvaluationResult(
        task_id="task",
        turn=turn,
        generation_pass=generation_pass,
        build_pass=generation_pass,
        actor_pass=False,
        evaluator_pass=False,
        evaluator_score=0.0,
        evaluator_summary="",
        dimensions={},
        official_pass=False,
        failure_reason="generation" if not generation_pass else "quality",
        details=details,
        failure_bucket="quality",
    )


class PaperReliabilityMetricsTests(unittest.TestCase):
    def test_computes_requested_slot_and_episode_metrics(self) -> None:
        conversations = [
            {
                "turns_breakdown": [
                    _turn(1, True),
                    _turn(2, True),
                    _turn(3, False),
                    _turn(4, True),
                    _turn(5, True),
                ]
            },
            {
                "turns_breakdown": [
                    _turn(1, True),
                    _turn(2, True),
                    _turn(3, True),
                    _turn(4, True),
                    _turn(5, True),
                ]
            },
        ]

        metrics = paper_reliability_metrics(conversations)

        self.assertEqual(metrics["requested_turns"], 10)
        self.assertEqual(metrics["passed_turns"], 9)
        self.assertEqual(metrics["turn_pass_rate"], 0.9)
        self.assertEqual(metrics["five_turn_pass_count"], 1)
        self.assertEqual(metrics["tp_at_5"], 0.5)
        self.assertEqual(metrics["cpt"], 3.5)
        self.assertEqual(metrics["apr_num"], 6)
        self.assertEqual(metrics["apr_den"], 7)
        self.assertEqual(metrics["apr"], 6 / 7)

    def test_apr_uses_model_response_not_fully_evaluated(self) -> None:
        conversations = [
            {
                "turns_breakdown": [
                    _turn(1, True),
                    {
                        **_turn(2, False, True),
                        "fully_evaluated": False,
                    },
                    _turn(3, True),
                    _turn(4, False, False),
                    _turn(5, False),
                ]
            }
        ]

        metrics = paper_reliability_metrics(conversations)

        self.assertEqual(metrics["apr_num"], 0)
        self.assertEqual(metrics["apr_den"], 1)
        self.assertEqual(metrics["apr"], 0.0)

    def test_apr_rejects_unknown_response_status_after_a_pass(self) -> None:
        conversations = [
            {
                "turns_breakdown": [
                    _turn(1, True),
                    _turn(2, False, None),
                    _turn(3, False),
                    _turn(4, False),
                    _turn(5, False),
                ]
            }
        ]

        with self.assertRaisesRegex(ValueError, "generation_response_received"):
            paper_reliability_metrics(conversations)

    def test_tp_at_5_rejects_non_five_turn_episode(self) -> None:
        with self.assertRaisesRegex(ValueError, "TP@5 requires exactly 5"):
            paper_reliability_metrics([{"turns_breakdown": [_turn(1, True), _turn(2, True)]}])

    def test_generation_response_status_distinguishes_response_and_request_failures(self) -> None:
        self.assertIs(
            generation_response_received(_result(turn=1, generation_pass=True, details={})),
            True,
        )
        self.assertIs(
            generation_response_received(
                _result(
                    turn=1,
                    generation_pass=False,
                    details={"provider_response": {"failure_bucket": "quality"}},
                )
            ),
            True,
        )
        self.assertIs(
            generation_response_received(
                _result(
                    turn=1,
                    generation_pass=False,
                    details={"provider_request": {"failure_bucket": "timeout"}},
                )
            ),
            False,
        )
        self.assertIsNone(
            generation_response_received(_result(turn=1, generation_pass=False, details={}))
        )

    def test_breakdowns_carry_explicit_response_status(self) -> None:
        evaluation_breakdown = breakdown_from_evaluation_results(
            turns_requested=[1, 2],
            results=[_result(turn=1, generation_pass=True, details={})],
            generation_responses={2: False},
        )
        stage_breakdown = breakdown_from_stage_results(
            turns_requested=[1, 2],
            results=[{"turn": 1, "official_pass": True}],
            generation_responses={2: True},
        )

        self.assertIs(evaluation_breakdown[0]["generation_response_received"], True)
        self.assertIs(evaluation_breakdown[1]["generation_response_received"], False)
        self.assertIs(stage_breakdown[0]["generation_response_received"], True)
        self.assertIs(stage_breakdown[1]["generation_response_received"], True)

    def test_diagnostics_report_position_dependence_and_countable_tp(self) -> None:
        conversations = [
            {
                "turns_breakdown": [
                    _turn(1, True),
                    _turn(2, True),
                    _turn(3, True),
                    _turn(4, True),
                    _turn(5, True),
                ]
            },
            {
                "turns_breakdown": [
                    _turn(1, False, False, provider_failure=True),
                    _turn(2, False, False, provider_failure=True),
                    _turn(3, False, False, provider_failure=True),
                    _turn(4, False, False, provider_failure=True),
                    _turn(5, False, False, provider_failure=True),
                ]
            },
        ]

        diagnostics = paper_reliability_diagnostics(conversations, bootstrap_samples=200)

        self.assertEqual(
            diagnostics["turn_position_stats"],
            [
                {
                    "position": position,
                    "turn": position,
                    "requested_turns": 2,
                    "passed_turns": 1,
                    "turn_pass_rate": 0.5,
                }
                for position in range(1, 6)
            ],
        )
        self.assertEqual(diagnostics["independence_tp_at_5"], 1 / 32)
        self.assertEqual(diagnostics["observed_tp_at_5"], 0.5)
        self.assertEqual(diagnostics["observed_to_independence_ratio"], 16.0)
        self.assertEqual(
            diagnostics["countable_turns"],
            {
                "requested_turns": 5,
                "passed_turns": 5,
                "excluded_provider_no_response_turns": 5,
                "turn_pass_rate": 1.0,
            },
        )
        self.assertEqual(diagnostics["paper_metrics"], paper_reliability_metrics(conversations))

    def test_bootstrap_is_deterministic_and_resamples_whole_episodes(self) -> None:
        conversations = [
            {
                "turns_breakdown": [
                    _turn(1, True),
                    _turn(2, True),
                    _turn(3, True),
                    _turn(4, True),
                    _turn(5, True),
                ]
            },
            {
                "turns_breakdown": [
                    _turn(1, False, False, provider_failure=True),
                    _turn(2, False, False, provider_failure=True),
                    _turn(3, False, False, provider_failure=True),
                    _turn(4, False, False, provider_failure=True),
                    _turn(5, False, False, provider_failure=True),
                ]
            },
        ]

        first = paper_reliability_diagnostics(conversations, bootstrap_samples=101)
        second = paper_reliability_diagnostics(conversations, bootstrap_samples=101)

        self.assertEqual(first["bootstrap"], second["bootstrap"])
        self.assertEqual(first["bootstrap"]["unit"], "task_episode")
        self.assertEqual(first["bootstrap"]["seed"], 7)
        self.assertEqual(first["bootstrap"]["samples"], 101)
        self.assertEqual(first["bootstrap"]["confidence_level"], 0.95)
        self.assertEqual(first["bootstrap"]["ci"]["tp"], first["bootstrap"]["ci"]["tp_at_5"])
        self.assertEqual(
            first["bootstrap"]["ci"]["cpt"]["lower"],
            5 * first["bootstrap"]["ci"]["tp"]["lower"],
        )
        self.assertEqual(
            first["bootstrap"]["ci"]["cpt"]["upper"],
            5 * first["bootstrap"]["ci"]["tp"]["upper"],
        )

    def test_bootstrap_degenerate_intervals_match_point_estimates(self) -> None:
        conversations = [
            {
                "turns_breakdown": [
                    _turn(1, True),
                    _turn(2, True),
                    _turn(3, True),
                    _turn(4, True),
                    _turn(5, True),
                ]
            }
        ]

        diagnostics = paper_reliability_diagnostics(conversations, bootstrap_samples=10)

        self.assertEqual(diagnostics["bootstrap"]["ci"]["tp"], {"lower": 1.0, "upper": 1.0})
        self.assertEqual(
            diagnostics["bootstrap"]["ci"]["tp_at_5"],
            {"lower": 1.0, "upper": 1.0},
        )
        self.assertEqual(diagnostics["bootstrap"]["ci"]["cpt"], {"lower": 5.0, "upper": 5.0})
        self.assertEqual(diagnostics["bootstrap"]["ci"]["apr"], {"lower": 1.0, "upper": 1.0})

    def test_apr_bootstrap_ci_is_none_without_an_eligible_denominator(self) -> None:
        conversations = [
            {
                "turns_breakdown": [
                    _turn(1, False),
                    _turn(2, False),
                    _turn(3, False),
                    _turn(4, False),
                    _turn(5, False),
                ]
            }
        ]

        diagnostics = paper_reliability_diagnostics(conversations, bootstrap_samples=10)

        self.assertIsNone(diagnostics["paper_metrics"]["apr"])
        self.assertIsNone(diagnostics["bootstrap"]["ci"]["apr"])

    def test_diagnostics_require_countability_for_every_slot(self) -> None:
        breakdown = [
            _turn(1, False),
            _turn(2, False),
            _turn(3, False),
            _turn(4, False),
            _turn(5, False),
        ]
        del breakdown[3]["generation_response_received"]

        with self.assertRaisesRegex(ValueError, "countable TP"):
            paper_reliability_diagnostics([{"turns_breakdown": breakdown}])

    def test_countable_tp_keeps_dependency_blocked_requested_slots(self) -> None:
        breakdown = []
        for turn in range(1, 6):
            slot = _turn(turn, turn == 1, turn == 1)
            slot["failure_reason"] = "blocked"
            slot["failure_bucket"] = "blocked"
            breakdown.append(slot)

        diagnostics = paper_reliability_diagnostics(
            [{"turns_breakdown": breakdown}],
            bootstrap_samples=10,
        )

        self.assertEqual(
            diagnostics["countable_turns"],
            {
                "requested_turns": 5,
                "passed_turns": 1,
                "excluded_provider_no_response_turns": 0,
                "turn_pass_rate": 0.2,
            },
        )
        self.assertIsNone(diagnostics["paper_metrics"]["apr"])
        self.assertIsNone(diagnostics["bootstrap"]["ci"]["apr"])

    def test_diagnostics_reject_pass_without_a_generation_response(self) -> None:
        conversations = [
            {
                "turns_breakdown": [
                    _turn(1, True, False),
                    _turn(2, False),
                    _turn(3, False),
                    _turn(4, False),
                    _turn(5, False),
                ]
            }
        ]

        with self.assertRaisesRegex(ValueError, "cannot pass without a generation response"):
            paper_reliability_diagnostics(conversations)

    def test_diagnostics_reject_mismatched_turn_positions(self) -> None:
        conversations = [
            {"turns_breakdown": [_turn(turn, False) for turn in range(1, 6)]},
            {"turns_breakdown": [_turn(turn, False) for turn in range(2, 7)]},
        ]

        with self.assertRaisesRegex(ValueError, "turn positions"):
            paper_reliability_diagnostics(conversations)

    def test_diagnostics_validate_bootstrap_parameters(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive integer"):
            paper_reliability_diagnostics([], bootstrap_samples=0)
        with self.assertRaisesRegex(ValueError, "positive integer"):
            paper_reliability_diagnostics([], bootstrap_samples=True)
        with self.assertRaisesRegex(ValueError, "seed must be an integer"):
            paper_reliability_diagnostics([], seed=True)


if __name__ == "__main__":
    unittest.main()
