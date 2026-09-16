#!/usr/bin/env python3
"""
Wrap cron commands: log invocation, run the child process, propagate its exit code.

Crontab usage:
  python scripts/cron_run_with_alert.py --job-id my_job -- /path/to/venv/bin/python /path/to/script.py
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone


def _parse_args(argv: list[str]) -> tuple[str | None, list[str]]:
    if "--" not in argv:
        return None, []
    sep = argv.index("--")
    head, tail = argv[:sep], argv[sep + 1 :]
    job_id = None
    i = 0
    while i < len(head):
        if head[i] == "--job-id" and i + 1 < len(head):
            job_id = head[i + 1]
            i += 2
        else:
            i += 1
    return job_id, tail


def main() -> None:
    job_id, cmd = _parse_args(sys.argv[1:])
    if not job_id or not cmd:
        sys.stderr.write(
            "usage: cron_run_with_alert.py --job-id ID -- command [arg ...]\n"
        )
        sys.exit(2)

    start_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cmd_display = " ".join(cmd)
    print(
        f"[cron_run_with_alert] start_utc={start_utc} job_id={job_id} cmd={cmd_display}",
        flush=True,
    )

    completed = subprocess.run(cmd)
    sys.exit(completed.returncode if completed.returncode is not None else 1)


if __name__ == "__main__":
    main()
