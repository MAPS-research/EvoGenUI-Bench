from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from runner.orchestration import cli, turn_stages
from runner.orchestration.experiment_planning import (
    experiment_model_summary_from_suite_summary,
)
from runner.orchestration.turn_bundle import create_turn_bundle, mark_stage_failed
from runner.tools.experiment_config import ComponentRuntimeConfig
from runtime.types import EvaluationResult, TaskDefinition


def _stage_summary(
    *,
    stage: str,
    turn: int,
    run_id: str = "pipeline-test",
    generation_failure_with_response: bool = False,
) -> dict[str, object]:
    result: dict[str, object] | None = None
    failure: dict[str, object] | None = None
    if stage == "generate":
        if generation_failure_with_response:
            failure = {
                "turn": turn,
                "failure_reason": "generate",
                "failure_bucket": "quality",
                "error": "Provider returned an unusable generation response: invalid JSON",
                "generation_response_received": True,
            }
        else:
            result = {
                "task_id": "task-a",
                "turn": turn,
                "generation_response_received": True,
                "token_usage": {},
            }
    elif stage == "execute":
        if turn == 2 and generation_failure_with_response:
            failure = {
                "turn": turn,
                "failure_reason": "blocked",
                "failure_bucket": "blocked",
                "error": "generation output missing",
            }
        else:
            result = {"task_id": "task-a", "turn": turn, "token_usage": {}}
    else:
        if turn == 2 and generation_failure_with_response:
            failure = {
                "turn": turn,
                "failure_reason": "blocked",
                "failure_bucket": "blocked",
                "error": "execution snapshot missing",
            }
        else:
            result = {
                "task_id": "task-a",
                "turn": turn,
                "official_pass": True,
                "evaluator_pass": True,
                "evaluator_score": 5.0,
                "evaluator_summary": "pass",
                "dimensions": {
                    name: {"score": 5, "passed": True}
                    for name in ("Presentation", "Execution", "Alignment")
                },
                "failure_reason": None,
                "failure_bucket": None,
                "token_usage": {},
            }
    results = [result] if result is not None else []
    failures = [failure] if failure is not None else []
    return {
        "stage": stage,
        "run_id": run_id,
        "token_usage": {},
        "conversations": [
            {
                "task_id": "task-a",
                "results": results,
                "turn_failures": failures,
                "token_usage": {},
            }
        ],
    }


def _install_task_scope(monkeypatch, *, turns: list[int]) -> None:
    monkeypatch.setattr(cli, "load_task_ids", lambda: ["task-a"])
    monkeypatch.setattr(
        cli,
        "load_task_entries",
        lambda: [
            {
                "task_id": "task-a",
                "turns": [{"turn": turn} for turn in turns],
            }
        ],
    )


def test_comparison_model_summary_carries_paper_metrics(tmp_path: Path) -> None:
    plan = SimpleNamespace(
        stage="all",
        model_root=tmp_path / "models" / "model-a",
        turns=[1, 2, 3, 4, 5],
        model_id="model-a",
        provider="openai-compatible",
        model_name="paper-model",
        resolved_experiment_root=tmp_path,
    )
    paper_metrics = {
        "requested_turns": 10,
        "passed_turns": 7,
        "turn_pass_rate": 0.7,
        "five_turn_episode_count": 2,
        "five_turn_pass_count": 1,
        "tp_at_5": 0.5,
        "cpt": 3.0,
        "apr_num": 4,
        "apr_den": 6,
        "apr": 2 / 3,
        "reliability_diagnostics": {"bootstrap": {"unit": "task_episode"}},
    }
    suite_summary = {
        "task_count": 2,
        "completed_task_count": 2,
        "completed": True,
        "failure_buckets": {},
        "failure_reasons": {},
        **paper_metrics,
    }

    summary = experiment_model_summary_from_suite_summary(  # type: ignore[arg-type]
        plan=plan,
        suite_summary=suite_summary,
    )

    assert {field: summary[field] for field in paper_metrics} == paper_metrics


def test_evaluate_stage_summary_preserves_provider_no_response_for_countable_tp(
    tmp_path: Path,
) -> None:
    task = TaskDefinition(
        task_id="task-a",
        task_dir=tmp_path,
        public_task={},
        private_eval={},
        tools=[],
        resources=[],
        metadata={"turn": 1},
    )
    bundle = create_turn_bundle(
        task,
        output_root=tmp_path,
        run_id="pipeline-test",
        provider="openai-compatible",
    )
    mark_stage_failed(
        bundle,
        "generation",
        error="provider request failed before a response",
        extra={"response_received": False, "terminal_failure": False},
    )
    conversations = [
        {
            "task_id": "task-a",
            "turns_requested": [1, 2, 3, 4, 5],
            "turns_breakdown": [
                {
                    "turn": turn,
                    "reached": False,
                    "fully_evaluated": False,
                    "official_pass": False,
                    "evaluator_score": None,
                    "dim_scores": None,
                    "failure_reason": "blocked",
                    "failure_bucket": "blocked",
                }
                for turn in (1, 2, 3, 4, 5)
            ],
        }
    ]

    metrics = cli._attach_stage_evaluation_reliability(
        conversations,
        output_root=tmp_path,
        run_id="pipeline-test",
    )

    first_turn = conversations[0]["turns_breakdown"][0]  # type: ignore[index]
    assert first_turn["generation_response_received"] is False
    assert first_turn["failure_reason"] == "generation"
    assert first_turn["failure_bucket"] == "infra"
    assert metrics["reliability_diagnostics"]["countable_turns"] == {  # type: ignore[index]
        "requested_turns": 4,
        "passed_turns": 0,
        "excluded_provider_no_response_turns": 1,
        "turn_pass_rate": 0.0,
    }


def test_all_stage_pipeline_runs_each_turn_as_a_complete_wave(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _install_task_scope(monkeypatch, turns=[1, 2])
    calls: list[tuple[str, int]] = []
    run_ids: list[str] = []

    def _run_suite_stage(
        *, stage: str, turns: list[int], run_id: str, **_kwargs
    ) -> dict[str, object]:
        calls.append((stage, turns[0]))
        run_ids.append(run_id)
        return _stage_summary(stage=stage, turn=turns[0], run_id=run_id)

    monkeypatch.setattr(cli, "run_suite_stage", _run_suite_stage)

    summary = cli.run_suite_all_stages(
        provider="openai-compatible",
        turns=None,
        output_root=tmp_path,
        generation_workers=3,
        execution_workers=2,
        evaluation_workers=4,
    )

    assert calls == [
        ("generate", 1),
        ("execute", 1),
        ("evaluate", 1),
        ("generate", 2),
        ("execute", 2),
        ("evaluate", 2),
    ]
    assert len(set(run_ids)) == 1
    assert run_ids[0].startswith("pipeline-")
    assert summary["run_id"] == run_ids[0]
    conversation = summary["conversations"][0]
    assert conversation["official_pass"] is True
    assert [slot["turn"] for slot in conversation["turns_breakdown"]] == [1, 2]
    assert summary["turn_pass_rate"] == 1.0
    assert summary["tp_at_5"] is None


def test_full_pipeline_apr_counts_invalid_model_response_as_eligible_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _install_task_scope(monkeypatch, turns=[1, 2, 3, 4, 5])

    def _run_suite_stage(
        *, stage: str, turns: list[int], run_id: str, **_kwargs
    ) -> dict[str, object]:
        turn = turns[0]
        if turn == 2:
            return _stage_summary(
                stage=stage,
                turn=turn,
                run_id=run_id,
                generation_failure_with_response=True,
            )
        if turn > 2:
            if stage == "generate":
                return {
                    "stage": stage,
                    "run_id": run_id,
                    "token_usage": {},
                    "conversations": [
                        {
                            "task_id": "task-a",
                            "results": [],
                            "turn_failures": [
                                {
                                    "turn": turn,
                                    "failure_reason": "blocked",
                                    "failure_bucket": "blocked",
                                    "error": "prior snapshot missing",
                                    "generation_response_received": False,
                                }
                            ],
                            "token_usage": {},
                        }
                    ],
                }
            return {
                "stage": stage,
                "run_id": run_id,
                "token_usage": {},
                "conversations": [
                    {
                        "task_id": "task-a",
                        "results": [],
                        "turn_failures": [
                            {
                                "turn": turn,
                                "failure_reason": "blocked",
                                "failure_bucket": "blocked",
                                "error": "prior snapshot missing",
                            }
                        ],
                        "token_usage": {},
                    }
                ],
            }
        return _stage_summary(stage=stage, turn=turn, run_id=run_id)

    monkeypatch.setattr(cli, "run_suite_stage", _run_suite_stage)

    summary = cli.run_suite_all_stages(
        provider="openai-compatible",
        output_root=tmp_path,
        generation_workers=1,
        execution_workers=1,
        evaluation_workers=1,
    )

    assert summary["requested_turns"] == 5
    assert summary["passed_turns"] == 1
    assert summary["tp_at_5"] == 0.0
    assert summary["cpt"] == 1.0
    assert summary["apr_num"] == 0
    assert summary["apr_den"] == 1
    assert summary["apr"] == 0.0
    breakdown = summary["conversations"][0]["turns_breakdown"]
    assert breakdown[1]["generation_response_received"] is True
    assert breakdown[1]["fully_evaluated"] is False
    assert breakdown[2]["generation_response_received"] is False


def test_resume_reuses_only_a_matching_persisted_pipeline_lineage(tmp_path: Path) -> None:
    current_request = {
        "config_path": "/paper/config.yaml",
        "config_sha256": "config-a",
        "task_suite_sha256": "suite-a",
        "pipeline_code_sha256": "code-a",
        "dataset": {"tasks_path": "suite", "turns": [1, 2, 3, 4, 5]},
        "runtime": {"concurrency": {"generation": 1}},
        "model": {"id": "model-a", "provider": "openai-compatible", "model": "m"},
        "evaluation_protocol_version": "paper-v1",
    }
    (tmp_path / "request.json").write_text(
        json.dumps({**current_request, "pipeline_run_id": "pipeline-persisted"}),
        encoding="utf-8",
    )
    plan = SimpleNamespace(resume=True, model_root=tmp_path, model_id="model-a")

    assert (
        cli._pipeline_run_id_for_plan(plan, current_request=current_request)  # type: ignore[arg-type]
        == "pipeline-persisted"
    )

    changed_request = {**current_request, "model": {**current_request["model"], "model": "other"}}
    with pytest.raises(ValueError, match="field 'model' changed"):
        cli._pipeline_run_id_for_plan(  # type: ignore[arg-type]
            plan,
            current_request=changed_request,
        )

    changed_code_request = {**current_request, "pipeline_code_sha256": "code-b"}
    with pytest.raises(ValueError, match="field 'pipeline_code_sha256' changed"):
        cli._pipeline_run_id_for_plan(  # type: ignore[arg-type]
            plan,
            current_request=changed_code_request,
        )


def test_pipeline_code_hash_includes_root_runtime_configuration(tmp_path: Path) -> None:
    runner_dir = tmp_path / "runner"
    runner_dir.mkdir()
    (runner_dir / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    yarn_config = tmp_path / ".yarnrc.yml"
    yarn_config.write_text("nodeLinker: node-modules\n", encoding="utf-8")

    initial_hash = cli._pipeline_code_sha256(tmp_path)
    yarn_config.write_text("nodeLinker: pnp\n", encoding="utf-8")

    assert cli._pipeline_code_sha256(tmp_path) != initial_hash


def test_evaluation_keeps_actor_final_page_state_and_screenshot(
    monkeypatch,
    tmp_path: Path,
) -> None:
    task = TaskDefinition(
        task_id="task-a",
        task_dir=tmp_path,
        public_task={},
        private_eval={},
        tools=[],
        resources=[],
        metadata={"turn": 1},
    )
    bundle = create_turn_bundle(
        task,
        output_root=tmp_path,
        run_id="run-a",
        provider="model-a",
    )
    actor_result = {
        "final_screenshot": "/actor/final-full-page.png",
        "final_url": "http://actor-preview.local/edited",
    }
    current_snapshot = {
        "final_ui": {
            "screenshot": "/actor/final-full-page.png",
            "dom_tree": '<button aria-pressed="true">Enabled</button>',
        },
        "runtime_state": {"scenarios": {"default": {"enabled": True}}},
    }
    captured: dict[str, object] = {}

    def _evaluate_snapshot(
        received_task,
        received_actor_result,
        *,
        previous_turns,
        current_snapshot,
        evaluator_config,
    ) -> EvaluationResult:
        captured.update(
            {
                "task": received_task,
                "actor_result": received_actor_result,
                "previous_turns": previous_turns,
                "current_snapshot": current_snapshot,
                "evaluator_config": evaluator_config,
            }
        )
        return EvaluationResult(
            task_id="task-a",
            turn=1,
            generation_pass=True,
            build_pass=True,
            actor_pass=True,
            evaluator_pass=True,
            evaluator_score=5.0,
            evaluator_summary="pass",
            dimensions={},
            official_pass=True,
            failure_reason=None,
            details={"evaluator": {}},
        )

    monkeypatch.setattr(turn_stages, "evaluate_snapshot", _evaluate_snapshot)
    monkeypatch.setattr(
        turn_stages,
        "start_preview_server",
        lambda *_args, **_kwargs: pytest.fail("evaluation must not start a fresh preview"),
    )

    turn_stages.run_evaluation_stage(
        task,
        actor_result=actor_result,
        current_snapshot=current_snapshot,
        previous_turns=[],
        bundle=bundle,
        evaluator_config=SimpleNamespace(
            use_screenshot=True,
            refresh_full_screenshot=True,
        ),
    )

    assert captured["actor_result"] is actor_result
    assert captured["current_snapshot"] is current_snapshot
    assert captured["actor_result"]["final_screenshot"] == "/actor/final-full-page.png"
    assert (
        captured["current_snapshot"]["final_ui"]["dom_tree"]
        == '<button aria-pressed="true">Enabled</button>'
    )
    assert not (bundle.evaluation_dir / "screenshot_refresh.json").exists()


def test_screenshot_refresh_option_is_removed_from_runtime() -> None:
    assert "refresh_full_screenshot" not in ComponentRuntimeConfig.__dataclass_fields__


def test_wave_summary_rejects_mixed_stage_lineages() -> None:
    wave = {
        stage: _stage_summary(
            stage=stage,
            turn=1,
            run_id="pipeline-a" if stage != "evaluate" else "pipeline-b",
        )
        for stage in ("generate", "execute", "evaluate")
    }

    with pytest.raises(ValueError, match="mixed stage run lineages"):
        cli._paper_slot_from_wave(task_id="task-a", turn=1, wave=wave)


def test_actor_status_is_diagnostic_in_stage_reports() -> None:
    execution = cli._stage_result_to_evaluation_result(
        {
            "task_id": "task-a",
            "turn": 1,
            "build": {"success": True},
            "actor": {"status": "failed"},
        },
        "execute",
    )
    assert execution.actor_pass is False
    assert execution.official_pass is False
    assert execution.failure_reason is None

    evaluation = cli._stage_result_to_evaluation_result(
        {
            "task_id": "task-a",
            "turn": 1,
            "actor": {"status": "failed"},
            "official_pass": True,
            "evaluator_pass": True,
            "evaluator_score": 5,
            "dimensions": {},
        },
        "evaluate",
    )
    assert evaluation.actor_pass is False
    assert evaluation.official_pass is True


def test_generation_only_stage_rejects_multiple_dependent_turns(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _install_task_scope(monkeypatch, turns=[1, 2])

    with pytest.raises(ValueError, match="generation-only stage may select only one turn"):
        cli.run_suite_stage(
            stage="generate",
            provider="openai-compatible",
            output_root=tmp_path,
        )
