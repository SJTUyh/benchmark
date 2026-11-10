import json
import socket
import unittest
import urllib.error
import urllib.request

from ais_bench.benchmark.runners.local_monitor import LocalMonitorServer


class _FakeMonitor:
    def __init__(self):
        self.output_path = "/tmp/work"
        self.tasks = [
            {
                "task_name": "m1/d1",
                "process_id": 123,
                "progress": "[##   ] 2/10",
                "status": "running",
                "task_log_path": "/tmp/m1/d1.log",
                "raw": {"status": "running", "finish_count": 2, "total_count": 10},
            },
            {
                "task_name": "m1/d2",
                "process_id": 124,
                "progress": "[#####] 10/10",
                "status": "finish",
                "task_log_path": "/tmp/m1/d2.log",
                "raw": {"status": "finish", "finish_count": 10, "total_count": 10},
            },
        ]

    def get_task_states_snapshot(self):
        return list(self.tasks)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class TestLocalMonitorServer(unittest.TestCase):
    def setUp(self):
        self.monitor = _FakeMonitor()
        self.port = _free_port()
        self.server = LocalMonitorServer(self.monitor, port=self.port)
        self.assertEqual(self.server.start(), self.port)
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.server.stop()

    def _get(self, path):
        with urllib.request.urlopen(self.base + path) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def test_health(self):
        status, data = self._get("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(data, {"status": "ok"})

    def test_tasks(self):
        status, data = self._get("/api/tasks")
        self.assertEqual(status, 200)
        self.assertEqual(data["work_dir"], "/tmp/work")
        self.assertEqual(len(data["tasks"]), 2)
        self.assertIn("updated_at", data)
        self.assertEqual(data["tasks"][0]["task_log_path"], "/tmp/m1/d1.log")
        self.assertEqual(data["tasks"][0]["raw"]["finish_count"], 2)

    def test_single_task(self):
        status, data = self._get("/api/tasks/m1/d1")
        self.assertEqual(status, 200)
        self.assertEqual(data["task_name"], "m1/d1")
        # A trailing slash is tolerated.
        status, data = self._get("/api/tasks/m1/d2/")
        self.assertEqual(status, 200)
        self.assertEqual(data["task_name"], "m1/d2")

    def test_not_found(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/tasks/m1/missing")
        self.assertEqual(ctx.exception.code, 404)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/unknown")
        self.assertEqual(ctx.exception.code, 404)

    def test_disabled_port(self):
        server = LocalMonitorServer(self.monitor, port=0)
        self.assertIsNone(server.start())
        server.stop()


if __name__ == '__main__':
    unittest.main()
