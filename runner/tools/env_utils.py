from __future__ import annotations

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]


def _strip_inline_comment(value: str) -> str:
    value = value.strip()
    if not value:
        return value
    if value[0] in ('"', "'"):
        quote = value[0]
        end = value.find(quote, 1)
        return value[1:end] if end != -1 else value[1:]
    for index, character in enumerate(value):
        if character == "#" and index > 0 and value[index - 1] in (" ", "\t"):
            return value[:index].rstrip()
    return value


def load_env_file() -> None:
    env_path = ROOT_DIR / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = _strip_inline_comment(value)


def required_env(name: str) -> str:
    load_env_file()
    value = os.getenv(name)
    if value is None or not value.strip():
        raise EnvironmentError(f"{name} must be set explicitly in .env or the environment")
    return value.strip()


def optional_env(name: str) -> str | None:
    load_env_file()
    value = os.getenv(name)
    if value is None or not value.strip():
        return None
    return value.strip()
