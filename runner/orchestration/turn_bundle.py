from __future__ import annotations

import calendar
import copy
import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from runtime.types import JsonDict, TaskDefinition

_TURN_BUNDLE_SCHEMA_VERSION = "2026-04-25"


@dataclass(slots=True)
class TurnBundle:
    output_root: Path
    root: Path
    relative_root: str
    manifest_path: Path
    generation_dir: Path
    execution_dir: Path
    evaluation_dir: Path
    execution_artifacts_dir: Path
    run_id: str
    task_id: str
    turn: int
    provider: str


def create_turn_bundle(
    task: TaskDefinition,
    *,
    output_root: Path,
    run_id: str,
    provider: str,
) -> TurnBundle:
    root = output_root / "runs" / task.task_id / f"turn-{task.turn_index}" / run_id
    bundle = TurnBundle(
        output_root=output_root,
        root=root,
        relative_root=_relative_path(output_root, root),
        manifest_path=root / "manifest.json",
        generation_dir=root / "generation",
        execution_dir=root / "execution",
        evaluation_dir=root / "evaluation",
        execution_artifacts_dir=root / "execution" / "artifacts",
        run_id=run_id,
        task_id=task.task_id,
        turn=task.turn_index,
        provider=provider,
    )
    _ensure_bundle_directories(bundle)
    if not bundle.manifest_path.exists():
        _write_json(bundle.manifest_path, _initial_manifest(bundle))
    return bundle


def load_turn_bundle(
    *,
    output_root: Path,
    task_id: str,
    turn: int,
    run_id: str | None = None,
    required_stage: str | None = None,
    required_status: str = "completed",
) -> TurnBundle:
    runs_dir = output_root / "runs" / task_id / f"turn-{turn}"
    if not runs_dir.exists():
        raise FileNotFoundError(f"No runs found for task '{task_id}' turn {turn} under {runs_dir}")
    normalized_stage = _normalize_stage_name(required_stage) if required_stage is not None else None
    if run_id:
        candidates = [runs_dir / run_id]
    else:
        ordered_candidates = sorted(
            (entry for entry in runs_dir.iterdir() if entry.is_dir()),
            key=lambda entry: entry.name,
            reverse=True,
        )
        candidates = ordered_candidates[:1]
    for candidate in candidates:
        if not candidate.exists() or not candidate.is_dir():
            continue
        if normalized_stage is not None:
            stage_status = _manifest_stage_status(candidate / "manifest.json", normalized_stage)
            if stage_status != required_status:
                continue
        if candidate.exists() and candidate.is_dir():
            return _bundle_from_root(
                output_root=output_root, root=candidate, task_id=task_id, turn=turn
            )
    requirement = (
        f" with stage '{normalized_stage}' status '{required_status}'"
        if normalized_stage is not None
        else ""
    )
    raise FileNotFoundError(
        f"No run bundle found for task '{task_id}' turn {turn}{requirement} under {runs_dir}"
    )


def load_turn_bundle_from_relative_path(*, output_root: Path, relative_root: str) -> TurnBundle:
    root = output_root / relative_root
    if not root.exists():
        raise FileNotFoundError(f"Turn bundle does not exist: {root}")
    turn_dir = root.parent
    task_dir = turn_dir.parent
    turn_name = turn_dir.name
    if not turn_name.startswith("turn-"):
        raise ValueError(f"Turn bundle path does not include a turn directory: {root}")
    return _bundle_from_root(
        output_root=output_root,
        root=root,
        task_id=task_dir.name,
        turn=int(turn_name.split("-", 1)[1]),
    )


def write_stage_payload(bundle: TurnBundle, stage: str, filename: str, payload: JsonDict) -> str:
    directory = _stage_directory(bundle, stage)
    path = directory / filename
    _write_json(path, payload)
    return _relative_path(bundle.root, path)


def read_stage_payload(bundle: TurnBundle, stage: str, filename: str) -> JsonDict:
    path = _stage_directory(bundle, stage) / filename
    return _load_json(path)


def mark_stage_completed(
    bundle: TurnBundle,
    stage: str,
    *,
    files: dict[str, str] | None = None,
    extra: JsonDict | None = None,
) -> None:
    _update_manifest_stage(bundle, stage, status="completed", files=files, extra=extra)


def mark_stage_running(
    bundle: TurnBundle,
    stage: str,
    *,
    files: dict[str, str] | None = None,
    extra: JsonDict | None = None,
) -> None:
    _update_manifest_stage(bundle, stage, status="running", files=files, extra=extra)


def mark_stage_failed(
    bundle: TurnBundle,
    stage: str,
    *,
    error: str,
    files: dict[str, str] | None = None,
    extra: JsonDict | None = None,
) -> None:
    _update_manifest_stage(bundle, stage, status="failed", error=error, files=files, extra=extra)


def reset_stage(bundle: TurnBundle, stage: str) -> None:
    directory = _stage_directory(bundle, stage)
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if stage == "execution":
        bundle.execution_artifacts_dir.mkdir(parents=True, exist_ok=True)
    _reset_manifest_stage(bundle, stage)


def bundle_relative_path(bundle: TurnBundle, path: Path) -> str:
    return _relative_path(bundle.root, path)


def normalize_actor_result_for_bundle(bundle: TurnBundle, actor_result: JsonDict) -> JsonDict:
    payload = copy.deepcopy(actor_result)
    payload["final_screenshot"] = _normalize_path_string(bundle, payload.get("final_screenshot"))
    for field in ("observations", "visual_process_screenshots", "visual_quality_findings"):
        _normalize_nested_screenshot_paths(bundle, payload.get(field))
    return payload


def materialize_actor_result_for_runtime(bundle: TurnBundle, actor_result: JsonDict) -> JsonDict:
    payload = copy.deepcopy(actor_result)
    payload["final_screenshot"] = _materialize_path_string(bundle, payload.get("final_screenshot"))
    for field in ("observations", "visual_process_screenshots", "visual_quality_findings"):
        _materialize_nested_screenshot_paths(bundle, payload.get(field))
    return payload


def _normalize_nested_screenshot_paths(bundle: TurnBundle, value: object) -> None:
    if not isinstance(value, list):
        return
    for item in value:
        if isinstance(item, dict):
            item["screenshot"] = _normalize_path_string(bundle, item.get("screenshot"))


def _materialize_nested_screenshot_paths(bundle: TurnBundle, value: object) -> None:
    if not isinstance(value, list):
        return
    for item in value:
        if isinstance(item, dict):
            item["screenshot"] = _materialize_path_string(bundle, item.get("screenshot"))


def normalize_snapshot_for_bundle(bundle: TurnBundle, snapshot: JsonDict) -> JsonDict:
    payload = copy.deepcopy(snapshot)
    final_ui = payload.get("final_ui")
    if isinstance(final_ui, dict):
        final_ui["screenshot"] = _normalize_path_string(bundle, final_ui.get("screenshot"))
    return payload


def materialize_snapshot_for_runtime(bundle: TurnBundle, snapshot: JsonDict) -> JsonDict:
    payload = copy.deepcopy(snapshot)
    final_ui = payload.get("final_ui")
    if isinstance(final_ui, dict):
        final_ui["screenshot"] = _materialize_path_string(bundle, final_ui.get("screenshot"))
    return payload


def _ensure_bundle_directories(bundle: TurnBundle) -> None:
    for directory in (
        bundle.root,
        bundle.generation_dir,
        bundle.execution_dir,
        bundle.evaluation_dir,
        bundle.execution_artifacts_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)


def _bundle_from_root(*, output_root: Path, root: Path, task_id: str, turn: int) -> TurnBundle:
    provider = "unknown"
    actual_run_id = root.name
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        manifest = _load_json(manifest_path)
        provider_value = manifest.get("provider")
        if isinstance(provider_value, str) and provider_value.strip():
            provider = provider_value
        run_id_value = manifest.get("run_id")
        if isinstance(run_id_value, str) and run_id_value.strip():
            actual_run_id = run_id_value
    return TurnBundle(
        output_root=output_root,
        root=root,
        relative_root=_relative_path(output_root, root),
        manifest_path=manifest_path,
        generation_dir=root / "generation",
        execution_dir=root / "execution",
        evaluation_dir=root / "evaluation",
        execution_artifacts_dir=root / "execution" / "artifacts",
        run_id=actual_run_id,
        task_id=task_id,
        turn=turn,
        provider=provider,
    )


def _initial_manifest(bundle: TurnBundle) -> JsonDict:
    return {
        "schema_version": _TURN_BUNDLE_SCHEMA_VERSION,
        "task_id": bundle.task_id,
        "turn": bundle.turn,
        "run_id": bundle.run_id,
        "provider": bundle.provider,
        "bundle_root": bundle.relative_root,
        "created_at": _timestamp(),
        "stages": {
            "generation": {
                "status": "pending",
                "response_received": False,
                "terminal_failure": False,
            },
            "execution": {"status": "pending"},
            "evaluation": {"status": "pending"},
        },
    }


def _stage_directory(bundle: TurnBundle, stage: str) -> Path:
    normalized_stage = _normalize_stage_name(stage)
    if normalized_stage == "generation":
        return bundle.generation_dir
    if normalized_stage == "execution":
        return bundle.execution_dir
    if normalized_stage == "evaluation":
        return bundle.evaluation_dir
    raise ValueError(f"Unsupported stage: {stage}")


def _normalize_stage_name(stage: str | None) -> str:
    normalized = {
        "generate": "generation",
        "generation": "generation",
        "execute": "execution",
        "execution": "execution",
        "evaluate": "evaluation",
        "evaluation": "evaluation",
    }.get(stage, stage)
    if normalized not in {"generation", "execution", "evaluation"}:
        raise ValueError(f"Unsupported stage: {stage}")
    return str(normalized)


def _update_manifest_stage(
    bundle: TurnBundle,
    stage: str,
    *,
    status: str,
    error: str | None = None,
    files: dict[str, str] | None = None,
    extra: JsonDict | None = None,
) -> None:
    manifest = _load_json(bundle.manifest_path)
    stages = manifest.get("stages")
    if not isinstance(stages, dict):
        raise ValueError("turn bundle manifest is missing stages")
    existing = stages.get(stage)
    existing_payload = existing if isinstance(existing, dict) else {}
    updated_at = _timestamp()
    payload: JsonDict = {
        "status": status,
        "updated_at": updated_at,
    }
    started_at = existing_payload.get("started_at")
    if status == "running":
        payload["started_at"] = (
            str(started_at) if isinstance(started_at, str) and started_at.strip() else updated_at
        )
    elif isinstance(started_at, str) and started_at.strip():
        payload["started_at"] = started_at
    if status in {"completed", "failed"}:
        payload["finished_at"] = updated_at
        if isinstance(payload.get("started_at"), str):
            duration_seconds = _duration_seconds(payload["started_at"], updated_at)
            if duration_seconds is not None:
                payload["duration_seconds"] = duration_seconds
    if files:
        payload["files"] = files
    if error is not None:
        payload["error"] = error
    if extra:
        payload.update(extra)
    stages[stage] = payload
    _write_json(bundle.manifest_path, manifest)


def _reset_manifest_stage(bundle: TurnBundle, stage: str) -> None:
    manifest = _load_json(bundle.manifest_path)
    stages = manifest.get("stages")
    if not isinstance(stages, dict):
        raise ValueError("turn bundle manifest is missing stages")
    reset_at = _timestamp()
    stages[stage] = {
        "status": "pending",
        "updated_at": reset_at,
        "reset_at": reset_at,
    }
    if stage == "generation":
        stages[stage]["response_received"] = False
        stages[stage]["terminal_failure"] = False
    _write_json(bundle.manifest_path, manifest)


def _normalize_path_string(bundle: TurnBundle, value: object) -> object:
    if not isinstance(value, str) or not value:
        return value
    path = Path(value)
    if not path.is_absolute():
        return value
    try:
        return _relative_path(bundle.root, path)
    except ValueError:
        return value


def _materialize_path_string(bundle: TurnBundle, value: object) -> object:
    if not isinstance(value, str) or not value:
        return value
    path = Path(value)
    if path.is_absolute():
        return value
    return str((bundle.root / path).resolve())


def _relative_path(base: Path, target: Path) -> str:
    return target.resolve().relative_to(base.resolve()).as_posix()


def _load_json(path: Path) -> JsonDict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def _manifest_stage_status(manifest_path: Path, stage: str) -> str | None:
    if not manifest_path.exists():
        return None
    manifest = _load_json(manifest_path)
    stages = manifest.get("stages")
    if not isinstance(stages, dict):
        return None
    payload = stages.get(stage)
    if not isinstance(payload, dict):
        return None
    status = payload.get("status")
    return str(status) if isinstance(status, str) else None


def _write_json(path: Path, payload: JsonDict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _duration_seconds(started_at: str, finished_at: str) -> float | None:
    try:
        started = time.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ")
        finished = time.strptime(finished_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return max(float(calendar.timegm(finished) - calendar.timegm(started)), 0.0)
