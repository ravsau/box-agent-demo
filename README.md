# Coding agents on Upstash Box

Give each coding task its own cloud sandbox, then review the branches that come back.
This is a small companion example for CloudYeti's Upstash Box video. Upstash sponsors the video.

Your laptop launches the workers. Each Box clones your repo, runs OpenCode, checks
the changes, and pushes a separate branch. You decide whether to open a PR.
The sample tasks add useful documentation to your fork of this repo.

The optional `controller/` directory is a portable adaptation of the recording dashboard
used to explain the workflow. It reads the same task and run-state files as `fleet.py`.
It does not contain the filmed task fleet, private run artifacts, or automatic PR creation.

## Try it

You need Python, the GitHub CLI, an Upstash Box account, and an OpenRouter API key.
Running the example can incur Box and model charges. Check your provider's billing limits first.

Fork and clone:

```bash
gh repo fork ravsau/box-agent-demo --clone
cd box-agent-demo
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp tasks.example.json tasks.local.json
```

Edit `tasks.local.json`: replace `YOUR_USER` with your GitHub username.
Keep the sample tasks, or supply a small task from a repo you own, its base branch,
and the command you want the worker to run to check the result.
For code changes, use that project's relevant tests as `verify`.

Set credentials in your terminal. In Zsh, these prompts hide typed values:

```bash
read -rs 'UPSTASH_BOX_API_KEY?Upstash Box key: '; echo
read -rs 'OPENROUTER_API_KEY?OpenRouter key: '; echo
read -rs 'GH_BOX_PAT?GitHub token: '; echo
export UPSTASH_BOX_API_KEY OPENROUTER_API_KEY GH_BOX_PAT
```

The prompt syntax above is for **Zsh**. In **Bash**, use `read -rs -p 'Prompt: ' VARIABLE` instead.
Create a fine-grained GitHub token restricted to your target fork with **Contents: read and write**.
PR creation later uses your local `gh` login.

Choose an available OpenRouter model in OpenCode's `openrouter/provider/model` format.
Preview the launch without calling any API:

```bash
python fleet.py launch tasks.local.json --model openrouter/PROVIDER/MODEL
```

You can run the controller's equivalent offline readiness check and dashboard:

```bash
python controller/controller.py check tasks.local.json --model openrouter/PROVIDER/MODEL
python controller/controller.py serve tasks.local.json --model openrouter/PROVIDER/MODEL
```

The dashboard listens on `127.0.0.1` and serves only its fixed UI assets and
`/api/state`. It never launches work. The instructions shown in the UI name the tasks
loaded from your file and show the same terminal command the controller uses.

Replace `PROVIDER/MODEL` with your chosen model ID. When ready to spend on the run:

```bash
python fleet.py launch tasks.local.json --model openrouter/PROVIDER/MODEL --run
```

The controller also requires the explicit `--run` flag before it passes a launch to
`fleet.py`:

```bash
python controller/controller.py launch tasks.local.json \
  --model openrouter/PROVIDER/MODEL --run
```

The launcher prints a state file path such as `runs/abc123.json`. Keep it.
Wait for **All workers started** before disconnecting your laptop.
Task execution, verification, and pushing happen inside each Box. The launcher exits;
there is no local retry loop to keep alive. Each worker makes a single agent attempt.
An error stops that worker and leaves its log for inspection.

## Check the results and open a PR

Use the actual state path printed by your launch:

```bash
python fleet.py status runs/abc123.json
```

To show safe live status in the local dashboard, pass the state file and opt into
read-only remote checks:

```bash
python controller/controller.py serve tasks.local.json \
  --model openrouter/PROVIDER/MODEL \
  --state runs/abc123.json --monitor
```

The monitor reads only the worker's structured result and process liveness. It does not
put worker logs or remote error text into the UI. A missing receipt is `unknown`; a worker
that exceeds the monitor window is `timed out`; `failed` is reserved for a structured
failure receipt. A returned branch is still waiting for human review. Fetch it, inspect
the diff, and create a pull request only if you want to propose the change.

Status checks are optional. A `pushed` result means the configured check succeeded and
a branch was pushed. Read the diff yourself before submitting it.
The example documentation checks only establish that a file exists and the diff has
no whitespace errors. You still need to check its content.

Copy a branch name from the status output, then review and open a PR **on your fork**:

```bash
git fetch origin
git diff origin/main...origin/box/RUN_ID/TASK_NAME
gh pr create --repo YOUR_USER/box-agent-demo --base main \
  --head box/RUN_ID/TASK_NAME --web
```

For parallel work, add task objects with unique names to `tasks.local.json`.
Each task gets its own Box and branch, even when tasks use the same repository.
Review overlapping changes before merging.

## Clean up

After reviewing results and saving any logs you need:

```bash
python fleet.py cleanup runs/abc123.json
```

This deletes only the Boxes recorded in that file. It leaves pushed branches intact.
If launch fails partway through, use its printed state file to clean up created Boxes.
If creation was interrupted before an ID was saved, check the Upstash console too.
Workers do not delete themselves. Check the console for remaining resources and actual charges.

## How it works

- `fleet.py` creates Boxes, uploads the worker and task, and starts detached workers.
- `worker.py` runs OpenCode, executes your check, and commits and pushes the result.
- `tasks.example.json` is the task list to copy and edit.

Credentials go through Upstash's outbound header injection. The worker uses a dummy
OpenRouter key locally; the proxy supplies the configured credential on outgoing requests.
The agent can use the access those headers grant, so restrict the GitHub token to your fork.
Sources: [Upstash attach headers](https://upstash.com/docs/box/overall/attach-headers),
[OpenCode CLI](https://opencode.ai/docs/cli/),
[OpenCode permissions](https://opencode.ai/docs/permissions/).

This example uses the Box runtime's installed OpenCode CLI and starts it through `exec`.
It does not use Upstash's managed agent runner. The subprocess timeout is defined in
[`worker.py`](worker.py); Box lifecycle and account limits still apply.

**Validation:** local worker checks use a stand-in agent and a local Git remote.
This companion has not yet been run end to end against live Box and model services.
It contains no claimed video results or measured costs.

## Questions or something broke?

[Open an issue](https://github.com/ravsau/box-agent-demo/issues/new) with what you tried,
what happened, and a redacted error. Suggestions and small fixes are welcome.
Never include API keys or unredacted logs.
