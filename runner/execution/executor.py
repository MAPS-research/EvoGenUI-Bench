from __future__ import annotations

from pathlib import Path

from runner.orchestration.conversation_state import required_previous_turn_snapshots
from runner.orchestration.stage_reporting import stage_payload_from_bundle
from runner.orchestration.turn_bundle import load_turn_bundle, read_stage_payload
from runner.orchestration.turn_stages import run_execution_stage
from runner.tools.experiment_config import BlindActorRuntimeConfig
from runner.tools.paths import resolve_output_root
from runner.tools.task_loader import load_task
from runtime.types import JsonDict


def run_execute_turn(
    task_id: str,
    *,
    turn: int,
    output_root: Path | None = None,
    run_id: str | None = None,
    previous_turns: list[JsonDict] | None = None,
    blind_actor_config: BlindActorRuntimeConfig | None = None,
) -> dict[str, object]:
    resolved_output_root = resolve_output_root(output_root=output_root)
    task = load_task(task_id, turn=turn)
    bundle = load_turn_bundle(
        output_root=resolved_output_root,
        task_id=task_id,
        turn=turn,
        run_id=run_id,
        required_stage="generation",
    )
    generation_output = read_stage_payload(bundle, "generation", "output.json")
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
    run_execution_stage(
        task,
        generation_output=generation_output,
        bundle=bundle,
        blind_actor_config=blind_actor_config,
        previous_turns=prior_turns,
    )
    return stage_payload_from_bundle(bundle, "execute")
