from __future__ import annotations

import copy
import os
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path

from runner.tools.evaluation_inputs import (
    benchmark_request,
    copy_validation_contract,
)
from runner.tools.io_utils import read_json_file
from runner.tools.scaffold_info import build_file_tree_summary
from runtime.python_tool_environment import python_backend_description, python_backend_schemas
from runtime.types import JsonDict, ResourceDefinition, TaskDefinition, ToolDefinition

ROOT_DIR = Path(__file__).resolve().parents[2]
GENERATED_TASKS_DIR = ROOT_DIR / "bench" / "generated_tasks"


def resolve_multiturn_tasks_path() -> Path:
    configured_path = os.getenv("GENUI_BENCH_TASKS_PATH")
    if configured_path:
        path = Path(configured_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(
                f"Task source {path} does not exist. Set GENUI_BENCH_TASKS_PATH to a valid file or directory."
            )
        return path.resolve()
    if GENERATED_TASKS_DIR.exists():
        return GENERATED_TASKS_DIR
    raise FileNotFoundError(
        "Default multiturn tasks are missing. Populate bench/generated_tasks/*.json."
    )


def _read_json(path: Path) -> JsonDict:
    return read_json_file(path)


def _normalize_turn_payload(payload: object, *, turn_index: int) -> JsonDict:
    if not isinstance(payload, dict):
        raise ValueError("turn payload must be an object")

    turn = payload.get("turn")
    if turn is None:
        turn = payload.get("turn_index", turn_index)

    query = payload.get("prompt")
    if query is None:
        query = payload.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError(f"turn {turn_index} query/prompt must be a non-empty string")

    normalized: JsonDict = {
        "turn": int(turn),
        "prompt": query,
    }
    if "annotator_notes" in payload:
        normalized["annotator_notes"] = payload["annotator_notes"]
    if "metadata" in payload:
        normalized["metadata"] = payload["metadata"]

    tags = payload.get("query_tags")
    if tags is None:
        tags = payload.get("query_tag")
    if tags is not None:
        if not isinstance(tags, list):
            raise ValueError("query_tags/query_tag must be an array")
        normalized["query_tags"] = tags

    return normalized


def _normalize_task_payload(payload: object, *, source: Path) -> JsonDict:
    if not isinstance(payload, dict):
        raise ValueError(f"Task file {source} must be a JSON object")

    task_id_value = payload.get("task_id")
    if task_id_value is None:
        task_id_value = payload.get("episode_id")
    if not isinstance(task_id_value, str) or not task_id_value.strip():
        raise ValueError(f"Task file {source} is missing task_id/episode_id")

    turns = payload.get("turns")
    if not isinstance(turns, list) or not turns:
        raise ValueError(f"Task {task_id_value} must contain a non-empty turns array")

    normalized: JsonDict = {
        "task_id": task_id_value,
        "title": str(payload.get("title", task_id_value)),
        "turns": [
            _normalize_turn_payload(turn_payload, turn_index=index + 1)
            for index, turn_payload in enumerate(turns)
        ],
        "_source": source,
    }

    for key in (
        "domain",
        "suite",
        "difficulty",
        "core_interaction",
        "rubric",
        "tools",
        "resources",
        "initial_state",
        "actor_hints",
        "scenario_fixtures",
        "metadata",
    ):
        if key in payload:
            normalized[key] = payload[key]

    return normalized


def _task_entries_from_payload(payload: JsonDict, *, source: Path) -> list[JsonDict]:
    tasks = payload.get("tasks")
    if isinstance(tasks, list):
        if not tasks:
            raise ValueError(f"Task suite file {source} has an empty tasks array")
        return [_normalize_task_payload(task, source=source) for task in tasks]
    return [_normalize_task_payload(payload, source=source)]


def _task_entries_from_source(path: Path) -> list[JsonDict]:
    if path.is_dir():
        entries: list[JsonDict] = []
        for task_file in sorted(path.glob("*.json")):
            entries.extend(_task_entries_from_payload(_read_json(task_file), source=task_file))
        if not entries:
            raise ValueError(f"Task directory {path} does not contain any JSON task files")
        return entries

    if not path.exists():
        raise FileNotFoundError(
            f"Task source {path} does not exist. Set GENUI_BENCH_TASKS_PATH to a valid file or directory."
        )
    return _task_entries_from_payload(_read_json(path), source=path)


@lru_cache(maxsize=8)
def _cached_task_entries(path: str) -> tuple[JsonDict, ...]:
    return tuple(_task_entries_from_source(Path(path)))


def multiturn_tasks_source_label() -> str:
    path = resolve_multiturn_tasks_path()
    try:
        return path.relative_to(ROOT_DIR).as_posix()
    except ValueError:
        return str(path)


def load_multiturn_suite() -> JsonDict:
    path = resolve_multiturn_tasks_path().resolve()
    entries = _cached_task_entries(str(path))
    return {"tasks": copy.deepcopy(list(entries))}


def load_task_entries() -> list[JsonDict]:
    payload = load_multiturn_suite()
    entries = payload.get("tasks")
    if not entries:
        raise ValueError("multiturn task suite must contain a non-empty tasks array")
    if not isinstance(entries, list):
        raise ValueError("multiturn task suite must contain a non-empty tasks array")
    return entries


def load_task_ids() -> list[str]:
    path = resolve_multiturn_tasks_path().resolve()
    return [str(entry["task_id"]) for entry in _cached_task_entries(str(path))]


def _task_entry(task_id: str) -> JsonDict:
    for entry in load_task_entries():
        if entry.get("task_id") == task_id:
            return entry
    raise FileNotFoundError(f"Unknown multiturn task: {task_id}")


def _turn_entry(entry: JsonDict, turn: int) -> JsonDict:
    turns = entry.get("turns")
    if not isinstance(turns, list) or not turns:
        raise ValueError(f"Task {entry.get('task_id')} has no turns")
    for turn_entry in turns:
        if int(turn_entry.get("turn", -1)) == turn:
            return turn_entry
    raise ValueError(f"Task {entry.get('task_id')} has no turn {turn}")


def _declared_runtime_value(entry: JsonDict, turn_payload: JsonDict, key: str) -> object:
    if key in turn_payload:
        return turn_payload[key]
    return entry.get(key)


def _json_object(value: object, *, label: str) -> JsonDict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _string(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _tool_mock_contract(
    data: JsonDict, *, label: str
) -> tuple[bool, str, str | None, str, JsonDict, JsonDict]:
    raw_allowed = data.get("allowed", True)
    mock_contract = data.get("mock_contract")
    if mock_contract is None:
        raise ValueError(f"{label} mock_contract is required")

    if "handler" in data:
        raise ValueError(f"{label} top-level handler is no longer supported")
    contract = _json_object(mock_contract, label=f"{label} mock_contract")
    allowed = contract.get("allowed", raw_allowed)
    if not isinstance(allowed, bool):
        raise ValueError(f"{label} mock_contract.allowed must be a boolean")
    if "handler" in contract:
        raise ValueError(
            f"{label} mock_contract.handler is no longer supported; use fixture_id with a Python backend"
        )
    handler_ref = contract.get("handler_ref")
    if handler_ref is not None and not isinstance(handler_ref, str):
        raise ValueError(f"{label} mock_contract.handler_ref must be a string")
    fixture_id = contract.get("fixture_id")
    if not isinstance(fixture_id, str) or not fixture_id.strip():
        raise ValueError(f"{label} mock_contract.fixture_id is required for Python tools")
    raw_backend = contract.get("backend")
    if raw_backend is not None and raw_backend != "python":
        raise ValueError(
            f"{label} mock_contract.backend={raw_backend!r} is no longer supported; only python tools are supported"
        )
    semantic_contract = contract.get("semantic_contract", {})
    fixture_policy = contract.get("fixture_policy", {})
    if not isinstance(semantic_contract, dict):
        raise ValueError(f"{label} mock_contract.semantic_contract must be an object")
    if not isinstance(fixture_policy, dict):
        raise ValueError(f"{label} mock_contract.fixture_policy must be an object")
    return allowed, "python", handler_ref, fixture_id.strip(), semantic_contract, fixture_policy


def _tool_definition(payload: object) -> ToolDefinition:
    data = _json_object(payload, label="tool definition")
    name = _string(data.get("name"), label="tool definition name")
    (
        allowed,
        backend,
        handler_ref,
        fixture_id,
        semantic_contract,
        fixture_policy,
    ) = _tool_mock_contract(data, label="tool definition")
    duplicated_fields = [
        key for key in ("description", "api_schema", "input_schema", "output_schema") if key in data
    ]
    if duplicated_fields:
        duplicated = ", ".join(sorted(duplicated_fields))
        raise ValueError(
            "Python-backed tool definitions must not duplicate public contract fields "
            f"in task JSON: {duplicated}"
        )
    input_schema, output_schema = python_backend_schemas(fixture_id, name)
    return ToolDefinition(
        name=name,
        description=python_backend_description(fixture_id, name),
        input_schema=input_schema,
        output_schema=output_schema,
        mode=_string(data.get("mode"), label="tool definition mode"),
        allowed=allowed,
        backend=backend,
        handler_ref=handler_ref,
        fixture_id=fixture_id,
        semantic_contract=semantic_contract,
        fixture_policy=fixture_policy,
    )


def _mock_tool_definition(payload: object) -> ToolDefinition:
    data = _json_object(payload, label="mock tool definition")
    name = _string(data.get("name"), label="mock tool definition name")
    (
        allowed,
        backend,
        handler_ref,
        fixture_id,
        semantic_contract,
        fixture_policy,
    ) = _tool_mock_contract(data, label="mock tool definition")

    mode = str(data.get("mode", "read")).strip() or "read"
    if mode not in {"read", "write"}:
        raise ValueError("mock tool definition mode must be 'read' or 'write'")

    duplicated_fields = [
        key for key in ("description", "api_schema", "input_schema", "output_schema") if key in data
    ]
    if duplicated_fields:
        duplicated = ", ".join(sorted(duplicated_fields))
        raise ValueError(
            "Python-backed mock tool definitions must not duplicate public contract fields "
            f"in task JSON: {duplicated}"
        )
    input_schema, output_schema = python_backend_schemas(fixture_id, name)
    description = python_backend_description(fixture_id, name)

    return ToolDefinition(
        name=name,
        description=description,
        input_schema=input_schema,
        output_schema=output_schema,
        mode=mode,
        allowed=allowed,
        backend=backend,
        handler_ref=handler_ref,
        fixture_id=fixture_id,
        semantic_contract=semantic_contract,
        fixture_policy=fixture_policy,
    )


def _tool_payload_kind(payload: object) -> str:
    data = _json_object(payload, label="tool payload")
    if "input_schema" in data or "output_schema" in data:
        return "full"
    return "mock"


def _declared_actor_hints(entry: JsonDict, turn_payload: JsonDict) -> dict[str, JsonDict]:
    declared = _declared_runtime_value(entry, turn_payload, "actor_hints")
    if declared is None:
        return {}
    if not isinstance(declared, dict):
        raise ValueError("actor_hints must be an object")
    hints: dict[str, JsonDict] = {}
    for tool_name, hint in declared.items():
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError("actor_hints keys must be non-empty strings")
        if not isinstance(hint, dict):
            raise ValueError(f"actor_hints[{tool_name!r}] must be an object")
        hints[tool_name] = copy.deepcopy(hint)
    return hints


def _apply_actor_hints(tools: list[ToolDefinition], actor_hints: dict[str, JsonDict]) -> None:
    if not actor_hints:
        return
    by_name = {tool.name: tool for tool in tools}
    unknown = sorted(name for name in actor_hints if name not in by_name)
    if unknown:
        raise ValueError(f"actor_hints reference unknown tool names: {', '.join(unknown)}")
    for tool_name, hint in actor_hints.items():
        merged = copy.deepcopy(by_name[tool_name].fixture_policy)
        merged.update(copy.deepcopy(hint))
        by_name[tool_name].fixture_policy = merged


def _resource_definition(payload: object) -> ResourceDefinition:
    data = _json_object(payload, label="resource definition")
    return ResourceDefinition(
        uri=_string(data.get("uri"), label="resource definition uri"),
        name=_string(data.get("name"), label="resource definition name"),
        mime_type=_string(data.get("mime_type"), label="resource definition mime_type"),
        description=_string(data.get("description"), label="resource definition description"),
    )


def _tools(task_id: str, entry: JsonDict, turn_payload: JsonDict) -> list[ToolDefinition]:
    declared = _declared_runtime_value(entry, turn_payload, "tools")
    if declared is not None:
        if not isinstance(declared, list):
            raise ValueError(f"Task {task_id} tools must be an array")
        kinds = {_tool_payload_kind(item) for item in declared}
        if len(kinds) > 1:
            raise ValueError(
                f"Task {task_id} tools must use a single declaration style, not {sorted(kinds)}"
            )
        tool_kind = next(iter(kinds), "mock")
        parser = _tool_definition if tool_kind == "full" else _mock_tool_definition
        tools = [parser(item) for item in declared]
        _apply_actor_hints(tools, _declared_actor_hints(entry, turn_payload))
        return tools
    if _declared_actor_hints(entry, turn_payload):
        raise ValueError("actor_hints require tools")
    return []


def _resources(
    _task_id: str, _turn: int, entry: JsonDict, turn_payload: JsonDict
) -> list[ResourceDefinition]:
    declared = _declared_runtime_value(entry, turn_payload, "resources")
    if declared is not None:
        if not isinstance(declared, list):
            raise ValueError(f"Task {_task_id} turn {_turn} resources must be an array")
        return [_resource_definition(item) for item in declared]
    return []


def _scenario_fixtures(
    entry: JsonDict,
    turn_payload: JsonDict,
    *,
    tools: list[ToolDefinition],
    resources: list[ResourceDefinition],
) -> JsonDict:
    initial_state = _declared_runtime_value(entry, turn_payload, "initial_state")
    declared = _declared_runtime_value(entry, turn_payload, "scenario_fixtures")
    if declared is not None:
        if initial_state is not None:
            raise ValueError("initial_state cannot be combined with scenario_fixtures")
        return _json_object(declared, label="scenario_fixtures")

    declared_tools = _declared_runtime_value(entry, turn_payload, "tools")
    if declared_tools is not None:
        if not isinstance(declared_tools, list):
            raise ValueError("tools must be an array")
        kinds = {_tool_payload_kind(item) for item in declared_tools}
        if not kinds and initial_state is not None:
            raise ValueError("initial_state requires at least one mock tool")
        if len(kinds) > 1:
            raise ValueError(f"tools must use a single declaration style, not {sorted(kinds)}")
        if kinds == {"mock"}:
            if initial_state is None:
                initial_state = {}
            if not isinstance(initial_state, dict):
                raise ValueError("initial_state must be an object")

            return {
                "default": {
                    "tools": {},
                    "resources": {},
                    "initial_state": initial_state,
                }
            }
    elif initial_state is not None:
        raise ValueError("initial_state requires mock tools")

    has_declared_runtime = any(
        _declared_runtime_value(entry, turn_payload, key) is not None
        for key in ("tools", "resources", "actor_hints")
    )
    if has_declared_runtime and (tools or resources):
        raise ValueError(
            "Tasks that declare custom tools/resources must also provide scenario_fixtures for the "
            "benchmark runtime."
        )

    return {}


def _private_eval(
    entry: JsonDict,
    turn_payload: JsonDict,
    *,
    tools: list[ToolDefinition],
    resources: list[ResourceDefinition],
) -> JsonDict:
    task_id = str(entry["task_id"])
    turn = int(turn_payload["turn"])
    scenario_fixtures = _scenario_fixtures(entry, turn_payload, tools=tools, resources=resources)
    private_eval = {
        "scenario_fixtures": scenario_fixtures,
        "actor_goal": {
            "instructions": f"Evaluate turn {turn} of {entry.get('title', task_id)}.",
            "conversation_history": _compact_turn_prompts(entry["turns"], before_turn=turn),
            "current_user_request": benchmark_request(turn_payload["prompt"]),
        },
        "actor_budget": {
            "max_steps": 24,
            "max_time_seconds": 300,
            "max_stuck_steps": 4,
        },
    }
    validation_contract = _validation_contract(entry, turn_payload)
    validation_contract = copy_validation_contract(validation_contract)
    if validation_contract:
        private_eval["validation_contract"] = validation_contract
    return private_eval


def _metadata_validation_contract(metadata: object) -> object | None:
    if metadata is None:
        return None
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object when present")
    return metadata.get("validation_contract")


def _validation_contract(entry: JsonDict, turn_payload: JsonDict) -> JsonDict | None:
    current_turn = int(turn_payload["turn"])
    turn_contract = _metadata_validation_contract(turn_payload.get("metadata"))
    task_contract = _metadata_validation_contract(entry.get("metadata"))
    contract = turn_contract if turn_contract is not None else task_contract
    if contract is None:
        return None
    if not isinstance(contract, dict):
        raise ValueError("metadata.validation_contract must be an object")

    scenarios = contract.get("validation_scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError(
            "metadata.validation_contract.validation_scenarios must be a non-empty array"
        )

    normalized_scenarios: list[JsonDict] = []
    for index, scenario in enumerate(scenarios, start=1):
        if not isinstance(scenario, dict):
            raise ValueError(
                f"metadata.validation_contract.validation_scenarios[{index}] must be an object"
            )
        normalized: JsonDict = {}
        scenario_turn = scenario.get("turn")
        if not isinstance(scenario_turn, int) or scenario_turn < 1:
            raise ValueError(
                "metadata.validation_contract.validation_scenarios"
                f"[{index}].turn must be a positive integer"
            )
        if scenario_turn > current_turn:
            continue
        normalized["turn"] = scenario_turn
        for key in ("name", "public_requirement_ref", "oracle", "fairness_risk"):
            value = scenario.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    "metadata.validation_contract.validation_scenarios"
                    f"[{index}].{key} must be a non-empty string"
                )
            normalized[key] = value.strip()
        evidence_requirements = scenario.get("evidence_requirements")
        if evidence_requirements is not None:
            if not isinstance(evidence_requirements, list) or not evidence_requirements:
                raise ValueError(
                    "metadata.validation_contract.validation_scenarios"
                    f"[{index}].evidence_requirements must be a non-empty array when present"
                )
            normalized_requirements: list[JsonDict] = []
            for req_index, requirement in enumerate(evidence_requirements, start=1):
                if not isinstance(requirement, dict):
                    raise ValueError(
                        "metadata.validation_contract.validation_scenarios"
                        f"[{index}].evidence_requirements[{req_index}] must be an object"
                    )
                normalized_requirement: JsonDict = {}
                for req_key in ("id", "surface", "expect"):
                    req_value = requirement.get(req_key)
                    if not isinstance(req_value, str) or not req_value.strip():
                        raise ValueError(
                            "metadata.validation_contract.validation_scenarios"
                            f"[{index}].evidence_requirements[{req_index}].{req_key} "
                            "must be a non-empty string"
                        )
                    normalized_requirement[req_key] = req_value.strip()
                for req_key in ("action", "evidence"):
                    req_value = requirement.get(req_key)
                    if isinstance(req_value, str) and req_value.strip():
                        normalized_requirement[req_key] = req_value.strip()
                normalized_requirements.append(normalized_requirement)
            normalized["evidence_requirements"] = normalized_requirements
        normalized_scenarios.append(normalized)

    if not normalized_scenarios:
        return None
    return {"validation_scenarios": normalized_scenarios}


def _compact_turn_prompts(turns: object, *, before_turn: int) -> list[JsonDict]:
    if not isinstance(turns, list):
        return []
    compacted: list[JsonDict] = []
    for item in turns:
        if not isinstance(item, dict):
            continue
        turn = int(item.get("turn", 0))
        if turn <= 0 or turn >= before_turn:
            continue
        prompt = item.get("prompt", item.get("query", ""))
        compacted.append({"turn": turn, "prompt": benchmark_request(prompt)})
    return compacted


def _public_metadata(value: object) -> JsonDict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("turn metadata must be an object when present")
    metadata: JsonDict = {}
    public_note = value.get("public_note")
    if public_note is not None:
        metadata["public_note"] = public_note
    task_world = value.get("task_world")
    if isinstance(task_world, dict):
        public_task_world = {
            key: copy.deepcopy(task_world[key])
            for key in ("product_shape", "public_context")
            if key in task_world
        }
        if public_task_world:
            metadata["task_world"] = public_task_world
    return metadata or None


def _public_turn_payload(turn_payload: JsonDict) -> JsonDict:
    public_turn = dict(turn_payload)
    for private_key in (
        "actor_guidance",
        "handler",
        "initial_state",
        "actor_hints",
        "mock_capability_report",
        "mock_contract",
        "scenario_fixtures",
        "tools",
    ):
        public_turn.pop(private_key, None)
    metadata = _public_metadata(public_turn.get("metadata"))
    if metadata is None:
        public_turn.pop("metadata", None)
    else:
        public_turn["metadata"] = metadata
    return public_turn


def load_task(task_id: str, *, turn: int = 1) -> TaskDefinition:
    entry = _task_entry(task_id)
    turn_payload = _turn_entry(entry, turn)
    turns = list(entry["turns"])
    prompt = str(turn_payload["prompt"])
    tools = _tools(task_id, entry, turn_payload)
    resources = _resources(task_id, turn, entry, turn_payload)
    public_turns = [_public_turn_payload(item) for item in turns]
    scaffold_summary = build_file_tree_summary()
    public_task = {
        "task_id": task_id,
        "title": str(entry.get("title", task_id)),
        "user_prompt": prompt,
        "current_turn": _public_turn_payload(turn_payload),
        "turns": public_turns,
        "conversation_history": [item for item in public_turns if int(item.get("turn", 0)) <= turn],
        "tools": [tool.api_doc() for tool in tools],
        "resources": [asdict(resource) for resource in resources],
        "scaffold_context": scaffold_summary,
    }
    for public_key in ("suite", "domain", "difficulty", "core_interaction"):
        if public_key in entry:
            public_task[public_key] = entry[public_key]
    metadata = _public_metadata(entry.get("metadata"))
    if metadata is not None:
        public_task["metadata"] = metadata
    source_path = resolve_multiturn_tasks_path()
    task_dir = source_path if source_path.is_dir() else source_path.parent
    source_label = entry.get("_source")
    if isinstance(source_label, Path):
        try:
            resolved_source_label = source_label.relative_to(ROOT_DIR).as_posix()
        except ValueError:
            resolved_source_label = str(source_label)
    else:
        resolved_source_label = multiturn_tasks_source_label()

    return TaskDefinition(
        task_id=task_id,
        task_dir=task_dir,
        public_task=public_task,
        private_eval=_private_eval(entry, turn_payload, tools=tools, resources=resources),
        tools=tools,
        resources=resources,
        split="multiturn",
        scaffold_summary=scaffold_summary,
        metadata={
            "source": resolved_source_label,
            "turn": turn,
            "total_turns": len(turns),
            "suite": str(entry.get("suite", "")),
            "domain": str(entry.get("domain", "")),
        },
    )


def load_all_tasks() -> list[TaskDefinition]:
    tasks: list[TaskDefinition] = []
    for entry in load_task_entries():
        for turn_payload in entry["turns"]:
            tasks.append(load_task(str(entry["task_id"]), turn=int(turn_payload["turn"])))
    return tasks
