from __future__ import annotations

from pathlib import Path

import pytest

from runner.tools.experiment_config import (
    ExperimentConfigError,
    _generation_response_mode,
    _parse_concurrency,
    load_experiment_config,
)
from runner.tools.llm_client import (
    LlmInput,
    LlmRequest,
    ProviderConfig,
    RetryPolicy,
    TimeoutPolicy,
    _openai_chat_payload,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _provider_config(*, model: str) -> ProviderConfig:
    return ProviderConfig(
        provider="openai-compatible",
        model=model,
        api_key="test-key",
        base_url="https://api.example.com/v1",
        temperature=0,
        output_token_limit=32768,
        reasoning_effort="none",
        prompt_cache_key=None,
        prompt_cache_retention=None,
        retry_policy=RetryPolicy(max_retries=0, backoff_seconds=0),
        timeout_policy=TimeoutPolicy(
            connect_timeout_seconds=30,
            read_timeout_seconds=1800,
            write_timeout_seconds=30,
            pool_timeout_seconds=30,
        ),
    )


def test_deprecated_response_mode_key_fails_instead_of_being_ignored() -> None:
    with pytest.raises(ExperimentConfigError, match="generation_response_mode"):
        _generation_response_mode(
            {"response_mode": "json_schema"},
            field="model",
        )


def test_unknown_concurrency_key_fails_instead_of_being_ignored() -> None:
    with pytest.raises(ExperimentConfigError, match="preprocessing"):
        _parse_concurrency(
            {
                "generation": 1,
                "execution": 1,
                "evaluation": 1,
                "preprocessing": 1,
            },
            field="runtime.concurrency",
        )


def test_public_example_config_is_valid_and_external_data_only() -> None:
    config = load_experiment_config(REPO_ROOT / "configs" / "example.yaml")

    assert config["dataset"]["tasks_path"] == "/absolute/path/to/external/tasks"
    assert config["dataset"]["limit"] == 1
    assert config["dataset"]["turns"] == [1]


def test_gpt55_requests_send_the_paper_temperature() -> None:
    body = _openai_chat_payload(
        LlmRequest(
            provider_config=_provider_config(model="gpt-5.5"),
            system_prompt="system",
            inputs=[LlmInput(type="text", text="user")],
            response_mode="text",
            component="tested_model",
        )
    )

    assert body["temperature"] == 0.0


def test_json_schema_generation_mode_reaches_the_wire_request() -> None:
    response_mode = _generation_response_mode(
        {"generation_response_mode": "json_schema"},
        field="model",
    )
    response_schema = {"type": "object"}

    body = _openai_chat_payload(
        LlmRequest(
            provider_config=_provider_config(model="glm-4.5-air"),
            system_prompt="system",
            inputs=[LlmInput(type="text", text="user")],
            response_mode=response_mode,  # type: ignore[arg-type]
            schema_name="code_output",
            response_schema=response_schema,
            component="tested_model",
        )
    )

    assert response_mode == "json_schema"
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "code_output",
            "schema": response_schema,
            "strict": True,
        },
    }
