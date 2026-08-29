from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from runtime.types import JsonDict

from .token_usage import merge_component_token_usage, summarize_component_token_usage


def _load_json_object(path: Path) -> JsonDict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def _extract_token_usage(payload: dict[str, object]) -> dict[str, object]:
    token_usage = payload.get("token_usage")
    if isinstance(token_usage, dict):
        return token_usage
    details = payload.get("details")
    if isinstance(details, dict):
        nested = details.get("token_usage")
        if isinstance(nested, dict):
            return nested
    return {}


def collect_token_usage_report_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]

    direct_summary = path / "reports" / "summary.json"
    if direct_summary.exists():
        return [direct_summary]

    direct_suite = path / "reports" / "suite.json"
    if direct_suite.exists():
        return [direct_suite]

    model_summaries = sorted(path.glob("models/*/reports/summary.json"))
    if model_summaries:
        return model_summaries

    model_suites = sorted(path.glob("models/*/reports/suite.json"))
    if model_suites:
        return model_suites

    direct_stage_summaries = sorted(path.glob("reports/stages/*.json"))
    if direct_stage_summaries:
        return direct_stage_summaries

    model_stage_summaries = sorted(path.glob("models/*/reports/stages/*.json"))
    if model_stage_summaries:
        return model_stage_summaries

    comparison_stage_summaries = sorted(path.glob("comparison/stages/*.json"))
    if comparison_stage_summaries:
        return comparison_stage_summaries

    turns = sorted(path.glob("reports/*/turn-*.json"))
    conversations = sorted(path.glob("reports/*/conversation.json"))
    if conversations:
        conversation_tasks = {report.parent.name for report in conversations}
        orphan_turns = [report for report in turns if report.parent.name not in conversation_tasks]
        return [*conversations, *orphan_turns]

    if turns:
        return turns

    raise FileNotFoundError(
        f"No suite/conversation/turn reports found under {path}. "
        "Point this command at an experiment root, model output root, or report JSON file."
    )


def _label_for_report(path: Path, payload: dict[str, object]) -> str:
    stage = payload.get("stage")
    if isinstance(stage, str) and stage:
        turn = payload.get("turn")
        suffix = f" / turn {turn}" if turn is not None else ""
        if path.name.endswith(".json") and path.parent.name == "stages":
            if path.parent.parent.name == "comparison":
                return f"{stage} comparison{suffix}"
            if path.parent.parent.name == "reports":
                return f"{stage} model{suffix}"
        if "models" in payload:
            return f"{stage} comparison{suffix}"
    if "task_id" in payload:
        task_id = str(payload.get("task_id"))
        turn = payload.get("turn")
        if turn is not None:
            return f"{task_id} / turn {turn}"
        return task_id
    if path.name in {"summary.json", "suite.json"} and path.parent.parent.parent.name == "models":
        return path.parent.parent.name
    return path.stem


def collect_token_usage_report(path: Path) -> JsonDict:
    target = path.resolve()
    report_files = collect_token_usage_report_files(target)

    reports: list[JsonDict] = []
    for report_file in report_files:
        payload = _load_json_object(report_file)
        reports.append(
            {
                "label": _label_for_report(report_file, payload),
                "path": str(report_file),
                "token_usage": summarize_component_token_usage(_extract_token_usage(payload)),
            }
        )

    total = summarize_component_token_usage(
        merge_component_token_usage(report["token_usage"] for report in reports)
    )

    return {
        "source": str(target),
        "report_count": len(reports),
        "token_usage": total,
        "reports": reports,
    }


def build_experiment_token_usage_log(
    comparison_summary: dict[str, object],
    *,
    created_at: str | None = None,
) -> JsonDict:
    models = comparison_summary.get("models", [])
    model_reports: list[JsonDict] = []
    if isinstance(models, list):
        for model in models:
            if not isinstance(model, dict):
                continue
            model_reports.append(
                {
                    "label": str(model.get("id", model.get("model", "unknown"))),
                    "provider": model.get("provider"),
                    "model": model.get("model"),
                    "output_root": model.get("output_root"),
                    "model_report": model.get("model_report"),
                    "token_usage": summarize_component_token_usage(model.get("token_usage", {})),
                }
            )

    token_usage = summarize_component_token_usage(
        merge_component_token_usage(report["token_usage"] for report in model_reports)
    )
    return {
        "schema_version": "2026-04-24",
        "created_at": created_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "experiment_id": comparison_summary.get("experiment_id"),
        "source": comparison_summary.get("output_root"),
        "report_count": len(model_reports),
        "token_usage": token_usage,
        "models": model_reports,
    }


def write_experiment_token_usage_log(
    experiment_root: Path,
    comparison_summary: dict[str, object],
    *,
    created_at: str | None = None,
) -> JsonDict:
    payload = build_experiment_token_usage_log(comparison_summary, created_at=created_at)
    timestamp = "".join(
        character for character in str(payload["created_at"]) if character.isalnum()
    )
    log_dir = experiment_root / "logs" / "token_usage"
    log_dir.mkdir(parents=True, exist_ok=True)

    history_path = log_dir / f"token-usage-{timestamp}.json"
    latest_path = log_dir / "latest.json"
    payload["paths"] = {
        "history": str(history_path),
        "latest": str(latest_path),
    }
    history_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def build_run_token_usage_log(
    payload: dict[str, object],
    *,
    command: str,
    source: str | None = None,
    created_at: str | None = None,
) -> JsonDict:
    return {
        "schema_version": "2026-04-24",
        "created_at": created_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "command": command,
        "source": source,
        "token_usage": summarize_component_token_usage(payload.get("token_usage", {})),
        "summary": {
            key: payload.get(key)
            for key in (
                "task_id",
                "provider",
                "task_count",
                "turns_requested",
                "turns_completed",
                "official_pass",
                "failure_buckets",
                "failure_reasons",
            )
            if key in payload
        },
    }


def write_run_token_usage_log(
    output_root: Path,
    payload: dict[str, object],
    *,
    command: str,
    created_at: str | None = None,
) -> JsonDict:
    log_payload = build_run_token_usage_log(
        payload,
        command=command,
        source=str(output_root.resolve()),
        created_at=created_at,
    )
    timestamp = "".join(
        character for character in str(log_payload["created_at"]) if character.isalnum()
    )
    safe_command = "".join(
        character if character.isalnum() or character in {"-", "_"} else "_"
        for character in command
    )
    log_dir = output_root / "logs" / "token_usage"
    log_dir.mkdir(parents=True, exist_ok=True)

    history_path = log_dir / f"{safe_command}-token-usage-{timestamp}.json"
    latest_path = log_dir / "latest.json"
    log_payload["paths"] = {
        "history": str(history_path),
        "latest": str(latest_path),
    }
    history_path.write_text(json.dumps(log_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_path.write_text(json.dumps(log_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return log_payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate EvoGenUI-Bench token usage reports.")
    parser.add_argument("path", type=Path, help="Experiment/model output root or report JSON file")
    args = parser.parse_args()
    print(json.dumps(collect_token_usage_report(args.path), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
