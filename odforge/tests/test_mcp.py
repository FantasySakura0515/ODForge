"""Tests for the ODForge MCP server tools.

The five tools are exercised as plain functions (the FastMCP registration is a
thin wrapper); no MCP server process is started. Every failure path must return
an ``"error: ..."`` string rather than raising, because an exception inside an
MCP tool becomes a protocol-level error.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from odforge import mcp_server
from odforge.mcp_server import (
    forge_presentation,
    forge_spreadsheet,
    forge_text_document,
    inspect_odf,
    preview_odf,
)
from odforge.render.odp import render_odp
from odforge.themes import SCALES, THEMES
from odforge.validate import find_soffice


@pytest.fixture(autouse=True)
def _forge_without_libreoffice(monkeypatch, request):
    """Default the ``forge_*`` LibreOffice gate off, for suite speed only.

    ``_forge`` now runs the real round-trip when LibreOffice is installed, which
    costs ~3.5s per call — the honest price of "ok from MCP means what ok from
    the CLI means". Paying it in *every* MCP test buys nothing: whether the flag
    is passed correctly is asserted directly by the two spy tests below, and the
    three-gate path end-to-end is covered in ``test_validate.py`` and
    ``test_integration_pipeline.py``.

    A test that genuinely wants the real thing requests ``real_soffice``.
    """
    if "real_soffice" in request.fixturenames:
        return
    monkeypatch.setattr(mcp_server, "find_soffice", lambda: None)


@pytest.fixture
def real_soffice():
    """Opt back in to the real LibreOffice round-trip (see the autouse fixture)."""
    return find_soffice()


# The skill lives inside the package tree (not gitignored) so it ships with a
# clone. Locate it relative to this test file: tests/ -> package root -> skills/.
SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills" / "odforge-design"
SKILL_MD = SKILLS_DIR / "SKILL.md"
THEME_MD_DIR = SKILLS_DIR / "themes"

# Every MCP tool name the SKILL.md must teach an agent to call.
MCP_TOOL_NAMES = [
    "forge_text_document",
    "forge_presentation",
    "forge_spreadsheet",
    "inspect_odf",
    "preview_odf",
]

# The 10 slide layouts (ir.PageRoleName); the outline discipline names them all.
LAYOUT_NAMES = [
    "title",
    "title-content",
    "two-col",
    "section",
    "big-fact",
    "quote",
    "agenda",
    "comparison",
    "chart",
    "closing",
]


def test_forge_presentation_ok(sample_presentation, tmp_path: Path) -> None:
    out = tmp_path / "x.odp"
    result = forge_presentation(sample_presentation.model_dump(), str(out))
    assert out.exists()
    assert "ok" in result


def test_forge_text_ok(sample_text_doc, tmp_path: Path) -> None:
    out = tmp_path / "x.odt"
    result = forge_text_document(sample_text_doc.model_dump(), str(out))
    assert out.exists()
    assert "ok" in result


def test_type_injected(sample_presentation, tmp_path: Path) -> None:
    # A caller (or a confused LLM) mislabels the payload as "text"; the tool
    # must override the type so the presentation still renders to .odp.
    doc = sample_presentation.model_dump()
    doc["type"] = "text"
    out = tmp_path / "injected.odp"
    result = forge_presentation(doc, str(out))
    assert out.exists()
    assert "ok" in result


def test_invalid_document_returns_error_string(tmp_path: Path) -> None:
    out = tmp_path / "bad.odp"
    result = forge_presentation({"garbage": 1}, str(out))
    assert result.startswith("error:")
    assert not out.exists()


def test_forge_spreadsheet_ok(sample_spreadsheet, tmp_path: Path) -> None:
    out = tmp_path / "x.ods"
    result = forge_spreadsheet(sample_spreadsheet.model_dump(), str(out))
    assert out.exists()
    assert "ok" in result


def test_out_dir_created(sample_presentation, tmp_path: Path) -> None:
    out = tmp_path / "deep" / "nested" / "dir" / "x.odp"
    result = forge_presentation(sample_presentation.model_dump(), str(out))
    assert out.exists()
    assert "ok" in result


def test_inspect_odf_valid(sample_presentation, tmp_path: Path, real_soffice) -> None:
    out = tmp_path / "x.odp"
    forge_presentation(sample_presentation.model_dump(), str(out))
    result = inspect_odf(str(out))
    assert "structure" in result
    assert "OK" in result


def test_inspect_odf_missing_file(tmp_path: Path) -> None:
    result = inspect_odf(str(tmp_path / "does-not-exist.odp"))
    assert result.startswith("error:")


def test_forge_error_string_bounded(tmp_path: Path) -> None:
    # A pathological payload with many invalid slides produces a huge pydantic
    # ValidationError; the returned error string must be length-bounded so it
    # never floods the MCP client.
    # 60 = the IR's own slide cap; every one of them still fails validation, so
    # the error text is long enough to need bounding.
    doc = {"title": "x", "slides": [{"title": "no layout"} for _ in range(60)]}
    result = forge_presentation(doc, str(tmp_path / "x.odp"))
    assert result.startswith("error:")
    assert "訊息截斷" in result
    assert len(result) < 1000


# ---------------------------------------------------------------------------
# preview_odf — exposes the harness PNG rendering to the wider agent ecosystem
# ---------------------------------------------------------------------------


@pytest.mark.skipif(find_soffice() is None, reason="LibreOffice not installed")
def test_preview_odf_returns_page_paths(sample_presentation, tmp_path: Path) -> None:
    # Render a real fixture .odp, then preview it. On success the tool returns a
    # JSON string an external agent can json.loads to get its page PNG paths.
    odp = render_odp(sample_presentation, tmp_path / "deck.odp")
    out_dir = tmp_path / "preview"

    result = preview_odf(str(odp), str(out_dir))

    data = json.loads(result)
    assert data["count"] == len(sample_presentation.slides)
    assert len(data["pages"]) == len(sample_presentation.slides)
    for page in data["pages"]:
        assert Path(page).exists(), page


def test_preview_odf_unavailable_returns_error(
    sample_presentation, tmp_path: Path, monkeypatch
) -> None:
    # No LibreOffice → render_pages raises PreviewUnavailable; the tool must
    # convert it to an "error: ..." string, never raise (an exception inside an
    # MCP tool becomes a protocol-level error).
    from odforge.preview import PreviewUnavailable

    def _boom(*args, **kwargs):
        raise PreviewUnavailable("soffice not found")

    monkeypatch.setattr(mcp_server, "render_pages", _boom)
    odp = render_odp(sample_presentation, tmp_path / "deck.odp")

    result = preview_odf(str(odp), str(tmp_path / "preview"))

    assert isinstance(result, str)
    assert result.startswith("error:")
    assert "preview unavailable" in result


def test_preview_odf_bad_path_returns_error(tmp_path: Path) -> None:
    # Any other failure (a missing/garbage file) is also caught, not raised.
    result = preview_odf(str(tmp_path / "does-not-exist.odp"), str(tmp_path / "out"))
    assert isinstance(result, str)
    assert result.startswith("error:")


# ---------------------------------------------------------------------------
# Theme spec markdown ⇄ THEMES/SCALES lockstep (prevents doc drift)
# ---------------------------------------------------------------------------


def _parse_md_two_col(path: Path) -> tuple[dict[str, str], dict[str, int]]:
    """Parse a theme spec md into (palette hex map, type-scale pt map).

    Walks every markdown table row ``| key | value | ... |`` and buckets it by
    the shape of the value cell: a ``#RRGGBB`` hex feeds the palette map, a bare
    integer feeds the pt-scale map. Text-valued rows (the per-layout guidance
    table) are ignored, so the two tables never collide.
    """
    palette: dict[str, str] = {}
    scale: dict[str, int] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        key, value = cells[0], cells[1]
        if re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
            palette[key.lower()] = value.upper()
        elif re.fullmatch(r"\d+", value):
            scale[key.lower()] = int(value)
    return palette, scale


@pytest.mark.parametrize("name", sorted(THEMES))
def test_theme_md_lockstep_with_themes(name: str) -> None:
    theme = THEMES[name]
    palette, scale = _parse_md_two_col(THEME_MD_DIR / f"{name}.md")

    assert palette["bg"] == theme.bg.upper()
    assert palette["surface"] == theme.surface.upper()
    assert palette["text"] == theme.text.upper()
    assert palette["muted"] == theme.muted.upper()
    assert palette["accent"] == theme.accent.upper()
    assert palette["title_color"] == theme.title_color.upper()

    assert scale["display"] == theme.display_pt
    assert scale["h1"] == theme.h1_pt
    assert scale["body"] == theme.body_pt
    assert scale["caption"] == theme.caption_pt


def test_theme_md_scale_equals_standard_tier() -> None:
    # Presets are built at the 'standard' tier; the md scale must mirror it too.
    display_pt, h1_pt, body_pt, caption_pt = SCALES["standard"]
    for name in THEMES:
        _, scale = _parse_md_two_col(THEME_MD_DIR / f"{name}.md")
        assert scale["display"] == display_pt
        assert scale["h1"] == h1_pt
        assert scale["body"] == body_pt
        assert scale["caption"] == caption_pt


# ---------------------------------------------------------------------------
# SKILL.md — the agent-facing playbook must name every tool + every layout
# ---------------------------------------------------------------------------


def _parse_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---"), "SKILL.md must open with YAML frontmatter"
    _, front, _body = text.split("---", 2)
    data: dict[str, str] = {}
    for line in front.strip().splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            data[key.strip()] = value.strip()
    return data


def test_skill_md_exists_and_frontmatter() -> None:
    assert SKILL_MD.exists(), SKILL_MD
    front = _parse_frontmatter(SKILL_MD)
    assert front.get("name") == "odforge-design"
    assert front.get("description")  # non-empty description required


def test_skill_md_references_all_tools_and_layouts() -> None:
    content = SKILL_MD.read_text(encoding="utf-8")
    for tool in MCP_TOOL_NAMES:
        assert tool in content, f"SKILL.md must reference MCP tool {tool!r}"
    for layout in LAYOUT_NAMES:
        assert layout in content, f"SKILL.md must reference layout {layout!r}"


def test_skill_md_links_theme_specs() -> None:
    content = SKILL_MD.read_text(encoding="utf-8")
    for name in THEMES:
        assert f"themes/{name}.md" in content, f"SKILL.md must link themes/{name}.md"


# ---------------------------------------------------------------------------
# forge_* now makes the SAME claim the CLI makes: three format gates plus the
# deterministic layout-budget check.
# ---------------------------------------------------------------------------


def test_forge_runs_the_libreoffice_gate_when_it_is_available(tmp_path, monkeypatch):
    """`with_soffice=False` used to be hard-coded, so "ok" from MCP was a weaker
    claim than "ok" from `odforge new` while reading identically."""
    seen: list[bool] = []
    real_validate = mcp_server.validate_odf

    def spy(path, **kwargs):
        seen.append(kwargs.get("with_soffice", False))
        return real_validate(path, with_soffice=False)

    monkeypatch.setattr(mcp_server, "validate_odf", spy)
    monkeypatch.setattr(mcp_server, "find_soffice", lambda: Path("soffice"))

    result = forge_presentation(
        {"title": "t", "slides": [{"layout": "title", "title": "封面"}]},
        str(tmp_path / "d.odp"),
    )
    assert result.startswith("ok:")
    assert seen == [True]


def test_forge_skips_the_libreoffice_gate_when_it_is_absent(tmp_path, monkeypatch):
    seen: list[bool] = []
    real_validate = mcp_server.validate_odf
    monkeypatch.setattr(
        mcp_server,
        "validate_odf",
        lambda path, **kw: (
            seen.append(kw.get("with_soffice", False)),
            real_validate(path, with_soffice=False),
        )[1],
    )
    monkeypatch.setattr(mcp_server, "find_soffice", lambda: None)

    forge_presentation(
        {"title": "t", "slides": [{"layout": "title", "title": "封面"}]},
        str(tmp_path / "d.odp"),
    )
    assert seen == [False]


def test_an_overset_deck_comes_back_with_a_named_warning(tmp_path):
    """外部 agent 塞 12 條 bullet 進來,舊行為是照畫、回一個開朗的 "ok:"。

    MCP 的分工是「ODForge 量,呼叫方改」——所以量出來的結果必須講清楚是哪一頁、
    哪個框,而不是一個 pass/fail 旗標。
    """
    deck = {
        "title": "超載",
        "slides": [
            {"layout": "title", "title": "封面"},
            {
                "layout": "title-content",
                "title": "太多重點",
                "bullets": [
                    f"第 {i} 條非常長的重點,長到一定會超出這一頁的版面預算限制"
                    for i in range(1, 13)
                ],
            },
        ],
    }
    out = tmp_path / "d.odp"
    result = forge_presentation(deck, str(out))

    # 檔案還是產出了,而且格式有效 —— 超載不是格式錯誤。
    assert result.startswith("ok:")
    assert out.is_file()
    # 但警告必須在,而且要指得出是第 2 頁。
    assert "warning:" in result
    assert "版面預算超載" in result
    assert "第 2 頁" in result
    assert "第 1 頁" not in result  # 封面沒問題,不要亂報


def test_a_deck_that_fits_carries_no_warning(tmp_path):
    result = forge_presentation(
        {
            "title": "剛好",
            "slides": [
                {"layout": "title", "title": "封面"},
                {"layout": "title-content", "title": "重點", "bullets": ["甲", "乙"]},
            ],
        },
        str(tmp_path / "d.odp"),
    )
    assert result.startswith("ok:")
    assert "warning:" not in result


@pytest.mark.parametrize(
    "tool,payload,suffix",
    [
        (forge_text_document, {"title": "t", "blocks": []}, ".odt"),
        (
            forge_spreadsheet,
            {"title": "t", "sheets": [{"name": "s", "columns": ["a"], "rows": [[1]]}]},
            ".ods",
        ),
    ],
)
def test_non_presentations_have_no_layout_budget(tmp_path, tool, payload, suffix):
    """.odt 會回流、.ods 沒有固定框,兩者都沒有版面預算可以爆。"""
    result = tool(payload, str(tmp_path / f"out{suffix}"))
    assert result.startswith("ok:")
    assert "warning:" not in result


def test_the_console_script_entry_point_resolves():
    """`pip install odforge` 之後要有一個指令可以把 MCP server 啟起來。

    在這之前只有模組路徑,想接的人得自己猜要用哪個直譯器 —— 那個障礙跟工具的
    能力毫無關係,卻擋住了所有人。
    """
    import tomllib

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    scripts = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["scripts"]
    assert scripts["odforge-mcp"] == "odforge.mcp_server:main"
    assert callable(mcp_server.main)


def test_importing_the_server_writes_nothing_to_stdout():
    """A stdio MCP server's stdout is protocol. One stray byte breaks it.

    PyMuPDF's legacy ``fitz`` alias prints a deprecation notice **to stdout** on
    import (1.28.2 does; 1.28.0 does not — which is why this never showed up on
    a dev machine). Importing it anywhere in the tree therefore corrupted the
    JSON-RPC stream before the server had answered a single request.

    A fresh interpreter is used deliberately: by the time this test module is
    loaded the import has already happened, so an in-process check would pass no
    matter what.
    """
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-c", "import odforge.mcp_server"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "", f"stdout must stay empty, got: {proc.stdout!r}"
