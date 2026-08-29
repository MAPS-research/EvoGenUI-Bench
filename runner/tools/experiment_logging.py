from __future__ import annotations

import json
import re
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from runtime.types import JsonDict

_LOGGER_LOCK = threading.Lock()
_ACTIVE_LOGGER: "ExperimentLogger | None" = None
_CLI_VERBOSE: bool = False
_COLOR_ENABLED: bool = True

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def set_cli_verbose(value: bool) -> None:
    global _CLI_VERBOSE
    _CLI_VERBOSE = value


def cli_verbose() -> bool:
    return _CLI_VERBOSE


def set_color_enabled(value: bool) -> None:
    global _COLOR_ENABLED
    _COLOR_ENABLED = value


def _supports_color() -> bool:
    return _COLOR_ENABLED and hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def color(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _supports_color() else text


def red(text: str) -> str:
    return color(text, "31")


def green(text: str) -> str:
    return color(text, "32")


def yellow(text: str) -> str:
    return color(text, "33")


def blue(text: str) -> str:
    return color(text, "34")


def cyan(text: str) -> str:
    return color(text, "36")


def bold(text: str) -> str:
    return color(text, "1")


def strip_color(text: str) -> str:
    return _ANSI_RE.sub("", text)


def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def timestamped_log_filename(prefix: str = "experiment") -> str:
    return f"{prefix}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}.log"


def _truncate(value: object, *, limit: int = 1200) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...[truncated]"


def _indent_block(value: str, *, prefix: str = "  ") -> str:
    if not value.strip():
        return ""
    return "\n".join(f"{prefix}{line}" if line else prefix.rstrip() for line in value.splitlines())


def format_log_block(title: str, body: str) -> str:
    cleaned = body.strip("\n")
    if not cleaned:
        return title
    return f"{title}\n{_indent_block(cleaned)}"


def format_command_output(
    title: str,
    *,
    stdout: str = "",
    stderr: str = "",
    limit: int = 1600,
) -> str:
    sections: list[str] = []
    cleaned_stdout = _truncate(stdout, limit=limit)
    cleaned_stderr = _truncate(stderr, limit=limit)
    if cleaned_stdout:
        sections.append(f"stdout:\n{_indent_block(cleaned_stdout)}")
    if cleaned_stderr:
        sections.append(f"stderr:\n{_indent_block(cleaned_stderr)}")
    if not sections:
        return title
    return format_log_block(title, "\n".join(sections))


def format_runtime_activity(actor_result: JsonDict, *, limit: int = 5) -> str:
    tool_logs = actor_result.get("tool_logs")
    resource_logs = actor_result.get("resource_logs")
    side_effect_logs = actor_result.get("side_effect_logs")
    confirmation_events = actor_result.get("confirmation_events")
    console_errors = actor_result.get("console_errors")
    interaction_errors = actor_result.get("interaction_errors")

    lines: list[str] = []

    if isinstance(tool_logs, list) and tool_logs:
        lines.append(f"tools: {len(tool_logs)} call(s)")
        for item in tool_logs[:limit]:
            if not isinstance(item, dict):
                continue
            status = "ERROR" if item.get("error") else "OK"
            lines.append(
                f"- [{status}] {item.get('name', '?')} scenario={item.get('scenario', 'default')}"
            )
    if isinstance(resource_logs, list) and resource_logs:
        lines.append(f"resources: {len(resource_logs)} read(s)")
        for item in resource_logs[:limit]:
            if not isinstance(item, dict):
                continue
            status = "ERROR" if item.get("error") else "OK"
            lines.append(
                f"- [{status}] {item.get('uri', '?')} scenario={item.get('scenario', 'default')}"
            )
    if isinstance(side_effect_logs, list) and side_effect_logs:
        lines.append(f"side effects: {len(side_effect_logs)}")
        for item in side_effect_logs[:limit]:
            if not isinstance(item, dict):
                continue
            lines.append(f"- {item.get('tool_name', '?')}")
    if isinstance(confirmation_events, list) and confirmation_events:
        lines.append(f"confirmations: {len(confirmation_events)}")
        for item in confirmation_events[:limit]:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- {item.get('type', '?')}: {_truncate(item.get('message', ''), limit=180)}"
            )
    if isinstance(console_errors, list) and console_errors:
        lines.append("console errors:")
        for item in console_errors[:limit]:
            lines.append(f"- {_truncate(item, limit=180)}")
    if isinstance(interaction_errors, list) and interaction_errors:
        lines.append("interaction errors:")
        for item in interaction_errors[:limit]:
            lines.append(f"- {_truncate(item, limit=180)}")

    if not lines:
        return "runtime activity\n  no tool, resource, or browser errors recorded"
    return format_log_block("runtime activity", "\n".join(lines))


def format_mapping_block(title: str, items: dict[str, object]) -> str:
    lines = [
        f"{key}={value}" for key, value in items.items() if value is not None and str(value) != ""
    ]
    return format_log_block(title, "\n".join(lines))


class ExperimentLogger:
    def __init__(self, root: Path, *, filename: str | None = None) -> None:
        self.root = root
        self.path = root / "logs" / (filename or timestamped_log_filename())

    def log(self, event: str, message: str, **fields: object) -> None:
        timestamp = utc_timestamp()
        with _LOGGER_LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(self._pretty_record(timestamp, event, message, fields))

    def _pretty_record(
        self,
        timestamp: str,
        event: str,
        message: str,
        fields: dict[str, object],
    ) -> str:
        scope = fields.get("scope")
        header = f"[{timestamp}]"
        if isinstance(scope, str) and scope.strip():
            header += f" [{scope}]"
        header += f" {self._label_for_event(event)}"

        body_lines: list[str] = [message.strip("\n")] if str(message).strip() else []
        detail_fields = {key: value for key, value in fields.items() if key != "scope"}
        if detail_fields:
            for key, value in detail_fields.items():
                if value is None:
                    continue
                rendered = (
                    value
                    if isinstance(value, str)
                    else json.dumps(value, ensure_ascii=False, indent=2)
                )
                body_lines.append(f"{key}: {rendered}")

        prefix = self._separator_for_event(event)
        if body_lines:
            joined_body = "\n".join(body_lines)
            return f"{prefix}{header}\n{_indent_block(joined_body)}\n"
        return f"{prefix}{header}\n"

    def _label_for_event(self, event: str) -> str:
        normalized = event.replace(":", "_")
        return normalized.replace("_", " ").upper()

    def _separator_for_event(self, event: str) -> str:
        if event.endswith("_started"):
            return "\n" + "=" * 72 + "\n"
        if event.endswith("_completed") or event.endswith("_failed"):
            return "-" * 72 + "\n"
        return ""


@contextmanager
def activate_experiment_logger(
    logger: ExperimentLogger | None,
) -> Iterator[ExperimentLogger | None]:
    global _ACTIVE_LOGGER
    previous = _ACTIVE_LOGGER
    _ACTIVE_LOGGER = logger
    try:
        yield logger
    finally:
        _ACTIVE_LOGGER = previous


def active_experiment_logger() -> ExperimentLogger | None:
    return _ACTIVE_LOGGER


def log_event(event: str, message: str, **fields: object) -> None:
    logger = active_experiment_logger()
    if logger is not None:
        logger.log(event, message, **fields)


def emit_cli(
    scope: str, message: str, *, event: str | None = None, level: str = "verbose", **fields: object
) -> None:
    timestamp = utc_timestamp()
    logger = active_experiment_logger()
    if logger is not None:
        logger.log(event or scope, strip_color(message), scope=scope, **fields)
    if level != "info" and not _CLI_VERBOSE:
        return
    print(f"[{timestamp}] [{scope}] {message}")
