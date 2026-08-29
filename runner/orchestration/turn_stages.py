from __future__ import annotations

from dataclasses import dataclass

from runner.evaluation.eval_runner import evaluate_snapshot
from runner.execution.build_runner import build_workspace, start_preview_server, stop_preview_server
from runner.execution.execution_session import run_actor_and_capture_snapshot
from runner.generation.model_runner import run_generation
from runner.tools.experiment_config import BlindActorRuntimeConfig, ComponentRuntimeConfig
from runner.tools.experiment_logging import log_event
from runner.tools.llm_client import LlmResponseError, failure_bucket_for_exception
from runner.tools.reporting import evaluation_to_report_dict
from runtime.types import BuildArtifacts, BuildResult, EvaluationResult, JsonDict, TaskDefinition

from .turn_bundle import (
    TurnBundle,
    bundle_relative_path,
    mark_stage_completed,
    mark_stage_failed,
    mark_stage_running,
    normalize_actor_result_for_bundle,
    normalize_snapshot_for_bundle,
    read_stage_payload,
    reset_stage,
    write_stage_payload,
)


@dataclass(slots=True)
class GenerationStageResult:
    output: JsonDict
    meta: JsonDict
    build_result: BuildResult


@dataclass(slots=True)
class ExecutionStageResult:
    build_result: BuildResult
    build_payload: JsonDict
    actor_result: JsonDict | None = None
    current_snapshot: JsonDict | None = None


class ExecutionStageError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        failure_reason: str,
        failure_bucket: str,
        build_payload: JsonDict,
        build_result: BuildResult | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_reason = failure_reason
        self.failure_bucket = failure_bucket
        self.build_payload = build_payload
        self.build_result = build_result


class ExecutionBuildError(ExecutionStageError):
    def __init__(
        self,
        message: str,
        *,
        build_payload: JsonDict,
        build_result: BuildResult | None = None,
    ) -> None:
        super().__init__(
            message,
            failure_reason="build",
            failure_bucket="build",
            build_payload=build_payload,
            build_result=build_result,
        )


class ExecutionRuntimeError(ExecutionStageError):
    def __init__(
        self,
        message: str,
        *,
        runtime_phase: str,
        build_payload: JsonDict,
        build_result: BuildResult | None = None,
        failure_bucket: str = "quality",
    ) -> None:
        super().__init__(
            message,
            failure_reason="execute",
            failure_bucket=failure_bucket,
            build_payload=build_payload,
            build_result=build_result,
        )
        self.runtime_phase = runtime_phase


@dataclass(slots=True)
class EvaluationStageResult:
    result: EvaluationResult


def run_generation_stage(
    task: TaskDefinition,
    *,
    payload: JsonDict,
    provider: str,
    bundle: TurnBundle,
    model_runtime_config: ComponentRuntimeConfig | None = None,
) -> GenerationStageResult:
    request_path = write_stage_payload(bundle, "generation", "request.json", payload)
    mark_stage_running(
        bundle,
        "generation",
        files={"request": request_path},
        extra={"response_received": False, "terminal_failure": False},
    )
    log_event(
        "generation_started",
        f"{task.task_id} turn {task.turn_index} generation started",
        task_id=task.task_id,
        turn=task.turn_index,
        run_id=bundle.run_id,
        turn_bundle=bundle.relative_root,
    )
    response_received = False
    try:
        del provider
        output, meta = run_generation(payload, runtime_config=model_runtime_config)
        response_received = True
        mark_stage_running(
            bundle,
            "generation",
            files={"request": request_path},
            extra={"response_received": True, "terminal_failure": False},
        )
        output_path = write_stage_payload(bundle, "generation", "output.json", output)
        meta_path = write_stage_payload(bundle, "generation", "meta.json", meta)
        build_result = _run_generation_preview_build(
            task,
            generation_output=output,
            bundle=bundle,
        )
        build_path = write_stage_payload(
            bundle,
            "generation",
            "build.json",
            _build_payload(build_result, bundle=bundle, source="generated"),
        )
    except Exception as exc:
        response_received = response_received or isinstance(exc, LlmResponseError)
        files = {"request": request_path}
        raw_payload = _generation_raw_response_payload(exc)
        if raw_payload is not None:
            files["raw_response"] = write_stage_payload(
                bundle,
                "generation",
                "raw_response.json",
                raw_payload,
            )
        mark_stage_failed(
            bundle,
            "generation",
            error=str(exc),
            files=files,
            extra={
                "response_received": response_received,
                "terminal_failure": response_received,
            },
        )
        log_event(
            "generation_failed",
            f"{bundle.task_id} turn {bundle.turn} generation failed",
            task_id=bundle.task_id,
            turn=bundle.turn,
            run_id=bundle.run_id,
            turn_bundle=bundle.relative_root,
            error=str(exc),
        )
        raise
    mark_stage_completed(
        bundle,
        "generation",
        files={
            "request": request_path,
            "output": output_path,
            "meta": meta_path,
            "build": build_path,
        },
        extra={"response_received": True, "terminal_failure": False},
    )
    log_event(
        "generation_completed",
        f"{bundle.task_id} turn {bundle.turn} generation completed",
        task_id=bundle.task_id,
        turn=bundle.turn,
        run_id=bundle.run_id,
        turn_bundle=bundle.relative_root,
        preview_build_success=build_result.success,
    )
    return GenerationStageResult(output=output, meta=meta, build_result=build_result)


def _generation_raw_response_payload(exc: Exception) -> JsonDict | None:
    raw_response = getattr(exc, "raw_response", None)
    content_text = getattr(exc, "content_text", None)
    if raw_response is None and content_text is None:
        return None
    payload: JsonDict = {}
    if raw_response is not None:
        payload["raw_response"] = raw_response
    if content_text is not None:
        payload["content_text"] = content_text
    return payload


def run_execution_stage(
    task: TaskDefinition,
    *,
    generation_output: JsonDict,
    bundle: TurnBundle,
    blind_actor_config: BlindActorRuntimeConfig,
    previous_turns: list[JsonDict] | None = None,
) -> ExecutionStageResult:
    mark_stage_running(bundle, "execution")
    log_event(
        "execution_started",
        f"{task.task_id} turn {task.turn_index} execution started",
        task_id=task.task_id,
        turn=task.turn_index,
        run_id=bundle.run_id,
        turn_bundle=bundle.relative_root,
    )
    try:
        build_result, build_source = _run_execution_build(
            task,
            generation_output=generation_output,
            bundle=bundle,
        )
    except Exception as exc:
        build_error = str(exc)
        build_result = BuildResult(
            success=False,
            artifacts=None,
            stdout="",
            stderr=build_error,
            errors=[build_error],
        )
        build_payload = _build_payload(build_result, bundle=bundle, source="rebuilt")
        build_path = write_stage_payload(bundle, "execution", "build.json", build_payload)
        _mark_execution_failure(bundle, build_path=build_path, error=build_error)
        log_event(
            "execution_failed",
            f"{bundle.task_id} turn {bundle.turn} build failed",
            task_id=bundle.task_id,
            turn=bundle.turn,
            run_id=bundle.run_id,
            turn_bundle=bundle.relative_root,
            error=build_error,
        )
        raise ExecutionBuildError(
            build_error,
            build_payload=build_payload,
            build_result=build_result,
        ) from exc

    build_payload = _build_payload(build_result, bundle=bundle, source=build_source)
    build_path = write_stage_payload(bundle, "execution", "build.json", build_payload)
    if not build_result.success or build_result.artifacts is None:
        build_error = build_result.stderr or build_result.stdout or "build failed"
        _mark_execution_failure(bundle, build_path=build_path, error=build_error)
        log_event(
            "execution_failed",
            f"{bundle.task_id} turn {bundle.turn} build failed",
            task_id=bundle.task_id,
            turn=bundle.turn,
            run_id=bundle.run_id,
            turn_bundle=bundle.relative_root,
            error=build_error,
        )
        raise ExecutionBuildError(
            build_error,
            build_payload=build_payload,
            build_result=build_result,
        )

    preview, preview_error = _start_execution_preview(
        task,
        build_result=build_result,
        bundle=bundle,
        previous_turns=list(previous_turns or []),
    )
    if preview_error is not None or preview is None:
        error = str(preview_error)
        _mark_execution_failure(bundle, build_path=build_path, error=error)
        log_event(
            "execution_failed",
            f"{bundle.task_id} turn {bundle.turn} preview startup failed",
            task_id=bundle.task_id,
            turn=bundle.turn,
            run_id=bundle.run_id,
            turn_bundle=bundle.relative_root,
            error=error,
        )
        raise ExecutionRuntimeError(
            error,
            runtime_phase="preview",
            build_payload=build_payload,
            build_result=build_result,
            failure_bucket=failure_bucket_for_exception(
                preview_error or Exception(error),
                default="quality",
            ),
        )

    try:
        actor_result, current_snapshot = _capture_execution_snapshot(
            task,
            build_result=build_result,
            preview=preview,
            blind_actor_config=blind_actor_config,
            bundle=bundle,
        )
    except Exception as exc:
        error = str(exc)
        _mark_execution_failure(bundle, build_path=build_path, error=error)
        log_event(
            "execution_failed",
            f"{bundle.task_id} turn {bundle.turn} actor run failed",
            task_id=bundle.task_id,
            turn=bundle.turn,
            run_id=bundle.run_id,
            turn_bundle=bundle.relative_root,
            error=error,
        )
        raise ExecutionRuntimeError(
            error,
            runtime_phase="actor",
            build_payload=build_payload,
            build_result=build_result,
            failure_bucket=failure_bucket_for_exception(exc, default="quality"),
        ) from exc

    if actor_result is None or current_snapshot is None:
        error = "actor run failed"
        _mark_execution_failure(bundle, build_path=build_path, error=error)
        log_event(
            "execution_failed",
            f"{bundle.task_id} turn {bundle.turn} actor run failed",
            task_id=bundle.task_id,
            turn=bundle.turn,
            run_id=bundle.run_id,
            turn_bundle=bundle.relative_root,
            error=error,
        )
        raise ExecutionRuntimeError(
            error,
            runtime_phase="actor",
            build_payload=build_payload,
            build_result=build_result,
        )

    actor_path, snapshot_path, runtime_state_path = _write_execution_outputs(
        bundle,
        actor_result=actor_result,
        current_snapshot=current_snapshot,
    )
    _mark_execution_completed(
        bundle,
        build_path=build_path,
        actor_path=actor_path,
        snapshot_path=snapshot_path,
        runtime_state_path=runtime_state_path,
    )
    log_event(
        "execution_completed",
        f"{bundle.task_id} turn {bundle.turn} execution completed",
        task_id=bundle.task_id,
        turn=bundle.turn,
        run_id=bundle.run_id,
        turn_bundle=bundle.relative_root,
    )
    return ExecutionStageResult(
        build_result=build_result,
        build_payload=build_payload,
        actor_result=actor_result,
        current_snapshot=current_snapshot,
    )


def _run_generation_preview_build(
    task: TaskDefinition,
    *,
    generation_output: JsonDict,
    bundle: TurnBundle,
) -> BuildResult:
    try:
        return build_workspace(
            task,
            generation_output,
            run_id=bundle.run_id,
            output_root=bundle.output_root,
        )
    except Exception as exc:
        error = str(exc)
        return BuildResult(
            success=False,
            artifacts=None,
            stdout="",
            stderr=error,
            errors=[error],
        )


def _run_execution_build(
    task: TaskDefinition,
    *,
    generation_output: JsonDict,
    bundle: TurnBundle,
) -> tuple[BuildResult, str]:
    reusable = _load_generation_build_result(bundle)
    if reusable is not None:
        return reusable, "reused"
    return (
        build_workspace(
            task,
            generation_output,
            run_id=bundle.run_id,
            output_root=bundle.output_root,
        ),
        "rebuilt",
    )


def _load_generation_build_result(bundle: TurnBundle) -> BuildResult | None:
    try:
        payload = read_stage_payload(bundle, "generation", "build.json")
    except FileNotFoundError:
        return None
    return _build_result_from_existing_workspace(bundle, payload)


def _build_result_from_existing_workspace(
    bundle: TurnBundle, payload: JsonDict
) -> BuildResult | None:
    if payload.get("success") is not True:
        return None
    artifacts_payload = payload.get("artifacts")
    if not isinstance(artifacts_payload, dict):
        return None

    workspace_dir = _materialize_bundle_path(bundle, artifacts_payload.get("workspace_dir"))
    build_dir = _materialize_bundle_path(bundle, artifacts_payload.get("build_dir"))
    assistant_text_path = _materialize_bundle_path(
        bundle, artifacts_payload.get("assistant_text_path")
    )
    generated_files_payload = artifacts_payload.get("generated_files")
    if (
        workspace_dir is None
        or build_dir is None
        or assistant_text_path is None
        or not workspace_dir.exists()
        or not workspace_dir.is_dir()
        or not build_dir.exists()
        or not build_dir.is_dir()
        or not (build_dir / "index.html").exists()
        or not assistant_text_path.exists()
    ):
        return None
    if not isinstance(generated_files_payload, list):
        return None

    generated_files: list = []
    for item in generated_files_payload:
        path = _materialize_bundle_path(bundle, item)
        if path is None or not path.exists():
            return None
        generated_files.append(path)

    errors = payload.get("errors")
    return BuildResult(
        success=True,
        artifacts=BuildArtifacts(
            workspace_dir=workspace_dir,
            build_dir=build_dir,
            generated_files=generated_files,
            assistant_text_path=assistant_text_path,
        ),
        stdout=str(payload.get("stdout") or ""),
        stderr=str(payload.get("stderr") or ""),
        errors=[str(item) for item in errors] if isinstance(errors, list) else [],
    )


def _materialize_bundle_path(bundle: TurnBundle, value: object):
    if not isinstance(value, str) or not value.strip():
        return None
    destination = (bundle.root / value).resolve()
    bundle_root = bundle.root.resolve()
    if bundle_root not in destination.parents and destination != bundle_root:
        return None
    return destination


def _start_execution_preview(
    task: TaskDefinition,
    *,
    build_result: BuildResult,
    bundle: TurnBundle,
    previous_turns: list[JsonDict],
) -> tuple[object | None, Exception | None]:
    try:
        return start_preview_server(
            task,
            build_result.artifacts,
            initial_runtime_state=_latest_runtime_state(previous_turns),
        ), None
    except Exception as exc:
        return None, exc


def _latest_runtime_state(previous_turns: list[JsonDict]) -> JsonDict | None:
    for snapshot in reversed(previous_turns):
        runtime_state = snapshot.get("runtime_state")
        if isinstance(runtime_state, dict):
            return runtime_state
        actor = snapshot.get("actor")
        if isinstance(actor, dict) and isinstance(actor.get("scenario_states"), dict):
            return {"scenarios": actor["scenario_states"]}
    return None


def _capture_execution_snapshot(
    task: TaskDefinition,
    *,
    build_result: BuildResult,
    preview,
    blind_actor_config: BlindActorRuntimeConfig,
    bundle: TurnBundle,
) -> tuple[JsonDict, JsonDict]:
    try:
        return run_actor_and_capture_snapshot(
            task,
            preview,
            build_result.artifacts,
            blind_actor_config=blind_actor_config,
            artifact_dir=bundle.execution_artifacts_dir,
        )
    finally:
        stop_preview_server(preview)


def _write_execution_outputs(
    bundle: TurnBundle,
    *,
    actor_result: JsonDict,
    current_snapshot: JsonDict,
) -> tuple[str, str, str]:
    actor_path = write_stage_payload(
        bundle,
        "execution",
        "actor_result.json",
        normalize_actor_result_for_bundle(bundle, actor_result),
    )
    snapshot_path = write_stage_payload(
        bundle,
        "execution",
        "snapshot.json",
        normalize_snapshot_for_bundle(bundle, current_snapshot),
    )
    runtime_state = current_snapshot.get("runtime_state", {})
    runtime_state_path = write_stage_payload(
        bundle,
        "execution",
        "runtime_state.json",
        runtime_state if isinstance(runtime_state, dict) else {},
    )
    return actor_path, snapshot_path, runtime_state_path


def _mark_execution_failure(bundle: TurnBundle, *, build_path: str, error: str) -> None:
    mark_stage_failed(
        bundle,
        "execution",
        error=error,
        files={"build": build_path},
    )


def _mark_execution_completed(
    bundle: TurnBundle,
    *,
    build_path: str,
    actor_path: str,
    snapshot_path: str,
    runtime_state_path: str,
) -> None:
    mark_stage_completed(
        bundle,
        "execution",
        files={
            "build": build_path,
            "actor_result": actor_path,
            "snapshot": snapshot_path,
            "runtime_state": runtime_state_path,
        },
    )


def run_evaluation_stage(
    task: TaskDefinition,
    *,
    actor_result: JsonDict,
    current_snapshot: JsonDict,
    previous_turns: list[JsonDict],
    bundle: TurnBundle,
    evaluator_config: ComponentRuntimeConfig | None = None,
) -> EvaluationStageResult:
    reset_stage(bundle, "evaluation")
    mark_stage_running(bundle, "evaluation")
    log_event(
        "evaluation_started",
        f"{task.task_id} turn {task.turn_index} evaluation started",
        task_id=task.task_id,
        turn=task.turn_index,
        run_id=bundle.run_id,
        turn_bundle=bundle.relative_root,
    )
    try:
        result = evaluate_snapshot(
            task,
            actor_result,
            previous_turns=previous_turns,
            current_snapshot=current_snapshot,
            evaluator_config=evaluator_config,
        )
    except Exception as exc:
        mark_stage_failed(bundle, "evaluation", error=str(exc))
        log_event(
            "evaluation_failed",
            f"{bundle.task_id} turn {bundle.turn} evaluation failed",
            task_id=bundle.task_id,
            turn=bundle.turn,
            run_id=bundle.run_id,
            turn_bundle=bundle.relative_root,
            error=str(exc),
        )
        raise

    evaluator_payload = result.details.get("evaluator")
    judge_result = evaluator_payload if isinstance(evaluator_payload, dict) else {}
    judge_path = write_stage_payload(bundle, "evaluation", "judge_result.json", judge_result)
    report_path = write_stage_payload(
        bundle,
        "evaluation",
        "report.json",
        evaluation_to_report_dict(result),
    )
    mark_stage_completed(
        bundle,
        "evaluation",
        files={
            "judge_result": judge_path,
            "report": report_path,
        },
    )
    log_event(
        "evaluation_completed",
        f"{bundle.task_id} turn {bundle.turn} evaluation completed",
        task_id=bundle.task_id,
        turn=bundle.turn,
        run_id=bundle.run_id,
        turn_bundle=bundle.relative_root,
        official_pass=result.official_pass,
    )
    return EvaluationStageResult(result=result)


def _build_payload(
    build_result: BuildResult, *, bundle: TurnBundle, source: str | None = None
) -> JsonDict:
    payload: JsonDict = {
        "success": build_result.success,
        "stdout": build_result.stdout,
        "stderr": build_result.stderr,
        "errors": build_result.errors,
    }
    if source is not None:
        payload["source"] = source
    if build_result.artifacts is not None:
        payload["artifacts"] = {
            "workspace_dir": bundle_relative_path(bundle, build_result.artifacts.workspace_dir),
            "build_dir": bundle_relative_path(bundle, build_result.artifacts.build_dir),
            "assistant_text_path": bundle_relative_path(
                bundle,
                build_result.artifacts.assistant_text_path,
            ),
            "generated_files": [
                bundle_relative_path(bundle, path)
                for path in build_result.artifacts.generated_files
            ],
        }
    return payload
