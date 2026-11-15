#!/usr/bin/env python3
import json
import os
import select
import subprocess
import shlex
import sys
import time

MODE = os.environ.get("FAKE_OMP_MODE", "")
LOG = os.environ.get("FAKE_OMP_LOG")
MAX_LOOP_TURNS = 2000
RATE_ERROR = ('429 {"type":"error","error":{"type":"rate_limit_error",'
              '"message":"Number of request tokens has exceeded your per-minute rate limit"}}')


def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def log(record):
    if LOG:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")


class Stdin:
    def __init__(self):
        self.buf = b""
        self.eof = False

    def readline(self, timeout=None):
        while b"\n" not in self.buf:
            if self.eof:
                rest, self.buf = self.buf, b""
                return rest.decode("utf-8", "replace")
            if not select.select([0], [], [], timeout)[0]:
                return None
            chunk = os.read(0, 65536)
            if not chunk:
                self.eof = True
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        return line.decode("utf-8", "replace") + "\n"


class Fake:
    def __init__(self):
        self.stdin = Stdin()
        self.host_tools = []
        self.builtin = []
        self.steps = None
        self.first_prompt = None
        self.next_step = 0
        self.bad_sent = False
        self.prompts = 0
        self.prompt_id = None
        self.active = False
        self.call_n = 0
        self.current_call = None
        self.ui_pending = None
        self.ui_sent = False
        self.error_text = None
        self.last_bad = False

    def run_delegate(self, text):
        done = subprocess.run(shlex.split(os.environ["FAKE_OMP_DELEGATE"]), input=text,
                              text=True, capture_output=True)
        if done.returncode:
            sys.stderr.write(done.stderr)
            sys.stderr.write(f"fake omp: delegate exited {done.returncode}\n")
            sys.exit(4)
        return json.loads(done.stdout)

    def script_steps(self):
        if self.steps is None:
            script = os.environ.get("FAKE_OMP_SCRIPT")
            delegate = os.environ.get("FAKE_OMP_DELEGATE")
            if script:
                with open(script, encoding="utf-8") as fh:
                    self.steps = json.load(fh)
            elif delegate:
                self.steps = [self.run_delegate(self.first_prompt)]
            else:
                self.steps = [{"tool": "done", "args": {}}]
        return self.steps

    def respond(self, frame, **data):
        out = {"id": frame.get("id"), "type": "response", "command": frame["type"], "success": True}
        if data:
            out["data"] = data
        emit(out)

    def turn(self):
        emit({"type": "turn_start"})
        emit({"type": "turn_end"})

    def finish(self, status):
        self.active = False
        self.current_call = None
        emit({"type": "prompt_result", "id": self.prompt_id, "agentInvoked": True,
              "status": status, "sessionSettled": True})
        emit({"type": "session_settled"})

    def handle(self, frame):
        log({"frame": frame})
        kind = frame.get("type")
        if kind == "set_host_tools":
            self.host_tools = [t["name"] for t in frame.get("tools", [])]
            self.respond(frame, toolNames=self.host_tools)
        elif kind == "get_state":
            self.respond(frame, systemPrompt=["fake omp system prompt"],
                         dumpTools=[{"name": n, "description": ""} for n in self.builtin + self.host_tools],
                         model="fake/model", sessionId="fake-session")
        elif kind == "abort":
            self.respond(frame)
            if self.active:
                self.finish("aborted")
        elif kind == "prompt":
            self.on_prompt(frame)
        elif kind == "host_tool_result":
            self.on_result(frame)
        elif kind == "extension_ui_response":
            if self.ui_pending:
                resume, self.ui_pending = self.ui_pending, None
                resume()

    def on_prompt(self, frame):
        self.respond(frame)
        self.prompts += 1
        self.prompt_id = frame.get("id")
        if self.first_prompt is None:
            self.first_prompt = frame.get("message", "")
        self.active = True
        emit({"type": "agent_start"})
        if MODE == "rate-limit" and self.prompts == 1 and self.claim_rate_limit():
            self.rate_limit()
        elif MODE == "supersede" and self.prompts == 1 and self.claim("supersede"):
            subprocess.run(os.environ["FAKE_OMP_BEFORE_CALL"], shell=True, check=True)
            os.chdir("/")  # the hook may have removed the worktree this process started in
            self.start_call()
        elif MODE == "loop":
            self.spin()
        elif MODE == "never" or (MODE == "silent" and self.prompts == 1):
            self.turn()
            self.finish("completed")
        else:
            self.start_call()

    def spin(self):
        for _ in range(MAX_LOOP_TURNS):
            self.turn()
            time.sleep(0.005)
            line = self.stdin.readline(0)
            while line:
                self.handle(json.loads(line))
                if not self.active:
                    return
                line = self.stdin.readline(0)
            if line == "":
                return
        self.finish("completed")

    @staticmethod
    def claim(name):
        try:
            os.close(os.open(os.path.join(os.environ["FAKE_OMP_STATE"], name),
                             os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        except FileExistsError:
            return False
        return True

    def claim_rate_limit(self):
        limits = os.environ.get("FAKE_OMP_RATE_LIMITS")
        if limits is None:
            return True
        for n in range(int(limits)):
            try:
                os.close(os.open(os.path.join(os.environ["FAKE_OMP_STATE"], f"rate-limit-{n}"),
                                 os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            except FileExistsError:
                continue
            return True
        return False

    def rate_limit(self):
        text = os.environ.get("FAKE_OMP_RATE_ERROR", RATE_ERROR)
        message = {"role": "assistant", "content": [], "stopReason": "error", "errorMessage": text}
        emit({"type": "turn_start"})
        emit({"type": "message_end", "message": message})
        emit({"type": "turn_end", "message": message, "toolResults": []})
        style = os.environ.get("FAKE_OMP_RATE_STYLE", "retry")
        if style == "stop":
            self.finish("completed")
            return
        emit({"type": "auto_retry_start", "attempt": 1, "maxAttempts": 3, "delayMs": 1,
              "errorMessage": text})
        if style == "stall":
            self.spin()
            return
        emit({"type": "auto_retry_end", "success": False, "attempt": 3, "finalError": text})

    def start_call(self):
        self.turn()
        if MODE == "ui" and not self.ui_sent:
            self.ui_sent = True
            self.ui_pending = self.emit_call
            emit({"type": "extension_ui_request", "id": "ui_1", "method": "confirm",
                  "title": "Proceed?", "message": "fake omp asks a question"})
        else:
            self.emit_call()

    def emit_call(self):
        steps = self.script_steps()
        if (os.environ.get("FAKE_OMP_DELEGATE") and not os.environ.get("FAKE_OMP_SCRIPT")
                and self.error_text is not None and not self.last_bad):
            steps.append(self.run_delegate("TOOL ERROR: " + self.error_text))
            self.next_step = len(steps) - 1
        self.error_text = None
        step = steps[min(self.next_step, len(steps) - 1)]
        args = step.get("args", {})
        self.last_bad = False
        if MODE == "bad-then-good" and not self.bad_sent:
            self.bad_sent = True
            self.last_bad = True
            args = {"wrong": True}
        else:
            self.next_step += 1
        self.call_n += 1
        self.current_call = f"host_{self.call_n}"
        emit({"type": "host_tool_call", "id": self.current_call, "toolCallId": f"tc_{self.call_n}",
              "toolName": step["tool"], "arguments": args})

    def on_result(self, frame):
        if frame.get("id") != self.current_call:
            return
        emit({"type": "tool_execution_end"})
        self.current_call = None
        if frame.get("isError"):
            content = (frame.get("result") or {}).get("content") or [{}]
            self.error_text = content[0].get("text", "")
            self.start_call()
        else:
            self.finish("completed")

    def run(self):
        argv = sys.argv[1:]
        if "--tools" in argv:
            self.builtin = [n for n in argv[argv.index("--tools") + 1].split(",") if n]
        log({"argv": argv, "pid": os.getpid()})
        emit({"type": "ready", "protocolVersion": 1})
        if MODE == "crash":
            sys.stderr.write("fake omp crashed: no model configured\n")
            sys.stderr.flush()
            sys.exit(3)
        while True:
            line = self.stdin.readline()
            if line == "":
                return
            if not line.strip():
                continue
            self.handle(json.loads(line))


if __name__ == "__main__":
    Fake().run()
