from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Protocol

from jsonschema import validate

from runner.generation.prompts import generation_system_prompt
from runner.tools.experiment_config import ComponentRuntimeConfig
from runner.tools.llm_client import (
    LlmInput,
    LlmRequest,
    LlmRequestError,
    LlmResponseError,
    call_llm,
)
from runner.tools.schemas import CODE_OUTPUT_SCHEMA
from runtime.types import JsonDict

ROOT_DIR = Path(__file__).resolve().parents[2]
_FILE_BLOCK_RE = re.compile(r"(?ms)^#{2,6}\s+`([^`\n]+)`\s*\n```[^\n]*\n(.*?)\n```")
_GEMINI_NATIVE_PROVIDER = "gemini-native"
_GEMINI_CODE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "contents": {"type": "string"},
                },
                "required": ["path", "contents"],
            },
        },
        "assistant_text": {"type": "string"},
    },
    "required": ["files", "assistant_text"],
}


ProviderRequestError = LlmRequestError
ProviderResponseError = LlmResponseError


def _decode_generation_output(payload: Any) -> JsonDict:
    if not isinstance(payload, dict):
        raise ValueError("Model output must be a JSON object")
    if "files" in payload:
        files = payload.get("files")
        if isinstance(files, list):
            files = _decode_file_entries(files)
        return {
            "files": files,
            "assistant_text": payload.get("assistant_text"),
        }
    raise ValueError("Model output must include files")


def _decode_file_entries(entries: list[Any]) -> dict[str, str]:
    files: dict[str, str] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"Model output files[{index}] must be an object")
        path = entry.get("path")
        contents = entry.get("contents")
        if not isinstance(path, str) or not path:
            raise ValueError(f"Model output files[{index}].path must be a non-empty string")
        if not isinstance(contents, str):
            raise ValueError(f"Model output files[{index}].contents must be a string")
        files[path] = contents
    return files


def _parse_generation_message(message: Any) -> JsonDict:
    if not isinstance(message, str):
        raise ValueError("Provider message content must be a string")
    try:
        parsed = json.loads(message)
    except json.JSONDecodeError as exc:
        markdown_output = _parse_markdown_generation_message(message)
        if markdown_output is not None:
            return markdown_output
        detail = f"{exc.msg} at line {exc.lineno} column {exc.colno} (char {exc.pos})"
        detail += "; no markdown file blocks were found"
        raise ValueError(f"Provider returned invalid generation transport: {detail}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Provider JSON transport must be an object")
    return parsed


def _parse_markdown_generation_message(message: str) -> JsonDict | None:
    matches = list(_FILE_BLOCK_RE.finditer(message))
    if not matches:
        return None

    files: dict[str, str] = {}
    for match in matches:
        path = match.group(1).strip()
        contents = match.group(2)
        if path:
            files[path] = contents

    assistant_text = _extract_markdown_assistant_text(message, matches[0].start())
    return {
        "files": files,
        "assistant_text": assistant_text,
    }


def _extract_markdown_assistant_text(message: str, first_file_start: int) -> str:
    text = message[:first_file_start].strip()
    text = re.sub(r"(?im)^#{1,6}\s*assistant[_\s-]*text\s*$", "", text).strip()
    text = re.sub(r"(?im)^assistant[_\s-]*text\s*:\s*", "", text).strip()
    return text


class ModelProvider(Protocol):
    def generate(self, payload: JsonDict) -> tuple[JsonDict, JsonDict]: ...


class OpenAICompatibleProvider:
    def __init__(self, config: ComponentRuntimeConfig) -> None:
        self.config = config
        self.model = config.provider_config.model
        self.api_key = config.provider_config.api_key
        self.base_url = config.provider_config.base_url
        self.temperature = config.provider_config.temperature
        self.max_tokens = config.provider_config.output_token_limit
        self.reasoning_effort = config.provider_config.reasoning_effort

    def generate(self, payload: JsonDict) -> tuple[JsonDict, JsonDict]:
        system_prompt = generation_system_prompt()
        if self.config.generation_prompt_suffix:
            system_prompt = f"{system_prompt}\n\n{self.config.generation_prompt_suffix}"
        response_mode = self.config.generation_response_mode
        response_schema = _generation_response_schema(
            provider=self.config.provider_config.provider,
            response_mode=response_mode,
        )
        response = call_llm(
            LlmRequest(
                provider_config=self.config.provider_config,
                system_prompt=system_prompt,
                inputs=[
                    LlmInput(
                        type="text",
                        text=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    )
                ],
                response_mode=response_mode,  # type: ignore[arg-type]
                schema_name="code_output" if response_mode == "json_schema" else None,
                response_schema=response_schema,
                component="tested_model",
            )
        )
        try:
            payload = response.parsed_json if response.parsed_json is not None else response.content_text
            output = normalize_generation_output(
                _decode_generation_output(
                    payload if isinstance(payload, dict) else _parse_generation_message(payload)
                )
            )
            validate(instance=output, schema=CODE_OUTPUT_SCHEMA)
        except Exception as exc:
            raise ProviderResponseError(
                f"Provider returned an unusable generation response: {exc}",
                endpoint=response.endpoint_family,
                usage=response.usage,
                raw_response=response.raw_response,
                content_text=response.content_text,
            ) from exc
        return output, response.to_meta()


def _generation_response_schema(*, provider: str, response_mode: str) -> JsonDict | None:
    if response_mode != "json_schema":
        return None
    if provider.strip() == _GEMINI_NATIVE_PROVIDER:
        return _GEMINI_CODE_OUTPUT_SCHEMA
    return CODE_OUTPUT_SCHEMA


def _normalize_required_non_empty_string(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty string")
    return normalized


def sanitize_output_files(files: Any) -> dict[str, str]:
    if not isinstance(files, dict):
        raise ValueError("Model output files must be an object")
    normalized_files: dict[str, str] = {}
    for relative_path, contents in files.items():
        if not isinstance(relative_path, str) or not relative_path:
            raise ValueError("Model output file paths must be non-empty strings")
        if not isinstance(contents, str):
            raise ValueError(f"Model output file {relative_path} must contain a string payload")
        normalized_files[relative_path] = contents
    if "src/App.tsx" not in normalized_files:
        raise ValueError("Model output must provide src/App.tsx")
    return normalized_files


def normalize_generation_output(output: Any) -> JsonDict:
    if not isinstance(output, dict):
        raise ValueError("Model output must be a JSON object")
    return {
        "files": sanitize_output_files(output.get("files")),
        "assistant_text": _normalize_required_non_empty_string(
            output.get("assistant_text"), field="assistant_text"
        ),
    }


def run_generation(
    payload: JsonDict,
    *,
    runtime_config: ComponentRuntimeConfig,
) -> tuple[JsonDict, JsonDict]:
    if runtime_config is None:
        raise ValueError("runtime_config is required for generation")
    provider = OpenAICompatibleProvider(runtime_config)
    return provider.generate(payload)
