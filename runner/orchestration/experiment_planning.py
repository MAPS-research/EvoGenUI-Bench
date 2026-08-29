from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from runner.orchestration.stage_reporting import stage_summary_path
from runner.tools.experiment_config import (
    BlindActorRuntimeConfig,
    ComponentRuntimeConfig,
    ConcurrencyConfig,
    ExperimentConfigError,
    build_model_environment,
    experiment_concurrency_config,
    experiment_id_from_config,
    experiment_request_payload,
    model_entries,
    model_output_root,
    resolve_blind_actor_runtime_config,
    resolve_evaluator_runtime_config,
    resolve_model_runtime_config,
)
from runner.tools.paths import ROOT_DIR
from runner.tools.reporting import write_json_report
from runtime.types import JsonDict

EXPERIMENT_MANIFEST_SCHEMA_VERSION = "2026-04-21"

_PAPER_RELIABILITY_SUMMARY_FIELDS = (
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
    "reliability_diagnostics",
)


@dataclass(slots=True)
class ExperimentModelPlan:
    experiment_id: str
    resolved_config_path: Path
    resolved_experiment_root: Path
    model_root: Path
    model_id: str
    provider: str
    model_name: str
    stage: str
    turns: list[int] | None
    limit: int | None
    task_ids: list[str] | None
    concurrency: ConcurrencyConfig
    resume: bool
    overwrite: bool
    request: dict[str, object]
    env_values: dict[str, str]
    model_runtime_config: ComponentRuntimeConfig
    evaluator_runtime_config: ComponentRuntimeConfig
    blind_actor_runtime_config: BlindActorRuntimeConfig


def model_request_payload(
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
    return {
        "config_path": str(resolved_config_path),
        "resume": resume,
        "overwrite": overwrite,
        "stage": stage,
        "turns": turns,
        "dataset": request["dataset"],
        "runtime": request["runtime"],
        "model": {
            "id": model_id,
            "provider": provider,
            "model": model_name,
        },
    }


def write_experiment_manifest(
    output_root: Path,
    *,
    experiment_id: str,
    command: str,
    provider: str,
    request: dict[str, object],
) -> None:
    manifest = {
        "schema_version": EXPERIMENT_MANIFEST_SCHEMA_VERSION,
        "experiment_id": experiment_id,
        "command": command,
        "provider": provider,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "paths": {
            "reports": "reports",
            "trajectories": "trajectories",
            "runs": "runs",
        },
    }
    write_json_report(output_root / "manifest.json", manifest)
    write_json_report(output_root / "request.json", request)


def resolved_dataset_path(config_path: Path, value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ExperimentConfigError("dataset.tasks_path must be a non-empty string when provided")
    path = Path(value)
    if not path.is_absolute():
        repo_relative_path = (ROOT_DIR / path).resolve()
        config_relative_path = (config_path.parent / path).resolve()
        path = repo_relative_path if repo_relative_path.exists() else config_relative_path
    return str(path)


def select_model_entry(
    config: JsonDict,
    *,
    provider: str | None = None,
    model_id: str | None = None,
) -> JsonDict:
    models = model_entries(config)
    if model_id is not None:
        for model in models:
            if str(model.get("id")) == model_id:
                if provider is not None and str(model.get("provider")) != provider:
                    raise ExperimentConfigError(
                        f"Model '{model_id}' does not use provider '{provider}'"
                    )
                return model
        raise ExperimentConfigError(f"No model with id '{model_id}' in the config")
    if provider is not None:
        matches = [model for model in models if str(model.get("provider")) == provider]
        if not matches:
            raise ExperimentConfigError(f"No model with provider '{provider}' in the config")
        if len(matches) > 1:
            raise ExperimentConfigError(
                f"Multiple models use provider '{provider}'. Explicit model id selection is required: "
                + ", ".join(str(model.get("id")) for model in matches)
            )
        return matches[0]
    if len(models) == 1:
        return models[0]
    raise ExperimentConfigError(
        "Config contains multiple models; explicit model id selection is required"
    )


def relative_output_path(path: Path, *, root: Path) -> str:
    return path.relative_to(root).as_posix()


def build_experiment_model_plan(
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
    runtime = config["runtime"]
    dataset = config["dataset"]
    if not isinstance(runtime, dict):
        raise ExperimentConfigError("runtime must be an object")
    if not isinstance(dataset, dict):
        raise ExperimentConfigError("dataset must be an object")
    request = experiment_request_payload(config, config_path=resolved_config_path, resume=resume)
    request["overwrite"] = overwrite
    request["stage"] = stage
    request["turns"] = turns
    model = select_model_entry(config, model_id=model_id)
    provider = str(model["provider"])
    model_name = str(model["model"])
    model_root = model_output_root(resolved_experiment_root, model_id)
    dataset_path = resolved_dataset_path(resolved_config_path, dataset.get("tasks_path"))
    model_runtime_config = resolve_model_runtime_config(config, model)
    evaluator_runtime_config = resolve_evaluator_runtime_config(config)
    blind_actor_runtime_config = resolve_blind_actor_runtime_config(config)
    env_values = build_model_environment(config, model)
    if dataset_path is not None:
        env_values["GENUI_BENCH_TASKS_PATH"] = dataset_path
    return ExperimentModelPlan(
        experiment_id=experiment_id_from_config(config, config_path=resolved_config_path),
        resolved_config_path=resolved_config_path,
        resolved_experiment_root=resolved_experiment_root,
        model_root=model_root,
        model_id=model_id,
        provider=provider,
        model_name=model_name,
        stage=stage,
        turns=turns,
        limit=dataset.get("limit"),
        task_ids=task_ids,
        concurrency=experiment_concurrency_config(config),
        resume=resume,
        overwrite=overwrite,
        request=request,
        env_values=env_values,
        model_runtime_config=model_runtime_config,
        evaluator_runtime_config=evaluator_runtime_config,
        blind_actor_runtime_config=blind_actor_runtime_config,
    )


def experiment_model_summary_from_suite_summary(
    *,
    plan: ExperimentModelPlan,
    suite_summary: dict[str, object],
) -> dict[str, object]:
    if plan.stage == "all":
        completed_task_count = int(suite_summary.get("completed_task_count", 0))
        completed = bool(suite_summary.get("completed", False))
        model_report_path = plan.model_root / "reports" / "summary.json"
    else:
        completed_task_count = int(suite_summary.get("completed_task_count", 0))
        completed = bool(suite_summary.get("completed", False))
        model_report_path = stage_summary_path(
            plan.model_root,
            plan.stage,
            turn=plan.turns[0] if plan.turns is not None and len(plan.turns) == 1 else None,
        )
    summary = {
        "id": plan.model_id,
        "provider": plan.provider,
        "model": plan.model_name,
        "stage": plan.stage,
        "turn": plan.turns[0] if plan.turns is not None and len(plan.turns) == 1 else None,
        "turns": plan.turns,
        "completed": completed,
        "task_count": suite_summary["task_count"],
        "completed_task_count": completed_task_count,
        "failure_buckets": suite_summary["failure_buckets"],
        "failure_reasons": suite_summary["failure_reasons"],
        "failure_modes": suite_summary.get("failure_modes", {}),
        "token_usage": suite_summary.get("token_usage", summarize_component_token_usage({})),
        "output_root": relative_output_path(plan.model_root, root=plan.resolved_experiment_root),
        "model_report": relative_output_path(
            model_report_path,
            root=plan.resolved_experiment_root,
        ),
    }
    for field in _PAPER_RELIABILITY_SUMMARY_FIELDS:
        if field in suite_summary:
            summary[field] = suite_summary[field]
    return summary


def summarize_component_token_usage(payload: dict[str, object]) -> dict[str, object]:
    from runner.tools.token_usage import (
        summarize_component_token_usage as _summarize_component_token_usage,
    )

    return _summarize_component_token_usage(payload)
