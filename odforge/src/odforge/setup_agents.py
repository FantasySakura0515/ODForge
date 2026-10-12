"""``odforge setup``: hook ODForge into the coding agents on this machine.

One command so nobody has to find interpreter paths or edit JSON by hand:

* registers the MCP server with Claude Code and Codex (user scope, so every
  project sees it),
* links the ``odforge-design`` skill where those agents look for skills,
* makes ``odforge`` itself runnable from a plain shell when it is not yet, so an
  agent can start ``odforge edit`` / ``odforge preview``.

Every step reports what it did; ``remove=True`` undoes them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

NAME = "odforge"
SKILL = "odforge-design"


@dataclass
class Step:
    label: str
    ok: bool
    detail: str


def _bin(name: str) -> Optional[Path]:
    candidate = Path(sys.executable).parent / name
    return candidate if candidate.exists() else None


def mcp_command() -> List[str]:
    script = _bin("odforge-mcp")
    return [str(script)] if script else [sys.executable, "-m", "odforge.mcp_server"]


def skill_source() -> Optional[Path]:
    path = Path(__file__).resolve().parents[2] / "skills" / SKILL
    return path if (path / "SKILL.md").is_file() else None


def _run(args: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60
    )


def _register(cli: str, add: List[str], remove: List[str], uninstall: bool) -> Step:
    exe = shutil.which(cli)
    if exe is None:
        return Step(f"{cli} MCP", False, f"找不到 {cli},略過")
    _run([exe, *remove])  # idempotent: replace an older registration
    if uninstall:
        return Step(f"{cli} MCP", True, "已移除")
    proc = _run([exe, *add])
    if proc.returncode != 0:
        return Step(f"{cli} MCP", False, (proc.stderr or proc.stdout).strip()[:200])
    return Step(f"{cli} MCP", True, "已註冊(" + " ".join(add[add.index("--") + 1 :]) + ")")


def _link(target: Path, source: Optional[Path], label: str, uninstall: bool) -> Step:
    if uninstall:
        if target.is_symlink():
            target.unlink()
            return Step(label, True, f"已移除 {target}")
        return Step(label, True, "沒有需要移除的連結")
    if source is None:
        return Step(label, False, "找不到來源,略過")
    if target.is_symlink():
        target.unlink()
    elif target.exists():
        return Step(label, False, f"{target} 已存在且不是 ODForge 建立的連結,未覆蓋")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(source)
    return Step(label, True, f"{target} → {source}")


def setup(
    remove: bool = False,
    claude: bool = True,
    codex: bool = True,
    home: Optional[Path] = None,
    which: Callable[[str], Optional[str]] = shutil.which,
) -> List[Step]:
    home = home or Path.home()
    steps: List[Step] = []
    command = mcp_command()
    if claude:
        steps.append(
            _register(
                "claude",
                ["mcp", "add", NAME, "--scope", "user", "--", *command],
                ["mcp", "remove", NAME, "--scope", "user"],
                remove,
            )
        )
        steps.append(
            _link(home / ".claude" / "skills" / SKILL, skill_source(), "Claude Code skill", remove)
        )
    if codex:
        steps.append(
            _register(
                "codex", ["mcp", "add", NAME, "--", *command], ["mcp", "remove", NAME], remove
            )
        )
        if (home / ".codex").is_dir():
            steps.append(
                _link(home / ".codex" / "skills" / SKILL, skill_source(), "Codex skill", remove)
            )

    # The skill tells agents to run `odforge edit`; make sure a shell can find it.
    local_bin = home / ".local" / "bin"
    target = local_bin / "odforge"
    cli = _bin("odforge")
    if remove:
        if target.is_symlink() and cli and os.path.realpath(target) == os.path.realpath(cli):
            target.unlink()
            steps.append(Step("odforge 指令", True, f"已移除 {target}"))
    elif which("odforge") is None and cli is not None:
        if target.exists() or target.is_symlink():
            steps.append(Step("odforge 指令", False, f"{target} 已存在,未覆蓋"))
        else:
            local_bin.mkdir(parents=True, exist_ok=True)
            target.symlink_to(cli)
            on_path = str(local_bin) in os.environ.get("PATH", "").split(os.pathsep)
            note = "" if on_path else f";請把 {local_bin} 加進 PATH"
            steps.append(Step("odforge 指令", True, f"{target} → {cli}{note}"))
    return steps
