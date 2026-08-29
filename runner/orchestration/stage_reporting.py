from __future__ import annotations

import json
from pathlib import Path

from runner.evaluation.eval_runner import evaluation_failure_mode_from_payload
from runner.orchestration.turn_bundle import read_stage_payload
from runner.tools.token_usage import summarize_component_token_usage, token_usage_from_meta


def stage_files(bundle_path: str, stage: str) -> dict[str, str]:
    stage_dir = bundle_stage_name(stage)
    base = f"{bundle_path}/{stage_dir}"
    if stage_dir == "generation":
        return {
            "request": f"{base}/request.json",
            "output": f"{base}/output.json",
            "meta": f"{base}/meta.json",
            "build": f"{base}/build.json",
        }
    if stage_dir == "execution":
        return {
            "build": f"{base}/build.json",
            "actor_result": f"{base}/actor_result.json",
            "snapshot": f"{base}/snapshot.json",
        }
    if stage_dir == "evaluation":
        return {
            "judge_result": f"{base}/judge_result.json",
            "report": f"{base}/report.json",
        }
    raise ValueError(f"Unsupported stage: {stage}")


def bundle_stage_name(stage: str) -> str:
    return {
        "generate": "generation",
        "execute": "execution",
        "evaluate": "evaluation",
        "generation": "generation",
        "execution": "execution",
        "evaluation": "evaluation",
    }.get(stage, stage)


def stage_status(bundle, stage: str) -> str | None:
    stage_payload = _manifest_stage(bundle, stage)
    status = stage_payload.get("status")
    return str(status) if isinstance(status, str) else None


def stage_error(bundle, stage: str) -> str | None:
    stage_payload = _manifest_stage(bundle, stage)
    error = stage_payload.get("error")
    return str(error) if isinstance(error, str) and error else None


def generation_response_received(bundle) -> bool:
    value = _manifest_stage(bundle, "generation").get("response_received")
    if not isinstance(value, bool):
        raise ValueError(
            f"Turn bundle {bundle.relative_root} is missing boolean generation.response_received"
        )
    return value


def generation_terminal_failure(bundle) -> bool:
    value = _manifest_stage(bundle, "generation").get("terminal_failure")
    if not isinstance(value, bool):
        raise ValueError(
            f"Turn bundle {bundle.relative_root} is missing boolean generation.terminal_failure"
        )
    return value


def stage_payload_from_bundle(bundle, stage: str) -> dict[str, object]:
    if stage == "generate":
        meta = read_stage_payload(bundle, "generation", "meta.json")
        payload: dict[str, object] = {
            "task_id": bundle.task_id,
            "turn": bundle.turn,
            "provider": bundle.provider,
            "run_id": bundle.run_id,
            "turn_bundle": bundle.relative_root,
            "files": stage_files(bundle.relative_root, "generation"),
            "token_usage": summarize_component_token_usage(
                {"tested_model": token_usage_from_meta(meta)}
            ),
            "generation_response_received": True,
        }
        build_path = bundle.generation_dir / "build.json"
        if build_path.exists():
            payload["build"] = read_stage_payload(bundle, "generation", "build.json")
        return payload
    if stage == "execute":
        payload: dict[str, object] = {
            "task_id": bundle.task_id,
            "turn": bundle.turn,
            "run_id": bundle.run_id,
            "provider": bundle.provider,
            "turn_bundle": bundle.relative_root,
            "build": read_stage_payload(bundle, "execution", "build.json"),
            "files": stage_files(bundle.relative_root, "execution"),
            "token_usage": summarize_component_token_usage({}),
        }
        actor_path = bundle.execution_dir / "actor_result.json"
        if actor_path.exists():
            actor_result = read_stage_payload(bundle, "execution", "actor_result.json")
            payload["actor"] = {
                "status": actor_result.get("status"),
                "summary": actor_result.get("summary"),
                "final_url": actor_result.get("final_url"),
            }
            payload["token_usage"] = summarize_component_token_usage(
                {"blind_actor": actor_result.get("token_usage", {})}
            )
        error = stage_error(bundle, "execution")
        if error is not None:
            payload["runtime_error"] = error
        return payload
    if stage == "evaluate":
        report = read_stage_payload(bundle, "evaluation", "report.json")
        details = report.get("details", {})
        turn_token_usage = details.get("token_usage", {}) if isinstance(details, dict) else {}
        evaluator_usage = (
            turn_token_usage.get("evaluator", {}) if isinstance(turn_token_usage, dict) else {}
        )
        actor_result = read_stage_payload(bundle, "execution", "actor_result.json")
        return {
            "task_id": str(report.get("task_id", bundle.task_id)),
            "turn": int(report.get("turn", bundle.turn)),
            "run_id": bundle.run_id,
            "provider": bundle.provider,
            "turn_bundle": bundle.relative_root,
            "official_pass": bool(report.get("official_pass", False)),
            "failure_reason": report.get("failure_reason"),
            "failure_bucket": report.get("failure_bucket"),
            "failure_mode": evaluation_failure_mode_from_payload(report),
            "dimensions": report.get("dimensions", {}),
            "evaluator_pass": bool(report.get("evaluator_pass", False)),
            "evaluator_score": float(report.get("evaluator_score", 0.0)),
            "evaluator_summary": str(report.get("evaluator_summary", "")),
            "actor": {
                "status": actor_result.get("status"),
                "summary": actor_result.get("summary"),
            },
            "files": stage_files(bundle.relative_root, "evaluation"),
            "token_usage": summarize_component_token_usage({"evaluator": evaluator_usage}),
            "turn_token_usage": (turn_token_usage if isinstance(turn_token_usage, dict) else {}),
        }
    raise ValueError(f"Unsupported stage: {stage}")


def stage_summary_path(output_root: Path, stage: str, *, turn: int | None = None) -> Path:
    suffix = f"{stage}-suite"
    if turn is not None:
        suffix += f"-turn-{turn}"
    return output_root / "reports" / "stages" / f"{suffix}.json"


def _load_json_object(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def _manifest_stage(bundle, stage: str) -> dict[str, object]:
    manifest = _load_json_object(bundle.manifest_path)
    stages = manifest.get("stages")
    if not isinstance(stages, dict):
        return {}
    stage_payload = stages.get(bundle_stage_name(stage))
    return stage_payload if isinstance(stage_payload, dict) else {}
