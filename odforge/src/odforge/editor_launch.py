"""Open the web editor for a file, from wherever ODForge is running (MCP included).

When an assistant makes a deck through MCP, the person should land in the
browser looking at it — not be told to run a command. :func:`ensure_editor`
starts ``odforge edit <file>`` as a detached background process (it opens the
browser itself), or reuses the one already serving that file.

Running editors are remembered in ``~/.odforge/editors.json`` as
``{absolute path: {port, pid}}``; a stale entry (process gone, port silent) is
simply replaced.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Dict, Optional

STATE_DIR = Path.home() / ".odforge"
START_TIMEOUT = 15.0


def disabled() -> bool:
    """``ODFORGE_NO_EDITOR=1`` turns auto-opening off (tests, CI, headless hosts)."""
    return os.environ.get("ODFORGE_NO_EDITOR", "").strip() not in ("", "0")


def _registry_path() -> Path:
    return STATE_DIR / "editors.json"


def _load() -> Dict[str, Dict[str, int]]:
    try:
        return json.loads(_registry_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(data: Dict[str, Dict[str, int]]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _registry_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _serving(port: int, name: str) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/doc", timeout=1.5) as response:
            return json.loads(response.read()).get("name") == name
    except Exception:
        return False


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _command() -> list[str]:
    script = Path(sys.executable).parent / "odforge"
    if script.exists():
        return [str(script)]
    return [sys.executable, "-c", "from odforge.cli import app; app()"]


def url_for(port: int) -> str:
    return f"http://127.0.0.1:{port}/editor.html"


def ensure_editor(path: Path, open_browser: bool = True) -> str:
    """The editor URL for ``path``, starting an editor when none is serving it.

    Raises ``RuntimeError`` with a readable reason when it cannot start one.
    """
    path = Path(path).resolve()
    registry = _load()
    entry = registry.get(str(path))
    if entry and _serving(int(entry["port"]), path.name):
        url = url_for(int(entry["port"]))
        if open_browser:
            webbrowser.open(url)
        return url

    from .webapi import frontend_dist

    if frontend_dist() is None:
        raise RuntimeError("找不到建置好的前端(在 odforge/ 下執行 npm --prefix web run build)")
    port = _free_port()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    log = open(STATE_DIR / f"editor-{port}.log", "w", encoding="utf-8")  # noqa: SIM115
    args = [*_command(), "edit", str(path), "--port", str(port)]
    if not open_browser:
        args.append("--no-open")
    process = subprocess.Popen(
        args,
        # Under an MCP host stdin/stdout are the protocol pipe: the child must
        # never inherit them, or it corrupts (or blocks on) the stream.
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        cwd=str(path.parent),
    )
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"編輯器啟動失敗,詳見 {STATE_DIR / f'editor-{port}.log'}")
        if _serving(port, path.name):
            registry[str(path)] = {"port": port, "pid": process.pid}
            _save(registry)
            return url_for(port)
        time.sleep(0.25)
    raise RuntimeError("編輯器沒有在時間內啟動")


def editor_note(path: Path) -> Optional[str]:
    """A line for MCP results: where the deck opened, or why it did not."""
    if disabled():
        return None
    try:
        url = ensure_editor(path)
    except Exception as exc:
        return f"(沒有自動打開網頁編輯器:{exc})"
    return (
        f"已在使用者的瀏覽器用 ODForge 網頁編輯器打開這份簡報:{url}\n"
        "使用者可以在網頁上瀏覽、拖動、改字,並用「留言」點出要改的地方、按「交給 AI 修改」。"
        f'下一步:呼叫 wait_for_feedback("{Path(path).resolve()}") 等他在網頁上的回饋。'
    )
