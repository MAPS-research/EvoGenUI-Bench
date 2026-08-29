from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from runner.tools.paths import resolve_output_root
from runner.tools.scaffold_info import RESERVED_FILES, SCAFFOLD_DIR
from runtime.types import BuildArtifacts, BuildResult, JsonDict, PreviewHandle, TaskDefinition

ROOT_DIR = Path(__file__).resolve().parents[2]
SHARED_NODE_MODULES_DIR = ROOT_DIR / "node_modules"
SHARED_TSC_BIN = SHARED_NODE_MODULES_DIR / "typescript" / "bin" / "tsc"
SHARED_VITE_BIN = SHARED_NODE_MODULES_DIR / "vite" / "bin" / "vite.js"
_PREVIEW_STOP_TIMEOUT_SECONDS = 5.0

# Packages that are already available in the shared node_modules.
# Model-generated code importing these will work out of the box.
BUILT_IN_PACKAGES = {
    "react",
    "react-dom",
    "react/jsx-runtime",
    "react/jsx-dev-runtime",
}


def _extract_imported_packages(files: dict[str, str]) -> set[str]:
    """Scan generated source files for external import/require statements."""
    packages: set[str] = set()
    # Match: import X from 'package' or import 'package' or require('package')
    import_re = re.compile(
        r"""(?:import\s+(?:(?:\{{[^}}]*\}}|[^'"]*)\s+from\s+)?|require\s*\(\s*)['"]([^'"./][^'"]*)['"]""",
        re.MULTILINE,
    )
    for contents in files.values():
        if not isinstance(contents, str):
            continue
        for match in import_re.finditer(contents):
            spec = match.group(1)
            # Skip relative and absolute paths (e.g. "./styles.css", "/absolute/path")
            if spec.startswith(".") or spec.startswith("/"):
                continue
            # Extract root package name.
            # Scoped packages: "@org/name/subpath" -> "@org/name"
            # Regular packages: "lodash/debounce" -> "lodash"
            if spec.startswith("@"):
                parts = spec.split("/")
                if len(parts) >= 2:
                    root = f"{parts[0]}/{parts[1]}"
                else:
                    continue  # invalid scoped package
            else:
                root = spec.split("/")[0]
            if root and root not in BUILT_IN_PACKAGES:
                packages.add(root)
    return packages


def _run_command(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )


def _yarn_command(*args: str) -> list[str]:
    return ["corepack", "yarn", *args]


def _copy_scaffold(destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(SCAFFOLD_DIR, destination)


def _initialize_workspace_package_boundary(workspace_dir: Path) -> None:
    (workspace_dir / "yarn.lock").write_text("", encoding="utf-8")


def _ensure_shared_node_modules() -> subprocess.CompletedProcess[str] | None:
    if SHARED_NODE_MODULES_DIR.exists() and SHARED_TSC_BIN.exists() and SHARED_VITE_BIN.exists():
        return None
    return _run_command(_yarn_command("install", "--immutable"), ROOT_DIR)


def _seed_workspace_node_modules(workspace_dir: Path) -> None:
    node_modules_dir = workspace_dir / "node_modules"
    if node_modules_dir.is_symlink():
        node_modules_dir.unlink()
    node_modules_dir.mkdir(parents=True, exist_ok=True)
    for entry in SHARED_NODE_MODULES_DIR.iterdir():
        target = node_modules_dir / entry.name
        if target.exists() or target.is_symlink():
            continue
        target.symlink_to(entry, target_is_directory=entry.is_dir())


def _safe_write(workspace_dir: Path, relative_path: str, contents: str) -> Path:
    destination = (workspace_dir / relative_path).resolve()
    workspace_root = workspace_dir.resolve()
    if workspace_root not in destination.parents and destination != workspace_root:
        raise ValueError(f"Refusing to write outside workspace: {relative_path}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(contents, encoding="utf-8")
    return destination


def _validate_generated_file_path(relative_path: str) -> None:
    normalized = Path(relative_path).as_posix()
    if normalized in RESERVED_FILES:
        raise ValueError(f"Model may not overwrite reserved scaffold file: {normalized}")
    if not normalized.startswith("src/"):
        raise ValueError(f"Model output file must stay under src/: {normalized}")


def build_workspace(
    task: TaskDefinition,
    output: JsonDict,
    run_id: str,
    *,
    output_root: Path | None = None,
) -> BuildResult:
    outputs_dir = resolve_output_root(output_root=output_root) / "runs"
    run_root = outputs_dir / task.task_id / f"turn-{task.turn_index}" / run_id
    workspace_dir = run_root / "workspace"
    _copy_scaffold(workspace_dir)
    _initialize_workspace_package_boundary(workspace_dir)

    # Temporarily remove the preview-only runtime script so Vite build does not fail
    # on a missing module. We will re-inject it into dist/index.html after bundling.
    index_html = workspace_dir / "index.html"
    original_index_html = ""
    if index_html.exists():
        original_index_html = index_html.read_text(encoding="utf-8")
        index_html.write_text(
            original_index_html.replace('<script src="/__genui_runtime.js"></script>', ""),
            encoding="utf-8",
        )

    install = _ensure_shared_node_modules()
    if install is not None and install.returncode != 0:
        return BuildResult(
            success=False,
            artifacts=None,
            stdout=install.stdout,
            stderr=install.stderr,
            errors=[install.stderr or install.stdout],
        )
    _seed_workspace_node_modules(workspace_dir)

    generated_files: list[Path] = []
    assistant_text_path = _safe_write(
        workspace_dir / "generated",
        "assistant_text.md",
        str(output.get("assistant_text", "")),
    )
    for relative_path, contents in output["files"].items():
        _validate_generated_file_path(relative_path)
        generated_files.append(_safe_write(workspace_dir, relative_path, contents))

    # Generated apps must stay self-contained. React and scaffold-local files are allowed,
    # but third-party package imports are not part of the tested surface.
    needed = _extract_imported_packages(output.get("files", {}))
    if needed:
        message = (
            "External package imports are not allowed in generated src/ files: "
            f"{', '.join(sorted(needed))}. Use React, local files, inline SVG, and CSS you write "
            "yourself instead."
        )
        return BuildResult(
            success=False,
            artifacts=None,
            stdout="",
            stderr=message,
            errors=[message],
        )

    # TypeScript type check: run for diagnostics but do NOT block build on type errors.
    # Generated code often has minor type imperfections; what matters is whether Vite can
    # bundle it successfully and the UI actually runs.
    typecheck = _run_command(["node", str(SHARED_TSC_BIN), "--noEmit"], workspace_dir)
    typecheck_errors: list[str] = []
    if typecheck.returncode != 0:
        typecheck_errors.append(typecheck.stderr or typecheck.stdout)

    build = _run_command(["node", str(SHARED_VITE_BIN), "build"], workspace_dir)
    success = build.returncode == 0
    errors = list(typecheck_errors)
    if not success:
        errors.append(build.stderr or build.stdout)

    # Re-inject the preview-only runtime script into the built HTML so the preview server
    # can serve the live runtime when the actor visits the page. Vite strips unknown external
    # scripts during build, so we add it back to dist/index.html after bundling.
    if success:
        dist_index = workspace_dir / "dist" / "index.html"
        if dist_index.exists():
            dist_html = dist_index.read_text(encoding="utf-8")
            if "__genui_runtime.js" not in dist_html:
                dist_index.write_text(
                    dist_html.replace(
                        "</body>",
                        '<script src="/__genui_runtime.js"></script>\n</body>',
                    ),
                    encoding="utf-8",
                )
        # Restore original index.html so preview server can inject runtime
        if original_index_html:
            index_html.write_text(original_index_html, encoding="utf-8")

    artifacts = BuildArtifacts(
        workspace_dir=workspace_dir,
        build_dir=workspace_dir / "dist",
        generated_files=generated_files,
        assistant_text_path=assistant_text_path,
    )
    return BuildResult(
        success=success,
        artifacts=artifacts if success else None,
        stdout=build.stdout,
        stderr=build.stderr,
        errors=errors,
    )


def _read_ready_file(path: Path) -> tuple[str, int] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Preview server ready file must contain an object: {path}")
    host = payload.get("host")
    port = payload.get("port")
    if not isinstance(host, str) or not isinstance(port, int):
        raise ValueError(f"Preview server ready file is missing host/port: {path}")
    return host, port


def _wait_for_ready_file(path: Path, *, timeout_seconds: float = 10.0) -> tuple[str, int]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        ready = _read_ready_file(path)
        if ready is not None:
            return ready
        time.sleep(0.1)
    raise TimeoutError(f"Timed out waiting for preview server readiness file {path}")


def start_preview_server(
    task: TaskDefinition,
    artifacts: BuildArtifacts,
    *,
    host: str = "127.0.0.1",
    port: int | None = None,
    initial_runtime_state: JsonDict | None = None,
) -> PreviewHandle:
    log_dir = artifacts.workspace_dir / "generated" / "preview_server"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_log = log_dir / "stdout.log"
    stderr_log = log_dir / "stderr.log"
    selected_port = port or 0
    last_error: Exception | None = None
    for attempt_number in range(1, 4):
        ready_file = log_dir / f"ready-{attempt_number}.json"
        if ready_file.exists():
            ready_file.unlink()
        runtime_state_file = log_dir / f"runtime-state-{attempt_number}.json"
        if initial_runtime_state:
            runtime_state_file.write_text(
                json.dumps(initial_runtime_state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        elif runtime_state_file.exists():
            runtime_state_file.unlink()
        command = [
            sys.executable,
            "-m",
            "runner.execution.preview_server",
            "--directory",
            str(artifacts.build_dir),
            "--port",
            str(selected_port),
            "--ready-file",
            str(ready_file),
            "--task-id",
            task.task_id,
            "--turn",
            str(task.turn_index),
            "--host",
            host,
        ]
        if initial_runtime_state:
            command.extend(["--runtime-state-file", str(runtime_state_file)])
        with (
            stdout_log.open("a", encoding="utf-8") as stdout_handle,
            stderr_log.open("a", encoding="utf-8") as stderr_handle,
        ):
            process = subprocess.Popen(
                command,
                cwd=ROOT_DIR,
                stdout=stdout_handle,
                stderr=stderr_handle,
            )
        try:
            ready_host, ready_port = _wait_for_ready_file(ready_file)
            return PreviewHandle(
                process_id=process.pid,
                base_url=f"http://{ready_host}:{ready_port}",
                port=ready_port,
                process=process,
            )
        except Exception as exc:
            last_error = exc
            stop_preview_server(
                PreviewHandle(
                    process_id=process.pid,
                    base_url="",
                    port=int(selected_port or 0),
                    process=process,
                )
            )
            if attempt_number < 3:
                time.sleep(float(attempt_number))
    stdout_excerpt = stdout_log.read_text(encoding="utf-8")[-4000:] if stdout_log.exists() else ""
    stderr_excerpt = stderr_log.read_text(encoding="utf-8")[-4000:] if stderr_log.exists() else ""
    details = "\n".join(
        part
        for part in (
            f"stdout:\n{stdout_excerpt}".strip() if stdout_excerpt.strip() else "",
            f"stderr:\n{stderr_excerpt}".strip() if stderr_excerpt.strip() else "",
        )
        if part
    )
    raise TimeoutError(
        "Preview server failed to start after 3 attempts" + (f"\n{details}" if details else "")
    ) from last_error


def stop_preview_server(handle: PreviewHandle) -> None:
    process = handle.process
    if process is not None:
        if process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=_PREVIEW_STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=_PREVIEW_STOP_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                return
        except ProcessLookupError:
            return
        return
    try:
        os.kill(handle.process_id, 15)
    except ProcessLookupError:
        return
