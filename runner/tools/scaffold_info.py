from __future__ import annotations

from functools import lru_cache
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
SCAFFOLD_DIR = ROOT_DIR / "scaffold"

RESERVED_FILES = {
    "index.html",
    "package.json",
    "vite.config.ts",
    "src/main.tsx",
    "src/genui/toolClient.ts",
    "src/genui/resourceClient.ts",
}


@lru_cache(maxsize=1)
def _scaffold_file_list() -> list[str]:
    files: list[str] = []
    for path in sorted(SCAFFOLD_DIR.rglob("*")):
        if path.is_file():
            files.append(path.relative_to(SCAFFOLD_DIR).as_posix())
    return files


@lru_cache(maxsize=1)
def build_file_tree_summary() -> str:
    existing_lines = [f"- {path}" for path in _scaffold_file_list()]
    reserved_lines = [f"- {path}" for path in sorted(RESERVED_FILES)]
    return "\n".join(
        [
            f"Scaffold root: {SCAFFOLD_DIR.name}",
            "",
            "Pre-existing scaffold files:",
            *existing_lines,
            "",
            "Reserved scaffold files:",
            *reserved_lines,
            "",
            "Writable output scope:",
            "- Return src/App.tsx and any additional new files under src/.",
            "- Do not return index.html, package.json, vite.config.ts, src/main.tsx, or src/genui/*.",
        ]
    )
