#!/usr/bin/env python3
"""Choose tools for the `cli` container from a library, install them, and copy logins in. Safe to re-run.

Runs on the host, usually as `canopy clis`. Nothing here restarts the stack. The library is public, in
config/cli-library.json. The choices are private, in agent-data/config/cli.json; see ../SKILL.md for the format.
apk packages live in the container, so they are lost when it is recreated: run `install` after an update.
Never prints a credential. Standard library only.

    clis.py [--agent-data DIR] [--list FILE] [--canopy CMD] list|choose|add|remove|logins|install|check|all [NAME...]
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
LIBRARY = Path(__file__).resolve().parents[5] / "config/cli-library.json"


def in_cli(canopy: str, script: str, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run([*shlex.split(canopy), "run", "sh", "-c", ON_PATH + script],
                          capture_output=capture, text=True, stdin=subprocess.DEVNULL)


def as_root(canopy: str, script: str) -> subprocess.CompletedProcess:
    return subprocess.run([*shlex.split(canopy), "compose", "exec", "-T", "-u", "0", "cli", "sh", "-c", script],
                          text=True, stdin=subprocess.DEVNULL)


def resolve(spec: dict, library: list[dict]) -> list[dict]:
    """The chosen library tools, then `clis` entries, which replace a library tool of the same name."""
    known = {t["name"]: t for t in library}
    unknown = [n for n in spec.get("tools", []) if n not in known]
    if unknown:
        sys.exit(f"not in the library: {', '.join(unknown)}. Add them under `clis` instead.")
    chosen = {n: known[n] for n in spec.get("tools", [])}
    return list({**chosen, **{c["name"]: c for c in spec.get("clis", [])}}.values())


def show(library: list[dict], chosen: set[str]) -> None:
    number, group = 0, None
    for tool in library:
        if tool["group"] != group:
            group = tool["group"]
            print(f"\n{group}")
        number += 1
        note = "  (LAN needs host networking)" if tool.get("needs") == "lan" else ""
        print(f'  {number:>2} [{"x" if tool["name"] in chosen else " "}] {tool["name"]}: {tool["about"]}{note}')


def toggle(reply: str, library: list[dict], chosen: set[str]) -> set[str]:
    """Numbers or names, separated by spaces or commas. Each one flips."""
    names = [t["name"] for t in library]
    result = set(chosen)
    for word in reply.replace(",", " ").split():
        name = names[int(word) - 1] if word.isdigit() and 0 < int(word) <= len(names) else word
        if name not in names:
            print(f"no such tool: {word}")
        else:
            result ^= {name}
    return result


def choose(library: list[dict], chosen: set[str]) -> set[str]:
    while True:
        show(library, chosen)
        reply = input("\nNumbers or names to switch on or off, Enter to finish: ").strip()
        if not reply:
            return chosen
        chosen = toggle(reply, library, chosen)


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


def apk_script(clis: list[dict], repos: dict[str, dict]) -> str:
    """One root shell command: trust each third-party repo the chosen apk tools need, then one `apk add`.

    A tool with `"repo": NAME` comes from `apk_repos[NAME]` in the library (or in cli.json). Its key is
    downloaded, checked against `signer_sha256` and put in /etc/apk/keys; the repo is passed with -X, so
    /etc/apk/repositories is left alone. Both are lost with the container, like the packages.
    """
    apks = [c for c in clis if c["via"] == "apk"]
    steps, urls = [], []
    for name in sorted({c["repo"] for c in apks if c.get("repo")}):
        if name not in repos:
            sys.exit(f"apk repo {name!r} is not defined under apk_repos")
        repo = repos[name]
        key = "/etc/apk/keys/" + repo["key_name"]
        steps.append(f'curl -fsSL -o /tmp/apk-key {shlex.quote(repo["key_url"])} && '
                     f'echo {shlex.quote(repo["signer_sha256"] + "  /tmp/apk-key")} | sha256sum -c -s && '
                     f'install -m644 /tmp/apk-key {shlex.quote(key)} && rm -f /tmp/apk-key || '
                     f'{{ echo "apk repo {name}: key checksum mismatch" >&2; exit 1; }}')
        urls.append(repo["url"])
    if not apks:
        return ""
    flags = "".join(f" -X {shlex.quote(u)}" for u in urls)
    steps.append("apk add -q --no-cache" + flags + " " + shlex.join(c["package"] for c in apks))
    return " && ".join(f"( {x} )" if "||" in x else x for x in steps)


def install_script(cli: dict) -> str:
    """The shell command that installs an npm, uv, bin or tar entry inside the container."""
    via = cli["via"]
    if via == "bin":
        target = f'"$HOME/.local/bin/"{shlex.quote(cli["name"])}'
        return f'mkdir -p "$HOME/.local/bin" && curl -fsSL -o {target} {shlex.quote(cli["url"])} && chmod +x {target}'
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
    ap.add_argument("action", choices=["list", "choose", "add", "remove", "logins", "install", "check", "all"])
    ap.add_argument("names", nargs="*")
    ap.add_argument("--agent-data", type=Path, default=Path(
        os.environ.get("CANOPY_AGENT_DATA", Path.home() / "canopy/agent-data")))
    ap.add_argument("--list", type=Path, help="default: <agent-data>/config/cli.json")
    ap.add_argument("--canopy", default="canopy", help="how to run the canopy CLI on this host")
    args = ap.parse_args()

    path = args.list or args.agent_data / "config/cli.json"
    spec = json.loads(path.read_text()) if path.exists() else {}
    lib = json.loads(LIBRARY.read_text())
    library = lib["tools"]
    repos = {**lib.get("apk_repos", {}), **spec.get("apk_repos", {})}
    chosen = set(spec.get("tools", []))

    if args.action == "list":
        show(library, chosen)
        return 0
    if args.action in ("choose", "add", "remove"):
        if args.action == "choose":
            chosen = choose(library, chosen)
        else:
            known = {t["name"] for t in library}
            if set(args.names) - known:
                sys.exit(f"not in the library: {', '.join(sorted(set(args.names) - known))}")
            chosen = chosen | set(args.names) if args.action == "add" else chosen - set(args.names)
        spec["tools"] = sorted(chosen)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(spec, indent=2) + "\n")
        print(f"{len(chosen)} tools chosen. Run `canopy clis install` to apply.")
        return 0

    clis, cli_home = resolve(spec, library), args.agent_data / "cli"
    if args.action in ("logins", "all"):
        for rel in copy_logins(spec.get("logins", []), Path.home(), cli_home):
            print(f"login not on host, skipped: {rel}")
    if args.action in ("install", "all"):
        script = apk_script(clis, repos)
        if script and as_root(args.canopy, script).returncode != 0:
            print("apk install FAILED")
        for cli in clis:
            if cli["via"] in ("image", "apk"):
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
