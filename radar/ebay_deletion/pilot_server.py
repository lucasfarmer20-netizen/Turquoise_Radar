"""Linux-only pilot child: die with the supervisor, including across exec."""

import ctypes
import os
import signal
import sys


def arm_parent_death(expected_parent):
    if sys.platform != "linux":
        raise RuntimeError("Pilot supervisor requires Linux parent-death signaling")
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        raise OSError(ctypes.get_errno(), "Cannot bind pilot lifetime")
    # If the parent died before prctl, the signal was missed: never start serving.
    if os.getppid() != expected_parent:
        raise RuntimeError("Pilot supervisor already exited")


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Expected supervisor PID")
    arm_parent_death(int(sys.argv[1]))
    os.execv(
        sys.executable,
        [
            sys.executable,
            "-m",
            "uvicorn",
            "radar.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8001",
            "--no-access-log",
            "--log-level",
            "warning",
        ],
    )


if __name__ == "__main__":
    main()
