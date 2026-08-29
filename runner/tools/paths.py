from __future__ import annotations

from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]


def experiments_root() -> Path:
    return ROOT_DIR / "experiments"


def resolve_output_root(
    *,
    output_root: Path | None = None,
    experiment_id: str | None = None,
) -> Path:
    if experiment_id:
        base_root = Path(output_root) if output_root is not None else experiments_root()
        return (base_root / experiment_id).resolve()
    if output_root is not None:
        return Path(output_root).resolve()
    return experiments_root().resolve()
