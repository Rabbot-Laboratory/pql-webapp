"""Checksum-based deploy of the local working tree to the robot's Raspberry Pi.

Usage:
    python scripts/pi_deploy.py [--restart] [--dry-run]

Syncs git-tracked files (minus exclusions) plus the built web-vue/dist to the
Pi's repo. Only files whose md5 differs are uploaded, so Pi-local gitignored
state (config/imu_calibration.json, .env, Logs/) is never touched.

Connection settings come from config/pi_connection.json (gitignored):
    {"host": "...", "user": "...", "password": "...", "remote_root": "..."}
or the HIGHEND_PI_HOST / HIGHEND_PI_USER / HIGHEND_PI_PASSWORD env vars.

NOTE: if web-vue/src changed, build first with `npx vite build` in web-vue/.
(`npm run build` also works — its vue-tsc step checks zero files because the
root tsconfig only has project references — but `npx vite build` is the
explicit, version-proof form. The real type check is
`npx vue-tsc -p tsconfig.app.json --noEmit`, which has pre-existing errors.)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import stat as statmod
import subprocess
import sys
from pathlib import Path

import paramiko

REPO_ROOT = Path(__file__).resolve().parents[1]
EXCLUDE_PREFIXES = ("single-leg-app/", "sankou/", ".claude/")
SERVICE = "highend-control.service"


def connection() -> dict:
    config_path = REPO_ROOT / "config" / "pi_connection.json"
    if config_path.exists():
        settings = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        settings = {}
    settings.setdefault("host", os.environ.get("HIGHEND_PI_HOST", ""))
    settings.setdefault("user", os.environ.get("HIGHEND_PI_USER", "rabbot"))
    settings.setdefault("password", os.environ.get("HIGHEND_PI_PASSWORD", ""))
    settings.setdefault("remote_root", "/home/rabbot/pql-webapp")
    if not settings["host"] or not settings["password"]:
        raise SystemExit(
            "missing Pi connection settings: create config/pi_connection.json or "
            "set HIGHEND_PI_HOST / HIGHEND_PI_PASSWORD"
        )
    return settings


def open_ssh(settings: dict) -> paramiko.SSHClient:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        settings["host"], username=settings["user"], password=settings["password"], timeout=15
    )
    return ssh


def run(ssh: paramiko.SSHClient, command: str, timeout: float = 120.0) -> tuple[int, str]:
    _, stdout, stderr = ssh.exec_command(command, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    return code, out + (("\n[stderr] " + err) if err.strip() else "")


def local_files() -> list[str]:
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    files = [f for f in tracked if not f.startswith(EXCLUDE_PREFIXES)]
    dist = REPO_ROOT / "web-vue" / "dist"
    for path in dist.rglob("*"):
        if path.is_file():
            files.append(path.relative_to(REPO_ROOT).as_posix())
    return files


def md5_local(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restart", action="store_true", help="restart the control service")
    parser.add_argument("--dry-run", action="store_true", help="list changed files only")
    args = parser.parse_args()

    settings = connection()
    remote_root = settings["remote_root"]
    files = local_files()
    ssh = open_ssh(settings)
    try:
        sftp = ssh.open_sftp()
        with sftp.open("/tmp/deploy_filelist.txt", "w") as handle:
            handle.write("\n".join(files) + "\n")
        _, stdout, _ = ssh.exec_command(
            f"cd {remote_root} && xargs -d '\\n' md5sum < /tmp/deploy_filelist.txt 2>/dev/null",
            timeout=300,
        )
        remote: dict[str, str] = {}
        for line in stdout.read().decode().splitlines():
            if len(line) > 34:
                remote[line[34:].strip()] = line[:32]

        changed = [rel for rel in files if remote.get(rel) != md5_local(REPO_ROOT / rel)]
        print(f"{len(changed)} / {len(files)} files differ")
        if args.dry_run:
            for rel in changed:
                print("  " + rel)
            return 0

        made_dirs: set[str] = set()

        def ensure_dir(remote_dir: str) -> None:
            if remote_dir in made_dirs or remote_dir == remote_root:
                return
            ensure_dir(posixpath.dirname(remote_dir))
            try:
                sftp.stat(remote_dir)
            except FileNotFoundError:
                sftp.mkdir(remote_dir)
            made_dirs.add(remote_dir)

        for index, rel in enumerate(changed, 1):
            target = posixpath.join(remote_root, rel)
            ensure_dir(posixpath.dirname(target))
            sftp.put(str(REPO_ROOT / rel), target)
            if rel.startswith("scripts/"):
                sftp.chmod(target, statmod.S_IRWXU | 0o55)
            print(f"[{index}/{len(changed)}] {rel}")
        sftp.close()

        if args.restart:
            password = settings["password"]
            code, out = run(
                ssh,
                f"echo {password} | sudo -S systemctl restart {SERVICE} 2>/dev/null; "
                f"sleep 5; systemctl is-active {SERVICE}; "
                "curl -s http://127.0.0.1:8000/api/health | head -c 80",
            )
            print(out)
            return 0 if "active" in out and '"ok":true' in out else 1
        return 0
    finally:
        ssh.close()


if __name__ == "__main__":
    sys.exit(main())
