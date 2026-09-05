"""Runs inside a Box. Agent work, verification and branch delivery stay remote."""
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback


def work(task, root):
    repo = root / "repo"

    def run(args, cwd=repo, **kwargs):
        return subprocess.run(args, cwd=cwd, check=True, timeout=1200, **kwargs)

    run(["git", "clone", "--branch", task["base"], "--single-branch",
         task.get("clone_url", f"https://github.com/{task['repo']}.git"), str(repo)], cwd=root)
    run(["git", "switch", "-c", task["branch"]])
    run(["git", "config", "user.name", "Box Demo Agent"])
    run(["git", "config", "user.email", "box-demo@example.invalid"])
    env = dict(os.environ, OPENROUTER_API_KEY="dummy",
               OPENCODE_CONFIG_CONTENT=json.dumps({"permission": {
                   "*": "allow", "external_directory": "deny", "question": "deny"}}))
    prompt = (f"Work only in {repo}. {task['prompt']}\n"
              f"Verification command: {task['verify']}\n"
              "Do not commit, push, open PRs, modify checks to hide failures, or fake tools. "
              "The worker will verify and push your changes. Stop and explain if blocked.")
    run(["opencode", "run", "-m", task["model"], prompt], env=env)
    run(["bash", "-e", "-o", "pipefail", "-c", task["verify"]])
    run(["git", "diff", "--check"])
    changes = run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout
    if not changes.strip():
        raise RuntimeError("Agent produced no changes; no branch pushed")
    run(["git", "add", "--all"])
    run(["git", "commit", "-m", f"Demo: {task['name']}"])
    run(["git", "push", "origin", f"HEAD:refs/heads/{task['branch']}"])
    return {"status": "pushed", "repo": task["repo"], "branch": task["branch"],
            "verify": task["verify"], "review": "Human review still needed"}


def main():
    root = Path(__file__).resolve().parent
    (root / "started").touch()
    try:
        result = work(json.loads((root / "task.json").read_text()), root)
    except Exception as exc:
        traceback.print_exc()
        result = {"status": "failed", "error": str(exc)}
    (root / "result.json").write_text(json.dumps(result, indent=2))
    return 0 if result["status"] == "pushed" else 1


if __name__ == "__main__":
    sys.exit(main())
