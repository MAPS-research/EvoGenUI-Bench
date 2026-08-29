from __future__ import annotations

import copy
import json
from pathlib import Path

from runner.evaluation.failure_taxonomy_evidence import (
    actor_supervision_signal,
    compact_actor,
    compact_snapshot,
    compact_turn_diffs,
    float_value,
    strings,
)
from runner.evaluation.prompts import failure_taxonomy_system_prompt
from runner.tools.experiment_config import ComponentRuntimeConfig
from runner.tools.io_utils import truncate_text
from runner.tools.llm_client import LlmInput, LlmRequest, call_llm
from runtime.types import JsonDict

FAILURE_TAXONOMY_STATUS = "completed"
_RAW_RESPONSE_CHARS = 8000
_OUTPUT_SOURCE_CHARS = 12000
_OUTPUT_FILE_LIMIT = 8
_BUILD_STDOUT_CHARS = 3000
_BUILD_STDERR_CHARS = 6000
_MANIFEST_ERROR_CHARS = 4000

TAXONOMY_ATTRIBUTION_VALUES = (
    "model_ui_failure",
    "actor_execution_gap",
    "benchmark_infra",
    "mixed",
    "inconclusive",
)

CAPABILITY_FAILURE_VALUES = (
    "information_architecture_failure",
    "domain_representation_failure",
    "requirement_decomposition_failure",
    "affordance_binding_failure",
    "derived_state_propagation_failure",
    "external_state_grounding_failure",
)

FAILURE_TAXONOMY_SCHEMA: JsonDict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "attribution": {
            "type": "string",
            "enum": list(TAXONOMY_ATTRIBUTION_VALUES),
        },
        "count_as_model_failure": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "capability_failure": {
            "type": "string",
            "enum": list(CAPABILITY_FAILURE_VALUES),
        },
        "secondary_capability_failures": {
            "type": "array",
            "items": {"type": "string", "enum": list(CAPABILITY_FAILURE_VALUES)},
            "maxItems": 2,
        },
        "rationale": {"type": "string", "minLength": 1},
        "code_evidence": {"type": "array", "items": {"type": "string"}},
        "actor_evidence": {"type": "array", "items": {"type": "string"}},
        "infra_evidence": {"type": "array", "items": {"type": "string"}},
        "capability_evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "attribution",
        "count_as_model_failure",
        "confidence",
        "capability_failure",
        "secondary_capability_failures",
        "rationale",
        "code_evidence",
        "actor_evidence",
        "infra_evidence",
        "capability_evidence",
    ],
}


def run_failure_taxonomy_judge(
    payload: JsonDict,
    *,
    runtime_config: ComponentRuntimeConfig,
    screenshot_path: Path | None = None,
) -> JsonDict:
    inputs = _failure_taxonomy_request_inputs(
        payload,
        use_screenshot=runtime_config.use_screenshot,
        screenshot_path=screenshot_path,
    )
    response = call_llm(
        LlmRequest(
            provider_config=runtime_config.provider_config,
            system_prompt=failure_taxonomy_system_prompt(),
            inputs=inputs,
            response_mode="json_schema",
            schema_name="genui_failure_taxonomy",
            response_schema=FAILURE_TAXONOMY_SCHEMA,
            component="evaluator",
        )
    )
    return normalize_failure_taxonomy(
        response.parsed_json,
        token_usage=response.token_usage,
    )


def _failure_taxonomy_request_inputs(
    payload: JsonDict,
    *,
    use_screenshot: bool,
    screenshot_path: Path | None,
) -> list[LlmInput]:
    request_payload = copy.deepcopy(payload)
    image_input: LlmInput | None = None
    if not use_screenshot:
        screenshot_input: JsonDict = {"requested": False, "status": "disabled_by_config"}
    elif screenshot_path is None:
        screenshot_input = {
            "requested": True,
            "status": "unavailable",
            "reason": "No readable final screenshot path was available for this turn bundle.",
        }
    else:
        try:
            image_bytes = screenshot_path.read_bytes()
        except OSError as exc:
            screenshot_input = {
                "requested": True,
                "status": "unreadable",
                "path": str(screenshot_path),
                "error": truncate_text(exc, 1000),
            }
        else:
            if image_bytes:
                mime_type = _screenshot_mime_type(screenshot_path)
                screenshot_input = {
                    "requested": True,
                    "status": "included",
                    "path": str(screenshot_path),
                    "mime_type": mime_type,
                    "byte_count": len(image_bytes),
                }
                image_input = LlmInput(
                    type="image",
                    image_bytes=image_bytes,
                    mime_type=mime_type,
                )
            else:
                screenshot_input = {
                    "requested": True,
                    "status": "unreadable",
                    "path": str(screenshot_path),
                    "error": "Final screenshot file is empty.",
                }
    request_payload["screenshot_input"] = screenshot_input
    inputs = [
        LlmInput(
            type="text",
            text=json.dumps(request_payload, ensure_ascii=False, separators=(",", ":")),
        )
    ]
    if image_input is not None:
        inputs.append(image_input)
    return inputs


def _screenshot_mime_type(path: Path) -> str:
    return "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"


def normalize_failure_taxonomy(value: object, *, token_usage: JsonDict) -> JsonDict:
    if not isinstance(value, dict):
        raise ValueError("Failure taxonomy response must be a JSON object")
    payload = _taxonomy_payload(value)
    attribution = str(payload.get("attribution") or "").strip()
    if attribution not in TAXONOMY_ATTRIBUTION_VALUES:
        raise ValueError(f"Unsupported failure taxonomy attribution: {attribution!r}")
    count_as_model_failure = _bool_value(payload.get("count_as_model_failure"))
    if attribution in {"model_ui_failure", "mixed"}:
        count_as_model_failure = True
    if attribution in {"actor_execution_gap", "benchmark_infra", "inconclusive"}:
        count_as_model_failure = False
    capability_failure = _capability_failure(payload)
    secondary_capability_failures = [
        item
        for item in _capability_failure_list(
            payload.get("secondary_capability_failures")
            or payload.get("secondary_labels")
            or payload.get("secondary_capability_bottlenecks")
        )
        if item != capability_failure
    ][:2]
    confidence = float_value(payload.get("confidence"))
    return {
        "status": FAILURE_TAXONOMY_STATUS,
        "attribution": attribution,
        "count_as_model_failure": count_as_model_failure,
        "confidence": max(0.0, min(confidence, 1.0)),
        "capability_failure": capability_failure,
        "secondary_capability_failures": secondary_capability_failures,
        "rationale": str(payload.get("rationale") or value.get("rationale") or ""),
        "code_evidence": strings(payload.get("code_evidence") or value.get("code_evidence")),
        "actor_evidence": strings(payload.get("actor_evidence") or value.get("actor_evidence")),
        "infra_evidence": strings(payload.get("infra_evidence") or value.get("infra_evidence")),
        "capability_evidence": strings(
            payload.get("capability_evidence") or value.get("capability_evidence")
        ),
        "token_usage": token_usage,
    }


def build_failure_taxonomy_payload_from_report(
    report: JsonDict,
    *,
    previous_reports: list[JsonDict],
    report_path: Path | None = None,
    suite: str = "",
    bundle_evidence: JsonDict | None = None,
) -> JsonDict:
    snapshot = _current_snapshot(report)
    actor_result = _actor_result(report, snapshot=snapshot)
    task_payload: JsonDict = {
        "task_id": report.get("task_id") or snapshot.get("task_id"),
        "title": report.get("task_id") or snapshot.get("task_id"),
        "turn": report.get("turn") or snapshot.get("turn"),
        "current_user_request": str(snapshot.get("user_request") or ""),
    }
    if suite:
        task_payload["suite"] = suite
    if report_path is not None:
        task_payload["report_path"] = str(report_path)
    payload: JsonDict = {
        "task": task_payload,
        "dimension_judge": _compact_report_dimensions(report),
        "actor_supervision": actor_supervision_signal(actor_result),
        "actor_trace": compact_actor(actor_result),
        "current_snapshot": compact_snapshot(snapshot),
        "previous_turns": _compact_previous_reports(previous_reports),
        "turn_diffs": compact_turn_diffs((report.get("details") or {}).get("turn_diffs", {})),
    }
    if bundle_evidence is not None:
        payload["pipeline_evidence"] = bundle_evidence
    return payload


def build_failure_taxonomy_bundle_evidence(
    bundle_root: Path,
    *,
    screenshot_evidence: JsonDict | None = None,
) -> JsonDict:
    """Build bounded evidence for failures that may predate dimension evaluation."""

    manifest = _read_json_object(bundle_root / "manifest.json")
    generation_dir = bundle_root / "generation"
    execution_dir = bundle_root / "execution"
    if screenshot_evidence is None:
        _, screenshot_evidence = resolve_failure_taxonomy_screenshot(bundle_root)
    return {
        "generation": {
            "stage": _compact_manifest_stage(manifest, "generation"),
            "raw_response": _compact_raw_response(
                _read_optional_json_object(generation_dir / "raw_response.json")
            ),
            "output": _compact_generation_output(
                _read_optional_json_object(generation_dir / "output.json")
            ),
            "preview_build": _compact_build(
                _read_optional_json_object(generation_dir / "build.json")
            ),
        },
        "execution": {
            "stage": _compact_manifest_stage(manifest, "execution"),
            "build": _compact_build(_read_optional_json_object(execution_dir / "build.json")),
            "final_screenshot": screenshot_evidence,
        },
        "evaluation": {
            "stage": _compact_manifest_stage(manifest, "evaluation"),
        },
    }


def resolve_failure_taxonomy_screenshot(bundle_root: Path) -> tuple[Path | None, JsonDict]:
    """Resolve the actor's final screenshot and describe why it is unavailable."""

    execution_dir = bundle_root / "execution"
    snapshot = _read_optional_json_object(execution_dir / "snapshot.json")
    actor = _read_optional_json_object(execution_dir / "actor_result.json")
    candidates: list[tuple[str, object]] = []
    final_ui = snapshot.get("final_ui")
    if isinstance(final_ui, dict):
        candidates.append(("execution.snapshot.final_ui.screenshot", final_ui.get("screenshot")))
    candidates.append(("execution.actor_result.final_screenshot", actor.get("final_screenshot")))
    declared = [
        (source, value.strip())
        for source, value in candidates
        if isinstance(value, str) and value.strip()
    ]
    if not declared:
        return None, {
            "status": "not_produced",
            "reason": "Execution artifacts do not declare a final screenshot path.",
        }

    failures: list[JsonDict] = []
    for source, value in declared:
        raw_path = Path(value)
        path = raw_path if raw_path.is_absolute() else (bundle_root / raw_path).resolve()
        try:
            byte_count = path.stat().st_size
            if not path.is_file():
                raise OSError("path is not a regular file")
            if byte_count <= 0:
                raise OSError("file is empty")
            with path.open("rb") as stream:
                stream.read(1)
        except OSError as exc:
            failures.append(
                {
                    "source": source,
                    "declared_path": value,
                    "resolved_path": str(path),
                    "error": truncate_text(exc, 1000),
                }
            )
            continue
        return path, {
            "status": "available",
            "source": source,
            "declared_path": value,
            "resolved_path": str(path),
            "mime_type": _screenshot_mime_type(path),
            "byte_count": byte_count,
        }
    return None, {
        "status": "missing_or_unreadable",
        "reason": "Every declared final screenshot path was missing or unreadable.",
        "candidates": failures,
    }


def _read_json_object(path: Path) -> JsonDict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return value


def _read_optional_json_object(path: Path) -> JsonDict:
    return _read_json_object(path) if path.exists() else {}


def _compact_manifest_stage(manifest: JsonDict, stage: str) -> JsonDict:
    stages = manifest.get("stages")
    payload = stages.get(stage) if isinstance(stages, dict) else None
    if not isinstance(payload, dict):
        return {}
    compact: JsonDict = {
        "status": payload.get("status"),
    }
    if stage == "generation":
        compact["response_received"] = payload.get("response_received")
        compact["terminal_failure"] = payload.get("terminal_failure")
    error = payload.get("error")
    if error is not None:
        compact["error"] = truncate_text(error, _MANIFEST_ERROR_CHARS)
    return compact


def _compact_raw_response(payload: JsonDict) -> JsonDict:
    if not payload:
        return {}
    compact: JsonDict = {}
    if payload.get("content_text") is not None:
        compact["content_text"] = truncate_text(
            payload.get("content_text"),
            _RAW_RESPONSE_CHARS,
        )
    if payload.get("raw_response") is not None:
        compact["raw_response_json"] = truncate_text(
            json.dumps(payload.get("raw_response"), ensure_ascii=False, default=str),
            _RAW_RESPONSE_CHARS,
        )
    return compact


def _compact_generation_output(payload: JsonDict) -> JsonDict:
    if not payload:
        return {}
    compact: JsonDict = {
        "assistant_text": truncate_text(payload.get("assistant_text", ""), 3000),
    }
    files = payload.get("files")
    if not isinstance(files, dict):
        return compact
    remaining_chars = _OUTPUT_SOURCE_CHARS
    compact_files: JsonDict = {}
    sorted_files = sorted((str(path), contents) for path, contents in files.items())
    for path, contents in sorted_files[:_OUTPUT_FILE_LIMIT]:
        if remaining_chars <= 0:
            break
        excerpt = truncate_text(contents, min(3000, remaining_chars))
        compact_files[path] = excerpt
        remaining_chars -= min(len(str(contents)), min(3000, remaining_chars))
    compact["files"] = compact_files
    omitted = len(sorted_files) - len(compact_files)
    if omitted > 0:
        compact["omitted_file_count"] = omitted
    return compact


def _compact_build(payload: JsonDict) -> JsonDict:
    if not payload:
        return {}
    errors = payload.get("errors")
    return {
        "success": payload.get("success"),
        "source": payload.get("source"),
        "stdout": truncate_text(payload.get("stdout", ""), _BUILD_STDOUT_CHARS),
        "stderr": truncate_text(payload.get("stderr", ""), _BUILD_STDERR_CHARS),
        "errors": (
            [truncate_text(item, 1200) for item in errors[:10]] if isinstance(errors, list) else []
        ),
    }


def _compact_report_dimensions(report: JsonDict) -> JsonDict:
    dimensions = report.get("dimensions")
    compact_dimensions: JsonDict = {}
    if isinstance(dimensions, dict):
        for name, payload in dimensions.items():
            if not isinstance(payload, dict):
                continue
            compact_dimensions[str(name)] = {
                "passed": payload.get("passed"),
                "score": payload.get("score"),
                "failure_types": payload.get("failure_types", []),
                "summary": truncate_text(str(payload.get("summary", "")), 2000),
                "advisory": payload.get("advisory"),
            }
    return {
        "passed": report.get("official_pass"),
        "score": report.get("evaluator_score"),
        "failure_reason": report.get("failure_reason"),
        "failure_bucket": report.get("failure_bucket"),
        "summary": truncate_text(str(report.get("evaluator_summary", "")), 4000),
        "dimensions": compact_dimensions,
    }


def _compact_previous_reports(reports: list[JsonDict]) -> list[JsonDict]:
    compacted: list[JsonDict] = []
    for report in reports[-3:]:
        snapshot = _current_snapshot(report)
        actor = _actor_result(report, snapshot=snapshot)
        final_ui = snapshot.get("final_ui") if isinstance(snapshot.get("final_ui"), dict) else {}
        compacted.append(
            {
                "turn": report.get("turn") or snapshot.get("turn"),
                "user_request": truncate_text(str(snapshot.get("user_request", "")), 2000),
                "assistant_text": truncate_text(str(snapshot.get("assistant_text", "")), 1200),
                "final_ui_text": truncate_text(str(final_ui.get("text", "")), 2500),
                "actor_status": actor.get("status"),
                "final_assessment": actor.get("final_assessment", {}),
                "verification_checks": (
                    actor.get("verification_checks", [])[:8]
                    if isinstance(actor.get("verification_checks"), list)
                    else []
                ),
            }
        )
    return compacted


def _current_snapshot(report: JsonDict) -> JsonDict:
    details = report.get("details")
    if isinstance(details, dict) and isinstance(details.get("current_turn_snapshot"), dict):
        return details["current_turn_snapshot"]
    return {}


def _actor_result(report: JsonDict, *, snapshot: JsonDict) -> JsonDict:
    details = report.get("details")
    if isinstance(details, dict) and isinstance(details.get("actor"), dict):
        return details["actor"]
    actor = snapshot.get("actor")
    if isinstance(actor, dict):
        return actor
    return {}


def _taxonomy_payload(value: dict[str, object]) -> dict[str, object]:
    for key in ("failure_taxonomy", "taxonomy", "result"):
        nested = value.get(key)
        if isinstance(nested, dict):
            return nested
    return value


def _bool_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    return bool(value)


def _capability_failure(value: dict[str, object]) -> str:
    raw = str(
        value.get("capability_failure")
        or value.get("paper_label")
        or value.get("primary_capability_failure")
        or value.get("primary_capability_bottleneck")
        or ""
    ).strip()
    normalized = normalize_capability_failure_label(raw)
    if normalized not in CAPABILITY_FAILURE_VALUES:
        raise ValueError(f"Unsupported capability failure label: {raw!r}")
    return normalized


def _capability_failure_list(value: object) -> list[str]:
    labels = strings(value)
    normalized: list[str] = []
    for label in labels:
        item = normalize_capability_failure_label(label)
        if item in CAPABILITY_FAILURE_VALUES and item not in normalized:
            normalized.append(item)
    return normalized


def normalize_capability_failure_label(value: str) -> str:
    key = value.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "requirement_realization_gap": "requirement_decomposition_failure",
        "capability_realization_gap": "requirement_decomposition_failure",
        "interaction_executability": "affordance_binding_failure",
        "affordance_executability_failure": "affordance_binding_failure",
        "affordance_execution_failure": "affordance_binding_failure",
        "state_synchronization": "derived_state_propagation_failure",
        "derived_state_synchronization_failure": "derived_state_propagation_failure",
        "derived_state_incoherence": "derived_state_propagation_failure",
        "external_grounding_failure": "external_state_grounding_failure",
        "visual_information_failure": "information_architecture_failure",
        "visual_information_breakdown": "information_architecture_failure",
    }
    return aliases.get(key, key)
