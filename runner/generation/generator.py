from __future__ import annotations

import time
import uuid
from pathlib import Path

from runner.generation.prompt_payload import build_prompt_payload
from runner.orchestration.stage_reporting import stage_payload_from_bundle
from runner.orchestration.turn_bundle import (
    create_turn_bundle,
    load_turn_bundle,
    read_stage_payload,
)
from runner.orchestration.turn_stages import run_generation_stage
from runner.tools.experiment_config import ComponentRuntimeConfig
from runner.tools.paths import resolve_output_root
from runner.tools.task_loader import load_task
from runtime.types import JsonDict


def _generation_context_from_turn_payload(payload: JsonDict) -> JsonDict:
    generated_files = payload.get("generated_files")
    assistant_text = payload.get("assistant_text")
    context: JsonDict = {
        "turn": payload.get("turn"),
        "user_request": str(payload.get("user_request", "")),
        "assistant_text": str(assistant_text or ""),
        "generated_files": (dict(generated_files) if isinstance(generated_files, dict) else {}),
    }
    for field in ("final_ui", "runtime_state"):
        value = payload.get(field)
        if isinstance(value, dict):
            context[field] = value
    return context


def _load_saved_generation_contexts(
    task_id: str,
    *,
    turn: int,
    output_root: Path,
    run_id: str | None = None,
) -> list[JsonDict]:
    contexts: list[JsonDict] = []
    for previous_turn in range(1, turn):
        bundle = load_turn_bundle(
            output_root=output_root,
            task_id=task_id,
            turn=previous_turn,
            run_id=run_id,
            required_stage="execution",
        )
        output = read_stage_payload(bundle, "generation", "output.json")
        snapshot = read_stage_payload(bundle, "execution", "snapshot.json")
        context = _generation_context_from_turn_payload(
            {
                "turn": previous_turn,
                "user_request": snapshot.get("user_request", ""),
                "assistant_text": output.get("assistant_text", ""),
                "generated_files": output.get("files", {}),
                "final_ui": snapshot.get("final_ui"),
                "runtime_state": snapshot.get("runtime_state"),
            }
        )
        contexts.append(context)
    return contexts


def run_generate_turn(
    task_id: str,
    *,
    turn: int,
    provider: str,
    output_root: Path | None = None,
    run_id: str | None = None,
    previous_turns: list[JsonDict] | None = None,
    model_runtime_config: ComponentRuntimeConfig | None = None,
) -> dict[str, object]:
    resolved_output_root = resolve_output_root(output_root=output_root)
    task = load_task(task_id, turn=turn)
    prior_turns = (
        [
            _generation_context_from_turn_payload(item)
            for item in previous_turns
            if isinstance(item, dict)
        ]
        if previous_turns is not None
        else _load_saved_generation_contexts(
            task_id,
            turn=turn,
            output_root=resolved_output_root,
            run_id=run_id,
        )
    )
    if previous_turns is not None and len(prior_turns) != len(previous_turns):
        raise ValueError("previous_turns must contain only JSON objects")
    payload = build_prompt_payload(task, previous_turns=prior_turns)
    actual_run_id = (
        run_id or f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}"
    )
    bundle = create_turn_bundle(
        task,
        output_root=resolved_output_root,
        run_id=actual_run_id,
        provider=provider,
    )
    run_generation_stage(
        task,
        payload=payload,
        provider=provider,
        bundle=bundle,
        model_runtime_config=model_runtime_config,
    )
    return stage_payload_from_bundle(bundle, "generate")
