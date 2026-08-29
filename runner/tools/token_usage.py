from __future__ import annotations

from collections.abc import Iterable

from runtime.types import JsonDict

TOKEN_USAGE_COMPONENTS = ("tested_model", "evaluator", "blind_actor")


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, float):
        return max(int(value), 0)
    if isinstance(value, str) and value.strip():
        try:
            return max(int(value.strip()), 0)
        except ValueError:
            return 0
    return 0


def empty_token_usage() -> JsonDict:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "calls": 0,
    }


def _cached_input_tokens(value: dict[str, object]) -> int:
    prompt_details = value.get("prompt_tokens_details")
    if isinstance(prompt_details, dict):
        tokens = _non_negative_int(prompt_details.get("cached_tokens"))
        if tokens:
            return tokens

    input_details = value.get("input_tokens_details")
    if isinstance(input_details, dict):
        tokens = _non_negative_int(input_details.get("cached_tokens"))
        if tokens:
            return tokens

    return _non_negative_int(value.get("cached_input_tokens"))


def _reasoning_tokens(value: dict[str, object]) -> int:
    completion_details = value.get("completion_tokens_details")
    if isinstance(completion_details, dict):
        tokens = _non_negative_int(completion_details.get("reasoning_tokens"))
        if tokens:
            return tokens

    output_details = value.get("output_tokens_details")
    if isinstance(output_details, dict):
        tokens = _non_negative_int(output_details.get("reasoning_tokens"))
        if tokens:
            return tokens

    return _non_negative_int(value.get("reasoning_tokens"))


def normalize_token_usage(value: object, *, default_calls: int = 0) -> JsonDict:
    if not isinstance(value, dict):
        return empty_token_usage()

    input_tokens = _non_negative_int(value.get("input_tokens"))
    if input_tokens == 0:
        input_tokens = _non_negative_int(value.get("prompt_tokens"))

    output_tokens = _non_negative_int(value.get("output_tokens"))
    if output_tokens == 0:
        output_tokens = _non_negative_int(value.get("completion_tokens"))

    total_tokens = _non_negative_int(value.get("total_tokens"))
    if total_tokens == 0:
        total_tokens = input_tokens + output_tokens

    calls = _non_negative_int(value.get("calls"))
    if calls == 0 and total_tokens > 0:
        calls = max(default_calls, 1)

    normalized = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "calls": calls,
    }
    cached_input_tokens = _cached_input_tokens(value)
    normalized["cached_input_tokens"] = cached_input_tokens
    normalized["uncached_input_tokens"] = max(input_tokens - cached_input_tokens, 0)
    reasoning_tokens = _reasoning_tokens(value)
    if reasoning_tokens:
        normalized["reasoning_tokens"] = reasoning_tokens
    return normalized


def token_usage_from_provider_usage(value: object) -> JsonDict:
    return normalize_token_usage(value, default_calls=1)


def token_usage_from_meta(value: object) -> JsonDict:
    if not isinstance(value, dict):
        return empty_token_usage()
    token_usage = value.get("token_usage")
    if token_usage is not None:
        return normalize_token_usage(token_usage)
    usage = value.get("usage")
    if usage is not None:
        return token_usage_from_provider_usage(usage)
    return normalize_token_usage(value)


def add_token_usage(left: object, right: object) -> JsonDict:
    normalized_left = normalize_token_usage(left)
    normalized_right = normalize_token_usage(right)
    merged = {
        "input_tokens": normalized_left["input_tokens"] + normalized_right["input_tokens"],
        "output_tokens": normalized_left["output_tokens"] + normalized_right["output_tokens"],
        "total_tokens": normalized_left["total_tokens"] + normalized_right["total_tokens"],
        "calls": normalized_left["calls"] + normalized_right["calls"],
    }
    cached_input_tokens = _non_negative_int(
        normalized_left.get("cached_input_tokens")
    ) + _non_negative_int(normalized_right.get("cached_input_tokens"))
    merged["cached_input_tokens"] = cached_input_tokens
    merged["uncached_input_tokens"] = max(merged["input_tokens"] - cached_input_tokens, 0)

    reasoning_tokens = _non_negative_int(
        normalized_left.get("reasoning_tokens")
    ) + _non_negative_int(normalized_right.get("reasoning_tokens"))
    if reasoning_tokens:
        merged["reasoning_tokens"] = reasoning_tokens
    return merged


def empty_component_token_usage() -> JsonDict:
    return {component: empty_token_usage() for component in TOKEN_USAGE_COMPONENTS}


def normalize_component_token_usage(value: object) -> JsonDict:
    if not isinstance(value, dict):
        return empty_component_token_usage()
    return {
        component: normalize_token_usage(value.get(component, {}))
        for component in TOKEN_USAGE_COMPONENTS
    }


def merge_component_token_usage(values: Iterable[object]) -> JsonDict:
    totals = empty_component_token_usage()
    for value in values:
        normalized = normalize_component_token_usage(value)
        for component in TOKEN_USAGE_COMPONENTS:
            totals[component] = add_token_usage(totals[component], normalized[component])
    return totals


def summarize_component_token_usage(value: object) -> JsonDict:
    normalized = normalize_component_token_usage(value)
    total = empty_token_usage()
    for component in TOKEN_USAGE_COMPONENTS:
        total = add_token_usage(total, normalized[component])
    return {
        **normalized,
        "total": total,
    }
