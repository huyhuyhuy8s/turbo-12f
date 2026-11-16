import importlib.util
import json
import os
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_cubic():
    spec = importlib.util.spec_from_file_location(
        "cubic_adapter_failures", os.path.join(ROOT, "adapters", "cubic.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CubicJobFailuresTest(unittest.TestCase):
    def test_no_recorded_commit_and_no_since_exits_1_without_calling_run(self):
        job = {"name": "cubic", "adapter": "cubic", "config": {}}
        calls = []

        def fake(argv, cwd, env):
            calls.append(argv)
            return 0, "", ""

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", "/repo", "--commit", "def",
                    "--home", tmp, "--out", out]
            code = load_cubic().main(argv, run=fake)
        self.assertEqual((code, calls), (1, []))


if __name__ == "__main__":
    unittest.main()
