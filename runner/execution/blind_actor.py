from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from runner.execution.blind_actor_runtime import run_blind_actor_session
from runner.tools.experiment_config import BlindActorRuntimeConfig
from runtime.types import JsonDict, PreviewHandle, TaskDefinition


def _require_task_public_list(task: TaskDefinition, field: str) -> list[JsonDict]:
    value = task.public_task.get(field)
    if not isinstance(value, list):
        raise ValueError(f"task.public_task['{field}'] must be a list")
    normalized: list[JsonDict] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"task.public_task['{field}'][{index}] must be an object")
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(
                f"task.public_task['{field}'][{index}].name must be a non-empty string"
            )
        description = item.get("description")
        if description is not None and not isinstance(description, str):
            raise ValueError(
                f"task.public_task['{field}'][{index}].description must be a string when present"
            )
        normalized.append(
            {
                "name": name,
                "description": description if isinstance(description, str) else "",
            }
        )
    return normalized


def _actor_fixture_policy_hints(task: TaskDefinition) -> list[JsonDict]:
    hints: list[JsonDict] = []
    for tool in task.tools:
        if not isinstance(tool.fixture_policy, dict):
            continue
        actor_inputs = tool.fixture_policy.get("actor_inputs")
        if not isinstance(actor_inputs, dict) or not actor_inputs:
            continue
        support_boundary = tool.fixture_policy.get("support_boundary")
        hints.append(
            {
                "tool": tool.name,
                "actor_inputs": actor_inputs,
                "note": (
                    str(support_boundary).strip()
                    if isinstance(support_boundary, str) and support_boundary.strip()
                    else (
                        "Use these values when the user request leaves the corresponding "
                        "tool arguments unspecified."
                    )
                ),
            }
        )
    return hints


def run_blind_actor(
    task: TaskDefinition,
    preview: PreviewHandle,
    *,
    artifact_dir: Path,
    runtime_config: BlindActorRuntimeConfig,
) -> JsonDict:
    actor_dir = artifact_dir
    actor_dir.mkdir(parents=True, exist_ok=True)

    enhanced_eval = deepcopy(task.private_eval)
    existing_extra_context = enhanced_eval.get("extra_context")
    if existing_extra_context is not None and not isinstance(existing_extra_context, dict):
        raise ValueError("task.private_eval['extra_context'] must be an object when present")
    enhanced_eval["extra_context"] = dict(existing_extra_context or {})
    enhanced_eval["extra_context"]["available_tools"] = _require_task_public_list(task, "tools")
    enhanced_eval["extra_context"]["available_resources"] = _require_task_public_list(
        task, "resources"
    )
    fixture_policy_hints = _actor_fixture_policy_hints(task)
    if fixture_policy_hints:
        enhanced_eval["extra_context"]["fixture_policy_hints"] = fixture_policy_hints
    validation_contract = enhanced_eval.get("validation_contract")
    if validation_contract is not None:
        if not isinstance(validation_contract, dict):
            raise ValueError(
                "task.private_eval['validation_contract'] must be an object when present"
            )
        enhanced_eval["extra_context"]["validation_contract"] = validation_contract

    private_eval_path = actor_dir / "private_eval.json"
    private_eval_path.write_text(
        json.dumps(enhanced_eval, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    result = run_blind_actor_session(
        base_url=preview.base_url,
        private_eval=enhanced_eval,
        artifact_dir=actor_dir,
        runtime_config=runtime_config,
    )

    output_path = actor_dir / "result.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
