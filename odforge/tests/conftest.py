import os
import socket
import subprocess
from pathlib import Path

import pytest

from odforge.ir import (
    FormulaSpec,
    HeadingBlock,
    ListBlock,
    PageBreakBlock,
    ParagraphBlock,
    Presentation,
    Sheet,
    Slide,
    Spreadsheet,
    TableBlock,
    TextDoc,
    TocBlock,
)


@pytest.fixture(autouse=True)
def isolated_sessions_dir(tmp_path, monkeypatch):
    """Never let a test write into the operator's real session history.

    ``create_app()`` without ``jobs_dir`` defaults to ``%LOCALAPPDATA%/ODForge/
    sessions`` — the directory the running console reads its 工作紀錄 from. One
    fixture that skipped the argument put sixteen failed "測試" jobs into a real
    user's history (four per suite run), which is indistinguishable from the
    product being broken. A test that forgets ``jobs_dir`` now lands in tmp_path
    instead of the operator's data.
    """
    monkeypatch.setenv("ODFORGE_SESSIONS_DIR", str(tmp_path / "sessions"))


# Every environment variable that can point the code at a real provider: a key,
# an endpoint, or a backend selection. If any of these survives into a test, that
# test can quietly make a paid network call on a developer's machine and pass for
# the wrong reason — and then fail in CI, where the key does not exist.
_PROVIDER_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "ODFORGE_BACKEND",
    "ODFORGE_MODEL",
    "ODFORGE_VISION_MODEL",
    "ODFORGE_OLLAMA_VISION_MODEL",
    "ODFORGE_CUSTOM_BASE_URL",
    "ODFORGE_CUSTOM_API_KEY",
    "ODFORGE_CUSTOM_MODEL",
    "ODFORGE_CUSTOM_VISION_BASE_URL",
    "ODFORGE_CUSTOM_VISION_API_KEY",
    "ODFORGE_CUSTOM_VISION_MODEL",
    "ODFORGE_CODEX_MODEL",
    "ODFORGE_IMAGE_BACKEND",
    "ODFORGE_IMAGE_ENDPOINT",
    "ODFORGE_MAX_TOKENS",
    "CODEX_HOME",
)


@pytest.fixture(autouse=True)
def scrub_provider_credentials(monkeypatch, tmp_path_factory):
    """No test inherits the developer's ``.env`` — or their Codex login.

    ``ODFORGE_VISION_BACKEND`` is pinned to ``off`` rather than merely removed:
    ``off`` is the documented default and the one backend that constructs no
    client at all, so a test that forgets to pass a backend degrades instead of
    dialling out.

    ``CODEX_HOME`` is *redirected*, not deleted, and that distinction is the
    whole fixture. Deleting it is what the code reads as "use the default", and
    the default is ``~/.codex/auth.json`` — so the scrub that was meant to
    isolate the suite handed it the maintainer's real subscription credentials,
    and every "codex is not logged in" branch went untested on the one machine
    that mattered. An empty directory means not-logged-in, everywhere.
    """
    for name in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ODFORGE_VISION_BACKEND", "off")
    monkeypatch.setenv(
        "CODEX_HOME", str(tmp_path_factory.mktemp("codex-home-empty"))
    )


@pytest.fixture(autouse=True)
def forbid_launching_the_codex_cli(monkeypatch):
    """Env isolation stops credentials being *read*; this stops the CLI being *run*.

    The Codex backend shells out. A test that reaches that line spends the
    operator's ChatGPT subscription and takes however long the model takes —
    neither of which any assertion in this suite is asking for. soffice and the
    other tooling are untouched; only ``codex`` is refused.
    """
    real_run = subprocess.run
    real_popen = subprocess.Popen.__init__

    def _is_codex(args) -> bool:
        if isinstance(args, (str, bytes, os.PathLike)):
            argv0 = os.fspath(args)
        elif args:
            argv0 = os.fspath(args[0])
        else:
            return False
        return Path(str(argv0)).stem.lower() == "codex"

    def guarded_run(args, *rest, **kwargs):
        if _is_codex(args):
            raise AssertionError(
                "a test tried to launch the real `codex` CLI — that spends the "
                "operator's subscription. Monkeypatch the backend instead."
            )
        return real_run(args, *rest, **kwargs)

    def guarded_popen(self, args, *rest, **kwargs):
        if _is_codex(args):
            raise AssertionError(
                "a test tried to launch the real `codex` CLI via Popen."
            )
        return real_popen(self, args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded_run)
    monkeypatch.setattr(subprocess.Popen, "__init__", guarded_popen)


@pytest.fixture(autouse=True)
def forbid_outbound_network(monkeypatch):
    """Any attempt to reach a non-localhost address fails the test immediately.

    A test that silently talks to api.deepseek.com is not testing this codebase;
    it is testing someone's quota. Loopback stays open because the fake HTTP
    servers used for provider-contract tests bind there.
    """
    real_create_connection = socket.create_connection
    real_connect = socket.socket.connect

    def _is_loopback(address) -> bool:
        if not isinstance(address, tuple) or not address:
            return False
        host = str(address[0])
        return host in {"localhost", "127.0.0.1", "::1", "0.0.0.0", ""} or host.startswith(
            "127."
        )

    def guard(address, *args, **kwargs):
        if not _is_loopback(address):
            raise AssertionError(
                f"outbound network call to {address!r} from a test — tests must "
                "not reach real providers. Monkeypatch the client, or bind a "
                "fake server on localhost."
            )
        return real_create_connection(address, *args, **kwargs)

    def guard_connect(self, address, *args, **kwargs):
        if not _is_loopback(address):
            raise AssertionError(
                f"outbound socket connect to {address!r} from a test — tests "
                "must not reach real providers."
            )
        return real_connect(self, address, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", guard)
    monkeypatch.setattr(socket.socket, "connect", guard_connect)


@pytest.fixture
def sample_text_doc() -> TextDoc:
    return TextDoc(
        title="ODForge 示範文件",
        blocks=[
            TocBlock(),
            HeadingBlock(level=1, text="第一章 緒論"),
            ParagraphBlock(text="這是一段引言。", style="quote"),
            HeadingBlock(level=2, text="1.1 研究背景"),
            ListBlock(ordered=False, items=["重點一", "重點二"]),
            ListBlock(ordered=True, items=["步驟一", "步驟二"]),
            TableBlock(header=["項目", "說明"], rows=[["A", "甲"], ["B", "乙"]]),
            PageBreakBlock(),
        ],
    )


@pytest.fixture
def sample_presentation() -> Presentation:
    return Presentation(
        title="ODForge 示範簡報",
        slides=[
            Slide(layout="title", title="ODForge", subtitle="自然語言轉 ODF"),
            Slide(
                layout="title-content",
                title="大綱",
                bullets=["背景", "方法", "成果"],
                notes="開場說明本次簡報結構。",
            ),
            Slide(
                layout="two-col",
                title="比較",
                left=["傳統做法", "耗時"],
                right=["ODForge", "自動化"],
            ),
            Slide(layout="section", title="研究方法"),
            Slide(
                layout="big-fact",
                fact="99%",
                notes="強調自動化涵蓋率。",
            ),
        ],
    )


@pytest.fixture
def sample_spreadsheet() -> Spreadsheet:
    return Spreadsheet(
        title="ODForge 示範試算表",
        sheets=[
            Sheet(
                name="銷售",
                columns=["項目", "數量", "單價"],
                rows=[
                    ["筆記本", 3, 25.5],
                    ["原子筆", 10, 12.0],
                    ["資料夾", 5, 8.75],
                ],
                formulas=[FormulaSpec(cell="B5", formula="of:=SUM([.B2:.B4])")],
            ),
        ],
    )
