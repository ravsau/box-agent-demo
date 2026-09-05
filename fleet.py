"""Launch detached workers, inspect them later, and clean up only this run."""
import argparse
import base64
import datetime as dt
import json
import os
from pathlib import Path
import re
import shlex
import uuid


def load_tasks(path):
    """Load and validate a public task file without making network calls."""
    tasks = json.loads(Path(path).read_text())
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("Tasks must be a nonempty JSON list")
    names = set()
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError("Each task must be a JSON object")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", task.get("repo", "")) or "YOUR_USER" in task["repo"]:
            raise ValueError("Set each repo to your own GitHub fork")
        name = task.get("name", "")
        if not re.fullmatch(r"[a-z0-9-]+", name) or name in names:
            raise ValueError("Task names must be unique lowercase letters, digits and hyphens")
        names.add(name)
        for key in ("base", "prompt", "verify"):
            if not isinstance(task.get(key), str) or not task[key].strip():
                raise ValueError(f"Task needs {key}")
        if task["base"].startswith("-"):
            raise ValueError("Base must be a Git branch name")
    return tasks


def command(box, text):
    result = box.exec.command(text)
    if result.exit_code != 0:
        raise RuntimeError("Remote setup command failed; inspect the Box in the console")
    return result.stdout or ""


def upload(box, name, content):
    encoded = base64.b64encode(content).decode()
    command(box, f"printf %s {shlex.quote(encoded)} | base64 -d > /workspace/home/{name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    launch = sub.add_parser("launch")
    launch.add_argument("tasks", type=Path)
    launch.add_argument("--model", required=True, help="OpenCode model ID: openrouter/provider/model")
    launch.add_argument("--run", action="store_true", help="Create Boxes and run billable agent work")
    for action in ("status", "cleanup"):
        sub.add_parser(action).add_argument("state", type=Path)
    args = parser.parse_args()
    if args.action == "launch":
        try:
            tasks = load_tasks(args.tasks)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            parser.error(str(error))
        if not args.model.startswith("openrouter/"):
            parser.error("This example configures the OpenRouter provider")
        print(f"Plan: {len(tasks)} tasks, one separate Box and branch per task.")
        if not args.run:
            print("No API calls made. Add --run to launch billable work.")
            return
        # Read credentials before creating any resources. Never upload them as files.
        pat = base64.b64encode(f"x-access-token:{os.environ['GH_BOX_PAT']}".encode()).decode()
        headers = {"github.com": {"Authorization": f"Basic {pat}"},
                   "openrouter.ai": {"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"}}
    from upstash_box import Box
    if args.action != "launch":
        state = json.loads(args.state.read_text())
        for row in state:
            if row.get("deleted"):
                continue
            box = Box.get(row["box_id"])
            if args.action == "cleanup":
                box.delete()
                row["deleted"] = True
                args.state.write_text(json.dumps(state, indent=2))
                print(f"Deleted {row['box_id']}")
            else:
                print(row["name"], row["box_id"], row["branch"])
                print(command(box, "if [ -f /workspace/home/result.json ]; then cat /workspace/home/result.json; "
                              "else echo 'No result yet'; fi; tail -n 12 /workspace/home/worker.log 2>/dev/null || true"))
        return
    run_id = uuid.uuid4().hex[:12]
    state_path = Path("runs") / f"{run_id}.json"
    state_path.parent.mkdir(exist_ok=True)
    state = []
    print(f"State: {state_path}", flush=True)
    for task in tasks:
        task = {k: task[k] for k in ("repo", "base", "name", "prompt", "verify")}
        task.update(branch=f"box/{run_id}/{task['name']}", model=args.model)
        box = Box.create(runtime="python", size="small", attach_headers=headers)
        state.append({"name": task["name"], "box_id": box.id, "repo": task["repo"],
                      "branch": task["branch"], "base": task["base"],
                      "started_at": dt.datetime.now(dt.timezone.utc).isoformat()})
        state_path.write_text(json.dumps(state, indent=2))
        upload(box, "task.json", json.dumps(task).encode())
        upload(box, "worker.py", Path(__file__).with_name("worker.py").read_bytes())
        command(box, "command -v opencode >/dev/null && command -v git >/dev/null")
        command(box, "nohup python3 -u /workspace/home/worker.py > /workspace/home/worker.log 2>&1 < /dev/null & echo $! > /workspace/home/worker.pid")
        command(box, "for i in 1 2 3 4 5; do test -f /workspace/home/started && exit 0; sleep 1; done; exit 1")
        print(f"Started {task['name']}: {box.id} -> {task['branch']}", flush=True)
    print("All workers started. They verify and push remotely. Status polling is optional.")


if __name__ == "__main__":
    main()
