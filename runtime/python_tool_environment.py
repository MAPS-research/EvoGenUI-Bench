from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from importlib import import_module
from types import ModuleType
from typing import Any

from jsonschema import validate

from .types import JsonDict, ToolDefinition


def _json_clone(value: Any) -> Any:
    return copy.deepcopy(value)


def _backend_module_name(tool: ToolDefinition) -> str | None:
    if not tool.fixture_id:
        raise ValueError(f"Python-backed tool {tool.name} must declare fixture_id")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tool.fixture_id):
        raise ValueError(f"Invalid Python fixture_id: {tool.fixture_id}")
    return f"runtime.tool_backends.{tool.fixture_id}"


def load_python_backend_module(fixture_id: str) -> ModuleType:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", fixture_id):
        raise ValueError(f"Invalid Python fixture_id: {fixture_id}")
    return import_module(f"runtime.tool_backends.{fixture_id}")


def python_backend_tool_contract(fixture_id: str, tool_name: str) -> JsonDict:
    module = load_python_backend_module(fixture_id)
    schemas = getattr(module, "TOOL_SCHEMAS", None)
    if not isinstance(schemas, dict):
        raise ValueError(f"Python backend {fixture_id} must expose TOOL_SCHEMAS")
    tool_schemas = schemas.get(tool_name)
    if not isinstance(tool_schemas, dict):
        raise ValueError(f"Python backend {fixture_id} is missing schema for tool {tool_name}")
    input_schema = tool_schemas.get("inputSchema") or tool_schemas.get("input_schema")
    output_schema = tool_schemas.get("outputSchema") or tool_schemas.get("output_schema")
    if not isinstance(input_schema, dict) or not isinstance(output_schema, dict):
        raise ValueError(
            f"Python backend {fixture_id} schema for {tool_name} must include "
            "inputSchema and outputSchema objects"
        )
    description = tool_schemas.get("description")
    if description is not None and not isinstance(description, str):
        raise ValueError(
            f"Python backend {fixture_id} schema for {tool_name} must declare description "
            "as a string when present"
        )
    return {
        "description": copy.deepcopy(description),
        "inputSchema": copy.deepcopy(input_schema),
        "outputSchema": copy.deepcopy(output_schema),
    }


def python_backend_description(fixture_id: str, tool_name: str) -> str:
    contract = python_backend_tool_contract(fixture_id, tool_name)
    description = contract.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError(
            f"Python backend {fixture_id} schema for {tool_name} must include a non-empty "
            "description"
        )
    return description


def python_backend_schemas(fixture_id: str, tool_name: str) -> tuple[JsonDict, JsonDict]:
    contract = python_backend_tool_contract(fixture_id, tool_name)
    return copy.deepcopy(contract["inputSchema"]), copy.deepcopy(contract["outputSchema"])


def _python_result(tool: ToolDefinition, args: JsonDict, state: JsonDict) -> JsonDict:
    module_name = _backend_module_name(tool)
    module = import_module(module_name)
    invoke = getattr(module, "invoke", None)
    if not callable(invoke):
        raise ValueError(f"Python backend {module_name} must expose invoke(...)")
    result = invoke(tool=tool, args=copy.deepcopy(args), state=state)
    if not isinstance(result, dict):
        raise ValueError(f"Python backend {module_name} returned non-object result")
    return result


def _state_delta(before: JsonDict, after: JsonDict) -> JsonDict:
    delta: JsonDict = {}
    for key, value in after.items():
        if before.get(key) != value:
            delta[key] = _json_clone(value)
    return delta


@dataclass(slots=True)
class PythonToolEnvironment:
    tools: list[ToolDefinition]
    fixture_scenarios: JsonDict
    task_id: str
    turn: int
    initial_scenario_state: dict[str, JsonDict] = field(default_factory=dict)
    _scenario_state: dict[str, JsonDict] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._scenario_state = copy.deepcopy(self.initial_scenario_state)

    def _tool_map(self) -> dict[str, ToolDefinition]:
        return {tool.name: tool for tool in self.tools}

    def _scenario_fixture(self, scenario: str) -> JsonDict:
        payload = self.fixture_scenarios.get(scenario)
        if not isinstance(payload, dict):
            raise ValueError(f"Missing scenario fixture for scenario {scenario}")
        return payload

    def scenario_state(self, scenario: str = "default") -> JsonDict:
        if scenario not in self._scenario_state:
            self._scenario_state[scenario] = copy.deepcopy(
                self._scenario_fixture(scenario).get("initial_state", {})
            )
        return self._scenario_state[scenario]

    def call(self, name: str, args: JsonDict, scenario: str = "default") -> tuple[Any, JsonDict]:
        tool_map = self._tool_map()
        if name not in tool_map:
            raise ValueError(f"Unknown tool: {name}")
        tool = tool_map[name]
        if tool.backend != "python":
            raise ValueError(f"Tool {name} is not a Python-backed tool")
        if not tool.allowed:
            raise PermissionError(f"Tool is not allowed: {name}")

        validate(instance=args, schema=tool.input_schema)

        state = self.scenario_state(scenario)
        before = copy.deepcopy(state)
        result = _python_result(tool, args, state)

        if tool.mode == "write":
            state.setdefault("writes", []).append(
                {
                    "tool": name,
                    "args": copy.deepcopy(args),
                    "result": copy.deepcopy(result),
                }
            )
        state.setdefault("tool_trace", []).append(
            {
                "turn": self.turn,
                "tool": name,
                "args": copy.deepcopy(args),
                "result": copy.deepcopy(result),
            }
        )
        after = copy.deepcopy(state)

        validate(instance=result, schema=tool.output_schema)
        return result, {
            "state_before": before,
            "state_after": after,
            "state_delta": _state_delta(before, after),
            "relevant_final_state": {
                "writes": copy.deepcopy(after.get("writes", [])),
                "last_tool_trace": copy.deepcopy(after.get("tool_trace", [])[-5:]),
            },
            "tool_use_assessment": {
                "required_tools_called": True,
                "argument_grounding": [
                    {"tool": name, "status": "accepted", "fields": sorted(args.keys())}
                ],
            },
            "tool_quality": {
                "tool": name,
                "backend": "python",
                "handler_ref": tool.handler_ref,
                "status": "ok",
                "schema_valid": True,
                "result_size": len(result.get("flights", [])) if isinstance(result, dict) else None,
            },
        }

    def runtime_logs(self) -> JsonDict:
        return {
            "scenarios": {
                scenario: {
                    "initial_state": copy.deepcopy(fixture.get("initial_state", {})),
                    "state": copy.deepcopy(self.scenario_state(scenario)),
                }
                for scenario, fixture in self.fixture_scenarios.items()
                if isinstance(fixture, dict)
            }
        }
