from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from runner.evaluation.failure_taxonomy import (
    build_failure_taxonomy_bundle_evidence,
    build_failure_taxonomy_payload_from_report,
    resolve_failure_taxonomy_screenshot,
    run_failure_taxonomy_judge,
)
from runner.tools.experiment_config import (
    load_experiment_config,
    resolve_evaluator_runtime_config,
)
from runner.tools.llm_client import token_usage_for_exception
from runner.tools.token_usage import add_token_usage, empty_token_usage
from runtime.types import JsonDict

DEFAULT_MAX_REPORT_MB = 50.0


@dataclass(frozen=True, slots=True)
class _TaxonomyCandidate:
    task_id: str
    turn: int
    pipeline_run_id: str
    resume_key: str
    bundle_root: Path
    bundle_relative_root: str
    manifest_path: Path
    report_path: Path | None


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    config = load_experiment_config(args.config)
    runtime_config = resolve_evaluator_runtime_config(config)
    model_root = args.experiment_root / "models" / args.model
    pipeline_run_id = _current_pipeline_run_id(model_root)
    output_dir = args.output_dir or model_root / "failure_taxonomy"
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / "failure_taxonomy.jsonl"
    summary_path = output_dir / "failure_taxonomy_summary.json"
    counts_path = output_dir / "failure_taxonomy_counts.csv"
    if jsonl_path.exists() and not args.overwrite and not args.resume:
        raise FileExistsError(f"Output already exists: {jsonl_path}. Pass --overwrite or --resume.")
    if args.overwrite and jsonl_path.exists():
        jsonl_path.unlink()
    completed_keys = _completed_resume_keys(jsonl_path) if args.resume else set()
    records: list[JsonDict] = []
    token_usage = empty_token_usage()
    previous_reports: dict[str, list[JsonDict]] = defaultdict(list)
    candidates = _pipeline_candidates(model_root, pipeline_run_id=pipeline_run_id)
    selected_count = 0
    max_report_bytes = int(args.max_report_mb * 1024 * 1024)
    with jsonl_path.open("a", encoding="utf-8") as stream:
        for candidate in candidates:
            source_path = candidate.report_path or candidate.manifest_path
            report = _report_for_candidate(candidate)
            task_id = candidate.task_id
            if not _should_classify(report):
                previous_reports[task_id].append(report)
                continue
            if candidate.resume_key in completed_keys:
                previous_reports[task_id].append(report)
                continue
            if args.limit is not None and selected_count >= args.limit:
                break
            size = source_path.stat().st_size
            if size > max_report_bytes:
                raise ValueError(
                    f"Failure report exceeds --max-report-mb ({size} bytes): {source_path}. "
                    "Increase the limit; silently omitting an executed non-passing slot would "
                    "make the paper taxonomy incomplete."
                )
            selected_count += 1
            if args.dry_run:
                record = _skipped_record(candidate, report=report, reason="dry run")
                _write_record(stream, record)
                records.append(record)
                previous_reports[task_id].append(report)
                continue
            screenshot_path, screenshot_evidence = resolve_failure_taxonomy_screenshot(
                candidate.bundle_root
            )
            bundle_evidence = build_failure_taxonomy_bundle_evidence(
                candidate.bundle_root,
                screenshot_evidence=screenshot_evidence,
            )
            payload = build_failure_taxonomy_payload_from_report(
                report,
                previous_reports=previous_reports[task_id],
                report_path=candidate.report_path,
                suite=args.suite or _infer_suite(args.experiment_root),
                bundle_evidence=bundle_evidence,
            )
            try:
                taxonomy = run_failure_taxonomy_judge(
                    payload,
                    runtime_config=runtime_config,
                    screenshot_path=screenshot_path,
                )
            except Exception as exc:
                taxonomy = {
                    "status": "error",
                    "error": str(exc),
                    "token_usage": token_usage_for_exception(exc),
                }
            token_usage = add_token_usage(token_usage, taxonomy.get("token_usage", {}))
            record = _record(candidate, report=report, taxonomy=taxonomy)
            _write_record(stream, record)
            records.append(record)
            previous_reports[task_id].append(report)
    existing_records = _read_jsonl(jsonl_path)
    summary = _summary(
        existing_records,
        experiment_root=args.experiment_root,
        model=args.model,
        output_dir=output_dir,
        token_usage=token_usage,
    )
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_counts_csv(counts_path, summary)
    print(
        json.dumps(
            {
                "output_jsonl": str(jsonl_path),
                "summary": str(summary_path),
                "counts_csv": str(counts_path),
                "processed_this_run": len(records),
                "completed_total": summary["completed"],
                "errors_total": summary["errors"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Posthoc failure taxonomy annotation for completed EvoGenUI-Bench evaluations."
    )
    parser.add_argument("--experiment-root", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--suite", default="")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-report-mb", type=float, default=DEFAULT_MAX_REPORT_MB)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.overwrite and args.resume:
        parser.error("--overwrite and --resume are mutually exclusive")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be >= 1")
    if args.max_report_mb <= 0:
        parser.error("--max-report-mb must be positive")
    return args


def _current_pipeline_run_id(model_root: Path) -> str:
    request_path = model_root / "request.json"
    if not request_path.exists():
        raise FileNotFoundError(f"Model request does not exist: {request_path}")
    request = _load_report(request_path)
    run_id = request.get("pipeline_run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError(f"Model request is missing pipeline_run_id: {request_path}")
    return run_id


def _pipeline_candidates(
    model_root: Path,
    *,
    pipeline_run_id: str,
) -> list[_TaxonomyCandidate]:
    """Enumerate model calls that received a response in the active pipeline lineage.

    A slot is considered executed once the generation manifest records
    ``response_received=true``. This intentionally excludes provider no-response
    failures and later dependency-blocked slots for which no model call ran.
    """

    runs_root = model_root / "runs"
    if not runs_root.exists():
        raise FileNotFoundError(f"Turn bundles directory does not exist: {runs_root}")
    candidates: list[_TaxonomyCandidate] = []
    seen_slots: set[tuple[str, int]] = set()
    for task_dir in sorted(path for path in runs_root.iterdir() if path.is_dir()):
        for turn_dir in sorted(
            (path for path in task_dir.iterdir() if path.is_dir()),
            key=lambda path: (_turn_number(path), path.name),
        ):
            manifest_path = turn_dir / pipeline_run_id / "manifest.json"
            if not manifest_path.exists():
                continue
            manifest = _load_report(manifest_path)
            task_id = str(manifest.get("task_id") or task_dir.name)
            turn = _manifest_turn(manifest, turn_dir)
            manifest_run_id = manifest.get("run_id")
            if manifest_run_id != pipeline_run_id:
                raise ValueError(
                    f"Turn bundle lineage mismatch in {manifest_path}: "
                    f"expected {pipeline_run_id!r}, found {manifest_run_id!r}"
                )
            stages = manifest.get("stages")
            generation = stages.get("generation") if isinstance(stages, dict) else None
            if not isinstance(generation, dict) or generation.get("response_received") is not True:
                continue
            slot = (task_id, turn)
            if slot in seen_slots:
                raise ValueError(
                    f"Duplicate turn bundle for current pipeline lineage: {task_id!r} turn {turn}"
                )
            seen_slots.add(slot)
            bundle_root = manifest_path.parent
            evaluation_report_path = bundle_root / "evaluation" / "report.json"
            candidates.append(
                _TaxonomyCandidate(
                    task_id=task_id,
                    turn=turn,
                    pipeline_run_id=pipeline_run_id,
                    resume_key=_resume_key(pipeline_run_id, task_id, turn),
                    bundle_root=bundle_root,
                    bundle_relative_root=bundle_root.relative_to(model_root).as_posix(),
                    manifest_path=manifest_path,
                    report_path=(
                        evaluation_report_path if evaluation_report_path.exists() else None
                    ),
                )
            )
    return sorted(candidates, key=lambda item: (item.task_id, item.turn, item.resume_key))


def _turn_number(path: Path) -> int:
    name = path.stem if path.suffix else path.name
    if name.startswith("turn-"):
        try:
            return int(name.removeprefix("turn-"))
        except ValueError:
            return 0
    return 0


def _manifest_turn(manifest: JsonDict, turn_dir: Path) -> int:
    value = manifest.get("turn")
    if isinstance(value, bool):
        raise ValueError(f"Turn bundle has invalid turn in {turn_dir / 'manifest.json'}")
    try:
        turn = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Turn bundle has invalid turn in {turn_dir / 'manifest.json'}") from exc
    if turn < 1 or _turn_number(turn_dir) != turn:
        raise ValueError(f"Turn bundle path/manifest mismatch in {turn_dir / 'manifest.json'}")
    return turn


def _load_report(path: Path) -> JsonDict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Report is not a JSON object: {path}")
    return value


def _load_optional_report(path: Path) -> JsonDict:
    return _load_report(path) if path.exists() else {}


def _report_for_candidate(candidate: _TaxonomyCandidate) -> JsonDict:
    if candidate.report_path is not None:
        report = _load_report(candidate.report_path)
        _validate_candidate_report(candidate, report)
        return report
    return _synthetic_report_from_bundle(candidate)


def _validate_candidate_report(candidate: _TaxonomyCandidate, report: JsonDict) -> None:
    if report.get("task_id") != candidate.task_id:
        raise ValueError(
            f"Evaluation report task mismatch in {candidate.report_path}: "
            f"expected {candidate.task_id!r}, found {report.get('task_id')!r}"
        )
    try:
        report_turn = int(report.get("turn"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Evaluation report has invalid turn: {candidate.report_path}") from exc
    if report_turn != candidate.turn:
        raise ValueError(
            f"Evaluation report turn mismatch in {candidate.report_path}: "
            f"expected {candidate.turn}, found {report_turn}"
        )


def _synthetic_report_from_bundle(candidate: _TaxonomyCandidate) -> JsonDict:
    manifest = _load_report(candidate.manifest_path)
    request = _load_optional_report(candidate.bundle_root / "generation" / "request.json")
    output = _load_optional_report(candidate.bundle_root / "generation" / "output.json")
    snapshot = _load_optional_report(candidate.bundle_root / "execution" / "snapshot.json")
    actor = _load_optional_report(candidate.bundle_root / "execution" / "actor_result.json")
    generation_build = _load_optional_report(candidate.bundle_root / "generation" / "build.json")
    execution_build = _load_optional_report(candidate.bundle_root / "execution" / "build.json")
    build = execution_build or generation_build
    failure = _bundle_failure(manifest, build=build)
    if failure is None:
        raise ValueError(
            "Turn bundle has a model response but no terminal non-passing outcome or "
            f"evaluation report: {candidate.manifest_path}. Complete the pipeline before "
            "running the paper failure taxonomy."
        )
    failure_reason, failure_bucket, error = failure

    current_snapshot = dict(snapshot)
    current_snapshot.setdefault("task_id", candidate.task_id)
    current_snapshot.setdefault("turn", candidate.turn)
    current_snapshot.setdefault("user_request", str(request.get("current_user_request") or ""))
    current_snapshot.setdefault("assistant_text", str(output.get("assistant_text") or ""))
    generated_files = output.get("files")
    if "generated_files" not in current_snapshot and isinstance(generated_files, dict):
        current_snapshot["generated_files"] = generated_files
    details: JsonDict = {
        "turn_bundle": candidate.bundle_relative_root,
        "current_turn_snapshot": current_snapshot,
    }
    if actor:
        details["actor"] = actor
    if error:
        details[f"{failure_reason}_failure"] = error
    return {
        "task_id": candidate.task_id,
        "turn": candidate.turn,
        "generation_pass": _stage_status(manifest, "generation") == "completed",
        "build_pass": build.get("success") is True,
        "actor_pass": actor.get("status") == "success",
        "evaluator_pass": False,
        "evaluator_score": 0.0,
        "evaluator_summary": error,
        "official_pass": False,
        "dimensions": {},
        "failure_reason": failure_reason,
        "failure_bucket": failure_bucket,
        "details": details,
    }


def _bundle_failure(
    manifest: JsonDict,
    *,
    build: JsonDict,
) -> tuple[str, str, str] | None:
    generation_status = _stage_status(manifest, "generation")
    execution_status = _stage_status(manifest, "execution")
    evaluation_status = _stage_status(manifest, "evaluation")
    if generation_status == "failed":
        return "generation", "quality", _stage_error(manifest, "generation")
    if build and build.get("success") is not True:
        return "build", "build", _build_error(build)
    if execution_status == "failed":
        return "execute", "quality", _stage_error(manifest, "execution")
    if evaluation_status == "failed":
        return "evaluate", "quality", _stage_error(manifest, "evaluation")
    return None


def _stage_payload(manifest: JsonDict, stage: str) -> JsonDict:
    stages = manifest.get("stages")
    value = stages.get(stage) if isinstance(stages, dict) else None
    return value if isinstance(value, dict) else {}


def _stage_status(manifest: JsonDict, stage: str) -> str:
    value = _stage_payload(manifest, stage).get("status")
    return str(value) if isinstance(value, str) else ""


def _stage_error(manifest: JsonDict, stage: str) -> str:
    value = _stage_payload(manifest, stage).get("error")
    return str(value) if value is not None else ""


def _build_error(build: JsonDict) -> str:
    errors = build.get("errors")
    if isinstance(errors, list) and errors:
        return "\n".join(str(item) for item in errors)
    return str(build.get("stderr") or build.get("stdout") or "Build failed")


def _should_classify(report: JsonDict) -> bool:
    return report.get("official_pass") is not True


def _resume_key(pipeline_run_id: str, task_id: str, turn: int) -> str:
    return json.dumps(
        [pipeline_run_id, task_id, turn],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _record(
    candidate: _TaxonomyCandidate,
    *,
    report: JsonDict,
    taxonomy: JsonDict,
) -> JsonDict:
    return {
        "resume_key": candidate.resume_key,
        "pipeline_run_id": candidate.pipeline_run_id,
        "bundle_path": candidate.bundle_relative_root,
        "bundle_manifest_path": str(candidate.manifest_path),
        "report_path": str(candidate.report_path) if candidate.report_path is not None else None,
        "task_id": candidate.task_id,
        "turn": candidate.turn,
        "official_pass": report.get("official_pass"),
        "failure_reason": report.get("failure_reason"),
        "failure_bucket": report.get("failure_bucket"),
        "dimension_scores": _dimension_scores(report),
        "dimension_failure_types": _dimension_failure_types(report),
        "taxonomy": taxonomy,
    }


def _skipped_record(
    candidate: _TaxonomyCandidate,
    *,
    reason: str,
    report: JsonDict | None = None,
) -> JsonDict:
    return {
        "resume_key": candidate.resume_key,
        "pipeline_run_id": candidate.pipeline_run_id,
        "bundle_path": candidate.bundle_relative_root,
        "bundle_manifest_path": str(candidate.manifest_path),
        "report_path": str(candidate.report_path) if candidate.report_path is not None else None,
        "task_id": candidate.task_id,
        "turn": candidate.turn,
        "official_pass": (report or {}).get("official_pass"),
        "failure_reason": (report or {}).get("failure_reason"),
        "failure_bucket": (report or {}).get("failure_bucket"),
        "taxonomy": {
            "status": "skipped",
            "reason": reason,
            "token_usage": empty_token_usage(),
        },
    }


def _dimension_scores(report: JsonDict) -> JsonDict:
    dimensions = report.get("dimensions")
    if not isinstance(dimensions, dict):
        return {}
    return {
        str(name): payload.get("score")
        for name, payload in dimensions.items()
        if isinstance(payload, dict)
    }


def _dimension_failure_types(report: JsonDict) -> JsonDict:
    dimensions = report.get("dimensions")
    if not isinstance(dimensions, dict):
        return {}
    return {
        str(name): payload.get("failure_types", [])
        for name, payload in dimensions.items()
        if isinstance(payload, dict)
    }


def _write_record(stream, record: JsonDict) -> None:
    stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    stream.flush()


def _completed_resume_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        str(record.get("resume_key"))
        for record in _read_jsonl(path)
        if isinstance(record.get("resume_key"), str)
        and isinstance(record.get("taxonomy"), dict)
        and record["taxonomy"].get("status") == "completed"
    }


def _read_jsonl(path: Path) -> list[JsonDict]:
    if not path.exists():
        return []
    records: list[JsonDict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            records.append(value)
    return records


def _summary(
    records: Iterable[JsonDict],
    *,
    experiment_root: Path,
    model: str,
    output_dir: Path,
    token_usage: JsonDict,
) -> JsonDict:
    records_list = list(records)
    completed = [
        record
        for record in records_list
        if isinstance(record.get("taxonomy"), dict)
        and record["taxonomy"].get("status") == "completed"
    ]
    errors = [
        record
        for record in records_list
        if isinstance(record.get("taxonomy"), dict) and record["taxonomy"].get("status") == "error"
    ]
    skipped = [
        record
        for record in records_list
        if isinstance(record.get("taxonomy"), dict)
        and record["taxonomy"].get("status") == "skipped"
    ]
    attribution_counts = Counter(
        str(record["taxonomy"].get("attribution"))
        for record in completed
        if record["taxonomy"].get("attribution")
    )
    capability_counts_all = Counter(
        str(record["taxonomy"].get("capability_failure"))
        for record in completed
        if record["taxonomy"].get("capability_failure")
    )
    capability_counts_countable = Counter(
        str(record["taxonomy"].get("capability_failure"))
        for record in completed
        if record["taxonomy"].get("count_as_model_failure")
        and record["taxonomy"].get("capability_failure")
    )
    return {
        "experiment_root": str(experiment_root),
        "model": model,
        "output_dir": str(output_dir),
        "records": len(records_list),
        "completed": len(completed),
        "errors": len(errors),
        "skipped": len(skipped),
        "attribution_counts": dict(sorted(attribution_counts.items())),
        "capability_counts_all": dict(sorted(capability_counts_all.items())),
        "capability_counts_countable_model_failures": dict(
            sorted(capability_counts_countable.items())
        ),
        "token_usage_this_run": token_usage,
    }


def _write_counts_csv(path: Path, summary: JsonDict) -> None:
    rows: list[dict[str, object]] = []
    for scope, key in (
        ("attribution", "attribution_counts"),
        ("capability_all", "capability_counts_all"),
        (
            "capability_countable_model_failures",
            "capability_counts_countable_model_failures",
        ),
    ):
        counts = summary.get(key)
        if not isinstance(counts, dict):
            continue
        for label, count in counts.items():
            rows.append({"scope": scope, "label": label, "count": count})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["scope", "label", "count"])
        writer.writeheader()
        writer.writerows(rows)


def _infer_suite(experiment_root: Path) -> str:
    name = experiment_root.name.lower()
    if "tool-grounded" in name or "tool_grounded" in name:
        return "tool_grounded"
    if "interactive" in name:
        return "interaction"
    if "presentation" in name:
        return "presentation"
    return ""


if __name__ == "__main__":
    main()
