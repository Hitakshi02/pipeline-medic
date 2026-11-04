#!/usr/bin/env python3
"""Pipeline Medic console: start and step through runs, follow the investigation, review
fixes, and browse benchmark results. Standard library only; binds to 127.0.0.1.

    python3 ui/ui.py            # http://127.0.0.1:8765
    python3 ui/ui.py --port 9000

Runs are started as subprocesses (python -m medic_agent ...) in agent/, so the console
never imports the agent loop or touches the model key; it reads the run files the agent
writes (workspace/runs, records, sandboxes) and the bench results (evals/results/).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
AGENT = PROJECT / "agent"
sys.path.insert(0, str(AGENT))
sys.path.insert(0, str(PROJECT / "world"))

from agent_kit import progress, report, settings  # noqa: E402
from agent_kit.incident_record import IncidentRecord, RecordError  # noqa: E402
from agent_kit.sandbox import Sandbox  # noqa: E402

RESULTS = PROJECT / "evals" / "results"
PYTHON = str(AGENT / ".venv" / "bin" / "python") if (AGENT / ".venv" / "bin" / "python").exists() else sys.executable


def _json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


class Jobs:
    """Agent subprocesses started from the console, with the tail of their output."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.jobs: list = []

    def start(self, argv: list, env: dict, label: str) -> dict:
        process = subprocess.Popen(argv, cwd=AGENT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, bufsize=1)
        job = {"label": label, "pid": process.pid, "lines": [], "process": process}
        with self.lock:
            self.jobs.append(job)
        threading.Thread(target=self._pump, args=(job,), daemon=True).start()
        return {"started": label, "pid": process.pid}

    def _pump(self, job: dict) -> None:
        for line in job["process"].stdout:
            with self.lock:
                job["lines"] = (job["lines"] + [line.rstrip()])[-200:]

    def running(self) -> bool:
        with self.lock:
            return any(j["process"].poll() is None for j in self.jobs)

    def snapshot(self) -> list:
        with self.lock:
            return [{"label": j["label"], "pid": j["pid"], "running": j["process"].poll() is None,
                     "exit": j["process"].poll(), "lines": j["lines"][-60:]} for j in self.jobs[-6:]]


class Console:
    def __init__(self) -> None:
        settings.load_env()  # only to know which settings are set; values never leave this process
        self.ws = settings.workspace()
        self.jobs = Jobs()

    # ---- reads ----
    def info(self) -> dict:
        return {"model": settings.model(), "effort": settings.reasoning_effort(),
                "verification": settings.verification_enabled(), "key_set": settings.has_key(),
                "running": self.jobs.running(), "control": progress.read_control(self.ws), "jobs": self.jobs.snapshot()}

    def incidents(self) -> list:
        out = []
        for folder in sorted((PROJECT / "incidents").glob("INC-*")):
            alert = (folder / "alert.md").read_text() if (folder / "alert.md").is_file() else ""
            title = alert.splitlines()[0].lstrip("# ").strip() if alert else folder.name
            out.append({"id": folder.name, "title": title, "kind": "ticket" if title.startswith("TICKET") else "page"})
        return out

    def runs(self) -> list:
        rows = []
        for run_id in progress.list_runs(self.ws):
            try:
                s = progress.load(self.ws, run_id)
            except (FileNotFoundError, ValueError):
                continue
            rows.append({k: s.get(k) for k in ("run_id", "incident_id", "status", "turn", "verification", "model",
                                               "effort", "started", "updated", "input_tokens", "output_tokens")})
        return rows

    def run(self, run_id: str) -> dict:
        state = progress.load(self.ws, run_id)
        record = IncidentRecord(self.ws, run_id).data
        box = Sandbox(self.ws, run_id)
        timeline = []
        for t in state["turns"]:
            if not t.get("turn"):
                continue
            tool = t.get("tool") or {}
            args = tool.get("arguments")
            try:
                args = json.loads(args) if isinstance(args, str) else args
            except ValueError:
                pass
            timeline.append({"turn": t["turn"], "time": t["time"], "status": t["status"], "text": t.get("text", ""),
                             "input_tokens": t.get("input_tokens", 0), "output_tokens": t.get("output_tokens", 0),
                             "tool": tool.get("name"), "arguments": args, "result": tool.get("result")})
        alert = (progress.folder(self.ws, run_id) / "alert.md")
        return {"state": {k: v for k, v in state.items() if k not in ("messages", "turns")},
                "alert": alert.read_text() if alert.is_file() else "",
                "timeline": timeline, "record": record,
                "diff": box.diff() if box.exists() else "",
                "failed": box.result("failed") if box.exists() else {},
                "latest": box.result(box.latest()) if box.exists() else {},
                "latest_name": box.latest() if box.exists() else None,
                "report": (report.reports_dir(self.ws, run_id) / "INCIDENT_REPORT.md").read_text()
                if (report.reports_dir(self.ws, run_id) / "INCIDENT_REPORT.md").is_file() else ""}

    def results(self) -> dict:
        data = _json(RESULTS / "results.json", {}) or {}
        rows = []
        path = RESULTS / "runs.jsonl"
        if path.is_file():
            for line in path.read_text().splitlines():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
        return {"results": data, "rows": rows}

    # ---- actions ----
    def _env(self, verification: bool | None = None) -> dict:
        env = dict(os.environ, PYTHONPATH=str(AGENT), PYTHONUNBUFFERED="1")
        if verification is not None:
            env["VERIFICATION"] = "on" if verification else "off"
        return env

    def _control(self, step: bool) -> None:
        progress.write_control(self.ws, {"step": bool(step), "grants": 0})

    def start(self, body: dict) -> dict:
        if self.jobs.running():
            return {"error": "a run is already in progress"}
        incident = PROJECT / "incidents" / str(body.get("incident", ""))
        if not (incident / "alert.md").is_file():
            return {"error": "pick an incident"}
        self._control(bool(body.get("step")))
        turns = str(int(body.get("turns") or 60))
        return self.jobs.start([PYTHON, "-m", "medic_agent", "run", "--incident", str(incident), "--turns", turns],
                               self._env(body.get("verification")), f"run {incident.name}")

    def resume(self, body: dict) -> dict:
        if self.jobs.running():
            return {"error": "a run is already in progress"}
        run_id = str(body.get("run_id", ""))
        self._control(bool(body.get("step")))
        turns = str(int(body.get("turns") or 20))
        return self.jobs.start([PYTHON, "-m", "medic_agent", "resume", run_id, "--turns", turns], self._env(),
                               f"resume {run_id}")

    def control(self, body: dict) -> dict:
        current = progress.read_control(self.ws)
        if "step" in body:
            current["step"] = bool(body["step"])
        current["grants"] = int(current.get("grants", 0)) + int(body.get("grant", 0))
        progress.write_control(self.ws, current)
        return current

    def release(self, body: dict) -> dict:
        import importlib.util

        spec = importlib.util.spec_from_file_location("world_release", PROJECT / "world" / "release.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        try:
            return module.release(self.ws, str(body.get("run_id", "")))
        except (module.NothingToRelease, FileNotFoundError) as error:
            return {"error": str(error)}

    def review(self, body: dict) -> dict:
        run_id, notes = str(body.get("run_id", "")), str(body.get("notes", ""))
        try:
            if body.get("decision") == "approve":
                return {"approved": str(report.approve(self.ws, run_id, notes))}
            if body.get("decision") == "reject":
                report.reject(self.ws, run_id, notes)
                return {"rejected": True}
            if body.get("decision") == "answer":
                return {"answered": report.answer(self.ws, run_id, str(body.get("request", "")), notes)}
        except (RecordError, FileNotFoundError) as error:
            return {"error": str(error)}
        return {"error": "decision must be approve, reject or answer"}


def make_handler(console: Console):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002 - quiet console
            pass

        def _send(self, payload, status: int = 200, kind: str = "application/json") -> None:
            body = payload if isinstance(payload, bytes) else json.dumps(payload, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            try:
                if url.path in ("/", "/index.html"):
                    return self._send((HERE / "index.html").read_bytes(), kind="text/html; charset=utf-8")
                if url.path == "/api/info":
                    return self._send(console.info())
                if url.path == "/api/incidents":
                    return self._send(console.incidents())
                if url.path == "/api/runs":
                    return self._send(console.runs())
                if url.path == "/api/run":
                    return self._send(console.run(parse_qs(url.query).get("id", [""])[0]))
                if url.path == "/api/results":
                    return self._send(console.results())
            except FileNotFoundError as error:
                return self._send({"error": str(error)}, HTTPStatus.NOT_FOUND)
            self._send({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._send({"error": "bad JSON"}, HTTPStatus.BAD_REQUEST)
            routes = {"/api/start": console.start, "/api/resume": console.resume, "/api/control": console.control,
                      "/api/release": console.release, "/api/review": console.review}
            action = routes.get(urlparse(self.path).path)
            if action is None:
                return self._send({"error": "not found"}, HTTPStatus.NOT_FOUND)
            self._send(action(body if isinstance(body, dict) else {}))

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline Medic console")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(Console()))
    print(f"Pipeline Medic console: http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
