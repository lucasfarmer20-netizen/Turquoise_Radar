"""Linux process lifecycle checks; isolated pilot DB and loopback server only."""

import os
import signal
import socket
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Ubuntu/Linux supervisor")


def kill_if_present(pid):
    if pid is not None:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_sigkill_supervisor_kills_actual_uvicorn_child(tmp_path):
    # This is the real supervisor and child launcher. Only the remote check is
    # replaced; no HTTPS request, marketplace request or credential is involved.
    script = """
import sys
import radar.ebay_deletion.pilot_job as job
root = job.prepare(sys.argv[1])
job.cycle = lambda *args: 0
original = job.subprocess.Popen
def spawn(*args, **kwargs):
    child = original(*args, **kwargs)
    print(child.pid, flush=True)
    return child
job.subprocess.Popen = spawn
job.run(root, "https://fixture.invalid/pilot-deletion", "SYNTHETIC-unused-token")
"""
    parent = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path / "pilot")],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    child = None
    try:
        child = int(parent.stdout.readline().strip())
        deadline = time.monotonic() + 10
        while True:
            with socket.socket() as sock:
                ready = sock.connect_ex(("127.0.0.1", 8001)) == 0
            if ready:
                break
            assert time.monotonic() < deadline, "Pilot did not start"
            time.sleep(0.05)
        parent.kill()  # finally cannot execute.
        parent.communicate(timeout=5)  # Child must close its inherited stdout too.
        with socket.socket() as sock:
            assert sock.connect_ex(("127.0.0.1", 8001)) != 0
    finally:
        kill_if_present(child)
        if parent.poll() is None:
            parent.kill()
        parent.communicate(timeout=5)


def test_parent_dies_before_child_arms_signal(tmp_path):
    gate = tmp_path / "continue"
    child_code = """
import os, sys, time
from pathlib import Path
from radar.ebay_deletion.pilot_server import arm_parent_death
print("ready", flush=True)
while not Path(sys.argv[2]).exists():
    time.sleep(.01)
arm_parent_death(int(sys.argv[1]))
print("UNSAFE_SERVER_START", flush=True)
"""
    parent_code = """
import os, subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", sys.argv[1], str(os.getpid()), sys.argv[2]])
print(child.pid, flush=True)
time.sleep(60)
"""
    parent = subprocess.Popen(
        [sys.executable, "-c", parent_code, child_code, str(gate)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    child = None
    try:
        lines = [parent.stdout.readline().strip(), parent.stdout.readline().strip()]
        assert "ready" in lines
        child = int(next(line for line in lines if line != "ready"))
        parent.kill()
        parent.wait(timeout=5)
        gate.touch()
        out, _ = parent.communicate(timeout=5)
        assert "UNSAFE_SERVER_START" not in out
    finally:
        gate.touch()
        kill_if_present(child)
        if parent.poll() is None:
            parent.kill()
        parent.communicate(timeout=5)
