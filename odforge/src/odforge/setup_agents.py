"""``odforge setup``: hook ODForge into the coding agents on this machine.

One command so nobody has to find interpreter paths or edit JSON by hand:

* registers the MCP server with Claude Code and Codex (user scope, so every
  project sees it),
* links the ``odforge-design`` skill where those agents look for skills,
* makes ``odforge`` itself runnable from a plain shell when it is not yet, so an
  agent can start ``odforge edit`` / ``odforge preview``.

Every step reports what it did; ``remove=True`` undoes them.

``odforge init`` is the per-project counterpart (the open-slide model): it drops
AGENTS.md / CLAUDE.md, a copy of the skill and an ``.mcp.json`` into a folder, so
any agent opened there learns ODForge from files it reads anyway — nobody has to
point it at the README.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

NAME = "odforge"
SKILL = "odforge-design"
# What a machine without a checkout runs: the PyPI package plus the extras the
# editor and CJK line breaking need. Shared with the Claude Code plugin manifest.
UVX_SPEC = "odforge[web,cjk]"


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
    # Inside the package for a wheel install (force-included at build time); the
    # repo's own skills/ for a source checkout, which is also the plugin's copy.
    here = Path(__file__).resolve().parent
    for path in (here / "skills" / SKILL, here.parents[1] / "skills" / SKILL):
        if (path / "SKILL.md").is_file():
            return path
    return None


def portable_mcp_command() -> List[str]:
    """An MCP command that survives being committed to git or copied to another machine.

    ``mcp_command()`` names this interpreter's absolute path, which is right for a
    per-user registration but breaks in a project file the moment the folder moves
    or ``uvx`` garbage-collects its temporary environment.
    """
    if shutil.which("uvx"):
        return ["uvx", "--from", UVX_SPEC, "odforge-mcp"]
    return mcp_command()


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


def ephemeral_install() -> bool:
    """True when running from ``uvx``'s throwaway environment.

    Registering paths inside it would work today and break whenever uv prunes its
    cache, long after anyone remembers running setup.
    """
    return any(part.startswith("archive-v") for part in Path(sys.prefix).parts)


def setup(
    remove: bool = False,
    claude: bool = True,
    codex: bool = True,
    home: Optional[Path] = None,
    which: Callable[[str], Optional[str]] = shutil.which,
) -> List[Step]:
    home = home or Path.home()
    if ephemeral_install() and not remove:
        return [
            Step(
                "安裝方式",
                False,
                f'uvx 的環境是暫時的,請改用 uv tool install "{UVX_SPEC}" 後再執行 odforge setup',
            )
        ]
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


# ---------------------------------------------------------------- odforge init

BEGIN = "<!-- odforge:begin -->"
END = "<!-- odforge:end -->"

AGENTS_BLOCK = f"""{BEGIN}
## 簡報與 ODF 文件:用 ODForge

這個資料夾裝好了 ODForge。使用者要做簡報、文件或試算表(尤其 .odp/.odt/.ods),
或要看、改既有的 .odp 時:

1. **先讀 skill**:`.claude/skills/{SKILL}/SKILL.md`(同一份也在 `.agents/skills/`)。
   裡面有訪談、大綱、IR 契約與主題規格,照著寫出來的簡報才不會跑版。
2. **用 MCP 工具 `odforge`**(`forge_presentation`、`edit_odp`、`inspect_odf`…)產檔與修改,
   不要自己手寫 ODF XML。
3. **在網頁裡來回修改**:產出後編輯器會自動在瀏覽器打開;接著呼叫 `wait_for_feedback`,
   照使用者的留言用 `edit_odp` 修改,直到回 `done`。
4. **絕對不要叫使用者打開 LibreOffice**,它只在背景當渲染器。

找不到 `odforge` MCP 工具時,請使用者在終端機執行
`uv tool install "odforge[web,cjk]" && odforge setup`,再開新對話。
{END}
"""

CLAUDE_BLOCK = f"""{BEGIN}
@AGENTS.md
{END}
"""


def _upsert_block(path: Path, block: str) -> Step:
    """Write ODForge's marked block into ``path``, leaving everything else alone."""
    if not path.exists():
        path.write_text(block, encoding="utf-8")
        return Step(path.name, True, f"已建立 {path}")
    text = path.read_text(encoding="utf-8")
    start, end = text.find(BEGIN), text.find(END)
    if start != -1 and end > start:
        new = text[:start] + block.rstrip("\n") + text[end + len(END) :]
        verb = "已更新"
    else:
        new = text.rstrip("\n") + "\n\n" + block
        verb = "已附加到"
    if new == text:
        return Step(path.name, True, "已是最新")
    path.write_text(new, encoding="utf-8")
    return Step(path.name, True, f"{verb} {path}")


def _copy_skill(target: Path, source: Optional[Path], label: str) -> Step:
    if source is None:
        return Step(label, False, "找不到 skill 來源,略過")
    if target.is_symlink() or (target.exists() and not (target / "SKILL.md").is_file()):
        return Step(label, False, f"{target} 已存在且不是 ODForge 的 skill,未覆蓋")
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    # A copy, not a link: the folder must still work after it is zipped, pushed
    # or opened on a machine where this interpreter does not exist.
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"))
    return Step(label, True, f"已複製到 {target}")


def _write_mcp_json(path: Path, command: List[str]) -> Step:
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return Step(".mcp.json", False, f"讀不懂既有的 {path}({exc}),未修改")
        if not isinstance(data, dict):
            return Step(".mcp.json", False, f"{path} 不是 JSON 物件,未修改")
    servers = data.setdefault("mcpServers", {})
    entry = {"command": command[0], "args": command[1:]}
    if servers.get(NAME) == entry:
        return Step(".mcp.json", True, "已是最新")
    servers[NAME] = entry
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return Step(".mcp.json", True, f"已登記 {NAME} → {' '.join(command)}")


def init_project(directory: Path) -> List[Step]:
    """Make ``directory`` a folder where any coding agent already knows ODForge."""
    directory.mkdir(parents=True, exist_ok=True)
    source = skill_source()
    return [
        _upsert_block(directory / "AGENTS.md", AGENTS_BLOCK),
        # Claude Code reads CLAUDE.md, not AGENTS.md; importing keeps one source.
        _upsert_block(directory / "CLAUDE.md", CLAUDE_BLOCK),
        _copy_skill(directory / ".claude" / "skills" / SKILL, source, "Claude Code skill"),
        _copy_skill(directory / ".agents" / "skills" / SKILL, source, "Codex skill"),
        _write_mcp_json(directory / ".mcp.json", portable_mcp_command()),
    ]
