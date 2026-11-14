"""adapters/omp_rpc.py against tests/fake_omp.py: the RPC driver's session
control, with no omp, no model and no network."""

import json
import os
import shlex
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAKE = os.path.join(ROOT, "tests", "fake_omp.py")
sys.path.insert(0, os.path.join(ROOT, "adapters"))
import omp_rpc  # noqa: E402

DONE = {"name": "done", "label": "Done", "description": "Finish the task.",
        "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}


def checker(name, arguments):
    """Accept any call that carries no `wrong` argument; an accepted call is final."""
    if "wrong" in arguments:
        return False, "unexpected argument: wrong", False
    return True, "accepted", True


class OmpArgvCase(unittest.TestCase):
    def test_the_base_flags_isolate_and_go_headless(self):
        argv = omp_rpc.omp_argv(["omp"])
        self.assertEqual(argv, ["omp", "--mode", "rpc", "--no-ui", "--no-skills", "--no-rules",
                                "--no-context-files", "--no-extensions", "--no-tools"])

    def test_no_tools_means_no_tools_flag_and_no_tools_list(self):
        argv = omp_rpc.omp_argv(["omp"], tools=())
        self.assertIn("--no-tools", argv)
        self.assertNotIn("--tools", argv)

    def test_tools_are_a_comma_list_and_replace_no_tools(self):
        argv = omp_rpc.omp_argv(["omp"], tools=("read", "grep"))
        self.assertEqual(argv[argv.index("--tools") + 1], "read,grep")
        self.assertNotIn("--no-tools", argv)

    def test_every_optional_flag_appears_when_set(self):
        argv = omp_rpc.omp_argv(["bunx", "omp"], profile="t12", model="m-1", thinking="high",
                                system_prompt="You build.", extensions=("a.ts", "b.ts"),
                                session_dir="/s", max_time="10m", max_cost=1.5)
        self.assertEqual(argv[:2], ["bunx", "omp"])
        for flag, value in (("--profile", "t12"), ("--model", "m-1"), ("--thinking", "high"),
                            ("--system-prompt", "You build."),
                            ("--session-dir", "/s"), ("--max-time", "10m"), ("--max-cost", "1.5")):
            self.assertEqual(argv[argv.index(flag) + 1], value)
        self.assertEqual([argv[i + 1] for i, a in enumerate(argv) if a == "-e"], ["a.ts", "b.ts"])

    def test_mounted_skills_drop_no_skills_and_pass_the_config_overlay(self):
        argv = omp_rpc.omp_argv(["omp"], tools=("read",), config=["/s/a.yml"], skills_enabled=True)
        self.assertNotIn("--no-skills", argv)
        self.assertEqual(argv[argv.index("--config") + 1], "/s/a.yml")
        for flag in ("--no-rules", "--no-context-files", "--no-extensions"):
            self.assertIn(flag, argv)

    def test_skills_stay_off_without_the_switch_and_no_overlay_adds_no_config(self):
        argv = omp_rpc.omp_argv(["omp"], tools=("read",))
        self.assertIn("--no-skills", argv)
        self.assertNotIn("--config", argv)

    def test_unset_options_add_no_flags(self):
        argv = omp_rpc.omp_argv(["omp"], tools=("read",))
        for flag in ("--profile", "--model", "--thinking", "--system-prompt", "--session-dir",
                     "--max-time", "--max-cost", "-e"):
            self.assertNotIn(flag, argv)


class RunWorkerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = os.path.join(self.tmp.name, "fake.log")
        self.transcript = os.path.join(self.tmp.name, "out", "transcript.jsonl")

    def script(self, *steps):
        path = os.path.join(self.tmp.name, "script.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(list(steps), fh)
        return path

    def run_fake(self, mode="", steps=(), delegate=None, on_call=checker, max_turns=50, timeout_s=10,
                 prompt="Do the task.", env=None, **kwargs):
        env = dict(os.environ, FAKE_OMP_MODE=mode, FAKE_OMP_LOG=self.log, **(env or {}))
        if steps:
            env["FAKE_OMP_SCRIPT"] = self.script(*steps)
        if delegate:
            env["FAKE_OMP_DELEGATE"] = delegate
        argv = omp_rpc.omp_argv([sys.executable, FAKE], tools=("read",))
        return omp_rpc.run_worker(argv, cwd=self.tmp.name, env=env, prompt=prompt, host_tools=[DONE],
                                  on_call=on_call, max_turns=max_turns, timeout_s=timeout_s,
                                  transcript_path=self.transcript, **kwargs)

    def records(self):
        with open(self.log, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh]

    def frames(self, kind=None):
        found = [r["frame"] for r in self.records() if "frame" in r]
        return [f for f in found if kind is None or f["type"] == kind]

    def assertProcessGone(self):
        pid = self.records()[0]["pid"]
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_an_accepted_call_ends_the_session(self):
        out = self.run_fake(steps=[{"tool": "done", "args": {"x": 1}}])
        self.assertEqual((out.status, out.detail), ("accepted", "accepted"))
        self.assertEqual(out.calls, [{"tool": "done", "args": {"x": 1}, "ok": True, "message": "accepted"}])
        self.assertEqual(len(self.frames("abort")), 1)
        result = self.frames("host_tool_result")[0]
        self.assertNotIn("isError", result)
        self.assertEqual(result["result"]["content"][0]["text"], "accepted")
        self.assertEqual(len([r for r in self.records() if "argv" in r]), 1)
        self.assertProcessGone()

    def test_host_tools_are_mounted_essential_before_state_and_prompt(self):
        self.run_fake(steps=[{"tool": "done", "args": {}}])
        order = [f["type"] for f in self.frames()]
        self.assertEqual(order[:3], ["set_host_tools", "get_state", "prompt"])
        tool = self.frames("set_host_tools")[0]["tools"][0]
        self.assertEqual((tool["name"], tool["loadMode"]), ("done", "essential"))
        self.assertEqual(self.frames("prompt")[0]["message"], "Do the task.")

    def test_a_tool_that_asks_for_loadmode_gets_essential_anyway(self):
        env = dict(os.environ, FAKE_OMP_LOG=self.log)
        argv = omp_rpc.omp_argv([sys.executable, FAKE])
        omp_rpc.run_worker(argv, cwd=self.tmp.name, env=env, prompt="p",
                           host_tools=[dict(DONE, loadMode="deferred")], on_call=checker,
                           max_turns=5, timeout_s=10, transcript_path=self.transcript)
        self.assertEqual(self.frames("set_host_tools")[0]["tools"][0]["loadMode"], "essential")

    def test_get_state_system_prompt_and_tool_names_are_captured(self):
        out = self.run_fake(steps=[{"tool": "done", "args": {}}])
        self.assertEqual(out.system_prompt, ["fake omp system prompt"])
        self.assertEqual(out.tools, ["read", "done"])

    def test_a_rejected_call_gets_iserror_and_the_model_fixes_it_in_the_same_session(self):
        out = self.run_fake("bad-then-good", steps=[{"tool": "done", "args": {"x": 2}}])
        self.assertEqual(out.status, "accepted")
        self.assertEqual([(c["args"], c["ok"]) for c in out.calls],
                         [({"wrong": True}, False), ({"x": 2}, True)])
        results = self.frames("host_tool_result")
        self.assertTrue(results[0]["isError"])
        self.assertEqual(results[0]["result"]["content"][0]["text"], "unexpected argument: wrong")
        self.assertNotIn("isError", results[1])
        self.assertEqual(len(self.frames("prompt")), 1)
        self.assertEqual(len([r for r in self.records() if "argv" in r]), 1)

    def test_more_than_max_rejections_is_rejected_and_aborts(self):
        out = self.run_fake(steps=[{"tool": "done", "args": {"wrong": 1}}], max_rejections=2)
        self.assertEqual(out.status, "rejected")
        self.assertEqual(out.detail, "unexpected argument: wrong")
        self.assertEqual(len(out.calls), 3)
        self.assertFalse(any(c["ok"] for c in out.calls))
        self.assertEqual(len(self.frames("abort")), 1)
        self.assertProcessGone()

    def test_exactly_max_rejections_still_lets_the_next_call_through(self):
        out = self.run_fake(steps=[{"tool": "done", "args": {"wrong": 1}}, {"tool": "done", "args": {"x": 3}}],
                            max_rejections=1)
        self.assertEqual(out.status, "accepted")
        self.assertEqual([c["ok"] for c in out.calls], [False, True])

    def test_turns_over_the_cap_abort_the_session(self):
        out = self.run_fake("loop", max_turns=3)
        self.assertEqual(out.status, "turn-cap")
        self.assertEqual(out.turns, 4)
        self.assertEqual(out.calls, [])
        self.assertEqual(len(self.frames("abort")), 1)
        self.assertProcessGone()

    def test_a_silent_turn_gets_one_nudge_and_the_call_after_it_is_accepted(self):
        out = self.run_fake("silent", steps=[{"tool": "done", "args": {"x": 4}}], nudge="Call done now.")
        self.assertEqual(out.status, "accepted")
        prompts = self.frames("prompt")
        self.assertEqual([p["message"] for p in prompts], ["Do the task.", "Call done now."])
        self.assertNotEqual(prompts[0]["id"], prompts[1]["id"])

    def test_a_worker_that_never_calls_gets_exactly_one_nudge_then_no_call(self):
        out = self.run_fake("never")
        self.assertEqual(out.status, "no-call")
        self.assertEqual(len(self.frames("prompt")), 2)
        self.assertEqual(out.calls, [])
        self.assertProcessGone()

    def test_ui_dialogs_are_cancelled(self):
        out = self.run_fake("ui", steps=[{"tool": "done", "args": {}}])
        self.assertEqual(out.status, "accepted")
        responses = self.frames("extension_ui_response")
        self.assertEqual(responses, [{"type": "extension_ui_response", "id": "ui_1", "cancelled": True}])

    def test_a_crash_is_an_error_with_the_stderr_tail(self):
        out = self.run_fake("crash")
        self.assertEqual(out.status, "error")
        self.assertIn("code 3", out.detail)
        self.assertIn("fake omp crashed: no model configured", out.detail)
        self.assertEqual(out.calls, [])

    def test_a_session_that_outlives_the_wall_clock_is_killed(self):
        out = self.run_fake("loop", max_turns=10 ** 9, timeout_s=0.5)
        self.assertEqual(out.status, "timeout")
        self.assertGreater(out.turns, 0)
        self.assertProcessGone()

    def test_omp_giving_up_its_own_retries_is_rate_limited(self):
        out = self.run_fake("rate-limit", steps=[{"tool": "done", "args": {}}])
        self.assertEqual(out.status, "rate-limited")
        self.assertIn("rate_limit_error", out.detail)
        self.assertIn("gave up retrying", out.detail)
        self.assertEqual(out.calls, [])
        self.assertEqual(len(self.frames("abort")), 1)
        self.assertProcessGone()

    def test_a_prompt_that_ends_on_a_provider_error_is_rate_limited(self):
        for text in ('429 {"error": {"type": "rate_limit_error"}}',
                     '529 {"type":"overloaded_error","message":"Overloaded"}',
                     "Too Many Requests"):
            with self.subTest(text=text):
                if os.path.exists(self.log):
                    os.remove(self.log)
                out = self.run_fake("rate-limit", env={"FAKE_OMP_RATE_STYLE": "stop",
                                                       "FAKE_OMP_RATE_ERROR": text})
                self.assertEqual((out.status, out.detail), ("rate-limited", text))
                self.assertEqual(len(self.frames("prompt")), 1)

    def test_other_assistant_errors_are_not_rate_limits(self):
        out = self.run_fake("rate-limit", steps=[{"tool": "done", "args": {}}],
                            env={"FAKE_OMP_RATE_STYLE": "stop",
                                 "FAKE_OMP_RATE_ERROR": "401 invalid x-api-key"})
        self.assertNotEqual(out.status, "rate-limited")
        self.assertEqual(out.status, "accepted")

    def test_a_session_after_the_scripted_limits_runs_normally(self):
        state = os.path.join(self.tmp.name, "state")
        os.makedirs(state)
        env = {"FAKE_OMP_RATE_LIMITS": "1", "FAKE_OMP_STATE": state}
        steps = [{"tool": "done", "args": {"x": 6}}]
        first = self.run_fake("rate-limit", steps=steps, env=env)
        second = self.run_fake("rate-limit", steps=steps, env=env)
        self.assertEqual((first.status, second.status), ("rate-limited", "accepted"))

    def test_a_retry_still_in_flight_at_the_wall_clock_is_rate_limited_not_a_timeout(self):
        out = self.run_fake("rate-limit", timeout_s=0.5, max_turns=10 ** 9,
                            env={"FAKE_OMP_RATE_STYLE": "stall"})
        self.assertEqual(out.status, "rate-limited")
        self.assertProcessGone()

    def test_the_transcript_records_both_directions(self):
        self.run_fake(steps=[{"tool": "done", "args": {"x": 5}}])
        with open(self.transcript, encoding="utf-8") as fh:
            lines = [json.loads(line) for line in fh]
        self.assertEqual({line["dir"] for line in lines}, {"in", "out"})
        outs = [line["frame"] for line in lines if line["dir"] == "out"]
        ins = [line["frame"] for line in lines if line["dir"] == "in"]
        self.assertEqual(outs[0]["type"], "ready")
        self.assertIn("host_tool_call", [f["type"] for f in outs])
        self.assertIn("prompt_result", [f["type"] for f in outs])
        self.assertEqual([f["type"] for f in ins][:3], ["set_host_tools", "get_state", "prompt"])
        self.assertEqual(ins[-1]["type"], "abort")

    def test_delegate_runs_a_command_with_the_prompt_on_stdin_and_uses_its_call(self):
        model = os.path.join(self.tmp.name, "model.py")
        with open(model, "w", encoding="utf-8") as fh:
            fh.write("import json, os, sys\n"
                     "print(json.dumps({'tool': 'done', 'args': {'prompt': sys.stdin.read(),"
                     " 'cwd': os.getcwd()}}))\n")
        seen = []

        def on_call(name, arguments):
            seen.append((name, arguments))
            return True, "ok", True

        out = self.run_fake(delegate=shlex.join([sys.executable, model]), on_call=on_call,
                            prompt="Write the test.")
        self.assertEqual(out.status, "accepted")
        self.assertEqual(seen, [("done", {"prompt": "Write the test.",
                                          "cwd": os.path.realpath(self.tmp.name)})])


if __name__ == "__main__":
    unittest.main()
