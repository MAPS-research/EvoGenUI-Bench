from __future__ import annotations

from runner.tools.io_utils import truncate_text
from runtime.types import JsonDict, TaskDefinition

_PRIVATE_PROMPT_KEYS = {
    "api_schema",
    "actor_guidance",
    "actor_hints",
    "handler",
    "initial_state",
    "generation_guidance",
    "mock_capability_report",
    "mock_contract",
    "query_tags",
    "scenario_fixtures",
}
_PUBLIC_TOOL_FIELDS = ("name", "description", "mode", "inputSchema", "outputSchema")
_PUBLIC_RESOURCE_FIELDS = ("uri", "name", "mime_type", "description")


def _generation_guidance(public_task: JsonDict) -> list[str]:
    if public_task.get("suite") != "interactive_tool_ui":
        return []
    return [
        (
            "For visual or simulation tasks, render the primary surface with concrete "
            "task-specific objects or a skeuomorphic workbench. Task-relevant objects "
            "should be visible as objects in the UI, not only generic cards, tables, "
            "charts, or abstract boxes."
        ),
        (
            "If using a schematic, make object relationships explicit: paths, flows, "
            "rays, links, gauges, warning indicators, and readouts should be attached "
            "to the objects or process they describe."
        ),
        (
            "When the request implies stateful interaction, user-driven changes should "
            "update the primary visual and at least one derived readout, status, or "
            "warning."
        ),
    ]


def _public_contract(payload: JsonDict, fields: tuple[str, ...]) -> JsonDict:
    return {field: payload[field] for field in fields if field in payload}


def _public_contract_list(value: object, *, label: str, fields: tuple[str, ...]) -> list[JsonDict]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"task {label} must be an array")
    contracts: list[JsonDict] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"task {label} contracts must be objects")
        contracts.append(_public_contract(item, fields))
    return contracts


def _strip_private_prompt_keys(payload: JsonDict) -> JsonDict:
    return {key: value for key, value in payload.items() if key not in _PRIVATE_PROMPT_KEYS}


def _public_turn_payload(turn_payload: JsonDict) -> JsonDict:
    return _strip_private_prompt_keys(turn_payload)


def _turn_prompt_by_index(public_task: JsonDict) -> dict[int, str]:
    prompts: dict[int, str] = {}
    for item in public_task.get("conversation_history", []):
        if not isinstance(item, dict):
            continue
        try:
            turn = int(item.get("turn", 0))
        except (TypeError, ValueError):
            continue
        prompt = item.get("prompt") or item.get("query") or item.get("user_request")
        if isinstance(prompt, str) and prompt.strip():
            prompts[turn] = prompt
    return prompts


def _compact_previous_turns(
    previous_turns: list[JsonDict], public_task: JsonDict
) -> list[JsonDict]:
    fallback_prompts = _turn_prompt_by_index(public_task)
    compacted: list[JsonDict] = []
    for turn in previous_turns:
        if not isinstance(turn, dict):
            continue
        turn_index = turn.get("turn")
        try:
            fallback_prompt = fallback_prompts.get(int(turn_index))
        except (TypeError, ValueError):
            fallback_prompt = None
        user_request = turn.get("user_request")
        if not isinstance(user_request, str) or not user_request.strip():
            user_request = fallback_prompt or ""
        compact_turn: JsonDict = {
            "turn": turn_index,
            "user_request": user_request,
            "assistant_text": truncate_text(turn.get("assistant_text", ""), 1200),
        }
        final_ui = turn.get("final_ui")
        if isinstance(final_ui, dict):
            final_ui_text = truncate_text(final_ui.get("text", ""), 2000)
            if final_ui_text:
                compact_turn["final_ui_text"] = final_ui_text
        compacted.append(compact_turn)
    return compacted


def _previous_turn_source(previous_turns: list[JsonDict]) -> JsonDict | None:
    if not previous_turns:
        return None
    latest = previous_turns[-1]
    if not isinstance(latest, dict):
        return None
    generated_files = latest.get("generated_files")
    if not isinstance(generated_files, dict) or not generated_files:
        return None
    return {
        "turn": latest.get("turn"),
        "files": {str(path): str(contents) for path, contents in generated_files.items()},
    }


def _validated_previous_turns(
    task: TaskDefinition,
    previous_turns: list[JsonDict],
) -> list[JsonDict]:
    indexed: dict[int, JsonDict] = {}
    for item in previous_turns:
        if not isinstance(item, dict):
            raise ValueError("previous_turns must contain only JSON objects")
        raw_turn = item.get("turn")
        if isinstance(raw_turn, bool):
            raise ValueError("previous turn indices must be integers")
        try:
            turn_index = int(raw_turn)
        except (TypeError, ValueError) as exc:
            raise ValueError("previous turn indices must be integers") from exc
        if turn_index < 1 or turn_index >= task.turn_index:
            raise ValueError(
                f"previous turn {turn_index} is invalid for current turn {task.turn_index}"
            )
        if turn_index in indexed:
            raise ValueError(f"duplicate previous turn context: {turn_index}")
        indexed[turn_index] = item

    expected_turns = list(range(1, task.turn_index))
    missing_turns = [turn for turn in expected_turns if turn not in indexed]
    if missing_turns:
        missing = ", ".join(str(turn) for turn in missing_turns)
        raise FileNotFoundError(
            f"Missing prior generation context for task '{task.task_id}' turn(s) {missing}; "
            f"turn {task.turn_index} cannot restart from a clean state"
        )

    ordered = [indexed[turn] for turn in expected_turns]
    for previous_turn, context in zip(expected_turns, ordered, strict=True):
        generated_files = context.get("generated_files")
        if not isinstance(generated_files, dict) or not generated_files:
            raise FileNotFoundError(
                f"Missing prior generated source for task '{task.task_id}' turn {previous_turn}"
            )
        if not all(
            isinstance(path, str) and path.strip() and isinstance(contents, str)
            for path, contents in generated_files.items()
        ):
            raise ValueError(
                f"Prior generated source for task '{task.task_id}' turn {previous_turn} "
                "must map non-empty paths to text contents"
            )
        if not isinstance(context.get("final_ui"), dict):
            raise FileNotFoundError(
                f"Missing prior execution snapshot context for task '{task.task_id}' "
                f"turn {previous_turn}"
            )

    if task.tools and ordered and not isinstance(ordered[-1].get("runtime_state"), dict):
        raise FileNotFoundError(
            f"Missing prior runtime state for tool-grounded task '{task.task_id}' "
            f"turn {task.turn_index - 1}"
        )
    return ordered


def _prompt_task_context(
    task: TaskDefinition,
    *,
    include_conversation_history: bool = True,
) -> JsonDict:
    prompt_task = _strip_private_prompt_keys(task.public_task)
    guidance = _generation_guidance(prompt_task)
    prompt_task.pop("task_id", None)
    prompt_task.pop("title", None)
    prompt_task.pop("current_turn", None)
    prompt_task.pop("turns", None)
    prompt_task.pop("user_prompt", None)
    prompt_task.pop("suite", None)
    prompt_task.pop("domain", None)
    prompt_task.pop("difficulty", None)
    prompt_task.pop("core_interaction", None)
    prompt_task.pop("metadata", None)
    if guidance:
        prompt_task["generation_guidance"] = guidance
    prompt_task["tools"] = _public_contract_list(
        task.public_task.get("tools"),
        label="tools",
        fields=_PUBLIC_TOOL_FIELDS,
    )
    prompt_task["resources"] = _public_contract_list(
        task.public_task.get("resources"),
        label="resources",
        fields=_PUBLIC_RESOURCE_FIELDS,
    )
    conversation_history = task.public_task.get("conversation_history", [])
    if include_conversation_history and isinstance(conversation_history, list):
        prompt_task["conversation_history"] = [
            _public_turn_payload(item)
            for item in conversation_history
            if isinstance(item, dict) and int(item.get("turn", 0)) < task.turn_index
        ]
    else:
        prompt_task.pop("conversation_history", None)
    current_turn = task.public_task.get("current_turn")
    if isinstance(current_turn, dict):
        prompt_current_turn = _public_turn_payload(current_turn)
        prompt_current_turn.pop("prompt", None)
        prompt_current_turn.pop("turn", None)
        prompt_current_turn.pop("turn_index", None)
        if prompt_current_turn:
            prompt_task["current_turn"] = prompt_current_turn
    return prompt_task


def build_prompt_payload(
    task: TaskDefinition,
    *,
    previous_turns: list[JsonDict] | None = None,
) -> JsonDict:
    raw_previous_turns = _validated_previous_turns(task, list(previous_turns or []))
    return {
        "current_user_request": task.user_prompt,
        "task": _prompt_task_context(
            task,
            include_conversation_history=not raw_previous_turns,
        ),
        "previous_turns": _compact_previous_turns(raw_previous_turns, task.public_task),
        "previous_turn_source": _previous_turn_source(raw_previous_turns),
    }
