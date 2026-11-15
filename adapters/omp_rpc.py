"""Drive one `omp --mode rpc` worker session over JSONL on stdin/stdout.

Serves the 12 factors this way:

- 1 and 4, tools are structured outputs: the worker's final answer is a
  host tool call whose arguments `on_call` checks against the registry.
  The call is the only way to finish; text alone is never an answer.
- 2 and 3, own the prompt and the tools: the factory sends the prompt,
  mounts the host tools (always `loadMode: essential`, so they are
  top-level tools and not `xd://` devices), restricts the built-ins with
  `omp_argv`, and records the system prompt and tool list omp actually
  built (`get_state`).
- 5, evidence: every frame, both directions, goes to a transcript JSONL.
- 8 and 10, own the control flow, small bounded loops: this driver, not the
  model, counts turns, rejections and wall time, sends `abort`, and ends
  the session as soon as one call is accepted.
- 9, errors in the context window: a rejected call is answered with
  `isError`, so the model sees why and retries in the same session.
- provider load: omp retries a refused request itself. When it gives up (or
  the prompt ends on a 429/529/overloaded error), the session ends as
  "rate-limited", so the caller can run it again later; nothing was judged.

One reader thread feeds a queue, so the wall clock holds even when omp says
nothing. A second drains stderr, so a chatty omp never blocks on a full pipe
and a crash can be reported with its last words.
"""

import collections
import json
import os
import queue
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field

DRAIN_GRACE_S = 10.0
KILL_GRACE_S = 2.0
STDERR_TAIL_LINES = 50
STDERR_TAIL_CHARS = 2000
CANCEL_METHODS = frozenset({"select", "confirm", "input", "editor", "ask"})
_EOF = object()
RATE_LIMITED = re.compile(r"\b(?:429|529)\b|rate[ _-]?limit|overloaded|too many requests", re.I)
MESSAGE_FRAMES = frozenset({"message_end", "turn_end", "agent_end"})


@dataclass
class Outcome:
    """How a worker session ended.

    status: "accepted" | "rejected" | "turn-cap" | "timeout" | "no-call" | "error"
        | "rate-limited". The last means the provider refused for load (omp's
        own retries ended with `auto_retry_end` success false, or the last
        assistant message stopped with an error naming 429, 529, a rate limit
        or an overloaded provider): the session is worth running again later,
        and no work of the model was judged.
    calls: every host tool call as {"tool", "args", "ok", "message"}.
    system_prompt, tools: what omp reported from `get_state` (None if it did not).
    """

    status: str
    detail: str
    turns: int = 0
    calls: list = field(default_factory=list)
    system_prompt: list | None = None
    tools: list | None = None


def omp_argv(omp_cmd, *, profile=None, model=None, thinking=None, tools=(), system_prompt=None,
             extensions=(), session_dir=None, max_time=None, max_cost=None,
             config=(), skills_enabled=False):
    """The argv that starts an isolated, headless omp RPC session.

    `tools` restricts the built-in tools; empty means `--no-tools` (host tools
    are added on top either way). Each optional flag appears only when set.
    `--no-skills` is there unless `skills_enabled`: a session that mounts
    skills must also pass a `config` overlay that says which (and from where),
    or omp would load whatever its profile and the repo hold.
    """
    argv = [*omp_cmd, "--mode", "rpc", "--no-ui"]
    if not skills_enabled:
        argv.append("--no-skills")
    argv += ["--no-rules", "--no-context-files", "--no-extensions"]
    argv += ["--tools", ",".join(tools)] if tools else ["--no-tools"]
    for flag, value in (("--profile", profile), ("--model", model), ("--thinking", thinking),
                        ("--system-prompt", system_prompt), ("--session-dir", session_dir),
                        ("--max-time", max_time), ("--max-cost", max_cost)):
        if value is not None:
            argv += [flag, str(value)]
    for path in config:
        argv += ["--config", str(path)]
    for path in extensions:
        argv += ["-e", str(path)]
    return argv


def _kill(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


def _pump_stdout(stream, out):
    for line in stream:
        out.put(line)
    out.put(_EOF)


def _pump_stderr(stream, tail):
    for line in stream:
        tail.append(line)


class _Session:
    def __init__(self, proc, transcript, stdout_q, stderr_tail, stderr_thread, *, host_tools,
                 on_call, max_turns, deadline, timeout_s, max_rejections, nudge, prompt):
        self.proc, self.transcript = proc, transcript
        self.stdout_q, self.stderr_tail, self.stderr_thread = stdout_q, stderr_tail, stderr_thread
        self.host_tools = [dict(tool, loadMode="essential") for tool in host_tools]
        self.on_call, self.max_turns = on_call, max_turns
        self.deadline, self.timeout_s = deadline, timeout_s
        self.max_rejections, self.nudge, self.prompt = max_rejections, nudge, prompt
        self.stage = "starting"
        self.prompt_id = None
        self.nudged = False
        self.turns = 0
        self.rejections = 0
        self.last_rejection = ""
        self.calls = []
        self.extension_errors = []
        self.system_prompt = None
        self.tools = None
        self.retrying = False
        self.rate_error = None

    def record(self, direction, frame):
        self.transcript.write(json.dumps({"dir": direction, "frame": frame}) + "\n")
        self.transcript.flush()

    def send(self, frame):
        self.record("in", frame)
        try:
            self.proc.stdin.write(json.dumps(frame) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, ValueError, OSError):
            pass

    def close_stdin(self):
        try:
            self.proc.stdin.close()
        except (BrokenPipeError, ValueError, OSError):
            pass

    def send_prompt(self, pid, message):
        self.prompt_id = pid
        self.send({"id": pid, "type": "prompt", "message": message})

    def outcome(self, status, detail):
        if self.extension_errors:
            detail += "; extension_error: " + "; ".join(self.extension_errors)
        return Outcome(status, detail, self.turns, self.calls, self.system_prompt, self.tools)

    def read_frame(self):
        """The next frame; `_EOF` at end of output; None on timeout. Raw lines are recorded and skipped."""
        while True:
            try:
                item = self.stdout_q.get(timeout=max(0.0, self.deadline - time.monotonic()))
            except queue.Empty:
                return None
            if item is _EOF:
                return _EOF
            text = item.strip()
            if not text:
                continue
            try:
                frame = json.loads(text)
            except ValueError:
                self.record("out", text)
                continue
            self.record("out", frame)
            if isinstance(frame, dict):
                return frame

    def drain(self):
        """Read and record output until EOF, then wait for the process; kill it past the grace."""
        end = time.monotonic() + DRAIN_GRACE_S
        while True:
            try:
                item = self.stdout_q.get(timeout=max(0.0, end - time.monotonic()))
            except queue.Empty:
                _kill(self.proc)
                break
            if item is _EOF:
                break
            text = item.strip()
            if text:
                try:
                    self.record("out", json.loads(text))
                except ValueError:
                    self.record("out", text)
        try:
            self.proc.wait(timeout=KILL_GRACE_S)
        except subprocess.TimeoutExpired:
            _kill(self.proc)
            self.proc.wait()

    def conclude(self, status, detail, *, abort):
        if abort:
            self.send({"id": "a1", "type": "abort"})
        self.close_stdin()
        self.drain()
        return self.outcome(status, detail)

    def timed_out(self):
        _kill(self.proc)
        self.close_stdin()
        self.drain()
        if self.retrying or self.rate_error:
            return self.rate_limited()
        return self.outcome("timeout", f"no outcome within {self.timeout_s}s")

    def died(self):
        try:
            code = self.proc.wait(timeout=KILL_GRACE_S)
        except subprocess.TimeoutExpired:
            _kill(self.proc)
            code = self.proc.wait()
        self.stderr_thread.join(KILL_GRACE_S)
        if self.rate_error:
            return self.rate_limited()
        tail = "".join(self.stderr_tail).strip()[-STDERR_TAIL_CHARS:]
        detail = f"omp exited with code {code} before a tool call was accepted"
        return self.outcome("error", f"{detail}: {tail}" if tail else detail)

    def run(self):
        while True:
            frame = self.read_frame()
            if frame is None:
                return self.timed_out()
            if frame is _EOF:
                return self.died()
            verdict = self.on_frame(frame)
            if verdict:
                status, detail, abort = verdict
                return self.conclude(status, detail, abort=abort)

    def note_assistant_stop(self, frame):
        """Remember the last assistant message that stopped with a rate-limit or overload
        error; a later message that did not clears it. omp retries on its own, so the
        refusal counts only if the prompt ends (or omp gives up) with it still standing."""
        messages = frame.get("messages") if frame.get("type") == "agent_end" else [frame.get("message")]
        for message in messages or []:
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            text = str(message.get("errorMessage") or "")
            if message.get("stopReason") == "error" and RATE_LIMITED.search(text):
                self.rate_error = text
            else:
                self.rate_error = None

    def on_frame(self, frame):
        """None to keep going, or (status, detail, send_abort) to end the session."""
        kind = frame.get("type")
        if kind == "ready" and self.stage == "starting":
            self.stage = "tools"
            self.send({"id": "h1", "type": "set_host_tools", "tools": self.host_tools})
        elif kind == "response":
            return self.on_response(frame)
        elif kind == "turn_end":
            self.note_assistant_stop(frame)
            self.turns += 1
            if self.turns > self.max_turns:
                return "turn-cap", f"more than {self.max_turns} turns without an accepted call", True
        elif kind in MESSAGE_FRAMES:
            self.note_assistant_stop(frame)
        elif kind == "auto_retry_start":
            self.retrying = True
        elif kind == "auto_retry_end":
            self.retrying = False
            if frame.get("success") is False:
                return "rate-limited", f"omp gave up retrying: {frame.get('finalError') or 'no reason given'}", True
            self.rate_error = None
        elif kind == "host_tool_call":
            return self.on_host_tool_call(frame)
        elif kind == "prompt_result" and frame.get("id") == self.prompt_id:
            return self.on_prompt_result(frame)
        elif kind == "extension_ui_request":
            if frame.get("method") in CANCEL_METHODS:
                self.send({"type": "extension_ui_response", "id": frame.get("id"), "cancelled": True})
        elif kind == "extension_error":
            self.extension_errors.append(
                f"{frame.get('event', '?')} in {frame.get('extensionPath', '?')}: {frame.get('error', '?')}")
        return None

    def on_response(self, frame):
        rid = frame.get("id")
        if rid not in ("h1", "g1", self.prompt_id):
            return None
        if frame.get("success") is False:
            return "error", f"omp rejected {frame.get('command', rid)}: {frame.get('error')}", False
        if rid == "h1":
            self.stage = "state"
            self.send({"id": "g1", "type": "get_state"})
        elif rid == "g1":
            data = frame.get("data") or {}
            self.system_prompt = data.get("systemPrompt")
            self.tools = [tool.get("name") for tool in data.get("dumpTools") or []]
            self.stage = "prompting"
            self.send_prompt("p1", self.prompt)
        return None

    def on_host_tool_call(self, frame):
        name, arguments = frame.get("toolName"), frame.get("arguments") or {}
        ok, message, final = self.on_call(name, arguments)
        self.calls.append({"tool": name, "args": arguments, "ok": bool(ok), "message": message})
        reply = {"type": "host_tool_result", "id": frame.get("id"),
                 "result": {"content": [{"type": "text", "text": message}]}}
        if not ok:
            reply["isError"] = True
        self.send(reply)
        if ok and final:
            return "accepted", message, True
        if not ok and final:
            return "rejected", message, True
        if not ok:
            self.rejections += 1
            self.last_rejection = message
            if self.rejections > self.max_rejections:
                return "rejected", message, True
        return None

    def rate_limited(self):
        return self.outcome("rate-limited", self.rate_error or "omp was still retrying a refused request")

    def on_prompt_result(self, frame):
        if frame.get("status") == "error":
            error = frame.get("error")
            message = str((error.get("message") if isinstance(error, dict) else error)
                          or "omp reported an error")
            if RATE_LIMITED.search(message):
                return "rate-limited", message, False
            return "error", message, False
        if self.rate_error and not self.retrying:
            return "rate-limited", self.rate_error, False
        if self.nudged:
            detail = "no tool call after the follow-up prompt"
            if self.last_rejection:
                detail += f"; last rejection: {self.last_rejection}"
            return "no-call", detail, False
        self.nudged = True
        self.send_prompt("p2", self.nudge)
        return None


def run_worker(argv, *, cwd, env, prompt, host_tools, on_call, max_turns, timeout_s,
               transcript_path, max_rejections=3, nudge="Finish by calling exactly one of your tools."):
    """Run one worker session and return how it ended; never raises for model behaviour.

    `on_call(name, arguments)` returns (ok, message, final): `message` is shown
    to the model, `ok` False answers the call with isError, an ok `final`
    call ends the session as "accepted", and a refused `final` one (nothing
    the model can fix) ends it as "rejected" at once. Every frame in both directions is
    appended to `transcript_path` as {"dir": "in"|"out", "frame": ...} JSONL.
    """
    deadline = time.monotonic() + timeout_s
    parent = os.path.dirname(os.path.abspath(transcript_path))
    os.makedirs(parent, exist_ok=True)
    with open(transcript_path, "a", encoding="utf-8") as transcript, subprocess.Popen(
            argv, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", start_new_session=True) as proc:
        try:
            stdout_q = queue.Queue()
            stderr_tail = collections.deque(maxlen=STDERR_TAIL_LINES)
            stderr_thread = threading.Thread(target=_pump_stderr, args=(proc.stderr, stderr_tail),
                                             daemon=True)
            threading.Thread(target=_pump_stdout, args=(proc.stdout, stdout_q), daemon=True).start()
            stderr_thread.start()
            session = _Session(proc, transcript, stdout_q, stderr_tail, stderr_thread,
                               host_tools=host_tools, on_call=on_call, max_turns=max_turns,
                               deadline=deadline, timeout_s=timeout_s, max_rejections=max_rejections,
                               nudge=nudge, prompt=prompt)
            return session.run()
        finally:
            if proc.poll() is None:
                _kill(proc)
                proc.wait()
