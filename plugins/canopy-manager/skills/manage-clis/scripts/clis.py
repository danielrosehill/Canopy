#!/usr/bin/env python3
"""Install CLIs into the `cli` container and copy logins into it from the host. Safe to re-run.

Runs on the host. The container's home is agent-data/cli, so nothing here restarts the stack.
Reads agent-data/config/cli.json; see ../SKILL.md for the format. Never prints a credential.
Standard library only.

    clis.py [--agent-data DIR] [--list FILE] [--canopy CMD] logins|install|check|all
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ON_PATH = 'export PATH="$HOME/.local/bin:$PATH"; '


def in_cli(canopy: str, script: str, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run([*shlex.split(canopy), "run", "sh", "-c", ON_PATH + script],
                          capture_output=capture, text=True, stdin=subprocess.DEVNULL)


def copy_logins(paths: list[str], host_home: Path, cli_home: Path) -> list[str]:
    """Replace each path under cli_home with the host's copy. Returns the paths not on the host."""
    missing = []
    for rel in paths:
        src, dst = host_home / rel, cli_home / rel
        if not src.exists():
            missing.append(rel)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.is_dir() and not dst.is_symlink():
            shutil.rmtree(dst)
        elif dst.exists() or dst.is_symlink():
            dst.unlink()
        if src.is_dir():
            shutil.copytree(src, dst, symlinks=True)
        else:
            shutil.copy2(src, dst)
    return missing


def wrapper(binary: str, env: dict[str, str]) -> str:
    """A launcher that sets env first. Values may use $HOME, which the container's shell expands."""
    pairs = " ".join(f'{k}="{v}"' for k, v in env.items())
    return f'#!/bin/sh\n{pairs} exec "{binary}" "$@"\n'


def install_host(cli: dict, cli_home: Path) -> None:
    """Copy a static binary from the host. Fails on Alpine for anything linked against glibc."""
    name = cli["name"]
    src = shutil.which(name)
    if not src:
        sys.exit(f"{name}: not on the host's PATH")
    bin_dir = cli_home / ".local/bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    env = cli.get("env")
    target = bin_dir / (f"{name}.bin" if env else name)
    target.unlink(missing_ok=True)
    shutil.copy2(src, target)
    if env:
        launcher = bin_dir / name
        launcher.unlink(missing_ok=True)
        launcher.write_text(wrapper(f"$HOME/.local/bin/{name}.bin", env))
        launcher.chmod(0o755)


def install_script(cli: dict) -> str:
    """The shell command that installs an npm, uv or tar entry inside the container."""
    via = cli["via"]
    if via == "npm":
        return f'npm install -g --silent --prefix "$HOME/.local" {shlex.quote(cli["package"])}'
    if via == "uv":
        return f'uv tool install -q {shlex.quote(cli["package"])}'
    if via == "tar":
        binary = shlex.quote(cli["bin"])
        return (f'cd "$HOME" && {{ [ -x {binary} ] || curl -fsSL {shlex.quote(cli["url"])} | tar xz; }} '
                f'&& mkdir -p .local/bin && ln -sf "$HOME/"{binary} .local/bin/{shlex.quote(cli["name"])}')
    sys.exit(f'{cli.get("name")}: unknown via "{via}"')


def check(clis: list[dict], canopy: str) -> bool:
    """Judged on exit code, since an error line looks the same as a result to anything that counts lines."""
    good = True
    for cli in clis:
        command = cli.get("check")
        if not command:
            print(f'{cli["name"]}: no check')
            continue
        r = in_cli(canopy, command, capture=True)
        last = ((r.stdout or r.stderr).strip().splitlines() or [""])[-1][:80]
        print(f'{cli["name"]}: {"ok" if r.returncode == 0 else f"FAILED ({r.returncode})"}  {last}')
        good = good and r.returncode == 0
    return good


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=["logins", "install", "check", "all"])
    ap.add_argument("--agent-data", type=Path, default=Path(
        os.environ.get("CANOPY_AGENT_DATA", Path.home() / "canopy/agent-data")))
    ap.add_argument("--list", type=Path, help="default: <agent-data>/config/cli.json")
    ap.add_argument("--canopy", default="canopy", help="how to run the canopy CLI on this host")
    args = ap.parse_args()

    spec = json.loads((args.list or args.agent_data / "config/cli.json").read_text())
    clis, cli_home = spec.get("clis", []), args.agent_data / "cli"

    if args.action in ("logins", "all"):
        for rel in copy_logins(spec.get("logins", []), Path.home(), cli_home):
            print(f"login not on host, skipped: {rel}")
    if args.action in ("install", "all"):
        for cli in clis:
            if cli["via"] == "image":
                continue
            if cli["via"] == "host":
                install_host(cli, cli_home)
            elif in_cli(args.canopy, install_script(cli)).returncode != 0:
                print(f'{cli["name"]}: install FAILED')
    if args.action in ("check", "all"):
        return 0 if check(clis, args.canopy) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
