#!/usr/bin/env python3
"""Portable local dashboard and guarded entry point for the Box agent demo."""
import argparse
import datetime as dt
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
ASSETS = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet import load_tasks  # noqa: E402

ROUTES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/workflow.svg": ("workflow.svg", "image/svg+xml"),
    "/workflow.excalidraw": ("workflow.excalidraw", "application/json"),
}
def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def relative_label(path):
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return Path(path).name


def launch_command(tasks_path, model, run=False):
    command = [sys.executable, str(ROOT / "fleet.py"), "launch", str(Path(tasks_path).resolve()), "--model", model]
    if run:
        command.append("--run")
    return command


def assignment_prompt(tasks, tasks_path, model):
    names = ", ".join(task["name"] for task in tasks)
    shown = "python fleet.py launch " + relative_label(tasks_path) + " --model " + model
    return (
        f"Run the tasks selected in {relative_label(tasks_path)}: {names}.\n\n"
        f"Preview the launch with `{shown}`. After checking the plan and provider costs, "
        f"add `--run` to that command to create billable sandboxes. Each remote worker receives "
        "the prompt and verification command from its task entry, then commits and pushes a separate "
        "GitHub branch. Review every returned branch before opening a pull request."
    )


def read_launch_state(path):
    if path is None:
        return []
    rows = json.loads(Path(path).read_text())
    if not isinstance(rows, list):
        raise ValueError("Run state must be a JSON list")
    return rows


def branch_url(repo, branch):
    return f"https://github.com/{repo}/tree/{quote(branch, safe='/')}"


def monitor_box(box, row, timeout_seconds=1800):
    """Return a safe status summary. Remote logs and error text never leave this function."""
    command = (
        "if [ -f /workspace/home/result.json ]; then printf 'RESULT\\n'; "
        "head -c 65536 /workspace/home/result.json; "
        "elif [ -f /workspace/home/worker.pid ] && kill -0 \"$(cat /workspace/home/worker.pid)\" 2>/dev/null; "
        "then printf 'RUNNING\\n'; else printf 'NO_RECEIPT\\n'; fi"
    )
    try:
        result = box.exec.command(command)
        if getattr(result, "exit_code", 1) != 0:
            raise RuntimeError("status command failed")
        output = result.stdout or ""
    except Exception:
        return "unknown", "Could not confirm remote state"
    if output.startswith("RESULT\n"):
        try:
            receipt = json.loads(output.split("\n", 1)[1])
        except (ValueError, IndexError):
            return "unknown", "Remote worker returned an unreadable receipt"
        if receipt.get("status") == "pushed":
            return "ready", "Verification passed and branch was pushed"
        if receipt.get("status") == "failed":
            return "failed", "Remote worker reported failure; inspect it outside the dashboard"
        return "unknown", "Remote worker returned an unknown receipt"
    if output.startswith("RUNNING"):
        started = row.get("started_at")
        if started:
            try:
                age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(started)).total_seconds()
                if age > timeout_seconds:
                    return "timed_out", "Worker exceeded the monitor time budget; outcome is unconfirmed"
            except (TypeError, ValueError):
                pass
        return "running", "Remote worker is still running"
    return "unknown", "Worker stopped without a final receipt"


def build_state(tasks, tasks_path, model, launch_rows=None, monitor=None):
    stamp = now()
    rows = {row.get("name"): row for row in (launch_rows or []) if isinstance(row, dict)}
    items = []
    for task in tasks:
        row = rows.get(task["name"])
        status, activity = ("queued", "Prepared; no sandbox launched")
        if row:
            status, activity = ("unknown", "Launch recorded; remote outcome not checked")
            observed_at = None
            if monitor is not None:
                observation = monitor(row)
                status, activity = observation[:2]
                observed_at = observation[2] if len(observation) > 2 else stamp
        items.append({
            "id": task["name"], "name": task["name"], "repo": task["repo"],
            "title": task["prompt"].splitlines()[0][:160], "status": status,
            "branch": row.get("branch") if row else None, "last_activity": activity,
            "last_update": observed_at if row and monitor is not None else None,
            "result_url": branch_url(task["repo"], row["branch"]) if row and status == "ready" else None,
            "result_kind": "branch", "detail": activity,
        })
    statuses = {item["status"] for item in items}
    if not rows:
        phase, message = "ready", "Tasks are ready. Previewing and serving are offline."
    elif statuses == {"ready"}:
        phase, message = "complete", "All branches returned. Human review is still required."
    elif statuses & {"failed", "timed_out", "unknown"}:
        phase, message = "attention", "Some remote outcomes need inspection."
    else:
        phase, message = "running", "Remote workers are running."
    started = next((row.get("started_at") for row in rows.values() if row.get("started_at")), None)
    events = [{"at": stamp, "message": f"{item['name']}: {item['last_activity']}",
               "level": "warning" if item["status"] in {"failed", "timed_out", "unknown"} else "info"}
              for item in items if item["status"] != "queued"]
    return {"run": {"id": Path(tasks_path).stem, "phase": phase, "started_at": started,
                    "updated_at": stamp, "message": message},
            "tasks": items, "events": events,
            "prompt": assignment_prompt(tasks, tasks_path, model)}


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/state":
            try:
                data = json.dumps(self.server.state_provider()).encode()
            except (OSError, ValueError, json.JSONDecodeError):
                self.send_error(500, "Could not read dashboard state")
                return
            mime = "application/json"
        elif path in ROUTES:
            filename, mime = ROUTES[path]
            try:
                data = (ASSETS / filename).read_bytes()
            except FileNotFoundError:
                self.send_error(404)
                return
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self'; connect-src 'self'")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def make_state_provider(tasks_path, model, state_path=None, remote=False, timeout_seconds=1800):
    tasks = load_tasks(tasks_path)
    rows = read_launch_state(state_path)
    if not remote:
        return lambda: build_state(tasks, tasks_path, model, read_launch_state(state_path))

    class CachedRemoteState:
        def __init__(self):
            self.lock = threading.Lock()
            self.observations = {}
            self.value = build_state(tasks, tasks_path, model, rows)
            threading.Thread(target=self.refresh_loop, daemon=True).start()

        def __call__(self):
            with self.lock:
                return self.value

        def refresh_loop(self):
            from upstash_box import Box

            while True:
                try:
                    current_rows = read_launch_state(state_path)
                except (OSError, ValueError, json.JSONDecodeError):
                    time.sleep(5)
                    continue
                for row in current_rows:
                    try:
                        box = Box.get(row["box_id"])
                        status, detail = monitor_box(box, row, timeout_seconds)
                    except Exception:
                        status, detail = "unknown", "Could not connect to the recorded sandbox"
                    self.observations[row.get("name")] = (status, detail, now())
                    state = build_state(
                        tasks, tasks_path, model, current_rows,
                        lambda item: self.observations.get(
                            item.get("name"),
                            ("unknown", "Waiting for the first remote status check", None),
                        ),
                    )
                    with self.lock:
                        self.value = state
                time.sleep(5)

    return CachedRemoteState()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("check", "serve", "launch"):
        command = sub.add_parser(action)
        command.add_argument("tasks", type=Path)
        command.add_argument("--model", required=True, help="OpenCode model ID: openrouter/provider/model")
        if action == "serve":
            command.add_argument("--state", type=Path, help="State file printed by a fleet launch")
            command.add_argument("--monitor", action="store_true", help="Read safe status receipts from recorded Boxes")
            command.add_argument("--timeout-seconds", type=int, default=1800)
            command.add_argument("--port", type=int, default=8765)
        if action == "launch":
            command.add_argument("--run", action="store_true", help="Create Boxes and run billable agent work")
    args = parser.parse_args()
    if not args.model.startswith("openrouter/"):
        parser.error("This example configures the OpenRouter provider")
    try:
        tasks = load_tasks(args.tasks)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    if args.action == "check":
        state = build_state(tasks, args.tasks, args.model)
        print(state["prompt"])
        print("\nOffline readiness check passed. No cloud or model calls made.")
        return 0
    if args.action == "launch":
        return subprocess.run(launch_command(args.tasks, args.model, args.run), cwd=ROOT).returncode
    provider = make_state_provider(args.tasks, args.model, args.state, args.monitor, args.timeout_seconds)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), DashboardHandler)
    server.state_provider = provider
    print(f"Dashboard: http://127.0.0.1:{args.port}", flush=True)
    print("Remote status reads enabled." if args.monitor else "Offline dashboard; remote status reads disabled.", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    raise SystemExit(main())
