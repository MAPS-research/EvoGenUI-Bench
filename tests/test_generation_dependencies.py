from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from runner.generation import generator as generation_module
from runner.generation.generator import run_generate_turn
from runner.generation.prompt_payload import build_prompt_payload
from runner.orchestration import cli, turn_stages
from runner.orchestration.conversation_state import load_saved_turn_snapshot
from runner.orchestration.turn_bundle import (
    create_turn_bundle,
    load_turn_bundle,
    mark_stage_completed,
    mark_stage_failed,
    mark_stage_running,
    write_stage_payload,
)
from runtime.types import TaskDefinition, ToolDefinition


def _task(*, turn: int, tool_grounded: bool = False) -> TaskDefinition:
    tools = (
        [
            ToolDefinition(
                name="read_state",
                description="Read state.",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                mode="read",
                backend="python",
            )
        ]
        if tool_grounded
        else []
    )
    return TaskDefinition(
        task_id="task",
        task_dir=Path("."),
        public_task={"title": "Task", "user_prompt": f"turn {turn}"},
        private_eval={},
        tools=tools,
        resources=[],
        metadata={"turn": turn, "total_turns": 3},
    )


def _previous_context(*, turn: int = 1) -> dict[str, object]:
    return {
        "turn": turn,
        "user_request": f"turn {turn}",
        "assistant_text": f"assistant {turn}",
        "generated_files": {"src/App.tsx": "export default function App() { return null; }"},
        "final_ui": {"text": f"ui {turn}", "elements": []},
        "runtime_state": {"scenarios": {"default": {"state": {"turn": turn}}}},
    }


def test_later_turn_prompt_requires_every_prior_turn_context() -> None:
    with pytest.raises(FileNotFoundError, match=r"turn\(s\) 1"):
        build_prompt_payload(_task(turn=2))

    with pytest.raises(FileNotFoundError, match=r"turn\(s\) 1"):
        build_prompt_payload(_task(turn=3), previous_turns=[_previous_context(turn=2)])


def test_later_turn_prompt_requires_prior_generated_source() -> None:
    context = _previous_context()
    context["generated_files"] = {}

    with pytest.raises(FileNotFoundError, match="Missing prior generated source"):
        build_prompt_payload(_task(turn=2), previous_turns=[context])


def test_later_turn_prompt_requires_prior_execution_snapshot_context() -> None:
    context = _previous_context()
    context.pop("final_ui")

    with pytest.raises(FileNotFoundError, match="Missing prior execution snapshot context"):
        build_prompt_payload(_task(turn=2), previous_turns=[context])


def test_tool_grounded_later_turn_requires_runtime_state() -> None:
    context = _previous_context()
    context.pop("runtime_state")

    with pytest.raises(FileNotFoundError, match="Missing prior runtime state"):
        build_prompt_payload(
            _task(turn=2, tool_grounded=True),
            previous_turns=[context],
        )


def test_valid_previous_context_uses_immediate_source_without_leaking_runtime_state() -> None:
    payload = build_prompt_payload(
        _task(turn=2, tool_grounded=True),
        previous_turns=[_previous_context()],
    )

    assert payload["previous_turn_source"] == {
        "turn": 1,
        "files": {"src/App.tsx": "export default function App() { return null; }"},
    }
    assert payload["previous_turns"] == [
        {
            "turn": 1,
            "user_request": "turn 1",
            "assistant_text": "assistant 1",
            "final_ui_text": "ui 1",
        }
    ]
    assert "runtime_state" not in str(payload)


def test_run_generate_turn_loads_source_and_snapshot_from_completed_previous_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    previous_bundle = create_turn_bundle(
        _task(turn=1),
        output_root=tmp_path,
        run_id="pipeline-a",
        provider="openai-compatible",
    )
    generation_output = write_stage_payload(
        previous_bundle,
        "generation",
        "output.json",
        {
            "files": {"src/App.tsx": "export default function App() { return <main>one</main>; }"},
            "assistant_text": "Built turn one.",
        },
    )
    mark_stage_completed(previous_bundle, "generation", files={"output": generation_output})
    snapshot = write_stage_payload(
        previous_bundle,
        "execution",
        "snapshot.json",
        {
            "turn": 1,
            "user_request": "turn one request",
            "final_ui": {"text": "turn one ui", "elements": []},
            "runtime_state": {"scenarios": {}},
        },
    )
    mark_stage_completed(previous_bundle, "execution", files={"snapshot": snapshot})

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        generation_module,
        "load_task",
        lambda task_id, *, turn=1: _task(turn=turn),
    )

    def _fake_run_generation_stage(_task, *, payload, **_kwargs):
        captured["payload"] = payload
        return SimpleNamespace()

    monkeypatch.setattr(generation_module, "run_generation_stage", _fake_run_generation_stage)
    monkeypatch.setattr(
        generation_module,
        "stage_payload_from_bundle",
        lambda bundle, _stage: {"run_id": bundle.run_id},
    )

    result = run_generate_turn(
        "task",
        turn=2,
        provider="openai-compatible",
        output_root=tmp_path,
        run_id="pipeline-a",
    )

    assert result == {"run_id": "pipeline-a"}
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["previous_turn_source"] == {
        "turn": 1,
        "files": {"src/App.tsx": "export default function App() { return <main>one</main>; }"},
    }
    assert payload["previous_turns"][0]["final_ui_text"] == "turn one ui"


def test_run_generate_turn_blocks_when_previous_execution_snapshot_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    previous_bundle = create_turn_bundle(
        _task(turn=1),
        output_root=tmp_path,
        run_id="pipeline-a",
        provider="openai-compatible",
    )
    generation_output = write_stage_payload(
        previous_bundle,
        "generation",
        "output.json",
        {
            "files": {"src/App.tsx": "export default function App() { return <main>one</main>; }"},
            "assistant_text": "Built turn one.",
        },
    )
    mark_stage_completed(previous_bundle, "generation", files={"output": generation_output})
    monkeypatch.setattr(
        generation_module,
        "load_task",
        lambda task_id, *, turn=1: _task(turn=turn),
    )
    called = False

    def _unexpected_generation(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(generation_module, "run_generation_stage", _unexpected_generation)

    with pytest.raises(FileNotFoundError, match="stage 'execution' status 'completed'"):
        run_generate_turn(
            "task",
            turn=2,
            provider="openai-compatible",
            output_root=tmp_path,
            run_id="pipeline-a",
        )

    assert called is False


def test_bundle_lookup_does_not_fall_back_to_an_older_completed_lineage(
    tmp_path: Path,
) -> None:
    old_bundle = create_turn_bundle(
        _task(turn=1),
        output_root=tmp_path,
        run_id="20260828T100000Z-old",
        provider="openai-compatible",
    )
    mark_stage_completed(old_bundle, "generation")
    latest_bundle = create_turn_bundle(
        _task(turn=1),
        output_root=tmp_path,
        run_id="20260828T110000Z-latest",
        provider="openai-compatible",
    )
    mark_stage_failed(latest_bundle, "generation", error="request failed")

    with pytest.raises(FileNotFoundError, match="stage 'generation' status 'completed'"):
        load_turn_bundle(
            output_root=tmp_path,
            task_id="task",
            turn=1,
            required_stage="generation",
        )

    selected = load_turn_bundle(
        output_root=tmp_path,
        task_id="task",
        turn=1,
        run_id="20260828T100000Z-old",
        required_stage="generation",
    )
    assert selected.run_id == "20260828T100000Z-old"


def test_generation_manifest_preserves_response_eligibility_after_local_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bundle = create_turn_bundle(
        _task(turn=1),
        output_root=tmp_path,
        run_id="pipeline-a",
        provider="openai-compatible",
    )
    monkeypatch.setattr(
        turn_stages,
        "run_generation",
        lambda *_args, **_kwargs: (
            {
                "files": {"src/App.tsx": "export default function App() { return <main />; }"},
                "assistant_text": "done",
            },
            {},
        ),
    )

    def _local_failure(*_args, **_kwargs):
        raise OSError("local build infrastructure failed")

    monkeypatch.setattr(turn_stages, "_run_generation_preview_build", _local_failure)

    with pytest.raises(OSError, match="local build infrastructure failed"):
        turn_stages.run_generation_stage(
            _task(turn=1),
            payload={"task": "test"},
            provider="openai-compatible",
            bundle=bundle,
        )

    manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
    generation = manifest["stages"]["generation"]
    assert generation["status"] == "failed"
    assert generation["response_received"] is True
    assert generation["terminal_failure"] is True


def test_resume_freezes_an_interrupted_generation_after_response(tmp_path: Path) -> None:
    bundle = create_turn_bundle(
        _task(turn=1),
        output_root=tmp_path,
        run_id="pipeline-a",
        provider="openai-compatible",
    )
    mark_stage_running(
        bundle,
        "generation",
        extra={"response_received": True, "terminal_failure": False},
    )

    failure = cli._resume_terminal_stage_failure(
        "task",
        stage="generate",
        bundle=bundle,
    )

    assert failure is not None
    assert failure["reason"] == "terminal_failure"
    assert failure["generation_response_received"] is True
    assert failure["error"] == "Generation stopped after receiving a model response"


def test_report_with_missing_snapshot_does_not_fall_back_to_an_old_bundle(
    tmp_path: Path,
) -> None:
    old_bundle = create_turn_bundle(
        _task(turn=1),
        output_root=tmp_path,
        run_id="pipeline-old",
        provider="openai-compatible",
    )
    snapshot_path = write_stage_payload(
        old_bundle,
        "execution",
        "snapshot.json",
        {"turn": 1, "final_ui": {"text": "stale"}},
    )
    mark_stage_completed(old_bundle, "execution", files={"snapshot": snapshot_path})

    report_path = tmp_path / "reports" / "task" / "turn-1.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(
        json.dumps(
            {
                "task_id": "task",
                "turn": 1,
                "generation_pass": True,
                "build_pass": True,
                "actor_pass": False,
                "evaluator_pass": True,
                "evaluator_score": 5,
                "evaluator_summary": "pass",
                "dimensions": {},
                "official_pass": True,
                "failure_reason": None,
                "failure_bucket": None,
                "details": {"turn_bundle": "runs/task/turn-1/pipeline-missing"},
            }
        ),
        encoding="utf-8",
    )

    assert load_saved_turn_snapshot("task", turn=1, output_root=tmp_path) is None


def test_run_generate_turn_does_not_silently_drop_malformed_explicit_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        generation_module,
        "load_task",
        lambda task_id, *, turn=1: _task(turn=turn),
    )

    with pytest.raises(ValueError, match="only JSON objects"):
        run_generate_turn(
            "task",
            turn=2,
            provider="openai-compatible",
            output_root=tmp_path,
            previous_turns=[None],  # type: ignore[list-item]
        )
