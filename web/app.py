from __future__ import annotations

import copy
import json
import os
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Any, Iterator
from urllib.parse import quote

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

app = Flask(__name__)
PLAYGROUND_SESSIONS: dict[str, dict[str, Any]] = {}

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TURN_DIMENSION_ORDER = ("Presentation", "Execution", "Alignment")
PAPER_RELIABILITY_FIELDS = (
    "requested_turns",
    "passed_turns",
    "turn_pass_rate",
    "five_turn_episode_count",
    "five_turn_pass_count",
    "tp_at_5",
    "cpt",
    "apr_num",
    "apr_den",
    "apr",
)
MAX_CODE_FILES = 40
MAX_CODE_FILE_CHARS = 120_000
PUBLIC_IMAGE_SUFFIXES = {".gif", ".jpeg", ".jpg", ".png", ".webp"}
_TASK_SOURCE_LOCK = RLock()


def experiments_root() -> Path:
    configured = os.environ.get("GENUI_EXPERIMENTS_ROOT")
    return Path(configured).expanduser().resolve() if configured else PROJECT_ROOT / "experiments"


def configured_tasks_root() -> Path:
    configured = os.environ.get("GENUI_BENCH_TASKS_PATH")
    return (
        Path(configured).expanduser().resolve()
        if configured
        else PROJECT_ROOT / "bench" / "generated_tasks"
    )


def safe_read_json(path: Path) -> dict[str, Any] | list[Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def shorten_text(value: object, max_chars: int = 6_000) -> str:
    if not isinstance(value, str):
        return ""
    value = "\n".join(line.rstrip() for line in value.strip().splitlines())
    return value if len(value) <= max_chars else f"{value[:max_chars]}..."


def _safe_directory(root: Path, name: str, *, label: str) -> Path:
    if not name or name in {".", ".."}:
        abort(404, f"{label} not found")
    root = root.resolve()
    candidate = (root / name).resolve()
    if candidate.parent != root or not candidate.is_dir():
        abort(404, f"{label} not found: {name}")
    return candidate


def _safe_descendant(root: Path, *parts: str, label: str) -> Path:
    root = root.resolve()
    candidate = root.joinpath(*parts).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        abort(404, f"{label} not found")
    return candidate


def resolve_experiment_dir(experiment_id: str) -> Path:
    return _safe_directory(experiments_root(), experiment_id, label="Experiment")


def list_available_experiments() -> list[dict[str, str]]:
    root = experiments_root()
    if not root.is_dir():
        return []
    return [
        {"id": path.name, "name": path.name, "label": path.name}
        for path in sorted(root.iterdir())
        if path.is_dir() and (path / "models").is_dir()
    ]


def collect_model_order(base_dir: Path) -> list[str]:
    ordered: list[str] = []
    comparison = safe_read_json(base_dir / "comparison" / "summary.json")
    models = comparison.get("models") if isinstance(comparison, dict) else None
    if isinstance(models, list):
        for item in models:
            if not isinstance(item, dict):
                continue
            model_id = item.get("id") or item.get("model")
            if isinstance(model_id, str) and model_id and model_id not in ordered:
                ordered.append(model_id)

    models_dir = base_dir / "models"
    if models_dir.is_dir():
        for model_dir in sorted(models_dir.iterdir()):
            if model_dir.is_dir() and model_dir.name not in ordered:
                ordered.append(model_dir.name)
    return ordered


def _paper_reliability_payload(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if not isinstance(value.get("requested_turns"), int):
        return None
    if not isinstance(value.get("passed_turns"), int):
        return None
    return {field: value.get(field) for field in PAPER_RELIABILITY_FIELDS}


def collect_paper_reliability_by_model(
    base_dir: Path,
    *,
    model_order: list[str],
) -> dict[str, dict[str, Any]]:
    collected: dict[str, dict[str, Any]] = {}
    comparison = safe_read_json(base_dir / "comparison" / "summary.json")
    models = comparison.get("models") if isinstance(comparison, dict) else None
    if isinstance(models, list):
        for model in models:
            if not isinstance(model, dict):
                continue
            model_id = model.get("id") or model.get("model")
            metrics = _paper_reliability_payload(model)
            if isinstance(model_id, str) and metrics is not None:
                collected[model_id] = metrics

    for model_id in model_order:
        if model_id in collected:
            continue
        reports_dir = base_dir / "models" / model_id / "reports"
        for summary_path in (
            reports_dir / "summary.json",
            reports_dir / "stages" / "evaluate-suite.json",
        ):
            metrics = _paper_reliability_payload(safe_read_json(summary_path))
            if metrics is not None:
                collected[model_id] = metrics
                break
    return collected


def _task_payloads(tasks_root: Path) -> Iterator[dict[str, Any]]:
    if not tasks_root.is_dir():
        return
    for task_file in sorted(tasks_root.glob("*.json")):
        payload = safe_read_json(task_file)
        if not isinstance(payload, dict):
            continue
        wrapped = payload.get("tasks")
        if isinstance(wrapped, list):
            for task in wrapped:
                if isinstance(task, dict):
                    yield task
        else:
            yield payload


def _public_turn(turn: object, fallback_index: int) -> dict[str, Any] | None:
    if not isinstance(turn, dict):
        return None
    turn_index = turn.get("turn_index", turn.get("turn", fallback_index))
    if not isinstance(turn_index, int):
        turn_index = fallback_index
    prompt = turn.get("query") or turn.get("prompt") or ""
    tools = turn.get("tools") if isinstance(turn.get("tools"), list) else []
    return {
        "turn": turn_index,
        "prompt": shorten_text(prompt),
        "tool_names": [
            tool.get("name")
            for tool in tools
            if isinstance(tool, dict) and isinstance(tool.get("name"), str)
        ],
    }


def _public_task(task: dict[str, Any]) -> dict[str, Any] | None:
    task_id = task.get("task_id") or task.get("episode_id") or task.get("title")
    if not isinstance(task_id, str) or not task_id:
        return None
    turns = task.get("turns") if isinstance(task.get("turns"), list) else []
    public_turns = [
        public
        for index, turn in enumerate(turns, start=1)
        if (public := _public_turn(turn, index)) is not None
    ]
    tools = task.get("tools") if isinstance(task.get("tools"), list) else []
    public_tools = []
    for tool in tools:
        if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
            continue
        public_tools.append(
            {
                "name": tool["name"],
                "description": shorten_text(tool.get("description"), 800),
                "mode": tool.get("mode") if tool.get("mode") in {"read", "write"} else "",
            }
        )
    return {
        "task_id": task_id,
        "domain": task.get("domain") if isinstance(task.get("domain"), str) else "",
        "suite": task.get("suite") if isinstance(task.get("suite"), str) else "",
        "difficulty": task.get("difficulty") if isinstance(task.get("difficulty"), str) else "",
        "core_interaction": (
            task.get("core_interaction") if isinstance(task.get("core_interaction"), list) else []
        ),
        "turn_count": len(public_turns),
        "turns": public_turns,
        "tools": public_tools,
    }


def collect_task_catalog(tasks_root: Path | None = None) -> dict[str, Any]:
    root = tasks_root or configured_tasks_root()
    tasks = [public for task in _task_payloads(root) if (public := _public_task(task)) is not None]
    tasks.sort(key=lambda task: task["task_id"])
    return {"task_count": len(tasks), "tasks": tasks}


def _experiment_tasks_root(base_dir: Path) -> Path:
    request_payload = safe_read_json(base_dir / "request.json")
    dataset = request_payload.get("dataset") if isinstance(request_payload, dict) else None
    raw_path = dataset.get("tasks_path") if isinstance(dataset, dict) else None
    if not isinstance(raw_path, str) or not raw_path.strip():
        return configured_tasks_root()
    path = Path(raw_path).expanduser()
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _task_metadata_by_id(base_dir: Path) -> dict[str, dict[str, Any]]:
    return {
        task["task_id"]: task
        for task in collect_task_catalog(_experiment_tasks_root(base_dir))["tasks"]
    }


def normalize_dimensions(dimensions: object) -> list[dict[str, Any]]:
    if not isinstance(dimensions, dict):
        return []
    result = []
    for name in TURN_DIMENSION_ORDER:
        value = dimensions.get(name)
        if not isinstance(value, dict):
            continue
        result.append(
            {
                "name": name,
                "score": value.get("score"),
                "passed": value.get("passed"),
                "summary": shorten_text(value.get("summary"), 1_500),
            }
        )
    return result


def normalize_token_usage(value: object) -> dict[str, dict[str, float | int]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, dict[str, float | int]] = {}
    for component, payload in value.items():
        if not isinstance(component, str) or not isinstance(payload, dict):
            continue
        result[component] = {
            key: number
            for key, number in payload.items()
            if isinstance(key, str)
            and isinstance(number, (int, float))
            and not isinstance(number, bool)
        }
    return result


def build_report_payload(report: dict[str, Any]) -> dict[str, Any]:
    details = report.get("details") if isinstance(report.get("details"), dict) else {}
    return {
        "evaluator_score": report.get("evaluator_score"),
        "evaluator_pass": report.get("evaluator_pass"),
        "official_pass": report.get("official_pass"),
        "generation_pass": report.get("generation_pass"),
        "build_pass": report.get("build_pass"),
        "actor_pass": report.get("actor_pass"),
        "evaluator_summary": shorten_text(report.get("evaluator_summary"), 3_000),
        "failure_bucket": report.get("failure_bucket"),
        "failure_reason": shorten_text(report.get("failure_reason"), 2_000),
        "dimensions": normalize_dimensions(report.get("dimensions")),
        "token_usage": normalize_token_usage(details.get("token_usage")),
    }


def public_report_artifact(report: dict[str, Any]) -> dict[str, Any]:
    normalized = build_report_payload(report)
    return {
        "task_id": report.get("task_id"),
        "turn": report.get("turn"),
        "model": report.get("model"),
        "evaluator_score": normalized["evaluator_score"],
        "evaluator_pass": normalized["evaluator_pass"],
        "official_pass": normalized["official_pass"],
        "generation_pass": normalized["generation_pass"],
        "build_pass": normalized["build_pass"],
        "actor_pass": normalized["actor_pass"],
        "evaluator_summary": normalized["evaluator_summary"],
        "failure_bucket": normalized["failure_bucket"],
        "failure_reason": normalized["failure_reason"],
        "dimensions": normalized["dimensions"],
        "token_usage": normalized["token_usage"],
    }


def _report_files(model_dir: Path) -> Iterator[Path]:
    reports_dir = model_dir / "reports"
    if not reports_dir.is_dir():
        return
    for task_dir in sorted(reports_dir.iterdir()):
        if task_dir.is_dir() and task_dir.name != "stages":
            yield from sorted(task_dir.glob("turn-*.json"))


def _report_identity(report: dict[str, Any]) -> tuple[str, int] | None:
    task_id = report.get("task_id")
    turn = report.get("turn")
    if not isinstance(task_id, str) or not isinstance(turn, int):
        return None
    return task_id, turn


def collect_task_turn_summary_data(base_dir: Path) -> tuple[list[str], list[dict[str, Any]]]:
    models = collect_model_order(base_dir)
    model_rank = {model: index for index, model in enumerate(models)}
    metadata = _task_metadata_by_id(base_dir)
    grouped: dict[str, dict[int, list[dict[str, Any]]]] = {}

    for model in models:
        model_dir = base_dir / "models" / model
        for report_path in _report_files(model_dir):
            report = safe_read_json(report_path)
            if not isinstance(report, dict):
                continue
            identity = _report_identity(report)
            if identity is None:
                continue
            task_id, turn = identity
            grouped.setdefault(task_id, {}).setdefault(turn, []).append(
                {"model": model, "report": build_report_payload(report)}
            )

    tasks = []
    for task_id, turns_by_number in sorted(grouped.items()):
        task_meta = metadata.get(task_id, {})
        turns = []
        scores = []
        for turn, items in sorted(turns_by_number.items()):
            items.sort(key=lambda item: model_rank.get(item["model"], len(model_rank)))
            turns.append({"turn": turn, "items": items})
            scores.extend(
                score
                for item in items
                if isinstance((score := item["report"].get("evaluator_score")), (int, float))
                and not isinstance(score, bool)
            )
        tasks.append(
            {
                "task_id": task_id,
                "domain": task_meta.get("domain", ""),
                "suite": task_meta.get("suite", ""),
                "core_interaction": task_meta.get("core_interaction", []),
                "best_score": max(scores) if scores else None,
                "avg_score": sum(scores) / len(scores) if scores else None,
                "turns": turns,
            }
        )
    return models, tasks


def find_latest_run_dir(base_dir: Path, model: str, task_id: str, turn: int) -> Path | None:
    model_dir = _safe_directory(base_dir / "models", model, label="Model")
    turn_dir = _safe_descendant(
        model_dir / "runs",
        task_id,
        f"turn-{turn}",
        label="Run",
    )
    if not turn_dir.is_dir():
        return None
    runs = sorted(path for path in turn_dir.iterdir() if path.is_dir())
    return runs[-1] if runs else None


def _generation_files(output: object) -> list[dict[str, Any]]:
    files = output.get("files") if isinstance(output, dict) else None
    if not isinstance(files, dict):
        return []
    result = []
    for raw_path, raw_content in sorted(files.items(), key=lambda item: str(item[0]))[
        :MAX_CODE_FILES
    ]:
        path = str(raw_path).removeprefix("workspace/")
        content = (
            raw_content
            if isinstance(raw_content, str)
            else json.dumps(raw_content, ensure_ascii=False, indent=2)
        )
        result.append(
            {
                "path": path,
                "content": content[:MAX_CODE_FILE_CHARS],
                "size_chars": len(content),
                "truncated": len(content) > MAX_CODE_FILE_CHARS,
            }
        )
    return result


def _conversation(
    report: dict[str, Any], generation_request: dict[str, Any]
) -> list[dict[str, Any]]:
    details = report.get("details") if isinstance(report.get("details"), dict) else {}
    previous = details.get("previous_turns")
    if not isinstance(previous, list):
        previous = generation_request.get("previous_turns")
    if not isinstance(previous, list):
        return []
    result = []
    for item in previous:
        if not isinstance(item, dict) or not isinstance(item.get("turn"), int):
            continue
        result.append(
            {
                "turn": item["turn"],
                "user_request": shorten_text(item.get("user_request")),
                "assistant_text": shorten_text(item.get("assistant_text")),
            }
        )
    return result


def _run_file_url(experiment: str, model: str, task_id: str, turn: int, path: Path) -> str:
    return "/run-file/{}/{}/{}/{}/{}".format(
        quote(experiment, safe=""),
        quote(model, safe=""),
        quote(task_id, safe=""),
        turn,
        quote(path.as_posix(), safe="/"),
    )


def _screenshot_url(
    run_dir: Path,
    experiment: str,
    model: str,
    task_id: str,
    turn: int,
    value: object,
) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    raw = Path(value)
    candidates = [raw] if raw.is_absolute() else [run_dir / raw, run_dir / "execution" / raw]
    for candidate in candidates:
        try:
            relative = candidate.resolve().relative_to(run_dir.resolve())
        except (OSError, ValueError):
            continue
        if candidate.is_file():
            return _run_file_url(experiment, model, task_id, turn, relative)
    return None


def _execution_payload(
    run_dir: Path,
    experiment: str,
    model: str,
    task_id: str,
    turn: int,
) -> dict[str, Any]:
    build = safe_read_json(run_dir / "execution" / "build.json")
    if not isinstance(build, dict):
        build = safe_read_json(run_dir / "generation" / "build.json")
    actor = safe_read_json(run_dir / "execution" / "actor_result.json")
    build = build if isinstance(build, dict) else {}
    actor = actor if isinstance(actor, dict) else {}
    return {
        "available": bool(build or actor),
        "run_id": run_dir.name,
        "build": {
            "success": build.get("success"),
            "stdout": shorten_text(build.get("stdout"), 12_000),
            "stderr": shorten_text(build.get("stderr"), 12_000),
        },
        "actor": {
            "available": bool(actor),
            "status": actor.get("status"),
            "finished": actor.get("finished"),
            "summary": shorten_text(actor.get("summary"), 2_000),
            "final_url": actor.get("final_url"),
            "final_text": shorten_text(actor.get("final_text"), 5_000),
            "final_screenshot_url": _screenshot_url(
                run_dir, experiment, model, task_id, turn, actor.get("final_screenshot")
            ),
            "steps": actor.get("steps") if isinstance(actor.get("steps"), list) else [],
            "tool_logs": (
                actor.get("tool_logs") if isinstance(actor.get("tool_logs"), list) else []
            ),
            "resource_logs": (
                actor.get("resource_logs") if isinstance(actor.get("resource_logs"), list) else []
            ),
            "side_effect_logs": (
                actor.get("side_effect_logs")
                if isinstance(actor.get("side_effect_logs"), list)
                else []
            ),
            "console_errors": (
                actor.get("console_errors") if isinstance(actor.get("console_errors"), list) else []
            ),
            "interaction_errors": (
                actor.get("interaction_errors")
                if isinstance(actor.get("interaction_errors"), list)
                else []
            ),
        },
    }


def _find_report(base_dir: Path, model: str, task_id: str, turn: int) -> dict[str, Any]:
    model_dir = _safe_directory(base_dir / "models", model, label="Model")
    path = _safe_descendant(
        model_dir / "reports",
        task_id,
        f"turn-{turn}.json",
        label="Evaluation report",
    )
    payload = safe_read_json(path)
    if not isinstance(payload, dict):
        abort(404, "Evaluation report not found")
    return payload


def build_experiment_task_turn_detail(
    base_dir: Path,
    experiment: str,
    model: str,
    task_id: str,
    turn: int,
) -> dict[str, Any]:
    report = _find_report(base_dir, model, task_id, turn)
    run_dir = find_latest_run_dir(base_dir, model, task_id, turn)
    generation_request: dict[str, Any] = {}
    generation_output: dict[str, Any] = {}
    execution: dict[str, Any] = {}
    dist_available = False
    if run_dir is not None:
        request_payload = safe_read_json(run_dir / "generation" / "request.json")
        output_payload = safe_read_json(run_dir / "generation" / "output.json")
        generation_request = request_payload if isinstance(request_payload, dict) else {}
        generation_output = output_payload if isinstance(output_payload, dict) else {}
        execution = _execution_payload(run_dir, experiment, model, task_id, turn)
        dist_available = (run_dir / "workspace" / "dist" / "index.html").is_file()

    details = report.get("details") if isinstance(report.get("details"), dict) else {}
    snapshot = (
        details.get("current_turn_snapshot")
        if isinstance(details.get("current_turn_snapshot"), dict)
        else {}
    )
    user_request = (
        snapshot.get("user_request")
        or report.get("user_request")
        or generation_request.get("current_user_request")
        or ""
    )
    model_output = generation_output.get("assistant_text") or report.get("assistant_text") or ""
    return {
        "model": model,
        "user_request": shorten_text(user_request),
        "model_output": shorten_text(model_output, 12_000),
        "conversation": _conversation(report, generation_request),
        "report": build_report_payload(report),
        "dist": {
            "available": dist_available,
            "entry_url": (
                f"/dist/{quote(experiment, safe='')}/{quote(model, safe='')}/"
                f"{quote(task_id, safe='')}/{turn}/"
                if dist_available
                else None
            ),
        },
        "execution": execution,
    }


def resolve_run_dir(base_dir: Path, model: str, task_id: str, turn: int) -> Path:
    run_dir = find_latest_run_dir(base_dir, model, task_id, turn)
    if run_dir is None:
        abort(404, "Run not found")
    return run_dir


def resolve_dist_dir(base_dir: Path, model: str, task_id: str, turn: int) -> Path:
    dist_dir = resolve_run_dir(base_dir, model, task_id, turn) / "workspace" / "dist"
    if not dist_dir.is_dir():
        abort(404, "Dist directory not found")
    return dist_dir


def _playground_runtime_state_from_run(run_dir: Path) -> dict[str, Any] | None:
    runtime_state = safe_read_json(run_dir / "execution" / "runtime_state.json")
    if isinstance(runtime_state, dict):
        return runtime_state
    snapshot = safe_read_json(run_dir / "execution" / "snapshot.json")
    if isinstance(snapshot, dict) and isinstance(snapshot.get("runtime_state"), dict):
        return snapshot["runtime_state"]
    actor = safe_read_json(run_dir / "execution" / "actor_result.json")
    if isinstance(actor, dict) and isinstance(actor.get("scenario_states"), dict):
        return {"scenarios": actor["scenario_states"]}
    return None


def _playground_initial_state(
    base_dir: Path,
    model: str,
    task_id: str,
    turn: int,
    source: str,
    custom_state: object = None,
) -> dict[str, Any]:
    if source == "custom":
        if not isinstance(custom_state, dict):
            abort(400, "custom runtime_state must be an object")
        return copy.deepcopy(custom_state)
    if source == "current":
        return (
            _playground_runtime_state_from_run(resolve_run_dir(base_dir, model, task_id, turn))
            or {}
        )
    if source == "previous":
        if turn <= 1:
            return {}
        previous_run = find_latest_run_dir(base_dir, model, task_id, turn - 1)
        if previous_run is None:
            return {}
        return _playground_runtime_state_from_run(previous_run) or {}
    if source == "empty":
        return {}
    abort(400, "state_source must be one of empty, previous, current, custom")


def _playground_scenarios_from_state(
    runtime_state: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    scenarios = runtime_state.get("scenarios", runtime_state)
    if not isinstance(scenarios, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for scenario, payload in scenarios.items():
        if not isinstance(scenario, str):
            continue
        state = payload.get("state") if isinstance(payload, dict) else None
        if state is None:
            state = payload
        if isinstance(state, dict):
            result[scenario] = copy.deepcopy(state)
    return result


def _playground_session(session_id: str) -> dict[str, Any]:
    session = PLAYGROUND_SESSIONS.get(session_id)
    if session is None:
        abort(404, "Playground session not found")
    return session


def _playground_public_payload(session_id: str, session: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "experiment": session["experiment"],
        "model": session["model"],
        "task_id": session["task_id"],
        "turn": session["turn"],
        "run_id": session["run_id"],
        "state_source": session["state_source"],
        "entry_url": f"/playground/session/{session_id}/index.html",
        "runtime_state": session["environment"].runtime_logs(),
    }


@contextmanager
def temporary_task_source(tasks_path: Path):
    with _TASK_SOURCE_LOCK:
        previous = os.environ.get("GENUI_BENCH_TASKS_PATH")
        os.environ["GENUI_BENCH_TASKS_PATH"] = str(tasks_path)
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop("GENUI_BENCH_TASKS_PATH", None)
            else:
                os.environ["GENUI_BENCH_TASKS_PATH"] = previous


def generate_runtime_script(base_dir: Path, task_id: str, turn: int) -> str:
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from runner.execution.runtime_script_builder import build_runtime_script

    with temporary_task_source(_experiment_tasks_root(base_dir)):
        return build_runtime_script(task_id, turn=turn)


@app.route("/")
def index() -> Response:
    return redirect(url_for("results"))


@app.route("/results")
def results() -> str:
    return render_template("results.html", active_page="results")


@app.route("/tasks")
def task_library() -> str:
    return render_template("task_library.html", active_page="tasks")


@app.route("/tools")
def tool_library() -> Response:
    response = make_response(render_template("tool_library.html", active_page="tools"))
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/playground")
def playground() -> Response:
    response = make_response(render_template("playground.html", active_page="playground"))
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/experiments")
def api_experiments():
    return jsonify({"experiments": list_available_experiments()})


@app.route("/api/task-turns")
def api_task_turns():
    experiment = request.args.get("experiment", "")
    if not experiment:
        available = list_available_experiments()
        if not available:
            return jsonify({"error": "No experiment results found"}), 400
        experiment = available[0]["id"]
    base_dir = resolve_experiment_dir(experiment)
    models, tasks = collect_task_turn_summary_data(base_dir)
    return jsonify(
        {
            "experiment_id": experiment,
            "models": models,
            "paper_reliability_by_model": collect_paper_reliability_by_model(
                base_dir, model_order=models
            ),
            "task_count": len(tasks),
            "turn_count": sum(len(task["turns"]) for task in tasks),
            "tasks": tasks,
        }
    )


@app.route("/api/task-turn-detail")
def api_task_turn_detail():
    experiment = request.args.get("experiment", "")
    model = request.args.get("model", "")
    task_id = request.args.get("task_id", "")
    turn_raw = request.args.get("turn", "")
    if not experiment or not model or not task_id or not turn_raw:
        return jsonify({"error": "experiment, model, task_id, and turn are required"}), 400
    try:
        turn = int(turn_raw)
    except ValueError:
        return jsonify({"error": "turn must be an integer"}), 400
    base_dir = resolve_experiment_dir(experiment)
    item = build_experiment_task_turn_detail(base_dir, experiment, model, task_id, turn)
    return jsonify({"task_id": task_id, "turn": turn, "model": model, "item": item})


@app.route("/api/raw-json")
def api_raw_json():
    experiment = request.args.get("experiment", "")
    model = request.args.get("model", "")
    task_id = request.args.get("task_id", "")
    target = request.args.get("target", "")
    try:
        turn = int(request.args.get("turn", ""))
    except ValueError:
        return jsonify({"error": "turn must be an integer"}), 400
    if not experiment or not model or not task_id or target != "report":
        return jsonify({"error": "invalid raw artifact request"}), 400
    base_dir = resolve_experiment_dir(experiment)
    payload = public_report_artifact(_find_report(base_dir, model, task_id, turn))
    return jsonify({"raw_json": json.dumps(payload, ensure_ascii=False, indent=2)})


@app.route("/api/task-catalog")
def api_task_catalog():
    return jsonify(collect_task_catalog())


@app.route("/api/generated-code")
def api_generated_code():
    experiment = request.args.get("experiment", "")
    model = request.args.get("model", "")
    task_id = request.args.get("task_id", "")
    try:
        turn = int(request.args.get("turn", ""))
    except ValueError:
        return jsonify({"error": "turn must be an integer"}), 400
    if not experiment or not model or not task_id:
        return jsonify({"error": "experiment, model, task_id, and turn are required"}), 400
    base_dir = resolve_experiment_dir(experiment)
    run_dir = resolve_run_dir(base_dir, model, task_id, turn)
    output = safe_read_json(run_dir / "generation" / "output.json")
    return jsonify(
        {
            "task_id": task_id,
            "turn": turn,
            "model": model,
            "files": _generation_files(output),
        }
    )


@app.route("/api/playground/session", methods=["POST"])
def api_playground_create_session():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "request body must be an object"}), 400

    experiment = payload.get("experiment")
    model = payload.get("model")
    task_id = payload.get("task_id")
    state_source = payload.get("state_source", "empty")
    try:
        turn = int(payload.get("turn"))
    except (TypeError, ValueError):
        return jsonify({"error": "turn must be an integer"}), 400
    if not all(isinstance(value, str) and value for value in (experiment, model, task_id)):
        return jsonify({"error": "experiment, model, task_id, and turn are required"}), 400
    if not isinstance(state_source, str):
        return jsonify({"error": "state_source must be a string"}), 400

    base_dir = resolve_experiment_dir(experiment)
    run_dir = resolve_run_dir(base_dir, model, task_id, turn)
    dist_dir = resolve_dist_dir(base_dir, model, task_id, turn)
    runtime_state = _playground_initial_state(
        base_dir,
        model,
        task_id,
        turn,
        state_source,
        payload.get("runtime_state"),
    )

    try:
        from runner.tools.task_loader import load_task
        from runtime.python_tool_environment import PythonToolEnvironment

        tasks_path = _experiment_tasks_root(base_dir)
        with temporary_task_source(tasks_path):
            task = load_task(task_id, turn=turn)
        environment = PythonToolEnvironment(
            tools=task.tools,
            fixture_scenarios=task.private_eval.get("scenario_fixtures", {}),
            task_id=task.task_id,
            turn=task.turn_index,
            initial_scenario_state=_playground_scenarios_from_state(runtime_state),
        )
    except Exception as exc:
        return jsonify({"error": f"Failed to initialize backend environment: {exc}"}), 400

    session_id = uuid.uuid4().hex
    PLAYGROUND_SESSIONS[session_id] = {
        "experiment": experiment,
        "model": model,
        "task_id": task_id,
        "turn": turn,
        "run_id": run_dir.name,
        "dist_dir": dist_dir,
        "state_source": state_source,
        "environment": environment,
    }
    response = jsonify(_playground_public_payload(session_id, PLAYGROUND_SESSIONS[session_id]))
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/playground/session/<session_id>/state")
def api_playground_session_state(session_id: str):
    response = jsonify(_playground_public_payload(session_id, _playground_session(session_id)))
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/playground/session/<session_id>/tool-call", methods=["POST"])
def api_playground_tool_call(session_id: str):
    session = _playground_session(session_id)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Tool call payload must be an object"}), 400
    name = payload.get("name")
    args = payload.get("args", {})
    scenario = payload.get("scenario", "default")
    if not isinstance(name, str) or not isinstance(args, dict):
        return jsonify({"error": "Tool call payload must include name and args"}), 400
    if not isinstance(scenario, str):
        return jsonify({"error": "Tool call scenario must be a string"}), 400
    try:
        result, evidence = session["environment"].call(name, args, scenario=scenario)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    response = jsonify({"result": result, "evidence": evidence})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/playground/session/<session_id>/")
@app.route("/playground/session/<session_id>/<path:subpath>")
def serve_playground_session_file(session_id: str, subpath: str = "index.html"):
    session = _playground_session(session_id)
    dist_dir = session["dist_dir"]
    if subpath == "__genui_runtime.js":
        try:
            body = generate_runtime_script(
                resolve_experiment_dir(session["experiment"]),
                session["task_id"],
                int(session["turn"]),
            )
        except Exception as exc:
            abort(500, f"Failed to generate runtime script: {exc}")
        body = body.replace(
            "fetch('/__genui/tool-call'",
            f"fetch('/api/playground/session/{session_id}/tool-call'",
        )
        response = Response(body, mimetype="application/javascript; charset=utf-8")
        response.headers["Cache-Control"] = "no-store"
        return response

    if subpath == "index.html":
        index_file = dist_dir / "index.html"
        if not index_file.is_file():
            abort(404, "dist/index.html not found")
        html = index_file.read_text(encoding="utf-8")
        asset_prefix = f"/playground/session/{session_id}/assets/"
        runtime_path = f"/playground/session/{session_id}/__genui_runtime.js"
        html = html.replace('"/assets/', f'"{asset_prefix}')
        html = html.replace("'/assets/", f"'{asset_prefix}")
        html = html.replace('"/__genui_runtime.js"', f'"{runtime_path}"')
        html = html.replace("'/__genui_runtime.js'", f"'{runtime_path}'")
        response = Response(html, mimetype="text/html; charset=utf-8")
        response.headers["Cache-Control"] = "no-store"
        return response

    return send_from_directory(dist_dir, subpath)


@app.route("/dist/<experiment>/<model>/<path:task_id>/<int:turn>/")
@app.route("/dist/<experiment>/<model>/<path:task_id>/<int:turn>/<path:subpath>")
def serve_dist_file(
    experiment: str,
    model: str,
    task_id: str,
    turn: int,
    subpath: str = "index.html",
):
    base_dir = resolve_experiment_dir(experiment)
    run_dir = resolve_run_dir(base_dir, model, task_id, turn)
    dist_dir = run_dir / "workspace" / "dist"
    if subpath != "index.html":
        return send_from_directory(dist_dir, subpath)
    index_file = dist_dir / "index.html"
    if not index_file.is_file():
        abort(404, "dist/index.html not found")
    html = index_file.read_text(encoding="utf-8")
    prefix = (
        f"/dist/{quote(experiment, safe='')}/{quote(model, safe='')}/"
        f"{quote(task_id, safe='')}/{turn}"
    )
    html = html.replace('"/assets/', f'"{prefix}/assets/')
    html = html.replace("'/assets/", f"'{prefix}/assets/")
    html = html.replace('"/__genui_runtime.js"', f'"{prefix}/__genui_runtime.js"')
    html = html.replace("'/__genui_runtime.js'", f"'{prefix}/__genui_runtime.js'")
    return Response(html, mimetype="text/html; charset=utf-8")


@app.route("/dist/<experiment>/<model>/<path:task_id>/<int:turn>/__genui_runtime.js")
def serve_runtime_script(experiment: str, model: str, task_id: str, turn: int):
    _ = model
    base_dir = resolve_experiment_dir(experiment)
    try:
        body = generate_runtime_script(base_dir, task_id, turn)
    except Exception as exc:
        abort(500, f"Failed to generate runtime script: {exc}")
    return Response(body, mimetype="application/javascript; charset=utf-8")


@app.route("/run-file/<experiment>/<model>/<path:task_id>/<int:turn>/<path:subpath>")
def serve_run_file(experiment: str, model: str, task_id: str, turn: int, subpath: str):
    base_dir = resolve_experiment_dir(experiment)
    run_dir = resolve_run_dir(base_dir, model, task_id, turn)
    requested = _safe_descendant(run_dir, subpath, label="Screenshot")
    if requested.suffix.lower() not in PUBLIC_IMAGE_SUFFIXES or not requested.is_file():
        abort(404, "Screenshot not found")
    return send_from_directory(run_dir, requested.relative_to(run_dir).as_posix())


def main() -> None:
    app.run(host="127.0.0.1", port=5001)


if __name__ == "__main__":
    main()
