from __future__ import annotations

from pathlib import Path

from runner.orchestration.conversation_state import required_previous_turn_snapshots
from runner.orchestration.stage_reporting import stage_payload_from_bundle
from runner.orchestration.turn_bundle import (
    load_turn_bundle,
    materialize_actor_result_for_runtime,
    materialize_snapshot_for_runtime,
    read_stage_payload,
)
from runner.orchestration.turn_stages import run_evaluation_stage
from runner.tools.experiment_config import ComponentRuntimeConfig
from runner.tools.paths import resolve_output_root
from runner.tools.task_loader import load_task
from runtime.types import JsonDict


def run_evaluate_turn(
    task_id: str,
    *,
    turn: int,
    output_root: Path | None = None,
    run_id: str | None = None,
    previous_turns: list[JsonDict] | None = None,
    evaluator_config: ComponentRuntimeConfig | None = None,
) -> dict[str, object]:
    resolved_output_root = resolve_output_root(output_root=output_root)
    task = load_task(task_id, turn=turn)
    bundle = load_turn_bundle(
        output_root=resolved_output_root,
        task_id=task_id,
        turn=turn,
        run_id=run_id,
        required_stage="execution",
    )
    actor_result = materialize_actor_result_for_runtime(
        bundle,
        read_stage_payload(bundle, "execution", "actor_result.json"),
    )
    current_snapshot = materialize_snapshot_for_runtime(
        bundle,
        read_stage_payload(bundle, "execution", "snapshot.json"),
    )
    prior_turns = (
        list(previous_turns)
        if previous_turns is not None
        else required_previous_turn_snapshots(
            task_id,
            turn=turn,
            output_root=resolved_output_root,
            run_id=run_id,
        )
    )
    run_evaluation_stage(
        task,
        actor_result=actor_result,
        current_snapshot=current_snapshot,
        previous_turns=prior_turns,
        bundle=bundle,
        evaluator_config=evaluator_config,
    )
    return stage_payload_from_bundle(bundle, "evaluate")
