from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from runner.evaluation import failure_taxonomy, failure_taxonomy_posthoc


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _taxonomy_response() -> SimpleNamespace:
    return SimpleNamespace(
        parsed_json={
            "attribution": "model_ui_failure",
            "count_as_model_failure": True,
            "confidence": 0.9,
            "capability_failure": "information_architecture_failure",
            "secondary_capability_failures": [],
            "rationale": "The screenshot shows an unreadable information hierarchy.",
            "code_evidence": [],
            "actor_evidence": [],
            "infra_evidence": [],
            "capability_evidence": ["The final rendered view is visually unreadable."],
        },
        token_usage={},
    )


def _write_bundle(
    model_root: Path,
    *,
    task_id: str,
    run_id: str,
    response_received: bool,
    generation_status: str,
    execution_status: str = "pending",
    evaluation_status: str = "pending",
) -> Path:
    root = model_root / "runs" / task_id / "turn-1" / run_id
    _write_json(
        root / "manifest.json",
        {
            "task_id": task_id,
            "turn": 1,
            "run_id": run_id,
            "stages": {
                "generation": {
                    "status": generation_status,
                    "response_received": response_received,
                    "terminal_failure": generation_status == "failed" and response_received,
                    "error": "invalid model response" if generation_status == "failed" else None,
                },
                "execution": {
                    "status": execution_status,
                    "error": "vite build failed" if execution_status == "failed" else None,
                },
                "evaluation": {"status": evaluation_status},
            },
        },
    )
    return root


def test_current_lineage_classifies_every_executed_nonpass(tmp_path: Path) -> None:
    model_root = tmp_path / "models" / "tested-model"
    run_id = "pipeline-current"
    _write_json(model_root / "request.json", {"pipeline_run_id": run_id})

    passing = _write_bundle(
        model_root,
        task_id="a-pass",
        run_id=run_id,
        response_received=True,
        generation_status="completed",
        execution_status="completed",
        evaluation_status="completed",
    )
    _write_json(
        passing / "evaluation" / "report.json",
        {
            "task_id": "a-pass",
            "turn": 1,
            "official_pass": True,
            "dimensions": {"Presentation": {"score": 4}},
            "details": {},
        },
    )

    invalid = _write_bundle(
        model_root,
        task_id="b-invalid-response",
        run_id=run_id,
        response_received=True,
        generation_status="failed",
    )
    _write_json(
        invalid / "generation" / "request.json",
        {"current_user_request": "build the requested interface"},
    )
    _write_json(
        invalid / "generation" / "raw_response.json",
        {
            "content_text": "not-json-" * 2000,
            "raw_response": {"output": "malformed-" * 2000},
        },
    )

    build_failure = _write_bundle(
        model_root,
        task_id="c-build-failure",
        run_id=run_id,
        response_received=True,
        generation_status="completed",
        execution_status="failed",
    )
    _write_json(
        build_failure / "generation" / "request.json",
        {"current_user_request": "build a working dashboard"},
    )
    _write_json(
        build_failure / "generation" / "output.json",
        {
            "assistant_text": "implemented",
            "files": {"src/App.tsx": "const broken = " + "x" * 20000},
        },
    )
    _write_json(
        build_failure / "execution" / "build.json",
        {
            "success": False,
            "stdout": "",
            "stderr": "TypeScript compilation failed",
            "errors": ["src/App.tsx: syntax error"],
        },
    )

    _write_bundle(
        model_root,
        task_id="d-provider-no-response",
        run_id=run_id,
        response_received=False,
        generation_status="failed",
    )
    _write_bundle(
        model_root,
        task_id="e-dependency-blocked",
        run_id=run_id,
        response_received=False,
        generation_status="pending",
    )
    _write_bundle(
        model_root,
        task_id="f-old-lineage",
        run_id="pipeline-old",
        response_received=True,
        generation_status="failed",
    )

    assert failure_taxonomy_posthoc._current_pipeline_run_id(model_root) == run_id
    candidates = failure_taxonomy_posthoc._pipeline_candidates(
        model_root,
        pipeline_run_id=run_id,
    )
    assert [candidate.task_id for candidate in candidates] == [
        "a-pass",
        "b-invalid-response",
        "c-build-failure",
    ]

    reports = {
        candidate.task_id: failure_taxonomy_posthoc._report_for_candidate(candidate)
        for candidate in candidates
    }
    assert failure_taxonomy_posthoc._should_classify(reports["a-pass"]) is False
    assert failure_taxonomy_posthoc._should_classify(reports["b-invalid-response"]) is True
    assert failure_taxonomy_posthoc._should_classify(reports["c-build-failure"]) is True
    assert reports["b-invalid-response"]["failure_reason"] == "generation"
    assert reports["c-build-failure"]["failure_reason"] == "build"

    invalid_evidence = failure_taxonomy.build_failure_taxonomy_bundle_evidence(invalid)
    invalid_payload = failure_taxonomy.build_failure_taxonomy_payload_from_report(
        reports["b-invalid-response"],
        previous_reports=[],
        bundle_evidence=invalid_evidence,
    )
    assert invalid_payload["task"]["current_user_request"] == "build the requested interface"
    raw_excerpt = invalid_payload["pipeline_evidence"]["generation"]["raw_response"]
    assert "...[truncated]" in raw_excerpt["content_text"]
    assert "...[truncated]" in raw_excerpt["raw_response_json"]

    build_evidence = failure_taxonomy.build_failure_taxonomy_bundle_evidence(build_failure)
    build_payload = failure_taxonomy.build_failure_taxonomy_payload_from_report(
        reports["c-build-failure"],
        previous_reports=[],
        bundle_evidence=build_evidence,
    )
    assert build_payload["pipeline_evidence"]["execution"]["build"]["success"] is False
    source_excerpt = build_payload["pipeline_evidence"]["generation"]["output"]["files"]
    assert "...[truncated]" in source_excerpt["src/App.tsx"]


def test_resume_key_is_stable_for_current_pipeline_slot(tmp_path: Path) -> None:
    model_root = tmp_path / "model"
    run_id = "pipeline-current"
    _write_bundle(
        model_root,
        task_id="task",
        run_id=run_id,
        response_received=True,
        generation_status="failed",
    )

    first = failure_taxonomy_posthoc._pipeline_candidates(
        model_root,
        pipeline_run_id=run_id,
    )[0]
    second = failure_taxonomy_posthoc._pipeline_candidates(
        model_root,
        pipeline_run_id=run_id,
    )[0]
    assert first.resume_key == second.resume_key

    jsonl = tmp_path / "taxonomy.jsonl"
    jsonl.write_text(
        json.dumps(
            {
                "resume_key": first.resume_key,
                "taxonomy": {"status": "completed"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert failure_taxonomy_posthoc._completed_resume_keys(jsonl) == {first.resume_key}


def test_unfinished_executed_bundle_is_not_silently_classified(tmp_path: Path) -> None:
    model_root = tmp_path / "model"
    run_id = "pipeline-current"
    _write_bundle(
        model_root,
        task_id="unfinished-task",
        run_id=run_id,
        response_received=True,
        generation_status="completed",
    )

    candidate = failure_taxonomy_posthoc._pipeline_candidates(
        model_root,
        pipeline_run_id=run_id,
    )[0]

    with pytest.raises(ValueError, match="Complete the pipeline"):
        failure_taxonomy_posthoc._report_for_candidate(candidate)


def test_taxonomy_judge_includes_readable_final_screenshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screenshot = tmp_path / "final.png"
    screenshot.write_bytes(b"\x89PNG\r\n\x1a\nimage-bytes")
    captured: dict[str, object] = {}

    def _call_llm(request):
        captured["request"] = request
        return _taxonomy_response()

    monkeypatch.setattr(failure_taxonomy, "call_llm", _call_llm)
    result = failure_taxonomy.run_failure_taxonomy_judge(
        {"task": {"task_id": "task-a", "turn": 1}},
        runtime_config=SimpleNamespace(
            provider_config=SimpleNamespace(),
            use_screenshot=True,
        ),
        screenshot_path=screenshot,
    )

    request = captured["request"]
    assert [item.type for item in request.inputs] == ["text", "image"]
    assert request.inputs[1].image_bytes == screenshot.read_bytes()
    assert request.inputs[1].mime_type == "image/png"
    text_payload = json.loads(request.inputs[0].text)
    assert text_payload["screenshot_input"] == {
        "requested": True,
        "status": "included",
        "path": str(screenshot),
        "mime_type": "image/png",
        "byte_count": len(screenshot.read_bytes()),
    }
    assert result["status"] == "completed"


def test_taxonomy_missing_screenshot_is_text_only_and_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle_root = tmp_path / "bundle"
    _write_json(
        bundle_root / "execution" / "snapshot.json",
        {"final_ui": {"screenshot": "execution/artifacts/missing.png"}},
    )
    screenshot_path, screenshot_evidence = failure_taxonomy.resolve_failure_taxonomy_screenshot(
        bundle_root
    )
    assert screenshot_path is None
    assert screenshot_evidence["status"] == "missing_or_unreadable"
    assert screenshot_evidence["candidates"][0]["declared_path"] == (
        "execution/artifacts/missing.png"
    )

    captured: dict[str, object] = {}

    def _call_llm(request):
        captured["request"] = request
        return _taxonomy_response()

    monkeypatch.setattr(failure_taxonomy, "call_llm", _call_llm)
    failure_taxonomy.run_failure_taxonomy_judge(
        {
            "task": {"task_id": "task-a", "turn": 1},
            "pipeline_evidence": {
                "execution": {"final_screenshot": screenshot_evidence},
            },
        },
        runtime_config=SimpleNamespace(
            provider_config=SimpleNamespace(),
            use_screenshot=True,
        ),
        screenshot_path=screenshot_path,
    )

    request = captured["request"]
    assert [item.type for item in request.inputs] == ["text"]
    text_payload = json.loads(request.inputs[0].text)
    assert text_payload["screenshot_input"]["status"] == "unavailable"
    assert (
        text_payload["pipeline_evidence"]["execution"]["final_screenshot"]["status"]
        == "missing_or_unreadable"
    )
