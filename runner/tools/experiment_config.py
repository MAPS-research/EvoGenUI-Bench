from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator

import yaml

from runtime.types import JsonDict

from .env_utils import load_env_file
from .llm_client import ProviderConfig, RetryPolicy, TimeoutPolicy, is_openai_compatible_provider


class ExperimentConfigError(ValueError):
    pass


BLIND_ACTOR_BRIDGE_API_KEY_ENV = "GENUI_BLIND_ACTOR_BRIDGE_API_KEY"
_PROVIDER_API_KEY_ENV = {
    "openai-compatible": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini-native": "GEMINI_API_KEY",
}


def _require_dict(value: object, *, field: str) -> JsonDict:
    if not isinstance(value, dict):
        raise ExperimentConfigError(f"{field} must be an object")
    return value


def _require_list(value: object, *, field: str) -> list[object]:
    if not isinstance(value, list):
        raise ExperimentConfigError(f"{field} must be an array")
    return value


def _require_string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExperimentConfigError(f"{field} must be a non-empty string")
    return value.strip()


def _require_number(value: object, *, field: str) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    raise ExperimentConfigError(f"{field} must be a number")


def _require_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ExperimentConfigError(f"{field} must be an integer")
    return value


def _require_positive_int(value: object, *, field: str) -> int:
    normalized = _require_int(value, field=field)
    if normalized < 1:
        raise ExperimentConfigError(f"{field} must be >= 1")
    return normalized


def _required_env_from_name(name: str) -> str:
    load_env_file()
    value = os.getenv(name)
    if value is None or not value.strip():
        raise ExperimentConfigError(f"Environment variable {name} must be set")
    return value.strip()


def _api_key_env_names(
    component: JsonDict,
    *,
    field: str,
    api_key_env_field: str = "api_key_env",
) -> tuple[str, ...]:
    plural = component.get("api_key_envs")
    singular = component.get(api_key_env_field)
    if plural is not None:
        values = _require_list(plural, field=f"{field}.api_key_envs")
        if not values:
            raise ExperimentConfigError(f"{field}.api_key_envs must contain at least one entry")
        return tuple(
            _require_string(value, field=f"{field}.api_key_envs[{index}]")
            for index, value in enumerate(values)
        )
    return (_require_string(singular, field=f"{field}.{api_key_env_field}"),)


def _resolve_model_name(component: JsonDict, *, field: str) -> str:
    raw_model = component.get("model")
    if raw_model is not None:
        return _require_string(raw_model, field=f"{field}.model")
    model_env = component.get("model_env")
    if model_env is not None:
        return _required_env_from_name(_require_string(model_env, field=f"{field}.model_env"))
    raise ExperimentConfigError(f"{field}.model or {field}.model_env must be set")


@dataclass(slots=True)
class ComponentRuntimeConfig:
    provider_config: ProviderConfig
    retry_policy: RetryPolicy
    timeout_policy: TimeoutPolicy
    use_screenshot: bool = field(default=True, kw_only=True)
    include_source_code: bool = field(default=True, kw_only=True)
    generation_prompt_suffix: str | None = field(default=None, kw_only=True)
    generation_response_mode: str = field(default="text", kw_only=True)


@dataclass(slots=True)
class BrowserUseRuntimeOptions:
    use_vision: bool
    step_timeout_seconds: int
    wait_between_actions_seconds: float


@dataclass(slots=True)
class BlindActorRuntimeConfig(ComponentRuntimeConfig):
    read_image: bool
    browser_use: BrowserUseRuntimeOptions


@dataclass(slots=True)
class ConcurrencyConfig:
    generation: int
    execution: int
    evaluation: int

    def for_stage(self, stage: str) -> int:
        normalized = {
            "generate": "generation",
            "generation": "generation",
            "execute": "execution",
            "execution": "execution",
            "evaluate": "evaluation",
            "evaluation": "evaluation",
        }.get(stage)
        if normalized is None:
            raise ExperimentConfigError(f"Unsupported stage for concurrency: {stage}")
        return getattr(self, normalized)

    def to_dict(self) -> JsonDict:
        return {
            "generation": self.generation,
            "execution": self.execution,
            "evaluation": self.evaluation,
        }


@dataclass(slots=True)
class ExperimentRuntimeConfig:
    retry_policy: RetryPolicy
    timeout_policy: TimeoutPolicy
    prompt_cache_retention: str | None
    proxy: str | None = None


def _require_bool(value: object, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ExperimentConfigError(f"{field} must be a boolean")


def _browser_use_runtime_options(
    payload: object | None, *, field: str, read_image: bool
) -> BrowserUseRuntimeOptions:
    if payload is None:
        return BrowserUseRuntimeOptions(
            use_vision=read_image,
            step_timeout_seconds=180,
            wait_between_actions_seconds=1.0,
        )
    browser_use = _require_dict(payload, field=field)
    step_timeout = browser_use.get("step_timeout_seconds")
    wait_between_actions = browser_use.get("wait_between_actions_seconds")
    use_vision = browser_use.get("use_vision")
    return BrowserUseRuntimeOptions(
        use_vision=(
            read_image
            if use_vision is None
            else _require_bool(use_vision, field=f"{field}.use_vision")
        ),
        step_timeout_seconds=(
            180
            if step_timeout is None
            else _require_positive_int(step_timeout, field=f"{field}.step_timeout_seconds")
        ),
        wait_between_actions_seconds=(
            1.0
            if wait_between_actions is None
            else _require_number(
                wait_between_actions, field=f"{field}.wait_between_actions_seconds"
            )
        ),
    )


def _validate_dataset_turns(value: object) -> None:
    if value is None or value == "all":
        return
    if not isinstance(value, list) or not value:
        raise ExperimentConfigError(
            "dataset.turns must be 'all', null, or a non-empty array of integers"
        )
    seen: set[int] = set()
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or item < 1:
            raise ExperimentConfigError("dataset.turns must only contain integers >= 1")
        if item in seen:
            raise ExperimentConfigError("dataset.turns must not contain duplicate turn numbers")
        seen.add(item)


def _prompt_cache_retention(config: JsonDict) -> str | None:
    runtime = _require_dict(config["runtime"], field="runtime")
    value = runtime.get("prompt_cache_retention")
    if value is None:
        return None
    normalized = _require_string(value, field="runtime.prompt_cache_retention")
    if normalized not in {"in_memory", "24h"}:
        raise ExperimentConfigError(
            "runtime.prompt_cache_retention must be either 'in_memory' or '24h'"
        )
    return normalized


def _runtime_proxy(config: JsonDict) -> str | None:
    runtime = _require_dict(config["runtime"], field="runtime")
    value = runtime.get("proxy")
    if value is None:
        return None
    return _require_string(value, field="runtime.proxy")


def _generation_response_mode(component: JsonDict, *, field: str) -> str:
    if "response_mode" in component:
        raise ExperimentConfigError(
            f"{field}.response_mode is unsupported; use {field}.generation_response_mode"
        )
    value = component.get("generation_response_mode")
    if value is None:
        return "text"
    normalized = _require_string(value, field=f"{field}.generation_response_mode")
    if normalized not in {"text", "json_object", "json_schema"}:
        raise ExperimentConfigError(
            f"{field}.generation_response_mode must be text, json_object, or json_schema"
        )
    return normalized


def _runtime_retry_policy(config: JsonDict) -> RetryPolicy:
    runtime = _require_dict(config["runtime"], field="runtime")
    retry = _require_dict(runtime.get("retry"), field="runtime.retry")
    max_retries = _require_int(retry.get("max_retries"), field="runtime.retry.max_retries")
    backoff_seconds = _require_number(
        retry.get("backoff_seconds"), field="runtime.retry.backoff_seconds"
    )
    return RetryPolicy(
        max_retries=max(max_retries, 0),
        backoff_seconds=backoff_seconds,
    )


def _runtime_timeout_policy(config: JsonDict) -> TimeoutPolicy:
    runtime = _require_dict(config["runtime"], field="runtime")
    timeouts = _require_dict(runtime.get("timeouts"), field="runtime.timeouts")
    return TimeoutPolicy(
        connect_timeout_seconds=_require_number(
            timeouts.get("connect_timeout_seconds"),
            field="runtime.timeouts.connect_timeout_seconds",
        ),
        read_timeout_seconds=_require_number(
            timeouts.get("read_timeout_seconds"),
            field="runtime.timeouts.read_timeout_seconds",
        ),
        write_timeout_seconds=_require_number(
            timeouts.get("write_timeout_seconds"),
            field="runtime.timeouts.write_timeout_seconds",
        ),
        pool_timeout_seconds=_require_number(
            timeouts.get("pool_timeout_seconds"),
            field="runtime.timeouts.pool_timeout_seconds",
        ),
    )


def _component_retry_policy(component: JsonDict, *, field: str) -> RetryPolicy:
    retry_value = component.get("retry")
    if retry_value is None:
        raise ExperimentConfigError(f"{field}.retry is required")
    retry = _require_dict(retry_value, field=f"{field}.retry")
    max_retries = _require_int(retry.get("max_retries"), field=f"{field}.retry.max_retries")
    backoff_seconds = _require_number(
        retry.get("backoff_seconds"), field=f"{field}.retry.backoff_seconds"
    )
    return RetryPolicy(
        max_retries=max_retries,
        backoff_seconds=backoff_seconds,
    )


def _component_timeout_policy(component: JsonDict, *, field: str) -> TimeoutPolicy:
    timeouts_value = component.get("timeouts")
    if timeouts_value is None:
        raise ExperimentConfigError(f"{field}.timeouts is required")
    timeouts = _require_dict(timeouts_value, field=f"{field}.timeouts")
    return TimeoutPolicy(
        connect_timeout_seconds=_require_number(
            timeouts.get("connect_timeout_seconds"),
            field=f"{field}.timeouts.connect_timeout_seconds",
        ),
        read_timeout_seconds=_require_number(
            timeouts.get("read_timeout_seconds"),
            field=f"{field}.timeouts.read_timeout_seconds",
        ),
        write_timeout_seconds=_require_number(
            timeouts.get("write_timeout_seconds"),
            field=f"{field}.timeouts.write_timeout_seconds",
        ),
        pool_timeout_seconds=_require_number(
            timeouts.get("pool_timeout_seconds"),
            field=f"{field}.timeouts.pool_timeout_seconds",
        ),
    )


def experiment_runtime_config(config: JsonDict) -> ExperimentRuntimeConfig:
    return ExperimentRuntimeConfig(
        retry_policy=_runtime_retry_policy(config),
        timeout_policy=_runtime_timeout_policy(config),
        prompt_cache_retention=_prompt_cache_retention(config),
        proxy=_runtime_proxy(config),
    )


def _parse_concurrency(payload: object, *, field: str) -> ConcurrencyConfig:
    concurrency = _require_dict(payload, field=field)
    unknown = sorted(set(concurrency) - {"generation", "execution", "evaluation"})
    if unknown:
        raise ExperimentConfigError(f"{field} contains unsupported keys: {', '.join(unknown)}")
    return ConcurrencyConfig(
        generation=_require_positive_int(
            concurrency.get("generation"), field=f"{field}.generation"
        ),
        execution=_require_positive_int(concurrency.get("execution"), field=f"{field}.execution"),
        evaluation=_require_positive_int(
            concurrency.get("evaluation"), field=f"{field}.evaluation"
        ),
    )


def experiment_concurrency_config(config: JsonDict) -> ConcurrencyConfig:
    runtime = _require_dict(config["runtime"], field="runtime")
    return _parse_concurrency(runtime.get("concurrency"), field="runtime.concurrency")


def load_component_concurrency_config(
    path: Path, *, required_stage: str | None = None
) -> ConcurrencyConfig:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    component = _require_dict(payload, field="component runtime config")
    concurrency = _parse_concurrency(component.get("concurrency"), field="concurrency")
    if required_stage is not None:
        concurrency.for_stage(required_stage)
    return concurrency


def _provider_config_from_resolved_component(
    component: JsonDict,
    *,
    field: str,
    api_key: str,
    api_keys: tuple[str, ...] = (),
    runtime_defaults: ExperimentRuntimeConfig,
    prompt_cache_key: str | None = None,
) -> ComponentRuntimeConfig:
    provider = _require_string(component.get("provider"), field=f"{field}.provider").strip()
    retry_policy = _component_retry_policy(component, field=field)
    timeout_policy = _component_timeout_policy(component, field=field)
    provider_config = ProviderConfig(
        provider=provider,
        model=_resolve_model_name(component, field=field),
        api_key=api_key,
        base_url=_require_string(component.get("base_url"), field=f"{field}.base_url"),
        temperature=_require_number(component.get("temperature"), field=f"{field}.temperature"),
        output_token_limit=_require_int(component.get("max_tokens"), field=f"{field}.max_tokens"),
        reasoning_effort=_require_string(
            component.get("reasoning_effort"),
            field=f"{field}.reasoning_effort",
        ),
        prompt_cache_key=prompt_cache_key,
        prompt_cache_retention=runtime_defaults.prompt_cache_retention,
        retry_policy=retry_policy,
        timeout_policy=timeout_policy,
        extra_body=(
            {}
            if component.get("extra_body") is None
            else _require_dict(component.get("extra_body"), field=f"{field}.extra_body")
        ),
        proxy=runtime_defaults.proxy,
        api_keys=api_keys,
    )
    return ComponentRuntimeConfig(
        provider_config=provider_config,
        retry_policy=retry_policy,
        timeout_policy=timeout_policy,
        use_screenshot=(
            True
            if component.get("use_screenshot") is None
            else _require_bool(component.get("use_screenshot"), field=f"{field}.use_screenshot")
        ),
        include_source_code=(
            True
            if component.get("include_source_code") is None
            else _require_bool(
                component.get("include_source_code"),
                field=f"{field}.include_source_code",
            )
        ),
        generation_prompt_suffix=(
            None
            if component.get("generation_prompt_suffix") is None
            else _require_string(
                component.get("generation_prompt_suffix"),
                field=f"{field}.generation_prompt_suffix",
            )
        ),
        generation_response_mode=_generation_response_mode(component, field=field),
    )


def _provider_config_from_component(
    component: JsonDict,
    *,
    field: str,
    api_key_env_field: str = "api_key_env",
    runtime_defaults: ExperimentRuntimeConfig,
    prompt_cache_key: str | None = None,
) -> ComponentRuntimeConfig:
    api_key_envs = _api_key_env_names(
        component,
        field=field,
        api_key_env_field=api_key_env_field,
    )
    api_keys = tuple(_required_env_from_name(name) for name in api_key_envs)
    return _provider_config_from_resolved_component(
        component,
        field=field,
        api_key=api_keys[0],
        api_keys=api_keys,
        runtime_defaults=runtime_defaults,
        prompt_cache_key=prompt_cache_key,
    )


def load_component_runtime_config(path: Path) -> ComponentRuntimeConfig:
    """Load a standalone component runtime config from a YAML file.

    The YAML must contain exactly the same keys as a model/evaluator/blind_actor
    block in an experiment config (provider, model or model_env, base_url, api_key_env,
    temperature, max_tokens, reasoning_effort, retry, timeouts).
    """
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    component = _require_dict(payload, field="component runtime config")
    return _provider_config_from_component(
        component,
        field="component",
        runtime_defaults=ExperimentRuntimeConfig(
            retry_policy=RetryPolicy(max_retries=0, backoff_seconds=0.0),
            timeout_policy=TimeoutPolicy(
                connect_timeout_seconds=0.0,
                read_timeout_seconds=0.0,
                write_timeout_seconds=0.0,
                pool_timeout_seconds=0.0,
            ),
            prompt_cache_retention=None,
        ),
    )


def blind_actor_runtime_config_from_dict(payload: JsonDict) -> BlindActorRuntimeConfig:
    provider_config_payload = _require_dict(payload.get("provider_config"), field="provider_config")
    read_image = _require_bool(payload.get("read_image"), field="read_image")
    browser_use = _browser_use_runtime_options(
        payload.get("browser_use"), field="browser_use", read_image=read_image
    )
    api_key_value = provider_config_payload.get("api_key")
    api_key_values = provider_config_payload.get("api_keys")
    if api_key_value is None:
        api_key_envs = _api_key_env_names(
            provider_config_payload,
            field="provider_config",
        )
        api_keys = tuple(_required_env_from_name(name) for name in api_key_envs)
    else:
        api_key = _require_string(api_key_value, field="provider_config.api_key")
        if api_key_values is None:
            api_keys = (api_key,)
        else:
            api_keys = tuple(
                _require_string(value, field=f"provider_config.api_keys[{index}]")
                for index, value in enumerate(
                    _require_list(api_key_values, field="provider_config.api_keys")
                )
            )
            if not api_keys:
                api_keys = (api_key,)
            if api_key not in api_keys:
                api_keys = (api_key, *api_keys)
    component_payload: JsonDict = {
        "provider": provider_config_payload.get("provider"),
        "model": provider_config_payload.get("model"),
        "base_url": provider_config_payload.get("base_url"),
        "temperature": provider_config_payload.get("temperature"),
        "max_tokens": provider_config_payload.get("output_token_limit"),
        "reasoning_effort": provider_config_payload.get("reasoning_effort"),
        "extra_body": provider_config_payload.get("extra_body"),
        "retry": provider_config_payload.get("retry_policy"),
        "timeouts": provider_config_payload.get("timeout_policy"),
    }
    component_config = _provider_config_from_resolved_component(
        component_payload,
        field="provider_config",
        api_key=api_keys[0],
        api_keys=api_keys,
        runtime_defaults=ExperimentRuntimeConfig(
            retry_policy=RetryPolicy(max_retries=0, backoff_seconds=0.0),
            timeout_policy=TimeoutPolicy(
                connect_timeout_seconds=0.0,
                read_timeout_seconds=0.0,
                write_timeout_seconds=0.0,
                pool_timeout_seconds=0.0,
            ),
            prompt_cache_retention=(
                None
                if provider_config_payload.get("prompt_cache_retention") is None
                else _require_string(
                    provider_config_payload.get("prompt_cache_retention"),
                    field="provider_config.prompt_cache_retention",
                )
            ),
        ),
        prompt_cache_key=(
            None
            if provider_config_payload.get("prompt_cache_key") is None
            else _require_string(
                provider_config_payload.get("prompt_cache_key"),
                field="provider_config.prompt_cache_key",
            )
        ),
    )
    return BlindActorRuntimeConfig(
        provider_config=component_config.provider_config,
        retry_policy=component_config.retry_policy,
        timeout_policy=component_config.timeout_policy,
        read_image=read_image,
        browser_use=browser_use,
    )


def blind_actor_runtime_config_to_dict(
    runtime_config: BlindActorRuntimeConfig,
    *,
    api_key_env_var: str = BLIND_ACTOR_BRIDGE_API_KEY_ENV,
) -> JsonDict:
    payload = asdict(runtime_config)
    provider_config_payload = _require_dict(payload.get("provider_config"), field="provider_config")
    provider_config_payload.pop("api_key", None)
    provider_config_payload.pop("api_keys", None)
    provider_config_payload["api_key_env"] = api_key_env_var
    return payload


def load_experiment_config(path: Path) -> JsonDict:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    config = _require_dict(payload, field="experiment config")
    _require_dict(config.get("experiment"), field="experiment")
    dataset = _require_dict(config.get("dataset"), field="dataset")
    _require_dict(config.get("runtime"), field="runtime")
    _require_dict(config.get("evaluator"), field="evaluator")
    _require_dict(config.get("blind_actor"), field="blind_actor")
    models = _require_list(config.get("models"), field="models")
    if not models:
        raise ExperimentConfigError("models must contain at least one entry")
    for index, model in enumerate(models):
        _require_dict(model, field=f"models[{index}]")
    unknown_dataset_fields = sorted(set(dataset) - {"tasks_path", "limit", "turns"})
    if unknown_dataset_fields:
        raise ExperimentConfigError(
            "dataset contains unsupported fields: " + ", ".join(unknown_dataset_fields)
        )
    _validate_dataset_turns(dataset.get("turns"))
    experiment_concurrency_config(config)
    return config


def experiment_id_from_config(config: JsonDict, *, config_path: Path) -> str:
    experiment = _require_dict(config.get("experiment"), field="experiment")
    configured_id = experiment.get("id")
    if configured_id is None:
        raise ExperimentConfigError("experiment.id must be set in the config file")
    return _require_string(configured_id, field="experiment.id")


def model_entries(config: JsonDict) -> list[JsonDict]:
    return [
        _require_dict(item, field="model")
        for item in _require_list(config["models"], field="models")
    ]


def model_output_root(experiment_root: Path, model_id: str) -> Path:
    return experiment_root / "models" / model_id


def resolve_model_runtime_config(config: JsonDict, model: JsonDict) -> ComponentRuntimeConfig:
    runtime_defaults = experiment_runtime_config(config)
    provider = _require_string(model.get("provider"), field="model.provider").strip()
    if is_openai_compatible_provider(provider):
        return _provider_config_from_component(
            model,
            field="model",
            runtime_defaults=runtime_defaults,
            prompt_cache_key="genui-tested-model-generation-v1",
        )
    if provider == "anthropic":
        return _provider_config_from_component(
            model,
            field="model",
            runtime_defaults=runtime_defaults,
        )
    if provider == "gemini-native":
        return _provider_config_from_component(
            model,
            field="model",
            runtime_defaults=runtime_defaults,
        )
    raise ExperimentConfigError(f"Unsupported model provider: {provider}")


def resolve_evaluator_runtime_config(config: JsonDict) -> ComponentRuntimeConfig:
    runtime_defaults = experiment_runtime_config(config)
    evaluator = _require_dict(config["evaluator"], field="evaluator")
    return _provider_config_from_component(
        evaluator,
        field="evaluator",
        runtime_defaults=runtime_defaults,
        prompt_cache_key="genui-dimension-judge-v4",
    )


def resolve_blind_actor_runtime_config(config: JsonDict) -> BlindActorRuntimeConfig:
    runtime_defaults = experiment_runtime_config(config)
    blind_actor = _require_dict(config["blind_actor"], field="blind_actor")
    component_config = _provider_config_from_component(
        blind_actor,
        field="blind_actor",
        runtime_defaults=runtime_defaults,
        prompt_cache_key="genui-blind-actor-v1",
    )
    return BlindActorRuntimeConfig(
        provider_config=component_config.provider_config,
        retry_policy=component_config.retry_policy,
        timeout_policy=component_config.timeout_policy,
        read_image=_require_bool(blind_actor.get("read_image"), field="blind_actor.read_image"),
        browser_use=_browser_use_runtime_options(
            blind_actor.get("browser_use"),
            field="blind_actor.browser_use",
            read_image=_require_bool(blind_actor.get("read_image"), field="blind_actor.read_image"),
        ),
    )


def build_model_environment(config: JsonDict, model: JsonDict) -> dict[str, str]:
    env_values: dict[str, str] = {}
    for component_name, component in (
        ("model", model),
        ("evaluator", _require_dict(config["evaluator"], field="evaluator")),
        ("blind_actor", _require_dict(config["blind_actor"], field="blind_actor")),
    ):
        provider = _require_string(
            component.get("provider"), field=f"{component_name}.provider"
        ).strip()
        if provider not in _PROVIDER_API_KEY_ENV:
            raise ExperimentConfigError(f"Unsupported {component_name} provider: {provider}")
        for api_key_env in _api_key_env_names(component, field=component_name):
            env_values[api_key_env] = _required_env_from_name(api_key_env)
    return env_values


def experiment_request_payload(
    config: JsonDict, *, config_path: Path, resume: bool
) -> dict[str, object]:
    dataset = _require_dict(config["dataset"], field="dataset")
    runtime = _require_dict(config["runtime"], field="runtime")
    return {
        "config_path": str(config_path),
        "resume": resume,
        "dataset": {
            "tasks_path": dataset.get("tasks_path"),
            "limit": dataset.get("limit"),
            "turns": dataset.get("turns"),
        },
        "runtime": {
            "concurrency": experiment_concurrency_config(config).to_dict(),
            "parallel_models": bool(runtime.get("parallel_models", False)),
            "retry": runtime.get("retry", {}),
            "proxy": runtime.get("proxy"),
        },
        "models": [
            {
                "id": _require_string(model.get("id"), field="model.id"),
                "provider": _require_string(model.get("provider"), field="model.provider"),
                "model": _require_string(model.get("model"), field="model.model"),
            }
            for model in model_entries(config)
        ],
    }


@contextmanager
def temporary_environment(values: dict[str, str]) -> Iterator[None]:
    original = {key: os.environ.get(key) for key in values}
    for key, value in values.items():
        os.environ[key] = value
    try:
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
