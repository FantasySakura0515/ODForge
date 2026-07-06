"""Tests for the ODForge MCP server tools.

The four tools are exercised as plain functions (the FastMCP registration is a
thin wrapper); no MCP server process is started. Every failure path must return
an ``"error: ..."`` string rather than raising, because an exception inside an
MCP tool becomes a protocol-level error.
"""

from __future__ import annotations

from pathlib import Path

from odforge.mcp_server import (
    forge_presentation,
    forge_spreadsheet,
    forge_text_document,
    inspect_odf,
)


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
