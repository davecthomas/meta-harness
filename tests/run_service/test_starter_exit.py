"""The starter ends without the interpreter's teardown, where it crashed now and then (#116)."""

import subprocess
import sys


def test_exit_now_keeps_the_output_and_the_exit_code():
    script = (
        "from meta_harness.run_service.starter import exit_now\n"
        "print('[starter] workflow w completed', end='')\n"
        "exit_now(3)\n"
    )
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 3
    assert done.stdout == "[starter] workflow w completed"
