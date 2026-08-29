from __future__ import annotations

import unittest

from runner.evaluation import dimension_judge, failure_taxonomy, prompts
from runner.evaluation.eval_runner import _evaluation_outcome
from runner.orchestration.conversation_state import stage_conversation_summary
from runner.tools.evaluation_inputs import benchmark_request


class PaperEvaluationProtocolTests(unittest.TestCase):
    def test_dimension_protocol_contains_only_the_three_paper_dimensions(self) -> None:
        expected = ("Presentation", "Execution", "Alignment")

        self.assertEqual(dimension_judge.DIMENSIONS, expected)
        self.assertEqual(prompts.DIMENSIONS, expected)
        self.assertEqual(set(prompts.DIMENSION_RUBRICS), set(expected))
        self.assertEqual(set(prompts.DIMENSION_SCORE_ANCHORS), set(expected))
        self.assertEqual(set(prompts.DIMENSION_ALLOWED_FAILURE_TYPES), set(expected))

    def test_diagnostic_taxonomy_contains_only_the_six_paper_mechanisms(self) -> None:
        expected = (
            "information_architecture_failure",
            "domain_representation_failure",
            "requirement_decomposition_failure",
            "affordance_binding_failure",
            "derived_state_propagation_failure",
            "external_state_grounding_failure",
        )

        self.assertEqual(failure_taxonomy.CAPABILITY_FAILURE_VALUES, expected)
        self.assertEqual(
            tuple(
                failure_taxonomy.FAILURE_TAXONOMY_SCHEMA["properties"]["capability_failure"]["enum"]
            ),
            expected,
        )
        prompt = prompts.failure_taxonomy_system_prompt()
        for removed_label in (
            "state_machine_construction_failure",
            "closed_loop_feedback_failure",
            "cross_turn_preservation_failure",
            "response_artifact_calibration_failure",
        ):
            self.assertNotIn(removed_label, prompt)
        self.assertNotIn("not_applicable", prompt)
        self.assertIn("attached image is the actor's final full-page screenshot", prompt)
        self.assertIn("do not invent screenshot evidence", prompt)

    def test_every_executed_nonpass_keeps_one_of_the_six_mechanism_labels(self) -> None:
        taxonomy = failure_taxonomy.normalize_failure_taxonomy(
            {
                "attribution": "actor_execution_gap",
                "count_as_model_failure": False,
                "confidence": 0.6,
                "capability_failure": "information_architecture_failure",
                "secondary_capability_failures": [],
                "rationale": "The visible hierarchy is the best-supported mechanism.",
                "code_evidence": [],
                "actor_evidence": [],
                "infra_evidence": [],
                "capability_evidence": [],
            },
            token_usage={},
        )

        self.assertFalse(taxonomy["count_as_model_failure"])
        self.assertEqual(
            taxonomy["capability_failure"],
            "information_architecture_failure",
        )

    def test_stage_summary_has_no_conversation_level_judge_state(self) -> None:
        results = [
            {
                "turn": turn,
                "official_pass": True,
                "evaluator_score": 4.0,
                "dimensions": {
                    name: {"score": 4, "passed": True}
                    for name in ("Presentation", "Execution", "Alignment")
                },
            }
            for turn in (1, 2)
        ]

        summary = stage_conversation_summary(
            "task",
            stage="evaluate",
            provider="model",
            turns_requested=[1, 2],
            results=results,
            turns_skipped=[],
            turn_failures=[],
        )

        self.assertTrue(summary["official_pass"])
        self.assertTrue(summary["strict_pass"])
        self.assertNotIn("conversation_consistency", summary)
        self.assertNotIn("consistency_pass", summary)
        self.assertNotIn("consistency_score", summary)
        for legacy_metric in (
            "depth",
            "quality",
            "quality_completed",
            "weighted_score",
        ):
            self.assertNotIn(legacy_metric, summary)

    def test_actor_reported_status_is_advisory_to_official_pass(self) -> None:
        official_pass, failure_reason, failure_bucket = _evaluation_outcome({"passed": True})

        self.assertTrue(official_pass)
        self.assertIsNone(failure_reason)
        self.assertIsNone(failure_bucket)

    def test_failed_dimensions_keep_the_dimension_judge_outcome(self) -> None:
        official_pass, failure_reason, failure_bucket = _evaluation_outcome({"passed": False})

        self.assertFalse(official_pass)
        self.assertEqual(failure_reason, "evaluator")
        self.assertEqual(failure_bucket, "quality")

    def test_benchmark_request_is_preserved_verbatim(self) -> None:
        request = (
            "Robustness: show missing data, edge cases, and unavailable actions.  Keep spacing."
        )

        self.assertEqual(benchmark_request(request), request)


if __name__ == "__main__":
    unittest.main()
