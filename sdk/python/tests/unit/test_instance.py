# Copyright (c) 2026 Ant Group Corporation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import unittest
from unittest.mock import patch

from ._instance_loader import load_instance_class


class SandboxInstanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.instance_class = load_instance_class()

    def setUp(self):
        self.instance = self.instance_class()

    def tearDown(self):
        for pid in list(self.instance._procs):
            self.instance.cmd_kill(pid)
            self.instance.cmd_wait(pid, timeout=5)

    def test_stdin_eof_allows_process_to_finish(self):
        started = self.instance.cmd_start("cat", want_stdin=True)
        self.assertIsNone(started["error"])
        pid = started["pid"]
        self.assertIsNone(
            self.instance.cmd_send_stdin(pid, "hello\nworld\n", eof=True)["error"]
        )
        result = self.instance.cmd_wait(pid, timeout=10)
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["stdout"], "hello\nworld\n")

    def test_stdin_is_detached_by_default(self):
        started = self.instance.cmd_start("cat")
        error = self.instance.cmd_send_stdin(started["pid"], "unexpected")
        self.assertIn("stdin enabled", error["error"])

    def test_command_list_reports_running_state(self):
        started = self.instance.cmd_start("sleep 30")
        processes = self.instance.cmd_list()["processes"]
        process = next(item for item in processes if item["pid"] == started["pid"])
        self.assertEqual(process["cmd"], "sleep 30")
        self.assertTrue(process["running"])

    def test_command_environment_filters_framework_python_path(self):
        runtime_path = "/__yuanrong/opt/venv-py3.13/lib/python3.13/site-packages"
        with patch.dict(
            os.environ,
            {"PYTHONPATH": f"{runtime_path}:/task/python"},
        ):
            result = self.instance.cmd_run('printf %s "$PYTHONPATH"')

        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["stdout"], "/task/python")

    def test_command_environment_honors_explicit_python_path(self):
        runtime_path = "/__yuanrong/opt/venv-py3.13/lib/python3.13/site-packages"
        with patch.dict(os.environ, {"PYTHONPATH": runtime_path}):
            result = self.instance.cmd_run(
                'printf %s "$PYTHONPATH"',
                envs={"PYTHONPATH": "/explicit/python"},
            )

        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["stdout"], "/explicit/python")


if __name__ == "__main__":
    unittest.main()
