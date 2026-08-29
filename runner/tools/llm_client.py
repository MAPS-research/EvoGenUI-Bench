from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import json
import random
import sys
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Literal

import httpx

from runtime.types import JsonDict

from .token_usage import token_usage_from_provider_usage

_TRANSIENT_HTTP_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}
_OPENAI_BASE_URL = "https://api.openai.com/v1"
_OPENAI_REASONING_MODEL_PREFIXES = ("gpt-5", "o1", "o3", "o4")
_OPENAI_COMPATIBLE_PROVIDERS = {"openai-compatible"}
_GEMINI_NATIVE_PROVIDER = "gemini-native"


def is_openai_compatible_provider(provider: str) -> bool:
    return provider in _OPENAI_COMPATIBLE_PROVIDERS


@dataclass(slots=True)
class RetryPolicy:
    max_retries: int
    backoff_seconds: float


@dataclass(slots=True)
class TimeoutPolicy:
    connect_timeout_seconds: float
    read_timeout_seconds: float
    write_timeout_seconds: float
    pool_timeout_seconds: float

    def to_httpx_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.connect_timeout_seconds,
            read=self.read_timeout_seconds,
            write=self.write_timeout_seconds,
            pool=self.pool_timeout_seconds,
        )


@dataclass(slots=True)
class ProviderConfig:
    provider: str
    model: str
    api_key: str
    base_url: str
    temperature: float
    output_token_limit: int
    reasoning_effort: str
    prompt_cache_key: str | None
    prompt_cache_retention: str | None
    retry_policy: RetryPolicy
    timeout_policy: TimeoutPolicy
    extra_body: JsonDict = field(default_factory=dict)
    proxy: str | None = None
    api_keys: tuple[str, ...] = ()


@dataclass(slots=True)
class LlmInput:
    type: Literal["text", "image"]
    text: str | None = None
    image_bytes: bytes | None = None
    mime_type: str | None = None


@dataclass(slots=True)
class LlmRequest:
    provider_config: ProviderConfig
    system_prompt: str
    inputs: list[LlmInput]
    response_mode: Literal["text", "json_object", "json_schema"]
    schema_name: str | None = None
    response_schema: JsonDict | None = None
    component: str = "unknown"


@dataclass(slots=True)
class LlmResponse:
    content_text: str
    parsed_json: object | None
    raw_response: JsonDict
    usage: JsonDict
    token_usage: JsonDict
    provider: str
    model: str
    endpoint_family: str
    response_mode: str
    retry_count: int
    finish_reason: str | None = None
    has_image: bool = False

    def to_meta(self) -> JsonDict:
        return {
            "provider": self.provider,
            "model": self.model,
            "usage": self.usage,
            "token_usage": self.token_usage,
            "endpoint_family": self.endpoint_family,
            "response_mode": self.response_mode,
            "retry_count": self.retry_count,
            "finish_reason": self.finish_reason,
            "has_image": self.has_image,
        }


class LlmRequestError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        failure_bucket: str,
        endpoint: str,
        attempts: int,
        status_code: int | None = None,
        response_body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_bucket = failure_bucket
        self.endpoint = endpoint
        self.attempts = attempts
        self.status_code = status_code
        self.response_body = response_body

    def to_dict(self) -> JsonDict:
        payload: JsonDict = {
            "failure_bucket": self.failure_bucket,
            "endpoint": self.endpoint,
            "attempts": self.attempts,
        }
        if self.status_code is not None:
            payload["status_code"] = self.status_code
        if self.response_body:
            payload["response_body"] = self.response_body
        return payload


class LlmResponseError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        endpoint: str,
        usage: object,
        failure_bucket: str = "quality",
        raw_response: JsonDict | None = None,
        content_text: str | None = None,
    ) -> None:
        super().__init__(message)
        self.endpoint = endpoint
        self.usage = usage
        self.failure_bucket = failure_bucket
        self.raw_response = raw_response
        self.content_text = content_text

    def to_dict(self) -> JsonDict:
        return {
            "failure_bucket": self.failure_bucket,
            "endpoint": self.endpoint,
            "token_usage": token_usage_from_provider_usage(self.usage),
        }


def uses_openai_reasoning_tokens(model: str) -> bool:
    normalized = model.strip().lower()
    return normalized.startswith(_OPENAI_REASONING_MODEL_PREFIXES)


def is_openai_base_url(base_url: str) -> bool:
    normalized = base_url.strip().lower()
    return normalized == _OPENAI_BASE_URL or normalized.startswith(f"{_OPENAI_BASE_URL}/")


def _retry_delay_seconds(base_backoff_seconds: float, *, attempt_number: int) -> float:
    if base_backoff_seconds <= 0:
        return 0.0
    return base_backoff_seconds * (2 ** max(attempt_number - 1, 0))


def _retry_after_seconds(response: httpx.Response) -> float | None:
    retry_after = response.headers.get("Retry-After")
    if retry_after is None:
        return None
    try:
        parsed = float(retry_after)
    except ValueError:
        return None
    return max(parsed, 0.0)


def _anthropic_messages_payload(request: LlmRequest) -> JsonDict:
    config = request.provider_config
    body: JsonDict = {
        "model": config.model,
        "system": request.system_prompt,
        "messages": [
            {
                "role": "user",
                "content": [_anthropic_input_part(item) for item in request.inputs],
            }
        ],
        "max_tokens": config.output_token_limit,
    }
    body["temperature"] = config.temperature
    if config.reasoning_effort != "none":
        body["thinking"] = {"type": "enabled", "budget_tokens": config.output_token_limit}
    return body


def _anthropic_input_part(item: LlmInput) -> JsonDict:
    if item.type == "text":
        return {"type": "text", "text": item.text or ""}
    if item.type == "image" and item.image_bytes is not None and item.mime_type:
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": item.mime_type,
                "data": base64.b64encode(item.image_bytes).decode("ascii"),
            },
        }
    raise ValueError("Unsupported Anthropic input part")


def _gemini_input_part(item: LlmInput) -> JsonDict:
    if item.type == "text":
        return {"text": item.text or ""}
    if item.type == "image" and item.image_bytes is not None and item.mime_type:
        return {
            "inlineData": {
                "mimeType": item.mime_type,
                "data": base64.b64encode(item.image_bytes).decode("ascii"),
            }
        }
    raise ValueError("Unsupported Gemini input part")


def _deep_merge_dict(left: JsonDict, right: JsonDict) -> JsonDict:
    merged: JsonDict = dict(left)
    for key, value in right.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dict(existing, value)
        else:
            merged[key] = value
    return merged


def _gemini_native_payload(request: LlmRequest) -> JsonDict:
    config = request.provider_config
    generation_config: JsonDict = {
        "temperature": config.temperature,
        "maxOutputTokens": config.output_token_limit,
    }
    if request.response_mode == "json_object":
        generation_config["responseMimeType"] = "application/json"
    elif request.response_mode == "json_schema":
        if not request.response_schema:
            raise ValueError("json_schema mode requires response_schema")
        generation_config["responseMimeType"] = "application/json"
        generation_config["responseSchema"] = request.response_schema
    body: JsonDict = {
        "systemInstruction": {"parts": [{"text": request.system_prompt}]},
        "contents": [
            {
                "role": "user",
                "parts": [_gemini_input_part(item) for item in request.inputs],
            }
        ],
        "generationConfig": generation_config,
    }
    if config.extra_body:
        body = _deep_merge_dict(body, config.extra_body)
    return body


def _chat_content_part(item: LlmInput) -> JsonDict:
    if item.type == "text":
        return {"type": "text", "text": item.text or ""}
    if item.type == "image" and item.image_bytes is not None and item.mime_type:
        data_url = (
            f"data:{item.mime_type};base64,{base64.b64encode(item.image_bytes).decode('ascii')}"
        )
        return {"type": "image_url", "image_url": {"url": data_url}}
    raise ValueError("Unsupported chat input part")


def _openai_chat_payload(request: LlmRequest) -> JsonDict:
    config = request.provider_config
    body: JsonDict = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": request.system_prompt},
            {
                "role": "user",
                "content": _chat_messages_content(request.inputs),
            },
        ],
    }
    _apply_token_limit(body, config)
    body["temperature"] = config.temperature
    if uses_openai_reasoning_tokens(config.model) and config.reasoning_effort != "none":
        body["reasoning_effort"] = config.reasoning_effort
    if request.response_mode == "json_object":
        body["response_format"] = {"type": "json_object"}
    elif request.response_mode == "json_schema":
        if not request.response_schema or not request.schema_name:
            raise ValueError("json_schema mode requires response_schema and schema_name")
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": request.schema_name,
                "schema": request.response_schema,
                "strict": True,
            },
        }
    _apply_prompt_cache_options(body, config)
    _apply_extra_body(body, config)
    return body


def _chat_messages_content(inputs: list[LlmInput]) -> str | list[JsonDict]:
    has_image = any(item.type == "image" for item in inputs)
    if not has_image:
        if len(inputs) == 1 and inputs[0].type == "text":
            return inputs[0].text or ""
        return "\n\n".join(item.text or "" for item in inputs if item.type == "text")
    return [_chat_content_part(item) for item in inputs]


def request_from_system_and_user_messages(
    *,
    provider_config: ProviderConfig,
    messages: list[dict[str, str]],
    response_mode: Literal["text", "json_object", "json_schema"],
    component: str,
    schema_name: str | None = None,
    response_schema: JsonDict | None = None,
    allow_multiple_system_messages: bool = False,
) -> LlmRequest:
    system_messages: list[str] = []
    inputs: list[LlmInput] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise ValueError(f"messages[{index}] must be an object")
        role = message.get("role")
        content = message.get("content")
        if not isinstance(role, str) or not role.strip():
            raise ValueError(f"messages[{index}].role must be a non-empty string")
        if not isinstance(content, str):
            raise ValueError(f"messages[{index}].content must be a string")
        if role == "system":
            system_messages.append(content)
            continue
        if role != "user":
            raise ValueError(
                f"messages[{index}].role must be either 'system' or 'user', got {role!r}"
            )
        inputs.append(LlmInput(type="text", text=content))
    if allow_multiple_system_messages:
        if not system_messages:
            raise ValueError("Expected at least one system prompt message")
        system_prompt = "\n\n".join(system_messages)
    else:
        if len(system_messages) != 1:
            raise ValueError("Expected exactly one system prompt message")
        system_prompt = system_messages[0]
    return LlmRequest(
        provider_config=provider_config,
        system_prompt=system_prompt,
        inputs=inputs,
        response_mode=response_mode,
        schema_name=schema_name,
        response_schema=response_schema,
        component=component,
    )


def request_from_messages(
    *,
    provider_config: ProviderConfig,
    messages: list[dict[str, str]],
    response_mode: Literal["text", "json_object", "json_schema"],
    component: str,
    schema_name: str | None = None,
    response_schema: JsonDict | None = None,
    allow_multiple_system_messages: bool = False,
) -> LlmRequest:
    return request_from_system_and_user_messages(
        provider_config=provider_config,
        messages=messages,
        response_mode=response_mode,
        component=component,
        schema_name=schema_name,
        response_schema=response_schema,
        allow_multiple_system_messages=allow_multiple_system_messages,
    )


def _apply_prompt_cache_options(body: JsonDict, config: ProviderConfig) -> None:
    if not is_openai_base_url(config.base_url):
        return
    if config.prompt_cache_key:
        body["prompt_cache_key"] = config.prompt_cache_key
    if config.prompt_cache_retention:
        body["prompt_cache_retention"] = config.prompt_cache_retention


def _apply_token_limit(body: JsonDict, config: ProviderConfig) -> None:
    if config.provider == "anthropic":
        body["max_tokens"] = config.output_token_limit
        return
    if config.provider == "openai-compatible" and uses_openai_reasoning_tokens(config.model):
        body["max_completion_tokens"] = config.output_token_limit
        return
    body["max_tokens"] = config.output_token_limit


def _apply_extra_body(body: JsonDict, config: ProviderConfig) -> None:
    if config.extra_body:
        body.update(config.extra_body)


def _extract_openai_text(payload: JsonDict) -> tuple[str, str | None]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("Provider response did not contain choices")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise ValueError("Provider choice must be an object")
    finish_reason = choice.get("finish_reason")
    message = choice.get("message")
    if not isinstance(message, dict):
        raise ValueError("Provider response did not contain a message")
    content = message.get("content")
    if isinstance(content, str):
        return content, finish_reason if isinstance(finish_reason, str) else None
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                chunks.append(item["text"])
        if chunks:
            return "\n".join(chunks), finish_reason if isinstance(finish_reason, str) else None
    raise ValueError("Provider message content must be a string")


def _extract_anthropic_text(payload: JsonDict) -> tuple[str, str | None]:
    content = payload.get("content")
    if not isinstance(content, list):
        raise ValueError("Anthropic response did not contain content")
    chunks: list[str] = []
    for item in content:
        if (
            isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        ):
            chunks.append(item["text"])
    if not chunks:
        raise ValueError("Anthropic response did not contain text content")
    stop_reason = payload.get("stop_reason")
    return "\n".join(chunks), stop_reason if isinstance(stop_reason, str) else None


def _normalize_anthropic_usage(payload: JsonDict) -> JsonDict:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return {}
    return {
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "total_tokens": int(usage.get("input_tokens", 0) or 0)
        + int(usage.get("output_tokens", 0) or 0),
    }


def _extract_gemini_text(payload: JsonDict) -> tuple[str, str | None]:
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("Gemini response did not contain candidates")
    candidate = candidates[0]
    if not isinstance(candidate, dict):
        raise ValueError("Gemini candidate must be an object")
    finish_reason = candidate.get("finishReason")
    content = candidate.get("content")
    if not isinstance(content, dict):
        raise ValueError("Gemini candidate did not contain content")
    parts = content.get("parts")
    if not isinstance(parts, list):
        raise ValueError("Gemini candidate content did not contain parts")
    chunks = [
        part["text"]
        for part in parts
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    ]
    if not chunks:
        raise ValueError("Gemini response did not contain text parts")
    return "\n".join(chunks), finish_reason if isinstance(finish_reason, str) else None


def _normalize_gemini_usage(payload: JsonDict) -> JsonDict:
    usage = payload.get("usageMetadata")
    if not isinstance(usage, dict):
        return {}
    return {
        "prompt_tokens": usage.get("promptTokenCount", 0),
        "completion_tokens": usage.get("candidatesTokenCount", 0),
        "total_tokens": usage.get("totalTokenCount", 0),
        "reasoning_tokens": usage.get("thoughtsTokenCount", 0),
    }


def _request_headers(config: ProviderConfig) -> dict[str, str]:
    if config.provider == "anthropic":
        return {
            "x-api-key": config.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
    if config.provider == _GEMINI_NATIVE_PROVIDER:
        return {
            "x-goog-api-key": config.api_key,
            "content-type": "application/json",
        }
    return {
        "Authorization": f"Bearer {config.api_key}",
        "content-type": "application/json",
    }


_KEY_ROTATION_LOCK = threading.Lock()
_KEY_ROTATION_COUNTERS: dict[tuple[str, str, str, tuple[str, ...]], int] = {}


def _resolved_api_keys(config: ProviderConfig) -> tuple[str, ...]:
    keys = tuple(key for key in config.api_keys if key.strip())
    return keys or (config.api_key,)


def _config_with_rotated_key(config: ProviderConfig, *, attempt_number: int) -> ProviderConfig:
    keys = _resolved_api_keys(config)
    if len(keys) == 1:
        return config if config.api_key == keys[0] else replace(config, api_key=keys[0])
    rotation_key = (config.provider, config.base_url, config.model, keys)
    with _KEY_ROTATION_LOCK:
        start = _KEY_ROTATION_COUNTERS.get(rotation_key, 0)
        if attempt_number == 1:
            _KEY_ROTATION_COUNTERS[rotation_key] = start + 1
    index = (start + attempt_number - 1) % len(keys)
    return replace(config, api_key=keys[index])


def _endpoint_family(config: ProviderConfig) -> str:
    if config.provider == "anthropic":
        return "messages"
    if config.provider == _GEMINI_NATIVE_PROVIDER:
        model = config.model.strip()
        if not model.startswith("models/"):
            model = f"models/{model}"
        return f"{model}:generateContent"
    return "chat/completions"


async def _post_json_once_async(
    *,
    config: ProviderConfig,
    endpoint: str,
    json_body: JsonDict,
) -> httpx.Response:
    timeout = config.timeout_policy.to_httpx_timeout()
    wall_clock_timeout = max(config.timeout_policy.read_timeout_seconds, 0.001)
    client_kwargs: dict[str, object] = {"timeout": timeout}
    if config.proxy:
        client_kwargs["proxy"] = config.proxy
        client_kwargs["verify"] = False
    async with httpx.AsyncClient(**client_kwargs) as client:
        return await asyncio.wait_for(
            client.post(
                f"{config.base_url.rstrip('/')}/{endpoint}",
                headers=_request_headers(config),
                json=json_body,
            ),
            timeout=wall_clock_timeout,
        )


def _run_async_request(coro_factory):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(coro_factory())).result()


def _post_json_once(
    *,
    config: ProviderConfig,
    endpoint: str,
    json_body: JsonDict,
) -> httpx.Response:
    return _run_async_request(
        lambda: _post_json_once_async(config=config, endpoint=endpoint, json_body=json_body)
    )


def _post_json(
    *,
    config: ProviderConfig,
    json_body: JsonDict,
) -> tuple[JsonDict, int]:
    endpoint = _endpoint_family(config)
    total_attempts = config.retry_policy.max_retries + 1
    last_error: Exception | None = None
    for attempt_number in range(1, total_attempts + 1):
        attempt_config = _config_with_rotated_key(config, attempt_number=attempt_number)
        try:
            response = _post_json_once(
                config=attempt_config,
                endpoint=endpoint,
                json_body=json_body,
            )
            response.raise_for_status()
            return response.json(), attempt_number
        except json.JSONDecodeError as exc:
            raise LlmResponseError(
                f"Provider returned a non-JSON response for {endpoint}: {exc}",
                endpoint=endpoint,
                usage={},
                content_text=response.text,
            ) from exc
        except httpx.HTTPStatusError as exc:
            last_error = exc
            retry_after_seconds = _retry_after_seconds(exc.response)
            if (
                exc.response.status_code not in _TRANSIENT_HTTP_STATUS_CODES
                or attempt_number == total_attempts
            ):
                raise LlmRequestError(
                    (
                        "Provider request failed with HTTP "
                        f"{exc.response.status_code} after {attempt_number} attempts for "
                        f"{endpoint}: {exc}"
                    ),
                    failure_bucket="infra",
                    endpoint=endpoint,
                    attempts=attempt_number,
                    status_code=exc.response.status_code,
                    response_body=exc.response.text,
                ) from exc
            if retry_after_seconds is not None:
                time.sleep(retry_after_seconds + random.uniform(0.0, 0.5))
                continue
        except (
            TimeoutError,
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        ) as exc:
            last_error = exc
            if attempt_number == total_attempts:
                failure_bucket = (
                    "timeout"
                    if isinstance(exc, (TimeoutError, httpx.TimeoutException))
                    else "infra"
                )
                label = "timed out" if failure_bucket == "timeout" else "failed"
                raise LlmRequestError(
                    (
                        f"Provider request {label} after {attempt_number} attempts for "
                        f"{endpoint}: {exc}"
                    ),
                    failure_bucket=failure_bucket,
                    endpoint=endpoint,
                    attempts=attempt_number,
                ) from exc
        time.sleep(
            _retry_delay_seconds(config.retry_policy.backoff_seconds, attempt_number=attempt_number)
            + random.uniform(0.0, 0.5)
        )
    if last_error is not None:
        raise LlmRequestError(
            f"Provider request failed after {total_attempts} attempts for {endpoint}: {last_error}",
            failure_bucket="infra",
            endpoint=endpoint,
            attempts=total_attempts,
        ) from last_error
    raise RuntimeError("Provider request failed without returning a response")


def call_llm(request: LlmRequest) -> LlmResponse:
    config = replace(
        request.provider_config, provider=request.provider_config.provider.strip()
    )
    request = replace(request, provider_config=config)
    if not (
        is_openai_compatible_provider(config.provider)
        or config.provider == "anthropic"
        or config.provider == _GEMINI_NATIVE_PROVIDER
    ):
        raise ValueError(f"Unsupported provider: {config.provider}")

    if config.provider == "anthropic":
        request_body = _anthropic_messages_payload(request)
    elif config.provider == _GEMINI_NATIVE_PROVIDER:
        request_body = _gemini_native_payload(request)
    else:
        request_body = _openai_chat_payload(request)

    payload, attempts = _post_json(config=config, json_body=request_body)
    if config.provider == "anthropic":
        usage = _normalize_anthropic_usage(payload)
    elif config.provider == _GEMINI_NATIVE_PROVIDER:
        usage = _normalize_gemini_usage(payload)
    else:
        usage = payload.get("usage", {})
    try:
        if config.provider == "anthropic":
            content_text, finish_reason = _extract_anthropic_text(payload)
        elif config.provider == _GEMINI_NATIVE_PROVIDER:
            content_text, finish_reason = _extract_gemini_text(payload)
            if finish_reason == "MAX_TOKENS":
                raise ValueError("Provider response was truncated before completion")
        else:
            content_text, finish_reason = _extract_openai_text(payload)
            if finish_reason == "length":
                raise ValueError("Provider response was truncated before completion")
        parsed_json = None
        if request.response_mode in {"json_object", "json_schema"}:
            try:
                parsed_json = json.loads(content_text)
            except json.JSONDecodeError:
                print(
                    f"\n{'=' * 60}\n"
                    f"[LLM RESPONSE DEBUG] Raw content for component {request.component}\n"
                    f"{'=' * 60}\n"
                    f"{content_text}\n"
                    f"{'=' * 60}",
                    file=sys.stderr,
                    flush=True,
                )
    except Exception as exc:
        raise LlmResponseError(
            f"Provider returned an unusable response for component {request.component}: {exc}",
            endpoint=_endpoint_family(config),
            usage=usage,
            raw_response=payload if isinstance(payload, dict) else None,
        ) from exc

    return LlmResponse(
        content_text=content_text,
        parsed_json=parsed_json,
        raw_response=payload,
        usage=usage if isinstance(usage, dict) else {},
        token_usage=token_usage_from_provider_usage(usage),
        provider=config.provider,
        model=config.model,
        endpoint_family=_endpoint_family(config),
        response_mode=request.response_mode,
        retry_count=max(attempts - 1, 0),
        finish_reason=finish_reason,
        has_image=any(item.type == "image" for item in request.inputs),
    )


def failure_bucket_for_exception(exc: Exception, *, default: str = "quality") -> str:
    if isinstance(exc, LlmRequestError):
        return exc.failure_bucket
    if isinstance(exc, LlmResponseError):
        return exc.failure_bucket
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return "timeout"
    return default


def provider_error_details(exc: Exception) -> JsonDict:
    if isinstance(exc, LlmRequestError):
        return {"provider_request": exc.to_dict()}
    if isinstance(exc, LlmResponseError):
        return {"provider_response": exc.to_dict()}
    return {}


def token_usage_for_exception(exc: Exception) -> JsonDict:
    if isinstance(exc, LlmResponseError):
        return token_usage_from_provider_usage(exc.usage)
    return {}
