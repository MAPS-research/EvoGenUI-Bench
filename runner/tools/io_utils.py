from __future__ import annotations

import json
from pathlib import Path

from runtime.types import JsonDict


def read_json_file(path: Path) -> JsonDict:
    return json.loads(path.read_text(encoding="utf-8"))


def truncate_text(value: object, limit: int) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"
