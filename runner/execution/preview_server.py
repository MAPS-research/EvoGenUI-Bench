from __future__ import annotations

import argparse
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from runner.tools.task_loader import load_task
from runtime.python_tool_environment import PythonToolEnvironment

from .runtime_script_builder import build_runtime_script


class GenUIRequestHandler(SimpleHTTPRequestHandler):
    def __init__(
        self,
        *args,
        directory: str,
        task_id: str,
        turn: int,
        runtime_state_file: str | None,
        **kwargs,
    ) -> None:
        self.task_id = task_id
        self.turn = turn
        self.runtime_state_file = runtime_state_file
        super().__init__(*args, directory=directory, **kwargs)

    def _initial_scenario_state(self) -> dict[str, dict]:
        if not self.runtime_state_file:
            return {}
        path = Path(self.runtime_state_file)
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Runtime state file must contain an object: {path}")
        scenarios = payload.get("scenarios", payload)
        if not isinstance(scenarios, dict):
            raise ValueError(f"Runtime state scenarios must be an object: {path}")
        state_by_scenario: dict[str, dict] = {}
        for scenario, scenario_payload in scenarios.items():
            if not isinstance(scenario, str):
                continue
            state = scenario_payload
            if isinstance(scenario_payload, dict) and isinstance(scenario_payload.get("state"), dict):
                state = scenario_payload["state"]
            if isinstance(state, dict):
                state_by_scenario[scenario] = state
        return state_by_scenario

    def _python_environment(self) -> PythonToolEnvironment:
        server = self.server
        environment = getattr(server, "genui_python_environment", None)
        if environment is None:
            task = load_task(self.task_id, turn=self.turn)
            environment = PythonToolEnvironment(
                tools=task.tools,
                fixture_scenarios=task.private_eval.get("scenario_fixtures", {}),
                task_id=task.task_id,
                turn=task.turn_index,
                initial_scenario_state=self._initial_scenario_state(),
            )
            server.genui_python_environment = environment
        return environment

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/__genui_runtime.js":
            body = build_runtime_script(
                self.task_id,
                turn=self.turn,
                initial_scenario_state=self._initial_scenario_state(),
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        full_path = Path(self.directory) / parsed.path.lstrip("/")
        if parsed.path != "/" and full_path.exists():
            return super().do_GET()

        self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/__genui/tool-call":
            self._send_json(404, {"error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            if not isinstance(payload, dict):
                raise ValueError("Tool call payload must be an object")
            name = payload.get("name")
            args = payload.get("args", {})
            scenario = payload.get("scenario", "default")
            if not isinstance(name, str) or not isinstance(args, dict):
                raise ValueError("Tool call payload must include name and args")
            if not isinstance(scenario, str):
                raise ValueError("Tool call scenario must be a string")
            result, evidence = self._python_environment().call(name, args, scenario=scenario)
        except Exception as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._send_json(200, {"result": result, "evidence": evidence})

    def end_headers(self) -> None:
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "font-src 'self'; "
            "connect-src 'self'; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "frame-ancestors 'none'",
        )
        super().end_headers()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--ready-file")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--turn", type=int, default=1)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--runtime-state-file")
    args = parser.parse_args()

    handler = partial(
        GenUIRequestHandler,
        directory=args.directory,
        task_id=args.task_id,
        turn=args.turn,
        runtime_state_file=args.runtime_state_file,
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    if args.ready_file:
        ready_path = Path(args.ready_file)
        ready_path.write_text(
            json.dumps(
                {
                    "host": args.host,
                    "port": int(server.server_address[1]),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    server.serve_forever()


if __name__ == "__main__":
    main()
