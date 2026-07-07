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


def test_inspect_odf_valid(sample_presentation, tmp_path: Path) -> None:
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
    doc = {"title": "x", "slides": [{"title": "no layout"} for _ in range(300)]}
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


@pytest.mark.parametrize("name", ["academic", "minimal", "dark"])
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
    for name in ("academic", "minimal", "dark"):
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
    for name in ("academic", "minimal", "dark"):
        assert f"themes/{name}.md" in content, f"SKILL.md must link themes/{name}.md"
