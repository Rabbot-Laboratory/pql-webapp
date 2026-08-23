"""Fetch experiment run directories from the Pi into Logs/experiments/pi/.

Usage:
    python scripts/pi_fetch_logs.py            # latest run
    python scripts/pi_fetch_logs.py <substr>   # all runs whose id contains substr
    python scripts/pi_fetch_logs.py --list     # list remote run ids
"""

from __future__ import annotations

import posixpath
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pi_deploy import REPO_ROOT, connection, open_ssh  # noqa: E402

LOCAL = REPO_ROOT / "Logs" / "experiments" / "pi"


def main() -> int:
    which = sys.argv[1] if len(sys.argv) > 1 else "latest"
    settings = connection()
    remote = posixpath.join(settings["remote_root"], "Logs/experiments")
    ssh = open_ssh(settings)
    try:
        sftp = ssh.open_sftp()
        runs = sorted(
            entry.filename
            for entry in sftp.listdir_attr(remote)
            if entry.st_mode and (entry.st_mode & 0o40000)
        )
        if which == "--list":
            print("\n".join(runs))
            return 0
        targets = [runs[-1]] if which == "latest" else [r for r in runs if which in r]
        if not targets:
            print(f"no runs matching {which!r}; latest: {runs[-5:]}")
            return 1
        for run in targets:
            local_dir = LOCAL / run
            local_dir.mkdir(parents=True, exist_ok=True)
            for name in sftp.listdir(posixpath.join(remote, run)):
                sftp.get(posixpath.join(remote, run, name), str(local_dir / name))
            print(f"fetched {run} -> {local_dir}")
        return 0
    finally:
        ssh.close()


if __name__ == "__main__":
    sys.exit(main())
