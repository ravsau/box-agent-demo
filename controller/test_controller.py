import datetime as dt
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

import controller


MODEL = "openrouter/provider/model"


class Result:
    def __init__(self, stdout, exit_code=0):
        self.stdout = stdout
        self.exit_code = exit_code


class Box:
    def __init__(self, output=None, error=None):
        self.output = output
        self.error = error

    class Exec:
        def __init__(self, owner):
            self.owner = owner

        def command(self, _command):
            if self.owner.error:
                raise self.owner.error
            return Result(self.owner.output)

    @property
    def exec(self):
        return self.Exec(self)


class ControllerTests(unittest.TestCase):
    def task_file(self):
        temporary = tempfile.TemporaryDirectory()
        path = Path(temporary.name) / "tasks.json"
        path.write_text(json.dumps([{
            "repo": "someone/box-agent-demo", "base": "main", "name": "docs",
            "prompt": "Write accurate documentation.", "verify": "test -s README.md",
        }]))
        self.addCleanup(temporary.cleanup)
        return path

    def test_offline_readiness_state_names_tasks_and_does_not_launch(self):
        path = self.task_file()
        tasks = controller.load_tasks(path)
        state = controller.build_state(tasks, path, MODEL)
        self.assertEqual(state["run"]["phase"], "ready")
        self.assertEqual(state["tasks"][0]["status"], "queued")
        self.assertIn("docs", state["prompt"])
        self.assertIn("python fleet.py launch", state["prompt"])
        self.assertNotIn("--run`", state["prompt"].split("Preview", 1)[1].split(".", 1)[0])
        self.assertNotIn("--run", controller.launch_command(path, MODEL))
        self.assertEqual(controller.launch_command(path, MODEL, run=True)[-1], "--run")
        self.assertEqual(controller.launch_command(path, MODEL)[3], str(path.resolve()))

    def test_monitor_maps_receipts_without_exposing_error_text(self):
        status, detail = controller.monitor_box(Box('RESULT\n{"status":"failed","error":"sensitive remote detail"}'), {})
        self.assertEqual(status, "failed")
        self.assertNotIn("sensitive", detail)
        self.assertEqual(controller.monitor_box(Box("NO_RECEIPT\n"), {})[0], "unknown")
        self.assertEqual(controller.monitor_box(Box(error=RuntimeError("remote detail")), {})[0], "unknown")

    def test_monitor_distinguishes_running_and_timed_out(self):
        self.assertEqual(controller.monitor_box(Box("RUNNING\n"), {})[0], "running")
        old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).isoformat()
        self.assertEqual(controller.monitor_box(Box("RUNNING\n"), {"started_at": old}, timeout_seconds=60)[0], "timed_out")

    def test_server_allows_only_fixed_routes(self):
        path = self.task_file()
        server = controller.ThreadingHTTPServer(("127.0.0.1", 0), controller.DashboardHandler)
        server.state_provider = controller.make_state_provider(path, MODEL)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        with urlopen(base + "/api/state") as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(json.load(response)["run"]["phase"], "ready")
        for blocked in ("/fleet.py", "/../README.md", "/api/other"):
            with self.assertRaises(HTTPError) as error:
                urlopen(base + blocked)
            self.assertEqual(error.exception.code, 404)
            error.exception.close()


if __name__ == "__main__":
    unittest.main()
