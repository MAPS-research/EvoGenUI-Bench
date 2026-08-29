from __future__ import annotations

import difflib
import json
from pathlib import Path

from runner.tools.io_utils import truncate_text
from runtime.types import BuildArtifacts, JsonDict, TaskDefinition


def _read_text(path: Path | None) -> str:
    if path is None:
        return ""
    return path.read_text(encoding="utf-8")


def generated_source_map(artifacts: BuildArtifacts) -> dict[str, str]:
    files: dict[str, str] = {}
    workspace_root = artifacts.workspace_dir.resolve()
    for path in sorted(artifacts.generated_files):
        relative_path = path.resolve().relative_to(workspace_root).as_posix()
        files[relative_path] = path.read_text(encoding="utf-8")
    return files


def _unified_diff(before: str, after: str, *, fromfile: str, tofile: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=fromfile,
            tofile=tofile,
            lineterm="",
        )
    )


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _compact_observations(value: object) -> list[JsonDict]:
    if not isinstance(value, list):
        return []
    observations: list[JsonDict] = []
    for item in value[:20]:
        if not isinstance(item, dict):
            continue
        observations.append(
            {
                "step": item.get("step"),
                "phase": item.get("phase"),
                "visible_text": truncate_text(item.get("visible_text", ""), 6000),
                "dom_tree": truncate_text(item.get("dom_tree", ""), 6000),
                "ax_tree": truncate_text(item.get("ax_tree", ""), 6000),
                "elements": (
                    item.get("elements", [])[:40] if isinstance(item.get("elements"), list) else []
                ),
                "runtime_logs": item.get("runtime_logs", {}),
            }
        )
    return observations


def actor_snapshot_from_result(actor_result: JsonDict) -> JsonDict:
    scenario_states = actor_result.get("scenario_states", {})
    if not isinstance(scenario_states, dict):
        scenario_states = {}
    return {
        "status": actor_result.get("status"),
        "summary": actor_result.get("summary"),
        "evidence_summary": actor_result.get("evidence_summary", {}),
        "verification_checks": actor_result.get("verification_checks", []),
        "state_diffs": actor_result.get("state_diffs", []),
        "final_assessment": actor_result.get("final_assessment", {}),
        "steps": actor_result.get("steps", []),
        "observations": actor_result.get("observations", []),
        "tool_logs": actor_result.get("tool_logs", []),
        "resource_logs": actor_result.get("resource_logs", []),
        "side_effect_logs": actor_result.get("side_effect_logs", []),
        "confirmation_events": actor_result.get("confirmation_events", []),
        "scenario_states": scenario_states,
        "console_errors": actor_result.get("console_errors", []),
        "interaction_errors": actor_result.get("interaction_errors", []),
        "diagnostics": actor_result.get("diagnostics", {}),
    }


def source_diffs(previous_files: dict[str, str], current_files: dict[str, str]) -> list[JsonDict]:
    diffs: list[JsonDict] = []
    for path in sorted(set(previous_files) | set(current_files)):
        before = previous_files.get(path, "")
        after = current_files.get(path, "")
        if before == after:
            continue
        if path not in previous_files:
            status = "added"
        elif path not in current_files:
            status = "deleted"
        else:
            status = "modified"
        diffs.append(
            {
                "path": path,
                "status": status,
                "diff": _unified_diff(
                    before, after, fromfile=f"previous/{path}", tofile=f"current/{path}"
                ),
            }
        )
    return diffs


def build_turn_snapshot(
    task: TaskDefinition,
    artifacts: BuildArtifacts,
    actor_result: JsonDict,
) -> JsonDict:
    files = generated_source_map(artifacts)
    scenario_states = actor_result.get("scenario_states", {})
    if not isinstance(scenario_states, dict):
        scenario_states = {}
    actor_snapshot = actor_snapshot_from_result(actor_result)
    return {
        "task_id": task.task_id,
        "turn": task.turn_index,
        "user_request": task.user_prompt,
        "assistant_text": _read_text(artifacts.assistant_text_path).strip(),
        "generated_files": files,
        "runtime_state": {"scenarios": scenario_states},
        "final_ui": {
            "url": actor_result.get("final_url"),
            "text": actor_result.get("final_text", ""),
            "dom_tree": actor_result.get("final_dom_tree", ""),
            "ax_tree": actor_result.get("final_ax_tree", ""),
            "elements": actor_result.get("final_elements", []),
            "screenshot": actor_result.get("final_screenshot", ""),
        },
        "actor": actor_snapshot,
    }


def build_turn_diffs(previous_snapshot: JsonDict | None, current_snapshot: JsonDict) -> JsonDict:
    if previous_snapshot is None:
        return {
            "has_previous_turn": False,
            "code_diffs": [],
            "assistant_text_diff": "",
            "ui_text_diff": "",
            "tool_call_diff": "",
            "resource_read_diff": "",
        }
    previous_files = previous_snapshot.get("generated_files", {})
    current_files = current_snapshot.get("generated_files", {})
    if not isinstance(previous_files, dict) or not isinstance(current_files, dict):
        raise ValueError("Turn snapshots must contain generated_files objects")
    previous_actor = previous_snapshot.get("actor", {})
    current_actor = current_snapshot.get("actor", {})
    return {
        "has_previous_turn": True,
        "previous_turn": previous_snapshot.get("turn"),
        "current_turn": current_snapshot.get("turn"),
        "code_diffs": source_diffs(
            {str(key): str(value) for key, value in previous_files.items()},
            {str(key): str(value) for key, value in current_files.items()},
        ),
        "assistant_text_diff": _unified_diff(
            str(previous_snapshot.get("assistant_text", "")),
            str(current_snapshot.get("assistant_text", "")),
            fromfile="previous/assistant_text",
            tofile="current/assistant_text",
        ),
        "ui_text_diff": _unified_diff(
            str((previous_snapshot.get("final_ui") or {}).get("text", "")),
            str((current_snapshot.get("final_ui") or {}).get("text", "")),
            fromfile="previous/final_ui_text",
            tofile="current/final_ui_text",
        ),
        "tool_call_diff": _unified_diff(
            _json_text(previous_actor.get("tool_logs", [])),
            _json_text(current_actor.get("tool_logs", [])),
            fromfile="previous/tool_logs",
            tofile="current/tool_logs",
        ),
        "resource_read_diff": _unified_diff(
            _json_text(previous_actor.get("resource_logs", [])),
            _json_text(current_actor.get("resource_logs", [])),
            fromfile="previous/resource_logs",
            tofile="current/resource_logs",
        ),
    }


def compact_turn_history(turns: list[JsonDict]) -> list[JsonDict]:
    compacted: list[JsonDict] = []
    for turn in turns:
        actor = turn.get("actor", {}) if isinstance(turn.get("actor"), dict) else {}
        final_ui = turn.get("final_ui", {}) if isinstance(turn.get("final_ui"), dict) else {}
        compacted.append(
            {
                "turn": turn.get("turn"),
                "user_request": turn.get("user_request"),
                "assistant_text": turn.get("assistant_text", ""),
                "final_ui_text": truncate_text(final_ui.get("text", ""), 8000),
                "final_ui_elements": (
                    final_ui.get("elements", [])[:40]
                    if isinstance(final_ui.get("elements"), list)
                    else []
                ),
                "actor_status": actor.get("status"),
                "evidence_summary": actor.get("evidence_summary", {}),
                "verification_checks": (
                    actor.get("verification_checks", [])[:20]
                    if isinstance(actor.get("verification_checks"), list)
                    else []
                ),
                "state_diffs": (
                    actor.get("state_diffs", [])[:20]
                    if isinstance(actor.get("state_diffs"), list)
                    else []
                ),
                "final_assessment": actor.get("final_assessment", {}),
                "diagnostics": actor.get("diagnostics", {}),
                "observations": _compact_observations(actor.get("observations", [])),
                "tool_logs": actor.get("tool_logs", []),
                "resource_logs": actor.get("resource_logs", []),
                "side_effect_logs": actor.get("side_effect_logs", []),
                "confirmation_events": actor.get("confirmation_events", []),
            }
        )
    return compacted
