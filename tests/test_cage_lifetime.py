import os
import resource
import signal
import subprocess
import time
import unittest
from unittest import mock

from t12 import sandbox

from tests.test_cage import CAGE, CageCase

DETACH_BEAT_PRINT_HOST_PID = r"""
import os, sys, time
beat = sys.argv[1]
read_end, write_end = os.pipe()
if os.fork():
    os.close(write_end)
    print(os.read(read_end, 32).decode(), flush=True)
    sys.exit(0)
os.setsid()
if os.fork():
    os._exit(0)
null = os.open(os.devnull, os.O_RDWR)
for fd in (0, 1, 2):
    os.dup2(null, fd)
host_pid = os.readlink("/proc/self")
os.write(write_end, host_pid.encode())
os.close(write_end)
while True:
    with open(beat, "a") as fh:
        fh.write("x")
    time.sleep(0.05)
"""

DETACH_ATTACK_PARENT_REWRITE_POINTER = r"""
import os, resource, sys, time
attack, delay, pid_file = sys.argv[1], float(sys.argv[2]), sys.argv[3]
parent = os.getppid()
read_end, write_end = os.pipe()
if os.fork():
    os.close(write_end)
    os.read(read_end, 1)
    if attack == "none":
        time.sleep(delay + 2)
    sys.exit(0)
os.setsid()
if os.fork():
    os._exit(0)
null = os.open(os.devnull, os.O_RDWR)
for fd in (0, 1, 2):
    os.dup2(null, fd)
host_pid = os.readlink("/proc/self")
with open(pid_file + ".tmp", "w") as fh:
    fh.write(host_pid)
os.rename(pid_file + ".tmp", pid_file)
if attack != "none":
    try:
        resource.prlimit(parent, int(attack), (0, 0))
    except OSError:
        pass
os.close(write_end)
time.sleep(delay)
with open(".git", "w") as fh:
    fh.write("gitdir: /tmp/attacker\n")
"""
REWRITE_DELAY_S = 1.0


def alive(pid):
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rpartition(")")[2].split()[0] != "Z"
    except OSError:
        return False


@unittest.skipUnless(sandbox.landlock_available(), "needs a Landlock kernel")
class CageLifetimeTest(CageCase):
    def detach(self, beat):
        return ["--home", self.home, "--task-id", "demo-1", "--",
                "python3", "-c", DETACH_BEAT_PRINT_HOST_PID, beat]

    def assert_silent(self, pid, beat):
        self.addCleanup(lambda: alive(pid) and os.kill(pid, signal.SIGKILL))
        self.assertFalse(alive(pid), "a detached process outlived the cage")
        size = os.path.getsize(beat)
        time.sleep(0.3)
        self.assertEqual(os.path.getsize(beat), size,
                         "the worktree was written after the cage returned")

    def test_detached_process_is_dead_when_the_cage_returns(self):
        beat = os.path.join(self.worktree, "beat.txt")
        result = self.cage(*self.detach(beat))
        pid = int(result.stdout.strip().splitlines()[-1])

        self.assertGreater(os.path.getsize(beat), 0, "the detached process never ran")
        self.assert_silent(pid, beat)

    def test_detached_process_is_dead_when_the_cage_is_terminated(self):
        beat = os.path.join(self.worktree, "beat.txt")
        hold = "import subprocess, sys, time; " \
               "subprocess.run([sys.executable, '-c', sys.argv[1], sys.argv[2]]); " \
               "time.sleep(60)"
        proc = subprocess.Popen(
            [CAGE, "--home", self.home, "--task-id", "demo-1", "--",
             "python3", "-c", hold, DETACH_BEAT_PRINT_HOST_PID, beat],
            stdout=subprocess.PIPE, text=True, cwd=self.worktree)
        self.addCleanup(proc.stdout.close)
        self.addCleanup(proc.kill)
        pid = int(proc.stdout.readline())

        proc.terminate()
        self.assertEqual(proc.wait(timeout=30), 128 + signal.SIGTERM)

        self.assert_silent(pid, beat)

    def read_pointer(self):
        with open(os.path.join(self.worktree, ".git")) as fh:
            return fh.read()

    def start_rewriter(self, attack):
        pid_file = os.path.join(self.worktree, "rewriter.pid")
        cage = subprocess.Popen(
            [CAGE, "--home", self.home, "--task-id", "demo-1", "--",
             "python3", "-c", DETACH_ATTACK_PARENT_REWRITE_POINTER,
             str(attack), str(REWRITE_DELAY_S), pid_file],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=self.worktree,
            env=dict(os.environ, TMPDIR=self.tmp.name))
        self.addCleanup(cage.wait)
        self.addCleanup(cage.kill)
        deadline = time.monotonic() + 30
        while not os.path.exists(pid_file):
            self.assertLess(time.monotonic(), deadline, "the detached process never started")
            time.sleep(0.01)
        with open(pid_file) as fh:
            pid = int(fh.read())
        self.addCleanup(lambda: alive(pid) and os.kill(pid, signal.SIGKILL))
        return cage, pid

    def assert_pointer_survives(self, pointer, pid):
        deadline = time.monotonic() + REWRITE_DELAY_S + 5
        while alive(pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.read_pointer(), pointer,
                         "a process that outlived the cage rewrote the .git pointer")
        self.assertFalse(alive(pid), "a detached process outlived the cage")

    def assert_zeroed_limit_on_the_parent_leaves_no_survivor(self, limit):
        pointer = self.read_pointer()
        cage, pid = self.start_rewriter(limit)

        cage.wait(timeout=30)

        self.assert_pointer_survives(pointer, pid)

    def test_a_zero_cpu_limit_that_kills_the_parent_leaves_no_survivor(self):
        self.assert_zeroed_limit_on_the_parent_leaves_no_survivor(resource.RLIMIT_CPU)

    def test_a_zero_open_file_limit_that_fails_cleanup_opening_files_leaves_no_survivor(self):
        self.assert_zeroed_limit_on_the_parent_leaves_no_survivor(resource.RLIMIT_NOFILE)

    def test_killing_the_cage_leaves_no_survivor(self):
        pointer = self.read_pointer()
        cage, pid = self.start_rewriter("none")

        cage.kill()
        cage.wait(timeout=30)

        self.assert_pointer_survives(pointer, pid)

    def give_the_user_an_exclude_file_the_cage_may_not_read(self):
        user_home = os.path.join(self.tmp.name, "user")
        os.makedirs(os.path.join(user_home, ".config", "git"))
        with open(os.path.join(user_home, ".config", "git", "ignore"), "w") as fh:
            fh.write("*.log\n")
        env = mock.patch.dict(os.environ, HOME=user_home)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("XDG_CONFIG_HOME", None)

    def test_git_status_works_despite_a_user_exclude_file_the_cage_may_not_read(self):
        self.give_the_user_an_exclude_file_the_cage_may_not_read()
        self.write_worktree("app.py", "BUILDER_EDIT = True\n")

        result = self.cage(
            "--home", self.home, "--task-id", "demo-1", "--",
            "sh", "-c", 'git status --porcelain && test -d "$XDG_CONFIG_HOME"')

        self.assertEqual(result.stdout.strip(), "M app.py")


if __name__ == "__main__":
    unittest.main()
