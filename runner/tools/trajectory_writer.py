from __future__ import annotations

import json
from pathlib import Path

from runtime.types import JsonDict


def append_trajectory_record(path: Path, record: JsonDict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True) + "\n")
