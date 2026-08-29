from __future__ import annotations

from pathlib import Path

from runner.execution.blind_actor import run_blind_actor
from runner.execution.multiturn_state import build_turn_snapshot
from runner.tools.experiment_config import BlindActorRuntimeConfig
from runtime.types import BuildArtifacts, JsonDict, PreviewHandle, TaskDefinition


def run_actor_and_capture_snapshot(
    task: TaskDefinition,
    preview: PreviewHandle,
    artifacts: BuildArtifacts,
    *,
    blind_actor_config: BlindActorRuntimeConfig,
    artifact_dir: Path | None = None,
) -> tuple[JsonDict, JsonDict]:
    actor_result = run_blind_actor(
        task,
        preview,
        artifact_dir=artifact_dir or artifacts.workspace_dir / "generated" / "actor",
        runtime_config=blind_actor_config,
    )
    current_snapshot = build_turn_snapshot(task, artifacts, actor_result)
    return actor_result, current_snapshot
