from __future__ import annotations

import json
from pathlib import Path

import pytest
from werkzeug.exceptions import NotFound

from web.app import (
    PLAYGROUND_SESSIONS,
    _find_report,
    app,
    collect_paper_reliability_by_model,
    collect_task_catalog,
    find_latest_run_dir,
)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_web_reads_requested_slot_paper_metrics_instead_of_loaded_report_rate(
    tmp_path: Path,
) -> None:
    metrics = {
        "requested_turns": 250,
        "passed_turns": 112,
        "turn_pass_rate": 0.448,
        "five_turn_episode_count": 50,
        "five_turn_pass_count": 2,
        "tp_at_5": 0.04,
        "cpt": 0.94,
        "apr_num": 43,
        "apr_den": 85,
        "apr": 43 / 85,
    }
    _write_json(
        tmp_path / "models" / "gpt-5.5" / "reports" / "summary.json",
        metrics,
    )

    result = collect_paper_reliability_by_model(
        tmp_path,
        model_order=["gpt-5.5"],
    )

    assert result == {"gpt-5.5": metrics}


def test_web_does_not_present_incomplete_summary_without_paper_denominators(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "models" / "incomplete" / "reports" / "summary.json",
        {"pass_rate": 0.9, "mean_quality": 4.8},
    )

    result = collect_paper_reliability_by_model(
        tmp_path,
        model_order=["incomplete"],
    )

    assert result == {}


def test_web_renders_public_pages() -> None:
    client = app.test_client()

    assert client.get("/results").status_code == 200
    assert client.get("/tasks").status_code == 200
    assert client.get("/tools").status_code == 200
    assert client.get("/playground").status_code == 200


def test_artifact_paths_cannot_escape_the_selected_model(tmp_path: Path) -> None:
    (tmp_path / "models" / "model-a" / "runs").mkdir(parents=True)

    with app.test_request_context():
        with pytest.raises(NotFound):
            find_latest_run_dir(tmp_path, "model-a", "../../outside", 1)
        with pytest.raises(NotFound):
            _find_report(tmp_path, "model-a", "../../outside", 1)


def test_playground_session_serves_artifact_with_isolated_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks_root = tmp_path / "tasks"
    _write_json(
        tasks_root / "example.json",
        {
            "task_id": "Example",
            "turns": [{"turn": 1, "query": "Build an example"}],
        },
    )
    experiment_dir = tmp_path / "experiments" / "example-experiment"
    _write_json(
        experiment_dir / "request.json",
        {"dataset": {"tasks_path": str(tasks_root)}},
    )
    dist_dir = (
        experiment_dir
        / "models"
        / "model-a"
        / "runs"
        / "Example"
        / "turn-1"
        / "run-001"
        / "workspace"
        / "dist"
    )
    dist_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text(
        '<html><body><script src="/__genui_runtime.js"></script></body></html>',
        encoding="utf-8",
    )
    monkeypatch.setenv("GENUI_EXPERIMENTS_ROOT", str(tmp_path / "experiments"))
    PLAYGROUND_SESSIONS.clear()
    client = app.test_client()

    response = client.post(
        "/api/playground/session",
        json={
            "experiment": "example-experiment",
            "model": "model-a",
            "task_id": "Example",
            "turn": 1,
            "state_source": "empty",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["runtime_state"] == {"scenarios": {}}
    assert client.get(payload["entry_url"]).status_code == 200
    runtime_response = client.get(f"/playground/session/{payload['session_id']}/__genui_runtime.js")
    assert runtime_response.status_code == 200
    assert f"/api/playground/session/{payload['session_id']}/tool-call" in runtime_response.text


def test_raw_report_and_run_file_endpoints_do_not_expose_private_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    experiment_dir = tmp_path / "experiments" / "example-experiment"
    report_path = experiment_dir / "models" / "model-a" / "reports" / "Example" / "turn-1.json"
    _write_json(
        report_path,
        {
            "task_id": "Example",
            "turn": 1,
            "official_pass": True,
            "dimensions": {},
            "details": {
                "validation_contract": {"secret": "not public"},
                "actor": {"verification_checks": [{"expectation": "not public"}]},
            },
        },
    )
    run_dir = experiment_dir / "models" / "model-a" / "runs" / "Example" / "turn-1" / "run-001"
    private_path = run_dir / "execution" / "artifacts" / "private_eval.json"
    _write_json(private_path, {"secret": "not public"})
    screenshot_path = run_dir / "execution" / "artifacts" / "final.png"
    screenshot_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setenv("GENUI_EXPERIMENTS_ROOT", str(tmp_path / "experiments"))
    client = app.test_client()
    query = "experiment=example-experiment&model=model-a&task_id=Example&turn=1"

    response = client.get(f"/api/raw-json?{query}&target=report")
    assert response.status_code == 200
    assert "not public" not in response.get_json()["raw_json"]
    assert client.get(f"/api/raw-json?{query}&target=actor").status_code == 400
    assert (
        client.get(
            "/run-file/example-experiment/model-a/Example/1/execution/artifacts/private_eval.json"
        ).status_code
        == 404
    )
    assert (
        client.get(
            "/run-file/example-experiment/model-a/Example/1/execution/artifacts/final.png"
        ).status_code
        == 200
    )


def test_task_catalog_omits_private_evaluation_fields(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "example.json",
        {
            "task_id": "Example",
            "domain": "Productivity",
            "turns": [{"turn": 1, "query": "Build a timer"}],
            "actor_hints": {"default": {"support_boundary": "secret"}},
            "scenario_fixtures": {"default": {"initial_state": {"secret": 1}}},
            "tools": [
                {
                    "name": "save_timer",
                    "description": "Save a timer",
                    "mode": "write",
                    "mock_contract": {"fixture_id": "private_fixture"},
                }
            ],
        },
    )

    catalog = collect_task_catalog(tmp_path)

    assert catalog["tasks"] == [
        {
            "task_id": "Example",
            "domain": "Productivity",
            "suite": "",
            "difficulty": "",
            "core_interaction": [],
            "turn_count": 1,
            "turns": [{"turn": 1, "prompt": "Build a timer", "tool_names": []}],
            "tools": [{"name": "save_timer", "description": "Save a timer", "mode": "write"}],
        }
    ]
