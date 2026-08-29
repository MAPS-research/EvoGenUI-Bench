from __future__ import annotations

import copy
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

JsonDict = dict[str, Any]


@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: JsonDict
    output_schema: JsonDict
    mode: str
    backend: str
    allowed: bool = True
    handler_ref: str | None = None
    fixture_id: str | None = None
    semantic_contract: JsonDict = field(default_factory=dict)
    fixture_policy: JsonDict = field(default_factory=dict)

    def api_doc(self) -> JsonDict:
        return {
            "name": self.name,
            "description": self.description,
            "mode": self.mode,
            "inputSchema": _public_json_schema(self.input_schema),
            "outputSchema": _public_json_schema(self.output_schema),
        }

    def runtime_contract(self, *, include_handler: bool = False) -> JsonDict:
        return {
            "name": self.name,
            "description": self.description,
            "mode": self.mode,
            "input_schema": copy.deepcopy(self.input_schema),
            "output_schema": copy.deepcopy(self.output_schema),
            "allowed": self.allowed,
            "backend": self.backend,
            "handler_ref": self.handler_ref,
            "fixture_id": self.fixture_id,
            "semantic_contract": copy.deepcopy(self.semantic_contract),
            "fixture_policy": copy.deepcopy(self.fixture_policy),
        }


@dataclass(slots=True)
class ResourceDefinition:
    uri: str
    name: str
    mime_type: str
    description: str


@dataclass(slots=True)
class TaskDefinition:
    task_id: str
    task_dir: Path
    public_task: JsonDict
    private_eval: JsonDict
    tools: list[ToolDefinition]
    resources: list[ResourceDefinition]
    split: str = "train"
    scaffold_summary: str = ""
    metadata: JsonDict = field(default_factory=dict)

    @property
    def title(self) -> str:
        return str(self.public_task.get("title", self.task_id))

    @property
    def user_prompt(self) -> str:
        return str(self.public_task.get("user_prompt", ""))

    @property
    def turn_index(self) -> int:
        return int(self.metadata.get("turn", 1))

    @property
    def total_turns(self) -> int:
        return int(self.metadata.get("total_turns", 1))


@dataclass(slots=True)
class BuildArtifacts:
    workspace_dir: Path
    build_dir: Path
    generated_files: list[Path]
    assistant_text_path: Path


@dataclass(slots=True)
class BuildResult:
    success: bool
    artifacts: BuildArtifacts | None
    stdout: str
    stderr: str
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PreviewHandle:
    process_id: int
    base_url: str
    port: int
    process: subprocess.Popen[str] | None = field(default=None, repr=False, compare=False)


@dataclass(slots=True)
class EvaluationResult:
    task_id: str
    turn: int
    generation_pass: bool
    build_pass: bool
    actor_pass: bool
    evaluator_pass: bool
    evaluator_score: float
    evaluator_summary: str
    dimensions: JsonDict
    official_pass: bool
    failure_reason: str | None
    details: JsonDict
    failure_bucket: str | None = None


def to_json_dict(value: object) -> JsonDict:
    if isinstance(value, ToolDefinition):
        return value.runtime_contract()
    return asdict(value)


def _public_json_schema(schema: JsonDict) -> JsonDict:
    cloned = copy.deepcopy(schema)

    def visit(value: object) -> None:
        if isinstance(value, dict):
            value.pop("x-genui-derived-enum", None)
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(cloned)
    return cloned
