from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from runner.evaluation.eval_runner import evaluation_failure_mode_from_payload
from runner.evaluation.evaluator import run_evaluate_turn
from runner.evaluation.reliability_metrics import (
    paper_reliability_diagnostics,
    paper_reliability_metrics,
)
from runner.evaluation.scoring import breakdown_from_stage_results
from runner.execution.executor import run_execute_turn
from runner.generation.generator import run_generate_turn
from runner.orchestration.conversation_state import (
    load_json_object as _load_json_object_impl,
)
from runner.orchestration.conversation_state import (
    stage_conversation_summary as _stage_conversation_summary_impl,
)
from runner.orchestration.experiment_planning import (
    ExperimentModelPlan,
)
from runner.orchestration.experiment_planning import (
    build_experiment_model_plan as _build_experiment_model_plan_impl,
)
from runner.orchestration.experiment_planning import (
    experiment_model_summary_from_suite_summary as _experiment_model_summary_from_suite_summary_impl,
)
from runner.orchestration.experiment_planning import (
    model_request_payload as _model_request_payload_impl,
)
from runner.orchestration.experiment_planning import (
    resolved_dataset_path as _resolved_dataset_path_impl,
)
from runner.orchestration.experiment_planning import (
    write_experiment_manifest as _write_experiment_manifest_impl,
)
from runner.orchestration.stage_reporting import (
    generation_response_received as _generation_response_received_impl,
)
from runner.orchestration.stage_reporting import (
    generation_terminal_failure as _generation_terminal_failure_impl,
)
from runner.orchestration.stage_reporting import (
    stage_error as _stage_error_impl,
)
from runner.orchestration.stage_reporting import (
    stage_payload_from_bundle as _stage_payload_from_bundle_impl,
)
from runner.orchestration.stage_reporting import (
    stage_status as _stage_status_impl,
)
from runner.orchestration.stage_reporting import (
    stage_summary_path as _stage_summary_path_impl,
)
from runner.orchestration.turn_bundle import (
    load_turn_bundle,
    load_turn_bundle_from_relative_path,
)
from runner.orchestration.turn_stages import (
    ExecutionBuildError,
    ExecutionRuntimeError,
)
from runner.tools.experiment_config import (
    BlindActorRuntimeConfig,
    ComponentRuntimeConfig,
    ExperimentConfigError,
    experiment_id_from_config,
    experiment_request_payload,
    load_experiment_config,
    model_entries,
    temporary_environment,
)
from runner.tools.experiment_logging import (
    ExperimentLogger,
    activate_experiment_logger,
    active_experiment_logger,
    cli_verbose,
    emit_cli,
    format_log_block,
    format_mapping_block,
    green,
    log_event,
    red,
    set_cli_verbose,
    set_color_enabled,
    yellow,
)
from runner.tools.llm_client import (
    failure_bucket_for_exception,
)
from runner.tools.paths import ROOT_DIR, resolve_output_root
from runner.tools.reporting import write_json_report, write_report
from runner.tools.task_loader import load_task_entries, load_task_ids
from runner.tools.token_usage import (
    merge_component_token_usage,
    summarize_component_token_usage,
)
from runner.tools.token_usage_report import (
    collect_token_usage_report,
    write_experiment_token_usage_log,
)
from runner.tools.trajectory_writer import append_trajectory_record
from runtime.types import EvaluationResult, JsonDict

EVALUATION_PROTOCOL_VERSION = "presentation-execution-alignment-v1"


def _model_request_payload(
    *,
    resolved_config_path: Path,
    request: dict[str, object],
    model_id: str,
    provider: str,
    model_name: str,
    resume: bool,
    overwrite: bool,
    stage: str,
    turns: list[int] | None,
) -> dict[str, object]:
    return _model_request_payload_impl(
        resolved_config_path=resolved_config_path,
        request=request,
        model_id=model_id,
        provider=provider,
        model_name=model_name,
        resume=resume,
        overwrite=overwrite,
        stage=stage,
        turns=turns,
    )


def _write_experiment_manifest(
    output_root: Path,
    *,
    experiment_id: str,
    command: str,
    provider: str,
    request: dict[str, object],
) -> None:
    _write_experiment_manifest_impl(
        output_root,
        experiment_id=experiment_id,
        command=command,
        provider=provider,
        request=request,
    )


def _initialize_experiment_root(
    *,
    config: JsonDict,
    resolved_config_path: Path,
    experiment_root: Path,
    resume: bool,
) -> dict[str, object]:
    experiment_root.mkdir(parents=True, exist_ok=True)
    config_copy_path = experiment_root / "config.yaml"
    config_copy_path.write_text(resolved_config_path.read_text(encoding="utf-8"), encoding="utf-8")
    write_json_report(experiment_root / "resolved_config.json", config)
    request = experiment_request_payload(config, config_path=resolved_config_path, resume=resume)
    return request


def _conversation_report_path(task_id: str, output_root: Path) -> Path:
    return output_root / "reports" / task_id / "conversation.json"


def _turn_report_path(task_id: str, turn: int, output_root: Path) -> Path:
    return output_root / "reports" / task_id / f"turn-{turn}.json"


def _resolved_dataset_path(config_path: Path, value: object) -> str | None:
    return _resolved_dataset_path_impl(config_path, value)


def _stage_status(bundle, stage: str) -> str | None:
    return _stage_status_impl(bundle, stage)


def _stage_error(bundle, stage: str) -> str | None:
    return _stage_error_impl(bundle, stage)


def _stage_payload_from_bundle(bundle, stage: str) -> dict[str, object]:
    return _stage_payload_from_bundle_impl(bundle, stage)


def _stage_failure(stage: str, exc: Exception) -> tuple[str, str]:
    if isinstance(exc, ExecutionBuildError):
        return exc.failure_reason, exc.failure_bucket
    if isinstance(exc, ExecutionRuntimeError):
        return exc.failure_reason, exc.failure_bucket
    message = str(exc)
    if isinstance(exc, FileNotFoundError) or message.startswith("Missing prior execution snapshot"):
        return "blocked", "blocked"
    failure_reason = {"generate": "generation", "execute": "execute", "evaluate": "evaluate"}[stage]
    return failure_reason, failure_bucket_for_exception(exc, default="quality")


def _stage_summary_path(output_root: Path, stage: str, *, turn: int | None = None) -> Path:
    return _stage_summary_path_impl(output_root, stage, turn=turn)


def _resume_required_stage(stage: str) -> str:
    return {
        "generate": "generation",
        "execute": "generation",
        "evaluate": "execution",
    }[stage]


def _resume_terminal_stage_failure(task_id: str, *, stage: str, bundle) -> dict[str, object] | None:
    if stage != "generate":
        return None

    status = _stage_status(bundle, "generation")
    response_received = _generation_response_received_from_bundle(bundle)
    terminal_failure = _generation_terminal_failure_impl(bundle)
    if status == "running":
        should_freeze = response_received
    elif status == "failed":
        should_freeze = response_received or terminal_failure
    else:
        should_freeze = False
    if not should_freeze:
        return None

    error = _stage_error(bundle, "generation") or (
        "Generation stopped after receiving a model response"
    )
    return {
        "turn": bundle.turn,
        "failure_reason": "generation",
        "failure_bucket": "quality",
        "error": error,
        "turn_bundle": bundle.relative_root,
        "run_id": bundle.run_id,
        "reason": "terminal_failure",
        "task_id": task_id,
        "generation_response_received": response_received,
    }


def _generation_response_received_from_bundle(bundle) -> bool:
    return _generation_response_received_impl(bundle)


def _generation_response_received_after_failure(
    *,
    output_root: Path,
    task_id: str,
    turn: int,
    run_id: str | None,
) -> bool:
    try:
        bundle = load_turn_bundle(
            output_root=output_root,
            task_id=task_id,
            turn=turn,
            run_id=run_id,
            required_stage="generation",
            required_status="failed",
        )
    except FileNotFoundError:
        return False
    return _generation_response_received_from_bundle(bundle)


def _normalize_selected_turns(value: object) -> list[int] | None:
    if value is None or value == "all":
        return None
    if not isinstance(value, list):
        raise ExperimentConfigError("dataset.turns must be a list after config validation")
    return list(value)


def _turns_label(turns: list[int] | None) -> str:
    return "all" if turns is None else ",".join(str(turn) for turn in turns)


def _existing_experiment_outputs(experiment_root: Path) -> bool:
    return experiment_root.exists() and any(experiment_root.iterdir())


def _print_experiment_plan(
    *,
    experiment_id: str,
    config_path: Path,
    experiment_root: Path,
    stage: str,
    resume: bool,
    overwrite: bool,
    turns: list[int] | None,
    model_ids: list[str],
    task_ids: list[str] | None = None,
) -> None:
    if resume:
        mode = "resume"
        behavior = "reuse completed stage outputs when present"
    elif overwrite:
        mode = "overwrite"
        behavior = "allow existing outputs and rerun without reusing prior outputs"
    else:
        mode = "new"
        behavior = "fail if outputs already exist; otherwise create a new output tree"
    task_label = f" tasks={','.join(task_ids)}" if task_ids else ""
    emit_cli(
        "experiment",
        f"plan id={experiment_id} stage={stage} mode={mode} turns={_turns_label(turns)} "
        f"models={','.join(model_ids)}{task_label}",
        level="info",
    )
    emit_cli("experiment", f"config={config_path} output={experiment_root}", level="info")
    emit_cli("experiment", f"behavior={behavior}", level="info")


def _print_model_plan(plan: ExperimentModelPlan) -> None:
    if plan.resume:
        mode = "resume"
        behavior = "reuse completed stage outputs when present"
    elif plan.overwrite:
        mode = "overwrite"
        behavior = "allow existing outputs and rerun without reusing prior outputs"
    else:
        mode = "new"
        behavior = "fail if outputs already exist; otherwise create a new output tree"
    emit_cli(
        "experiment:model",
        f"plan model={plan.model_id} stage={plan.stage} mode={mode} "
        f"turns={_turns_label(plan.turns)} output={plan.model_root}",
        level="info",
    )
    emit_cli("experiment:model", f"behavior={behavior}", level="info")


def _stage_conversation_summary(
    task_id: str,
    *,
    stage: str,
    provider: str | None,
    turns_requested: list[int],
    results: list[dict[str, object]],
    turns_skipped: list[dict[str, object]],
    turn_failures: list[dict[str, object]],
) -> dict[str, object]:
    return _stage_conversation_summary_impl(
        task_id,
        stage=stage,
        provider=provider,
        turns_requested=turns_requested,
        results=results,
        turns_skipped=turns_skipped,
        turn_failures=turn_failures,
    )


def _print_token_usage_summary(payload: dict[str, object], *, log_path: str | None = None) -> None:
    token_usage = summarize_component_token_usage(payload.get("token_usage", {}))

    def _pair(component: str) -> str:
        usage = token_usage.get(component, {})
        if not isinstance(usage, dict):
            usage = {}
        parts = [
            f"{component} input={int(usage.get('input_tokens', 0))} "
            f"output={int(usage.get('output_tokens', 0))}"
        ]
        cached_input_tokens = int(usage.get("cached_input_tokens", 0) or 0)
        parts.append(
            f"cached={cached_input_tokens} uncached={int(usage.get('uncached_input_tokens', 0) or 0)}"
        )
        return " ".join(parts)

    parts = [
        _pair("tested_model"),
        _pair("evaluator"),
        _pair("blind_actor"),
        _pair("total"),
    ]
    suffix = f" log={log_path}" if log_path else ""
    emit_cli("token-usage", f"{'; '.join(parts)}{suffix}", level="info")


def _with_runtime_configs(
    base: dict[str, object],
    *,
    model_runtime_config: ComponentRuntimeConfig | None = None,
    evaluator_config: ComponentRuntimeConfig | None = None,
    blind_actor_config: BlindActorRuntimeConfig | None = None,
) -> dict[str, object]:
    kwargs = dict(base)
    if model_runtime_config is not None:
        kwargs["model_runtime_config"] = model_runtime_config
    if evaluator_config is not None:
        kwargs["evaluator_config"] = evaluator_config
    if blind_actor_config is not None:
        kwargs["blind_actor_config"] = blind_actor_config
    return kwargs


def _run_stage_turn(
    task_id: str,
    *,
    stage: str,
    turn: int,
    provider: str | None,
    output_root: Path,
    run_id: str | None = None,
    previous_turns: list[JsonDict] | None = None,
    model_runtime_config: ComponentRuntimeConfig | None = None,
    evaluator_config: ComponentRuntimeConfig | None = None,
    blind_actor_config: BlindActorRuntimeConfig | None = None,
) -> dict[str, object]:
    if stage == "generate":
        return run_generate_turn(
            task_id,
            **_with_runtime_configs(
                {
                    "turn": turn,
                    "provider": provider or "",
                    "output_root": output_root,
                    "run_id": run_id,
                    "previous_turns": previous_turns,
                },
                model_runtime_config=model_runtime_config,
            ),
        )
    if stage == "execute":
        return run_execute_turn(
            task_id,
            **_with_runtime_configs(
                {
                    "turn": turn,
                    "output_root": output_root,
                    "run_id": run_id,
                    "previous_turns": previous_turns,
                },
                blind_actor_config=blind_actor_config,
            ),
        )
    if stage == "evaluate":
        return run_evaluate_turn(
            task_id,
            **_with_runtime_configs(
                {
                    "turn": turn,
                    "output_root": output_root,
                    "run_id": run_id,
                    "previous_turns": previous_turns,
                },
                evaluator_config=evaluator_config,
            ),
        )
    raise ValueError(f"Unsupported stage: {stage}")


def _aggregate_failure_counts(
    conversations: list[dict[str, object]],
    key: str,
) -> dict[str, int]:
    return dict(
        sorted(
            Counter(
                item
                for conversation in conversations
                for item, count in conversation.get(key, {}).items()
                for _ in range(int(count))
            ).items()
        )
    )


def _run_task_collection(
    task_ids: list[str],
    *,
    workers: int,
    prefix: str,
    runner: Callable[[str], dict[str, object]],
    state_label: Callable[[dict[str, object]], str],
) -> list[dict[str, object]]:
    total = len(task_ids)
    if workers == 1:
        conversations = []
        for idx, task_id in enumerate(task_ids, 1):
            emit_cli(prefix, f"{idx}/{total} running task {task_id} ...")
            result = runner(task_id)
            emit_cli(prefix, f"{idx}/{total} task {task_id} -> {state_label(result)}", level="info")
            conversations.append(result)
        return conversations

    conversations_by_task: dict[str, dict[str, object]] = {}
    completed = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_task = {executor.submit(runner, task_id): task_id for task_id in task_ids}
        for future in as_completed(future_to_task):
            task_id = future_to_task[future]
            result = future.result()
            conversations_by_task[task_id] = result
            completed += 1
            emit_cli(
                prefix, f"{completed}/{total} task {task_id} -> {state_label(result)}", level="info"
            )
    return [conversations_by_task[task_id] for task_id in task_ids]


def _truncate_log_text(value: object, limit: int = 600) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...[truncated]"


def _dim_score(dimensions: dict[str, object], name: str) -> int | None:
    dim = dimensions.get(name)
    if isinstance(dim, dict):
        score = dim.get("score")
        if isinstance(score, (int, float)):
            return int(score)
    return None


def _score_color(score: float) -> str:
    if score >= 4.0:
        return green(f"{score:.2f}")
    elif score >= 3.0:
        return yellow(f"{score:.2f}")
    return red(f"{score:.2f}")


def _provider_config_lines(
    runtime_config: ComponentRuntimeConfig | BlindActorRuntimeConfig | None,
) -> list[str]:
    if runtime_config is None:
        return []
    provider_config = runtime_config.provider_config
    lines = [
        f"provider={provider_config.provider}",
        f"model={provider_config.model}",
        f"base_url={provider_config.base_url}",
        f"temperature={provider_config.temperature}",
        f"output_token_limit={provider_config.output_token_limit}",
        f"reasoning_effort={provider_config.reasoning_effort}",
        f"max_retries={runtime_config.retry_policy.max_retries}",
        f"backoff_seconds={runtime_config.retry_policy.backoff_seconds}",
        (
            f"timeouts=connect:{runtime_config.timeout_policy.connect_timeout_seconds},"
            f"read:{runtime_config.timeout_policy.read_timeout_seconds},"
            f"write:{runtime_config.timeout_policy.write_timeout_seconds},"
            f"pool:{runtime_config.timeout_policy.pool_timeout_seconds}"
        ),
    ]
    if not isinstance(runtime_config, BlindActorRuntimeConfig):
        lines.append(f"use_screenshot={runtime_config.use_screenshot}")
        lines.append(f"include_source_code={runtime_config.include_source_code}")
    if isinstance(runtime_config, BlindActorRuntimeConfig):
        lines.append(f"read_image={runtime_config.read_image}")
        lines.append(f"browser_use.use_vision={runtime_config.browser_use.use_vision}")
        lines.append(
            f"browser_use.step_timeout_seconds={runtime_config.browser_use.step_timeout_seconds}"
        )
        lines.append(
            "browser_use.wait_between_actions_seconds="
            f"{runtime_config.browser_use.wait_between_actions_seconds}"
        )
    return lines


def _emit_runtime_config_summary(
    scope: str,
    *,
    title: str,
    model_runtime_config: ComponentRuntimeConfig | None = None,
    evaluator_config: ComponentRuntimeConfig | None = None,
    blind_actor_config: BlindActorRuntimeConfig | None = None,
) -> None:
    blocks: list[str] = []
    model_lines = _provider_config_lines(model_runtime_config)
    if model_lines:
        blocks.append(format_log_block("tested model", "\n".join(model_lines)))
    evaluator_lines = _provider_config_lines(evaluator_config)
    if evaluator_lines:
        blocks.append(format_log_block("evaluator", "\n".join(evaluator_lines)))
    actor_lines = _provider_config_lines(blind_actor_config)
    if actor_lines:
        blocks.append(format_log_block("blind actor", "\n".join(actor_lines)))
    if blocks:
        full_message = format_log_block(title, "\n\n".join(blocks))
        logger = active_experiment_logger()
        if logger is not None:
            logger.log(scope, full_message, scope=scope)
        if cli_verbose():
            emit_cli(scope, full_message)
        else:
            parts: list[str] = []
            if model_runtime_config is not None:
                parts.append(f"tested_model={model_runtime_config.provider_config.model}")
            if evaluator_config is not None:
                parts.append(f"evaluator={evaluator_config.provider_config.model}")
            if blind_actor_config is not None:
                parts.append(f"blind_actor={blind_actor_config.provider_config.model}")
            if parts:
                emit_cli(scope, f"runtime: {' '.join(parts)}", level="info")


def _emit_stage_turn_summary(task_id: str, *, stage: str, result: dict[str, object]) -> None:
    turn_value = result.get("turn")
    turn = int(turn_value) if isinstance(turn_value, int | float | str) else "?"
    scope = f"task {task_id}"

    if stage == "evaluate":
        official_pass = bool(result.get("official_pass", False))
        score = float(result.get("evaluator_score", 0.0))
        dimensions = result.get("dimensions", {})
        p = _dim_score(dimensions, "Presentation")
        e = _dim_score(dimensions, "Execution")
        a = _dim_score(dimensions, "Alignment")
        dim_str = f" [P={p} E={e} A={a}]" if any(v is not None for v in (p, e, a)) else ""
        lines = [
            f"official_pass={official_pass}",
            f"evaluator_pass={bool(result.get('evaluator_pass', False))}",
            f"evaluator_score={score:.2f}",
        ]
        failure_reason = result.get("failure_reason")
        if failure_reason is not None:
            lines.append(f"failure_reason={failure_reason}")
        failure_bucket = result.get("failure_bucket")
        if failure_bucket is not None:
            lines.append(f"failure_bucket={failure_bucket}")
        files = result.get("files")
        if isinstance(files, dict):
            for key, value in files.items():
                lines.append(f"{key}={value}")
        summary = str(result.get("evaluator_summary") or "").strip()
        if summary:
            lines.append("evaluator_summary=" + _truncate_log_text(summary, 800))

        turn_usage = result.get("turn_token_usage")
        if isinstance(turn_usage, dict):
            blind_actor_usage = turn_usage.get("blind_actor")
            if isinstance(blind_actor_usage, dict) and blind_actor_usage:
                lines.append(
                    f"blind_actor_tokens={int(blind_actor_usage.get('total_tokens', 0) or 0)}"
                )

        full_message = format_log_block(f"turn {turn} evaluation summary", "\n".join(lines))
        logger = active_experiment_logger()
        if logger is not None:
            logger.log(scope, full_message, scope=scope)
        if not cli_verbose():
            if official_pass:
                status = green("PASS")
                score_colored = _score_color(score)
                msg = f"turn {turn} -> {status} (score={score_colored}){dim_str}"
            else:
                status = red("FAIL")
                score_colored = _score_color(score)
                reason = result.get("failure_reason") or ""
                bucket = result.get("failure_bucket") or ""
                reason_str = f" [{reason}:{bucket}]" if reason or bucket else ""
                msg = f"turn {turn} -> {status} (score={score_colored}){dim_str}{reason_str}"
            emit_cli(scope, msg, level="info")
            return
        emit_cli(scope, full_message)
        return

    if not cli_verbose():
        return

    if stage == "generate":
        files = result.get("files")
        build = result.get("build")
        file_lines: list[str] = []
        if isinstance(files, dict):
            for key, value in files.items():
                file_lines.append(f"{key}={value}")
        if isinstance(build, dict):
            file_lines.append(f"build_success={bool(build.get('success', False))}")
            source = build.get("source")
            if isinstance(source, str) and source.strip():
                file_lines.append(f"build_source={source}")
            stdout = str(build.get("stdout") or "").strip()
            if stdout:
                file_lines.append("build_stdout=" + _truncate_log_text(stdout, 500))
            stderr = str(build.get("stderr") or "").strip()
            if stderr:
                file_lines.append("build_stderr=" + _truncate_log_text(stderr, 500))
        emit_cli(
            scope,
            format_log_block(
                f"turn {turn} generation summary",
                "\n".join(
                    [
                        f"run_id={result.get('run_id', '')}",
                        f"bundle={result.get('turn_bundle', '')}",
                        *file_lines,
                    ]
                ),
            ),
        )
        return

    if stage == "execute":
        build = result.get("build")
        actor = result.get("actor")
        lines: list[str] = []
        if isinstance(build, dict):
            lines.append(f"build_success={bool(build.get('success', False))}")
            artifacts = build.get("artifacts")
            if isinstance(artifacts, dict):
                for key, value in artifacts.items():
                    lines.append(f"artifact_{key}={value}")
            files = result.get("files")
            if isinstance(files, dict):
                for key, value in files.items():
                    lines.append(f"{key}={value}")
            stdout = str(build.get("stdout") or "").strip()
            if stdout:
                lines.append("build_stdout=" + _truncate_log_text(stdout, 500))
            stderr = str(build.get("stderr") or "").strip()
            if stderr:
                lines.append("build_stderr=" + _truncate_log_text(stderr, 500))
        if isinstance(actor, dict):
            lines.append(f"actor_status={actor.get('status', 'unknown')}")
            summary = str(actor.get("summary") or "").strip()
            if summary:
                lines.append(f"actor_summary={summary}")
            final_url = str(actor.get("final_url") or "").strip()
            if final_url:
                lines.append(f"actor_final_url={final_url}")
        runtime_error = result.get("runtime_error")
        if isinstance(runtime_error, str) and runtime_error.strip():
            lines.append(f"runtime_error={_truncate_log_text(runtime_error, 300)}")
        emit_cli(scope, format_log_block(f"turn {turn} execution summary", "\n".join(lines)))
        return


def _emit_stage_turn_failure(task_id: str, *, stage: str, failure: dict[str, object]) -> None:
    turn = failure.get("turn", "?")
    lines = [
        f"failure_reason={failure.get('failure_reason')}",
        f"failure_bucket={failure.get('failure_bucket')}",
    ]
    error = str(failure.get("error") or "").strip()
    if error:
        lines.append("error=" + _truncate_log_text(error, 800))
    full_message = format_log_block(f"turn {turn} {stage} failure", "\n".join(lines))
    logger = active_experiment_logger()
    if logger is not None:
        logger.log(f"task {task_id}", full_message, scope=f"task {task_id}")
    if not cli_verbose():
        reason = failure.get("failure_reason") or ""
        bucket = failure.get("failure_bucket") or ""
        error_short = _truncate_log_text(error, 120)
        status = red("FAIL")
        msg = f"turn {turn} {stage} -> {status} ({reason}:{bucket}) {error_short}"
        emit_cli(f"task {task_id}", msg, level="info")
        return
    emit_cli(f"task {task_id}", full_message)


def _extract_snapshot_from_stage_result(
    result: dict[str, object], output_root: Path
) -> JsonDict | None:
    bundle_path = result.get("turn_bundle")
    if not isinstance(bundle_path, str) or not bundle_path:
        return None
    try:
        bundle = load_turn_bundle_from_relative_path(
            output_root=output_root,
            relative_root=bundle_path,
        )
    except (FileNotFoundError, ValueError):
        return None
    snapshot_path = bundle.execution_dir / "snapshot.json"
    if not snapshot_path.exists():
        return None
    try:
        return json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _stage_result_to_evaluation_result(result: dict[str, object], stage: str) -> EvaluationResult:
    if stage == "evaluate":
        actor = result.get("actor", {})
        return EvaluationResult(
            task_id=str(result.get("task_id", "")),
            turn=int(result.get("turn", 0)),
            generation_pass=True,
            build_pass=True,
            actor_pass=isinstance(actor, dict) and actor.get("status") == "success",
            evaluator_pass=bool(result.get("evaluator_pass", False)),
            evaluator_score=float(result.get("evaluator_score", 0.0)),
            evaluator_summary=str(result.get("evaluator_summary", "")),
            dimensions=result.get("dimensions", {}),
            official_pass=bool(result.get("official_pass", False)),
            failure_reason=(
                None if result.get("failure_reason") is None else str(result["failure_reason"])
            ),
            details={
                "turn_bundle": str(result.get("turn_bundle", "")),
                "actor": actor if isinstance(actor, dict) else {},
                "token_usage": result.get("token_usage", {}),
            },
            failure_bucket=(
                None if result.get("failure_bucket") is None else str(result["failure_bucket"])
            ),
        )
    if stage == "execute":
        build = result.get("build", {})
        actor = result.get("actor", {})
        build_pass = isinstance(build, dict) and bool(build.get("success", False))
        actor_pass = isinstance(actor, dict) and actor.get("status") == "success"
        return EvaluationResult(
            task_id=str(result.get("task_id", "")),
            turn=int(result.get("turn", 0)),
            generation_pass=True,
            build_pass=build_pass,
            actor_pass=actor_pass,
            evaluator_pass=False,
            evaluator_score=0.0,
            evaluator_summary="",
            dimensions={},
            official_pass=False,
            failure_reason=None if build_pass else "build",
            details={
                "turn_bundle": str(result.get("turn_bundle", "")),
                "actor": actor if isinstance(actor, dict) else {},
                "token_usage": result.get("token_usage", {}),
            },
            failure_bucket=None if build_pass else str(result.get("failure_bucket", "build")),
        )
    # stage == "generate"
    build = result.get("build", {})
    build_pass = isinstance(build, dict) and bool(build.get("success", True))
    return EvaluationResult(
        task_id=str(result.get("task_id", "")),
        turn=int(result.get("turn", 0)),
        generation_pass=True,
        build_pass=build_pass,
        actor_pass=True,
        evaluator_pass=False,
        evaluator_score=0.0,
        evaluator_summary="",
        dimensions={},
        official_pass=False,
        failure_reason=None if build_pass else "build",
        details={
            "turn_bundle": str(result.get("turn_bundle", "")),
            "token_usage": result.get("token_usage", {}),
        },
        failure_bucket=None if build_pass else "build",
    )


def run_task_conversation_stage(
    task_id: str,
    *,
    stage: str,
    provider: str | None = None,
    turns: list[int] | None = None,
    output_root: Path | None = None,
    resume: bool = False,
    run_id: str | None = None,
    model_runtime_config: ComponentRuntimeConfig | None = None,
    evaluator_config: ComponentRuntimeConfig | None = None,
    blind_actor_config: BlindActorRuntimeConfig | None = None,
) -> dict[str, object]:
    resolved_output_root = resolve_output_root(output_root=output_root)
    entry = next((item for item in load_task_entries() if item["task_id"] == task_id), None)
    if entry is None:
        raise FileNotFoundError(f"Unknown multiturn task: {task_id}")
    available_turns = [int(item["turn"]) for item in entry["turns"]]
    selected_turns = list(turns or available_turns)
    if stage == "generate" and provider is None:
        raise ValueError("provider is required for generate stage")

    # Provider mismatch guard on resume (Bug #9)
    conversation_path = resolved_output_root / "reports" / task_id / "conversation.json"
    if resume and conversation_path.exists():
        try:
            existing_summary = _load_json_object_impl(conversation_path)
            existing_provider = existing_summary.get("provider")
            if (
                isinstance(existing_provider, str)
                and existing_provider.strip()
                and existing_provider != provider
            ):
                resume = False
        except (json.JSONDecodeError, OSError):
            pass

    results: list[dict[str, object]] = []
    turns_skipped: list[dict[str, object]] = []
    turn_failures: list[dict[str, object]] = []
    turn_snapshots: list[JsonDict] = []

    for turn_index in selected_turns:
        bundle = None
        if resume:
            try:
                bundle = load_turn_bundle(
                    output_root=resolved_output_root,
                    task_id=task_id,
                    turn=turn_index,
                    run_id=run_id,
                    required_stage=_resume_required_stage(stage),
                )
            except FileNotFoundError:
                bundle = None
        if bundle is not None:
            if provider is not None and bundle.provider != provider:
                raise ValueError(
                    f"Run bundle provider mismatch for task '{task_id}' turn {turn_index}: "
                    f"expected '{provider}', found '{bundle.provider}'"
                )
            status = _stage_status(bundle, stage)
            if status == "completed":
                resumed_result = _stage_payload_from_bundle(bundle, stage)
                results.append(resumed_result)
                turns_skipped.append(
                    {
                        "turn": turn_index,
                        "reason": "already_completed",
                        "turn_bundle": bundle.relative_root,
                        "run_id": bundle.run_id,
                    }
                )
                _emit_stage_turn_summary(task_id, stage=stage, result=resumed_result)
                # Append resumed snapshot to accumulator for chaining (Bug #3)
                snapshot = _extract_snapshot_from_stage_result(
                    resumed_result, output_root=resolved_output_root
                )
                if snapshot is not None:
                    turn_snapshots.append(snapshot)
                continue
        if resume and bundle is None:
            try:
                failed_bundle = load_turn_bundle(
                    output_root=resolved_output_root,
                    task_id=task_id,
                    turn=turn_index,
                    run_id=run_id,
                )
            except FileNotFoundError:
                failed_bundle = None
            if failed_bundle is not None:
                resumed_failure = _resume_terminal_stage_failure(
                    task_id,
                    stage=stage,
                    bundle=failed_bundle,
                )
                if resumed_failure is not None:
                    failure = {
                        "turn": resumed_failure["turn"],
                        "failure_reason": resumed_failure["failure_reason"],
                        "failure_bucket": resumed_failure["failure_bucket"],
                        "error": resumed_failure["error"],
                    }
                    if "generation_response_received" in resumed_failure:
                        failure["generation_response_received"] = resumed_failure[
                            "generation_response_received"
                        ]
                    turn_failures.append(failure)
                    turns_skipped.append(
                        {
                            "turn": resumed_failure["turn"],
                            "reason": resumed_failure["reason"],
                            "turn_bundle": resumed_failure["turn_bundle"],
                            "run_id": resumed_failure["run_id"],
                        }
                    )
                    _emit_stage_turn_failure(task_id, stage=stage, failure=turn_failures[-1])
                    continue

        try:
            result = _run_stage_turn(
                task_id,
                stage=stage,
                turn=turn_index,
                provider=provider,
                output_root=resolved_output_root,
                run_id=run_id,
                previous_turns=list(turn_snapshots) if turn_snapshots else None,
                model_runtime_config=model_runtime_config,
                evaluator_config=evaluator_config,
                blind_actor_config=blind_actor_config,
            )
        except Exception as exc:  # noqa: BLE001
            failure_reason, failure_bucket = _stage_failure(stage, exc)
            failure: dict[str, object] = {
                "turn": turn_index,
                "failure_reason": failure_reason,
                "failure_bucket": failure_bucket,
                "error": str(exc),
            }
            if stage == "generate":
                failure["generation_response_received"] = (
                    _generation_response_received_after_failure(
                        output_root=resolved_output_root,
                        task_id=task_id,
                        turn=turn_index,
                        run_id=run_id,
                    )
                )
            turn_failures.append(failure)
            _emit_stage_turn_failure(task_id, stage=stage, failure=turn_failures[-1])
            # Write trajectory record for exception (Bug #8)
            append_trajectory_record(
                resolved_output_root / "trajectories" / task_id / f"turn-{turn_index}.jsonl",
                {
                    "task_id": task_id,
                    "turn": turn_index,
                    "provider": provider,
                    "stage": stage,
                    "failure_reason": failure_reason,
                    "failure_bucket": failure_bucket,
                    "error": str(exc),
                    **(
                        {"generation_response_received": failure["generation_response_received"]}
                        if stage == "generate"
                        else {}
                    ),
                },
            )
            continue
        results.append(result)
        _emit_stage_turn_summary(task_id, stage=stage, result=result)

        # Write per-turn report (Bug #7)
        eval_result = _stage_result_to_evaluation_result(result, stage=stage)
        # Enrich details with snapshot for downstream compatibility
        snapshot = _extract_snapshot_from_stage_result(result, output_root=resolved_output_root)
        if snapshot is not None:
            eval_result.details["current_turn_snapshot"] = snapshot
        write_report(
            _turn_report_path(task_id, turn_index, resolved_output_root),
            eval_result,
        )

        # Write trajectory record (Bug #8)
        trajectory_record: dict[str, object] = {
            "task_id": task_id,
            "turn": turn_index,
            "provider": provider,
            "stage": stage,
            "run_id": result.get("run_id"),
            "turn_bundle": result.get("turn_bundle"),
            "token_usage": result.get("token_usage", {}),
        }
        if stage == "execute":
            trajectory_record["build"] = result.get("build")
            trajectory_record["actor"] = result.get("actor")
        if stage == "evaluate":
            trajectory_record["official_pass"] = result.get("official_pass")
            trajectory_record["evaluator_score"] = result.get("evaluator_score")
        append_trajectory_record(
            resolved_output_root / "trajectories" / task_id / f"turn-{turn_index}.jsonl",
            trajectory_record,
        )

        # Append snapshot to accumulator for multi-turn chaining (Bug #3)
        if snapshot is not None:
            turn_snapshots.append(snapshot)

    summary = _stage_conversation_summary(
        task_id,
        stage=stage,
        provider=provider,
        turns_requested=selected_turns,
        results=results,
        turns_skipped=turns_skipped,
        turn_failures=turn_failures,
    )
    # Write conversation summary report (Bug #7)
    write_json_report(_conversation_report_path(task_id, resolved_output_root), summary)
    return summary


def _filter_task_ids(all_task_ids: list[str], selected: list[str] | None) -> list[str]:
    if selected is None:
        return all_task_ids
    available = set(all_task_ids)
    missing = set(selected) - available
    if missing:
        raise ValueError(f"Requested task IDs not found in suite: {sorted(missing)}")
    return [tid for tid in all_task_ids if tid in selected]


def run_suite_stage(
    *,
    stage: str,
    provider: str | None = None,
    turns: list[int] | None = None,
    limit: int | None = None,
    task_ids: list[str] | None = None,
    workers: int = 1,
    output_root: Path | None = None,
    resume: bool = False,
    run_id: str | None = None,
    model_runtime_config: ComponentRuntimeConfig | None = None,
    evaluator_config: ComponentRuntimeConfig | None = None,
    blind_actor_config: BlindActorRuntimeConfig | None = None,
) -> dict[str, object]:
    resolved_output_root = resolve_output_root(output_root=output_root)
    if workers < 1:
        raise ValueError("workers must be >= 1")
    if stage == "generate" and provider is None:
        raise ValueError("provider is required for generate stage")

    all_task_ids = load_task_ids()
    filtered_task_ids = _filter_task_ids(all_task_ids, task_ids)
    if limit is not None:
        filtered_task_ids = filtered_task_ids[: max(limit, 0)]
    _, stage_turns = _all_stage_scope(
        turns=turns,
        limit=limit,
        task_ids=task_ids,
    )
    if stage == "generate":
        if len(stage_turns) > 1:
            raise ValueError(
                "A generation-only stage may select only one turn because each later turn "
                "requires the previous turn's execution snapshot. Use --stage all for the "
                "paper protocol, or a config whose dataset.turns selects one turn."
            )

    def _state_label(result: dict[str, object]) -> str:
        if stage == "evaluate":
            turn_results = result.get("results", [])
            last_turn = turn_results[-1] if isinstance(turn_results, list) and turn_results else {}
            if not isinstance(last_turn, dict):
                last_turn = {}
            official_pass = bool(last_turn.get("official_pass", False))
            score = float(last_turn.get("evaluator_score", 0.0))
            dimensions = last_turn.get("dimensions", {})
            p = _dim_score(dimensions, "Presentation")
            e = _dim_score(dimensions, "Execution")
            a = _dim_score(dimensions, "Alignment")
            dim_str = f" [P={p} E={e} A={a}]" if any(v is not None for v in (p, e, a)) else ""
            if official_pass:
                return f"{green('PASS')} (score={_score_color(score)}){dim_str}"
            reason = last_turn.get("failure_reason") or ""
            bucket = last_turn.get("failure_bucket") or ""
            reason_str = f" ({reason}:{bucket})" if reason or bucket else ""
            return f"{red('FAIL')} (score={_score_color(score)}){dim_str}{reason_str}"
        if result.get("completed", False):
            return green("DONE")
        return yellow("BLOCKED")

    conversations = _run_task_collection(
        filtered_task_ids,
        workers=workers,
        prefix=f"suite:{stage}",
        runner=lambda task_id: run_task_conversation_stage(
            task_id,
            **_with_runtime_configs(
                {
                    "stage": stage,
                    "provider": provider,
                    "turns": turns,
                    "output_root": resolved_output_root,
                    "resume": resume,
                    "run_id": run_id,
                },
                model_runtime_config=model_runtime_config,
                evaluator_config=evaluator_config,
                blind_actor_config=blind_actor_config,
            ),
        ),
        state_label=_state_label,
    )

    reliability_metrics: dict[str, object] = {}
    if stage == "evaluate":
        reliability_metrics = _attach_stage_evaluation_reliability(
            conversations,
            output_root=resolved_output_root,
            run_id=run_id,
        )
    summary = {
        "stage": stage,
        "provider": provider,
        "turn": turns[0] if turns is not None and len(turns) == 1 else None,
        "turns": turns,
        "workers": workers,
        "run_id": run_id,
        "task_count": len(filtered_task_ids),
        "completed": all(item.get("completed", False) for item in conversations),
        "completed_task_count": sum(1 for item in conversations if item.get("completed", False)),
        "blocked_task_count": sum(1 for item in conversations if item.get("blocked", False)),
        "infra_failed_task_count": sum(
            1 for item in conversations if item.get("infra_failed", False)
        ),
        **reliability_metrics,
        "failure_buckets": _aggregate_failure_counts(conversations, "failure_buckets"),
        "failure_reasons": _aggregate_failure_counts(conversations, "failure_reasons"),
        "failure_modes": _aggregate_failure_counts(conversations, "failure_modes"),
        "token_usage": summarize_component_token_usage(
            merge_component_token_usage(
                conversation.get("token_usage", {}) for conversation in conversations
            )
        ),
        "conversations": conversations,
    }
    if stage == "evaluate":
        summary["evaluation_complete_task_count"] = sum(
            1 for item in conversations if item.get("evaluation_complete", False)
        )
        summary["judge_failed_task_count"] = sum(
            1 for item in conversations if item.get("completion_reason") == "judge_failed"
        )
    write_json_report(
        _stage_summary_path(
            resolved_output_root,
            stage,
            turn=turns[0] if turns is not None and len(turns) == 1 else None,
        ),
        summary,
    )
    return summary


def _merge_stage_token_usage(stage_summaries: list[dict[str, object]]) -> dict[str, object]:
    return summarize_component_token_usage(
        merge_component_token_usage(summary.get("token_usage", {}) for summary in stage_summaries)
    )


def _attach_stage_evaluation_reliability(
    conversations: list[dict[str, object]],
    *,
    output_root: Path,
    run_id: str | None,
) -> dict[str, object]:
    requested_sequences: list[list[int]] = []
    for conversation in conversations:
        task_id = str(conversation.get("task_id", ""))
        turns_requested = conversation.get("turns_requested")
        breakdown = conversation.get("turns_breakdown")
        if not isinstance(turns_requested, list) or not isinstance(breakdown, list):
            raise ValueError(f"Evaluation conversation for '{task_id}' is missing turn slots")
        requested = [int(turn) for turn in turns_requested]
        requested_sequences.append(requested)
        slots_by_turn = {
            int(slot["turn"]): slot
            for slot in breakdown
            if isinstance(slot, dict) and slot.get("turn") is not None
        }
        for turn in requested:
            slot = slots_by_turn.get(turn)
            if slot is None:
                raise ValueError(f"Evaluation conversation for '{task_id}' is missing turn {turn}")
            try:
                bundle = load_turn_bundle(
                    output_root=output_root,
                    task_id=task_id,
                    turn=turn,
                    run_id=run_id,
                )
            except FileNotFoundError:
                response_received = False
            else:
                response_received = _generation_response_received_from_bundle(bundle)
                if not response_received and _stage_status(bundle, "generation") == "failed":
                    # Evaluator-only summaries otherwise inherit the downstream
                    # "blocked" label and lose the provider no-response cause.
                    # Restore that cause so the appendix's countable-only TP
                    # excludes exactly these slots, as the paper specifies.
                    slot["failure_reason"] = "generation"
                    slot["failure_bucket"] = "infra"
            slot["generation_response_received"] = response_received

        conversation_metrics = (
            paper_reliability_metrics([conversation])
            if requested == [1, 2, 3, 4, 5]
            else _partial_requested_slot_metrics([conversation])
        )
        conversation.update(conversation_metrics)

    if requested_sequences and all(
        requested == [1, 2, 3, 4, 5] for requested in requested_sequences
    ):
        diagnostics = paper_reliability_diagnostics(conversations)
        paper_metrics = diagnostics.pop("paper_metrics")
        return {
            **paper_metrics,
            "reliability_diagnostics": diagnostics,
        }
    return _partial_requested_slot_metrics(conversations)


def _all_stage_scope(
    *,
    turns: list[int] | None,
    limit: int | None,
    task_ids: list[str] | None,
) -> tuple[list[str], list[int]]:
    filtered_task_ids = _filter_task_ids(load_task_ids(), task_ids)
    if limit is not None:
        filtered_task_ids = filtered_task_ids[: max(limit, 0)]
    if not filtered_task_ids:
        return [], list(turns or [])

    entries = {
        str(entry["task_id"]): [int(item["turn"]) for item in entry["turns"]]
        for entry in load_task_entries()
        if str(entry.get("task_id", "")) in filtered_task_ids
    }
    missing_entries = [task_id for task_id in filtered_task_ids if task_id not in entries]
    if missing_entries:
        raise ValueError(f"Task entries missing for: {missing_entries}")

    selected_turns = list(turns) if turns is not None else list(entries[filtered_task_ids[0]])
    if any(
        current <= previous
        for previous, current in zip(selected_turns, selected_turns[1:], strict=False)
    ):
        raise ValueError("Selected turns must be strictly increasing")
    for task_id in filtered_task_ids:
        missing_turns = [turn for turn in selected_turns if turn not in entries[task_id]]
        if missing_turns:
            raise ValueError(f"Task '{task_id}' does not define requested turns {missing_turns}")
        if turns is None and entries[task_id] != selected_turns:
            raise ValueError("All tasks in an all-stage run must define the same ordered turns")
    return filtered_task_ids, selected_turns


def _task_conversation_from_stage_summary(
    summary: dict[str, object], *, task_id: str
) -> dict[str, object]:
    conversations = summary.get("conversations")
    if not isinstance(conversations, list):
        raise ValueError("Stage summary is missing conversations")
    matches = [
        item
        for item in conversations
        if isinstance(item, dict) and str(item.get("task_id", "")) == task_id
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Expected one stage conversation for task '{task_id}', found {len(matches)}"
        )
    return matches[0]


def _turn_item(
    conversation: dict[str, object], *, field: str, turn: int
) -> dict[str, object] | None:
    items = conversation.get(field)
    if not isinstance(items, list):
        raise ValueError(f"Stage conversation is missing {field}")
    matches = [
        item
        for item in items
        if isinstance(item, dict) and item.get("turn") is not None and int(item["turn"]) == turn
    ]
    if len(matches) > 1:
        raise ValueError(f"Stage conversation contains duplicate {field} for turn {turn}")
    return matches[0] if matches else None


def _generation_response_from_conversation(conversation: dict[str, object], *, turn: int) -> bool:
    result = _turn_item(conversation, field="results", turn=turn)
    if result is not None:
        value = result.get("generation_response_received", True)
        if not isinstance(value, bool):
            raise ValueError(f"Generation response status for turn {turn} must be boolean")
        return value
    failure = _turn_item(conversation, field="turn_failures", turn=turn)
    if failure is None:
        return False
    value = failure.get("generation_response_received")
    if isinstance(value, bool):
        return value
    raise ValueError(f"Generation failure for turn {turn} is missing response eligibility")


def _failure_slot(
    *,
    task_id: str,
    turn: int,
    generation_response_received: bool,
    failure: dict[str, object] | None,
) -> dict[str, object]:
    failure_reason = str((failure or {}).get("failure_reason") or "blocked")
    failure_bucket = str(
        (failure or {}).get("failure_bucket")
        or ("blocked" if failure_reason == "blocked" else "quality")
    )
    slot: dict[str, object] = {
        "task_id": task_id,
        "turn": turn,
        "official_pass": False,
        "evaluator_pass": False,
        "evaluator_score": 0.0,
        "evaluator_summary": "",
        "dimensions": {},
        "failure_reason": failure_reason,
        "failure_bucket": failure_bucket,
        "generation_response_received": generation_response_received,
        "evaluation_completed": False,
        "token_usage": {},
    }
    if failure is not None and failure.get("error") is not None:
        slot["error"] = str(failure["error"])
    slot["failure_mode"] = evaluation_failure_mode_from_payload(slot)
    return slot


def _paper_slot_from_wave(
    *,
    task_id: str,
    turn: int,
    wave: dict[str, dict[str, object]],
) -> tuple[dict[str, object], list[dict[str, object]]]:
    lineage_by_stage = {stage: summary.get("run_id") for stage, summary in wave.items()}
    if any(not isinstance(value, str) or not value for value in lineage_by_stage.values()):
        raise ValueError(f"Turn {turn} stage summaries are missing run lineage: {lineage_by_stage}")
    if len(set(lineage_by_stage.values())) != 1:
        raise ValueError(f"Turn {turn} mixed stage run lineages: {lineage_by_stage}")
    conversations = {
        stage: _task_conversation_from_stage_summary(summary, task_id=task_id)
        for stage, summary in wave.items()
    }
    generation_response_received = _generation_response_from_conversation(
        conversations["generate"], turn=turn
    )
    evaluation_result = _turn_item(conversations["evaluate"], field="results", turn=turn)
    if evaluation_result is not None:
        slot = dict(evaluation_result)
        slot["generation_response_received"] = generation_response_received
        slot["evaluation_completed"] = True
        return slot, list(conversations.values())

    generation_result = _turn_item(conversations["generate"], field="results", turn=turn)
    execution_result = _turn_item(conversations["execute"], field="results", turn=turn)
    if generation_result is None:
        failure = _turn_item(conversations["generate"], field="turn_failures", turn=turn)
    elif execution_result is None:
        failure = _turn_item(conversations["execute"], field="turn_failures", turn=turn)
    else:
        failure = _turn_item(conversations["evaluate"], field="turn_failures", turn=turn)
    return (
        _failure_slot(
            task_id=task_id,
            turn=turn,
            generation_response_received=generation_response_received,
            failure=failure,
        ),
        list(conversations.values()),
    )


def _partial_requested_slot_metrics(conversations: list[dict[str, object]]) -> dict[str, object]:
    breakdowns = [
        slot
        for conversation in conversations
        for slot in conversation.get("turns_breakdown", [])
        if isinstance(slot, dict)
    ]
    passed_turns = sum(1 for slot in breakdowns if bool(slot.get("official_pass", False)))
    requested_turns = len(breakdowns)
    return {
        "requested_turns": requested_turns,
        "passed_turns": passed_turns,
        "turn_pass_rate": passed_turns / requested_turns if requested_turns else None,
        "five_turn_episode_count": 0,
        "five_turn_pass_count": 0,
        "tp_at_5": None,
        "cpt": None,
        "apr_num": 0,
        "apr_den": 0,
        "apr": None,
    }


def _paper_conversation_from_waves(
    *,
    task_id: str,
    provider: str,
    turns: list[int],
    waves: list[dict[str, dict[str, object]]],
) -> dict[str, object]:
    slots: list[dict[str, object]] = []
    stage_conversations: list[dict[str, object]] = []
    for turn, wave in zip(turns, waves, strict=True):
        slot, conversations = _paper_slot_from_wave(task_id=task_id, turn=turn, wave=wave)
        slots.append(slot)
        stage_conversations.extend(conversations)

    generation_responses = {
        int(slot["turn"]): bool(slot["generation_response_received"]) for slot in slots
    }
    turns_breakdown = breakdown_from_stage_results(
        turns_requested=turns,
        results=slots,
        generation_responses=generation_responses,
    )
    for breakdown, slot in zip(turns_breakdown, slots, strict=True):
        breakdown["reached"] = bool(slot.get("evaluation_completed", False))

    turn_failures = []
    for slot in slots:
        if bool(slot.get("official_pass", False)):
            continue
        turn_failures.append(
            {
                key: slot.get(key)
                for key in (
                    "turn",
                    "failure_reason",
                    "failure_bucket",
                    "failure_mode",
                    "official_pass",
                    "error",
                )
                if key in slot
            }
        )
    failure_buckets = Counter(
        str(item.get("failure_bucket") or "unknown") for item in turn_failures
    )
    failure_reasons = Counter(
        str(item.get("failure_reason") or "unknown") for item in turn_failures
    )
    failure_modes = Counter(str(item.get("failure_mode") or "unknown") for item in turn_failures)
    evaluation_complete = all(bool(slot.get("evaluation_completed", False)) for slot in slots)
    official_pass = bool(slots) and all(bool(slot.get("official_pass", False)) for slot in slots)
    blocked = any(item.get("failure_bucket") == "blocked" for item in turn_failures)
    completion_reason = (
        "blocked"
        if blocked
        else "pipeline_failed"
        if not evaluation_complete
        else "passed"
        if official_pass
        else "judge_failed"
    )
    metrics = (
        paper_reliability_metrics([{"turns_breakdown": turns_breakdown}])
        if turns == [1, 2, 3, 4, 5]
        else _partial_requested_slot_metrics([{"turns_breakdown": turns_breakdown}])
    )
    return {
        "task_id": task_id,
        "provider": provider,
        "turns_requested": turns,
        "turns_completed": [
            int(slot["turn"]) for slot in slots if bool(slot.get("evaluation_completed", False))
        ],
        "turns_accounted": list(turns),
        "turns_breakdown": turns_breakdown,
        "turn_failures": turn_failures,
        "official_pass": official_pass,
        "strict_pass": official_pass,
        "completed": evaluation_complete,
        "blocked": blocked,
        "evaluation_complete": evaluation_complete,
        "infra_failed": not evaluation_complete,
        "infra_failure_count": sum(
            1 for slot in slots if not bool(slot.get("evaluation_completed", False))
        ),
        "judge_failed_turn_count": sum(
            1
            for slot in slots
            if bool(slot.get("evaluation_completed", False))
            and not bool(slot.get("official_pass", False))
        ),
        "completion_reason": completion_reason,
        "failure_buckets": dict(sorted(failure_buckets.items())),
        "failure_reasons": dict(sorted(failure_reasons.items())),
        "failure_modes": dict(sorted(failure_modes.items())),
        "token_usage": summarize_component_token_usage(
            merge_component_token_usage(
                conversation.get("token_usage", {}) for conversation in stage_conversations
            )
        ),
        "results": slots,
        **metrics,
    }


def run_suite_all_stages(
    *,
    provider: str,
    turns: list[int] | None = None,
    limit: int | None = None,
    task_ids: list[str] | None = None,
    output_root: Path | None = None,
    resume: bool = False,
    run_id: str | None = None,
    generation_workers: int,
    execution_workers: int,
    evaluation_workers: int,
    model_runtime_config: ComponentRuntimeConfig | None = None,
    evaluator_config: ComponentRuntimeConfig | None = None,
    blind_actor_config: BlindActorRuntimeConfig | None = None,
) -> dict[str, object]:
    if resume and run_id is None:
        raise ValueError("resume requires an explicit pipeline run_id")
    pipeline_run_id = run_id or (
        f"pipeline-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}"
    )
    effective_task_ids, selected_turns = _all_stage_scope(
        turns=turns,
        limit=limit,
        task_ids=task_ids,
    )
    waves: list[dict[str, dict[str, object]]] = []
    for turn in selected_turns:
        wave: dict[str, dict[str, object]] = {}
        for stage, workers in (
            ("generate", generation_workers),
            ("execute", execution_workers),
            ("evaluate", evaluation_workers),
        ):
            wave[stage] = run_suite_stage(
                stage=stage,
                provider=provider,
                turns=[turn],
                task_ids=effective_task_ids,
                workers=workers,
                output_root=output_root,
                resume=resume,
                run_id=pipeline_run_id,
                model_runtime_config=model_runtime_config,
                evaluator_config=evaluator_config,
                blind_actor_config=blind_actor_config,
            )
        waves.append(wave)

    conversations = [
        _paper_conversation_from_waves(
            task_id=task_id,
            provider=provider,
            turns=selected_turns,
            waves=waves,
        )
        for task_id in effective_task_ids
    ]
    reliability_diagnostics: dict[str, object] | None = None
    if selected_turns == [1, 2, 3, 4, 5]:
        diagnostics_payload = paper_reliability_diagnostics(conversations)
        reliability = diagnostics_payload.pop("paper_metrics")
        reliability_diagnostics = diagnostics_payload
    else:
        reliability = _partial_requested_slot_metrics(conversations)
    all_stage_summaries = [summary for wave in waves for summary in wave.values()]
    summary: dict[str, object] = {
        "provider": provider,
        "run_id": pipeline_run_id,
        "turn": selected_turns[0] if len(selected_turns) == 1 else None,
        "turns": selected_turns,
        "task_count": len(effective_task_ids),
        "completed": all(bool(item.get("completed", False)) for item in conversations),
        "completed_task_count": sum(
            1 for item in conversations if bool(item.get("completed", False))
        ),
        "official_pass": bool(conversations)
        and all(bool(item.get("official_pass", False)) for item in conversations),
        **reliability,
        **(
            {"reliability_diagnostics": reliability_diagnostics}
            if reliability_diagnostics is not None
            else {}
        ),
        "stage_workers": {
            "generation": generation_workers,
            "execution": execution_workers,
            "evaluation": evaluation_workers,
        },
        "failure_buckets": _aggregate_failure_counts(conversations, "failure_buckets"),
        "failure_reasons": _aggregate_failure_counts(conversations, "failure_reasons"),
        "failure_modes": _aggregate_failure_counts(conversations, "failure_modes"),
        "token_usage": _merge_stage_token_usage(all_stage_summaries),
        "stage_summaries": {
            stage: [wave[stage] for wave in waves] for stage in ("generate", "execute", "evaluate")
        },
        "conversations": conversations,
    }
    resolved_output_root = resolve_output_root(output_root=output_root)
    for conversation in conversations:
        write_json_report(
            _conversation_report_path(str(conversation["task_id"]), resolved_output_root),
            conversation,
        )
    write_json_report(resolved_output_root / "reports" / "summary.json", summary)
    return summary


def _build_experiment_model_plan(
    *,
    config: JsonDict,
    resolved_config_path: Path,
    resolved_experiment_root: Path,
    model_id: str,
    resume: bool,
    overwrite: bool,
    stage: str,
    turns: list[int] | None,
    task_ids: list[str] | None = None,
) -> ExperimentModelPlan:
    return _build_experiment_model_plan_impl(
        config=config,
        resolved_config_path=resolved_config_path,
        resolved_experiment_root=resolved_experiment_root,
        model_id=model_id,
        resume=resume,
        overwrite=overwrite,
        stage=stage,
        turns=turns,
        task_ids=task_ids,
    )


def _pipeline_run_id_for_plan(
    plan: ExperimentModelPlan,
    *,
    current_request: dict[str, object],
) -> str:
    if not plan.resume:
        return f"pipeline-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}"

    request_path = plan.model_root / "request.json"
    if not request_path.exists():
        raise FileNotFoundError(
            f"Cannot resume model '{plan.model_id}': missing pipeline request {request_path}"
        )
    existing_request = _load_json_object_impl(request_path)
    pipeline_run_id = existing_request.get("pipeline_run_id")
    if not isinstance(pipeline_run_id, str) or not pipeline_run_id.strip():
        raise ValueError(
            f"Cannot resume model '{plan.model_id}': existing request has no pipeline_run_id. "
            "Start a new lineage with --overwrite."
        )
    for field in (
        "config_path",
        "config_sha256",
        "task_suite_sha256",
        "task_ids",
        "dataset",
        "runtime",
        "model",
        "evaluation_protocol_version",
        "pipeline_code_sha256",
    ):
        if existing_request.get(field) != current_request.get(field):
            raise ValueError(
                f"Cannot resume model '{plan.model_id}': pipeline request field '{field}' changed"
            )
    return pipeline_run_id


def _path_content_sha256(path_value: str | None) -> str | None:
    if path_value is None:
        return None
    path = Path(path_value)
    if not path.exists():
        raise FileNotFoundError(f"Dataset path does not exist: {path}")
    digest = hashlib.sha256()
    files = [path] if path.is_file() else sorted(item for item in path.rglob("*") if item.is_file())
    for file_path in files:
        relative = file_path.name if path.is_file() else file_path.relative_to(path).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _pipeline_code_sha256(repo_root: Path | None = None) -> str:
    """Hash code and dependency inputs that can change experiment behavior."""

    root = (repo_root or Path(__file__).resolve().parents[2]).resolve()
    included_directories = ("runner", "runtime", "scaffold")
    included_root_files = (
        ".yarnrc.yml",
        "package.json",
        "pyproject.toml",
        "uv.lock",
        "yarn.lock",
    )
    excluded_directories = {"__pycache__", ".git", "dist", "node_modules"}
    excluded_suffixes = {".pyc", ".pyo"}

    files: list[Path] = []
    for directory_name in included_directories:
        directory = root / directory_name
        if not directory.exists():
            continue
        files.extend(
            path
            for path in directory.rglob("*")
            if path.is_file()
            and not any(part in excluded_directories for part in path.relative_to(root).parts)
            and path.suffix not in excluded_suffixes
        )
    files.extend(path for name in included_root_files if (path := root / name).is_file())

    digest = hashlib.sha256()
    for file_path in sorted(files, key=lambda path: path.relative_to(root).as_posix()):
        digest.update(file_path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _run_experiment_model_plan(plan: ExperimentModelPlan) -> dict[str, object]:
    logger = ExperimentLogger(plan.model_root)
    with activate_experiment_logger(logger):
        _print_model_plan(plan)
        emit_cli(
            "experiment:model",
            format_mapping_block(
                "model plan paths",
                {
                    "model_root": str(plan.model_root),
                    "manifest": str(plan.model_root / "manifest.json"),
                    "request": str(plan.model_root / "request.json"),
                    "report": str(plan.model_root / "reports" / "summary.json"),
                    "logs_dir": str(plan.model_root / "logs"),
                },
            ),
        )
        _emit_runtime_config_summary(
            "experiment:model",
            title="runtime configuration",
            model_runtime_config=plan.model_runtime_config,
            evaluator_config=plan.evaluator_runtime_config,
            blind_actor_config=plan.blind_actor_runtime_config,
        )
        log_event(
            "model_plan_started",
            f"model {plan.model_id} started",
            model_id=plan.model_id,
            output_root=str(plan.model_root),
            stage=plan.stage,
            turns=plan.turns,
        )
        model_request = _model_request_payload(
            resolved_config_path=plan.resolved_config_path,
            request=plan.request,
            model_id=plan.model_id,
            provider=plan.provider,
            model_name=plan.model_name,
            resume=plan.resume,
            overwrite=plan.overwrite,
            stage=plan.stage,
            turns=plan.turns,
        )
        model_request["config_sha256"] = hashlib.sha256(
            plan.resolved_config_path.read_bytes()
        ).hexdigest()
        model_request["task_suite_sha256"] = _path_content_sha256(
            plan.env_values.get("GENUI_BENCH_TASKS_PATH")
        )
        model_request["task_ids"] = sorted(plan.task_ids) if plan.task_ids is not None else None
        model_request["evaluation_protocol_version"] = EVALUATION_PROTOCOL_VERSION
        model_request["pipeline_code_sha256"] = _pipeline_code_sha256()
        pipeline_run_id = _pipeline_run_id_for_plan(plan, current_request=model_request)
        model_request["pipeline_run_id"] = pipeline_run_id
        _write_experiment_manifest(
            plan.model_root,
            experiment_id=plan.experiment_id,
            command="experiment",
            provider=plan.provider,
            request=model_request,
        )

        with temporary_environment(plan.env_values):
            suite_summary = (
                run_suite_all_stages(
                    provider=plan.provider,
                    turns=plan.turns,
                    limit=plan.limit,
                    task_ids=plan.task_ids,
                    output_root=plan.model_root,
                    resume=plan.resume,
                    run_id=pipeline_run_id,
                    generation_workers=plan.concurrency.generation,
                    execution_workers=plan.concurrency.execution,
                    evaluation_workers=plan.concurrency.evaluation,
                    model_runtime_config=plan.model_runtime_config,
                    evaluator_config=plan.evaluator_runtime_config,
                    blind_actor_config=plan.blind_actor_runtime_config,
                )
                if plan.stage == "all"
                else run_suite_stage(
                    stage=plan.stage,
                    provider=plan.provider,
                    turns=plan.turns,
                    limit=plan.limit,
                    task_ids=plan.task_ids,
                    workers=plan.concurrency.for_stage(plan.stage),
                    output_root=plan.model_root,
                    resume=plan.resume,
                    run_id=pipeline_run_id,
                    model_runtime_config=plan.model_runtime_config,
                    evaluator_config=plan.evaluator_runtime_config,
                    blind_actor_config=plan.blind_actor_runtime_config,
                )
            )
        log_event(
            "model_plan_completed",
            f"model {plan.model_id} completed",
            model_id=plan.model_id,
            output_root=str(plan.model_root),
            stage=plan.stage,
        )
    return _experiment_model_summary_from_suite_summary(plan=plan, suite_summary=suite_summary)


def _experiment_model_summary_from_suite_summary(
    *, plan: ExperimentModelPlan, suite_summary: dict[str, object]
) -> dict[str, object]:
    return _experiment_model_summary_from_suite_summary_impl(
        plan=plan,
        suite_summary=suite_summary,
    )


def run_experiment_model(
    *,
    config_path: Path,
    experiment_root: Path,
    model_id: str,
    resume: bool = False,
    overwrite: bool = False,
    stage: str = "all",
    turns: list[int] | None,
    task_ids: list[str] | None = None,
) -> dict[str, object]:
    if resume and overwrite:
        raise ValueError("resume and overwrite cannot both be enabled")
    resolved_config_path = config_path.resolve()
    resolved_experiment_root = experiment_root.resolve()
    config = load_experiment_config(resolved_config_path)
    plan = _build_experiment_model_plan(
        config=config,
        resolved_config_path=resolved_config_path,
        resolved_experiment_root=resolved_experiment_root,
        model_id=model_id,
        resume=resume,
        overwrite=overwrite,
        stage=stage,
        turns=turns,
        task_ids=task_ids,
    )
    return _run_experiment_model_plan(plan)


def run_experiment(
    *,
    config_path: Path,
    output_root: Path | None = None,
    resume: bool = False,
    overwrite: bool = False,
    stage: str = "all",
    task_ids: list[str] | None = None,
    turns: list[int] | None = None,
) -> dict[str, object]:
    if resume and overwrite:
        raise ValueError("resume and overwrite cannot both be enabled")
    resolved_config_path = config_path.resolve()
    config = load_experiment_config(resolved_config_path)
    experiment_id = experiment_id_from_config(config, config_path=resolved_config_path)
    experiment_root = resolve_output_root(output_root=output_root, experiment_id=experiment_id)
    selected_turns = (
        list(turns)
        if turns is not None
        else _normalize_selected_turns(config["dataset"].get("turns"))
    )
    runtime = config["runtime"]
    parallel_models = bool(runtime.get("parallel_models", False))
    all_models = model_entries(config)
    if not resume and not overwrite and _existing_experiment_outputs(experiment_root):
        raise ExperimentConfigError(
            f"Output root already contains artifacts: {experiment_root}. "
            "Use --resume to reuse them, or pass --overwrite to rerun against the existing output directory."
        )
    logger = ExperimentLogger(experiment_root)
    with activate_experiment_logger(logger):
        _print_experiment_plan(
            experiment_id=experiment_id,
            config_path=resolved_config_path,
            experiment_root=experiment_root,
            stage=stage,
            resume=resume,
            overwrite=overwrite,
            turns=selected_turns,
            model_ids=[str(model["id"]) for model in all_models],
            task_ids=task_ids,
        )
        tasks_path = _resolved_dataset_path(
            resolved_config_path, config["dataset"].get("tasks_path")
        )
        if tasks_path:
            emit_cli("experiment", f"tasks_dir={tasks_path}", level="info")
        request = _initialize_experiment_root(
            config=config,
            resolved_config_path=resolved_config_path,
            experiment_root=experiment_root,
            resume=resume,
        )
        request["stage"] = stage
        request["turns"] = selected_turns
        request["overwrite"] = overwrite
        if task_ids is not None:
            request["task_ids"] = task_ids
        _write_experiment_manifest(
            experiment_root,
            experiment_id=experiment_id,
            command="experiment",
            provider="multi-model",
            request=request,
        )
        emit_cli(
            "experiment",
            format_mapping_block(
                "experiment paths",
                {
                    "experiment_root": str(experiment_root),
                    "manifest": str(experiment_root / "manifest.json"),
                    "request": str(experiment_root / "request.json"),
                    "comparison_summary": str(experiment_root / "comparison" / "summary.json"),
                    "logs_dir": str(experiment_root / "logs"),
                },
            ),
        )
        log_event(
            "experiment_started",
            f"experiment {experiment_id} started",
            experiment_id=experiment_id,
            output_root=str(experiment_root),
            stage=stage,
            turns=selected_turns,
        )
    model_plans = [
        _build_experiment_model_plan(
            config=config,
            resolved_config_path=resolved_config_path,
            resolved_experiment_root=experiment_root,
            model_id=str(model["id"]),
            resume=resume,
            overwrite=overwrite,
            stage=stage,
            turns=selected_turns,
            task_ids=task_ids,
        )
        for model in all_models
    ]
    plans_by_model_id = {plan.model_id: plan for plan in model_plans}

    model_summaries: list[dict[str, object]] = []

    with activate_experiment_logger(logger):
        if parallel_models and len(model_plans) > 1:
            # Run each model in its own subprocess for full isolation.
            # This avoids Chromium contention and env var conflicts.
            running_procs: list[tuple[subprocess.Popen, str, int]] = []
            for model_idx, plan in enumerate(model_plans, 1):
                emit_cli(
                    "experiment",
                    f"{model_idx}/{len(model_plans)} spawning model {plan.model_id} ...",
                    level="info",
                )
                env = os.environ.copy()
                env.update({k: str(v) for k, v in plan.env_values.items()})
                command_expr = (
                    "from pathlib import Path; from runner.orchestration import cli; "
                    f"cli.run_experiment_model(config_path=Path({str(resolved_config_path)!r}), "
                    f"experiment_root=Path({str(experiment_root)!r}), model_id={plan.model_id!r}, "
                    f"resume={resume!r}, overwrite={overwrite!r}, stage={stage!r}, turns={selected_turns!r}, task_ids={task_ids!r})"
                )
                cmd = [sys.executable, "-c", command_expr]
                proc = subprocess.Popen(
                    cmd,
                    cwd=ROOT_DIR,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                running_procs.append((proc, plan.model_id, model_idx))

            for proc, model_id, model_idx in running_procs:
                plan = plans_by_model_id[model_id]
                stdout, _ = proc.communicate(timeout=None)
                emit_cli(
                    "experiment",
                    format_log_block(
                        f"{model_idx}/{len(model_plans)} model {model_id} finished",
                        stdout,
                    ),
                )
                suite_json = (
                    plan.model_root / "reports" / "summary.json"
                    if stage == "all"
                    else _stage_summary_path(
                        plan.model_root,
                        stage,
                        turn=(
                            selected_turns[0]
                            if selected_turns is not None and len(selected_turns) == 1
                            else None
                        ),
                    )
                )
                if suite_json.exists():
                    suite_summary = json.loads(suite_json.read_text(encoding="utf-8"))
                    summary = _experiment_model_summary_from_suite_summary(
                        plan=plan, suite_summary=suite_summary
                    )
                    emit_cli(
                        "experiment",
                        f"{model_idx}/{len(model_plans)} model {model_id} -> "
                        f"{summary['completed_task_count']}/{summary['task_count']} tasks completed",
                        level="info",
                    )
                    model_summaries.append(summary)
                else:
                    emit_cli(
                        "experiment", f"WARNING: {model_id} summary report not found", level="info"
                    )
        else:
            for model_idx, plan in enumerate(model_plans, 1):
                emit_cli(
                    "experiment",
                    f"{model_idx}/{len(model_plans)} starting model {plan.model_id} ...",
                    level="info",
                )
                summary = _run_experiment_model_plan(plan)
                emit_cli(
                    "experiment",
                    f"{model_idx}/{len(model_plans)} model {plan.model_id} -> "
                    f"{summary['completed_task_count']}/{summary['task_count']} tasks completed",
                    level="info",
                )
                model_summaries.append(summary)

    comparison_summary = {
        "experiment_id": experiment_id,
        "config_path": str(resolved_config_path),
        "resume": resume,
        "overwrite": overwrite,
        "stage": stage,
        "turn": (
            selected_turns[0] if selected_turns is not None and len(selected_turns) == 1 else None
        ),
        "turns": selected_turns,
        "task_ids": task_ids,
        "output_root": str(experiment_root),
        "models": model_summaries,
    }
    token_usage_log = write_experiment_token_usage_log(experiment_root, comparison_summary)
    comparison_summary["token_usage"] = token_usage_log["token_usage"]
    comparison_summary["token_usage_log"] = token_usage_log["paths"]
    comparison_path = (
        experiment_root / "comparison" / "summary.json"
        if stage == "all"
        else experiment_root
        / "comparison"
        / "stages"
        / (
            f"{stage}-turn-{selected_turns[0]}.json"
            if selected_turns is not None and len(selected_turns) == 1
            else f"{stage}.json"
        )
    )
    write_json_report(comparison_path, comparison_summary)
    total_tasks = sum(m["task_count"] for m in model_summaries)
    total_completed = sum(m["completed_task_count"] for m in model_summaries)
    with activate_experiment_logger(logger):
        emit_cli(
            "experiment",
            f"DONE -> {total_completed}/{total_tasks} tasks completed across {len(model_summaries)} models",
            level="info",
        )
        _print_token_usage_summary(
            comparison_summary,
            log_path=str(token_usage_log["paths"]["latest"]),
        )
        log_event(
            "experiment_completed",
            f"experiment {experiment_id} completed",
            experiment_id=experiment_id,
            output_root=str(experiment_root),
            stage=stage,
            total_tasks=total_tasks,
            total_completed=total_completed,
        )
    return comparison_summary


def main() -> None:
    parser = argparse.ArgumentParser(prog="evogenui-bench")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--json", action="store_true")

    experiment_parser = subparsers.add_parser("experiment")
    experiment_parser.add_argument("--config", type=Path, required=True)
    experiment_parser.add_argument(
        "--stage",
        choices=["all", "generate", "execute", "evaluate"],
        default="all",
    )
    experiment_parser.add_argument("--output-root", type=Path)
    experiment_parser.add_argument(
        "--task-id",
        type=str,
        action="append",
        help="Run only the specified task ID (can be given multiple times)",
    )
    experiment_parser.add_argument(
        "--turn",
        type=int,
        action="append",
        help="Override dataset.turns (can be given multiple times, in execution order)",
    )
    experiment_parser.add_argument(
        "--verbose", action="store_true", help="Show full per-turn output"
    )
    experiment_parser.add_argument("--no-color", action="store_true", help="Disable colored output")
    run_mode_group = experiment_parser.add_mutually_exclusive_group()
    run_mode_group.add_argument("--resume", action="store_true")
    run_mode_group.add_argument("--overwrite", action="store_true")

    token_usage_parser = subparsers.add_parser("token-usage")
    token_usage_parser.add_argument(
        "path", type=Path, help="Experiment/model output root or report JSON file"
    )

    args = parser.parse_args()
    if args.command == "list":
        payload = {"tasks": load_task_entries()}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            for task_id in load_task_ids():
                print(task_id)
        return

    if args.command == "experiment":
        set_cli_verbose(args.verbose)
        if args.no_color:
            set_color_enabled(False)
        if not cli_verbose():
            os.environ.setdefault("BROWSER_USE_LOGGING_LEVEL", "result")
        payload = run_experiment(
            config_path=args.config,
            output_root=args.output_root,
            resume=args.resume,
            overwrite=args.overwrite,
            stage=args.stage,
            task_ids=args.task_id,
            turns=args.turn,
        )
        if cli_verbose():
            print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return

    if args.command == "token-usage":
        payload = collect_token_usage_report(args.path)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return


if __name__ == "__main__":
    main()
