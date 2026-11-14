"""Report factory attempts to FactoryBench (best effort, never blocks).

An attempt is one runner invocation working on one issue-sourced run.
Claude Code sends its own usage over OTLP; this module emits only the
lifecycle (`attempt.started`, `pr.linked`, `attempt.finished`) through the
`factorybench` binary, which always exits 0. With `FACTORYBENCH_URL`
unset every call is a no-op.
"""

import json
import os
import re
import subprocess
import uuid

PHASES = {"analyzer": "implement", "red": "implement", "green": "implement",
          "refactor": "implement", "reviewer": "review"}


def enabled():
    return bool(os.environ.get("FACTORYBENCH_URL"))


def _emit(kind, attempt, key, payload):
    try:
        subprocess.run(["factorybench", "emit", kind, "--attempt", attempt, "--key", key,
                        "--json", json.dumps(payload)], timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pass


def config_version(omp_cmd, root):
    def out(*cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, cwd=root,
                                  stdin=subprocess.DEVNULL, timeout=10).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return ""
    sha = out("git", "rev-parse", "--short", "HEAD") or "unknown"
    omp = out(*omp_cmd, "--version").split("/")[-1].strip()
    return f"turbo-12f@{sha}+omp@{omp or 'unknown'}"


class Attempt:
    """One attempt per run; `start` is idempotent."""

    def __init__(self, run, version):
        source = (run.get("source") or {}).get("ref", "")
        match = re.fullmatch(r"(.+)#(\d+)", source)
        self.item = {"repo": match[1], "issue": int(match[2])} if match else None
        self.id = f"cc-{uuid.uuid4()}"
        self.version = version
        self.started = False

    def start(self):
        if self.item and not self.started:
            self.started = True
            _emit("attempt.started", self.id, "started",
                  {"work_item": self.item, "factory_config_version": self.version})

    def env(self, role):
        """OTLP settings for one worker session (empty when not reporting)."""
        if not (self.started and enabled()):
            return {}
        phase = PHASES.get(role)
        attrs = f"factorybench.attempt={self.id}" + (f",factorybench.phase={phase}" if phase else "")
        return {
            "CLAUDE_CODE_ENABLE_TELEMETRY": "1", "OTEL_LOGS_EXPORTER": "otlp",
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
            "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": os.environ["FACTORYBENCH_URL"] + "/v1/logs",
            "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Bearer " + os.environ.get("FACTORYBENCH_TOKEN", ""),
            "OTEL_RESOURCE_ATTRIBUTES": attrs,
        }

    def finish(self, events_path, status, reason=None):
        if not self.started:
            return
        try:
            with open(events_path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            text = ""
        pr = re.search(rf"https://github\.com/{re.escape(self.item['repo'])}/pull/\d+", text)
        if pr:
            _emit("pr.linked", self.id, "pr", {"pr_url": pr[0]})
        _emit("attempt.finished", self.id, "finished",
              {"status": status, **({"reason": reason} if reason else {})})
