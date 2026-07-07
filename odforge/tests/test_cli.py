"""CLI tests for `odforge new`.

All tests mock ``odforge.cli.generate_ir`` so no real API is ever hit;
rendering and validation run for real against a tmp_path output file.
"""

import zipfile

import pytest
from typer.testing import CliRunner

from odforge.cli import app

runner = CliRunner()


def _out(result) -> str:
    """Combined stdout + stderr for robust assertions across click versions."""
    err = ""
    try:
        err = result.stderr
    except (ValueError, AttributeError):
        err = ""
    return (result.stdout or "") + (err or "")


def test_new_odp_success(tmp_path, monkeypatch, sample_presentation):
    monkeypatch.setattr(
        "odforge.cli.generate_ir",
        lambda prompt, doc_type, backend=None: sample_presentation,
    )
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["new", "做簡報", "-o", "out.odp", "--no-soffice"])
    assert result.exit_code == 0, _out(result)
    assert (tmp_path / "out.odp").exists()
    assert "OK" in _out(result)


def test_new_odt_success(tmp_path, monkeypatch, sample_text_doc):
    monkeypatch.setattr(
        "odforge.cli.generate_ir",
        lambda prompt, doc_type, backend=None: sample_text_doc,
    )
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["new", "寫文件", "-o", "out.odt", "--no-soffice"])
    assert result.exit_code == 0, _out(result)
    assert (tmp_path / "out.odt").exists()


def test_doc_type_inference(tmp_path, monkeypatch, sample_presentation, sample_text_doc):
    seen = {}

    def fake(prompt, doc_type, backend=None):
        seen["doc_type"] = doc_type
        return sample_presentation if doc_type == "presentation" else sample_text_doc

    monkeypatch.setattr("odforge.cli.generate_ir", fake)
    monkeypatch.chdir(tmp_path)

    runner.invoke(app, ["new", "p", "-o", "out.odp", "--no-soffice"])
    assert seen["doc_type"] == "presentation"

    runner.invoke(app, ["new", "t", "-o", "out.odt", "--no-soffice"])
    assert seen["doc_type"] == "text"


def test_unknown_extension_exit_2(tmp_path, monkeypatch):
    called = {"hit": False}

    def fake(prompt, doc_type, backend=None):
        called["hit"] = True
        raise AssertionError("generate_ir must not be called")

    monkeypatch.setattr("odforge.cli.generate_ir", fake)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["new", "x", "-o", "out.xyz"])
    assert result.exit_code == 2
    assert ".odt" in _out(result)
    assert called["hit"] is False


def test_theme_override(tmp_path, monkeypatch, sample_presentation):
    assert sample_presentation.theme == "academic"
    monkeypatch.setattr(
        "odforge.cli.generate_ir",
        lambda prompt, doc_type, backend=None: sample_presentation,
    )
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app, ["new", "做簡報", "-o", "out.odp", "--theme", "dark", "--no-soffice"]
    )
    assert result.exit_code == 0, _out(result)
    with zipfile.ZipFile(tmp_path / "out.odp") as z:
        styles = z.read("styles.xml").decode("utf-8")
    assert "#1E1E2E" in styles


def test_theme_explicit_academic_overrides_llm(tmp_path, monkeypatch, sample_presentation):
    # Regression: an explicit --theme must override the LLM's theme even when
    # its value equals the option's former default (academic). Here the LLM
    # returns a dark deck; --theme academic must win, so styles.xml carries the
    # academic background (#FFFFFF), not the dark one (#1E1E2E).
    from odforge.themes import THEMES

    dark_pres = sample_presentation.model_copy(update={"theme": "dark"})
    assert dark_pres.theme == "dark"
    monkeypatch.setattr(
        "odforge.cli.generate_ir",
        lambda prompt, doc_type, backend=None: dark_pres,
    )
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app, ["new", "做簡報", "-o", "out.odp", "--theme", "academic", "--no-soffice"]
    )
    assert result.exit_code == 0, _out(result)
    with zipfile.ZipFile(tmp_path / "out.odp") as z:
        styles = z.read("styles.xml").decode("utf-8")
    assert THEMES["academic"].bg in styles
    assert THEMES["dark"].bg not in styles


def test_theme_omitted_respects_llm(tmp_path, monkeypatch, sample_presentation):
    # When --theme is omitted, the LLM's chosen theme must be respected: a dark
    # deck from the LLM stays dark (#1E1E2E), with no override applied.
    from odforge.themes import THEMES

    dark_pres = sample_presentation.model_copy(update={"theme": "dark"})
    monkeypatch.setattr(
        "odforge.cli.generate_ir",
        lambda prompt, doc_type, backend=None: dark_pres,
    )
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["new", "做簡報", "-o", "out.odp", "--no-soffice"])
    assert result.exit_code == 0, _out(result)
    with zipfile.ZipFile(tmp_path / "out.odp") as z:
        styles = z.read("styles.xml").decode("utf-8")
    assert THEMES["dark"].bg in styles


def test_generate_failure_exit_1(tmp_path, monkeypatch):
    def boom(prompt, doc_type, backend=None):
        raise RuntimeError("boom")

    monkeypatch.setattr("odforge.cli.generate_ir", boom)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["new", "x", "-o", "out.odp", "--no-soffice"])
    assert result.exit_code == 1
    out = _out(result)
    assert "boom" in out
    assert "Traceback" not in out


def test_backend_passthrough(tmp_path, monkeypatch, sample_presentation):
    seen = {}

    def fake(prompt, doc_type, backend=None):
        seen["backend"] = backend
        return sample_presentation

    monkeypatch.setattr("odforge.cli.generate_ir", fake)
    monkeypatch.chdir(tmp_path)
    runner.invoke(
        app, ["new", "p", "-o", "out.odp", "--backend", "ollama", "--no-soffice"]
    )
    assert seen["backend"] == "ollama"


def test_new_ods_success(tmp_path, monkeypatch, sample_spreadsheet):
    # Task 8.1 registers the spreadsheet renderer: .ods now renders through the
    # CLI and validates, so the command succeeds with exit 0.
    monkeypatch.setattr(
        "odforge.cli.generate_ir",
        lambda prompt, doc_type, backend=None: sample_spreadsheet,
    )
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["new", "x", "-o", "out.ods", "--no-soffice"])
    assert result.exit_code == 0, _out(result)
    assert (tmp_path / "out.ods").exists()
    assert "OK" in _out(result)


def test_error_message_with_brackets_survives(tmp_path, monkeypatch):
    # Pydantic-style errors contain bracketed segments like
    # "[type=missing, input_value=x]"; rich markup must not swallow them.
    def boom(prompt, doc_type, backend=None):
        raise RuntimeError("field required [type=missing, input_value=x]")

    monkeypatch.setattr("odforge.cli.generate_ir", boom)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["new", "x", "-o", "out.odp", "--no-soffice"])
    assert result.exit_code == 1
    assert "input_value" in _out(result)


def test_long_error_message_truncated(tmp_path, monkeypatch):
    # A real-API failure once dumped ~18KB of pydantic errors to the terminal;
    # the printed message must be bounded and flag the truncation.
    def boom(prompt, doc_type, backend=None):
        raise RuntimeError("x" * 2000)

    monkeypatch.setattr("odforge.cli.generate_ir", boom)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["new", "x", "-o", "out.odp", "--no-soffice"])
    assert result.exit_code == 1
    out = _out(result)
    assert "訊息截斷" in out
    # 500-char cap + FAIL prefix + suffix + rich line wrapping newlines.
    assert len(out) < 1000


def test_new_creates_missing_parent_dir(tmp_path, monkeypatch, sample_presentation):
    # -o into a not-yet-existing subdirectory must succeed: the CLI creates the
    # parent directory before rendering (matching the MCP server's behaviour).
    monkeypatch.setattr(
        "odforge.cli.generate_ir",
        lambda prompt, doc_type, backend=None: sample_presentation,
    )
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app, ["new", "做簡報", "-o", "sub/deep/out.odp", "--no-soffice"]
    )
    assert result.exit_code == 0, _out(result)
    assert (tmp_path / "sub" / "deep" / "out.odp").exists()


def test_validation_failure_exit_1(tmp_path, monkeypatch, sample_text_doc):
    from odforge.validate import ValidationReport

    monkeypatch.setattr(
        "odforge.cli.generate_ir",
        lambda prompt, doc_type, backend=None: sample_text_doc,
    )
    monkeypatch.setattr(
        "odforge.cli.validate_odf",
        lambda path, with_soffice=False: ValidationReport(
            ok=False, gates={"structure": (False, "broken")}
        ),
    )
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["new", "t", "-o", "out.odt", "--no-soffice"])
    assert result.exit_code == 1
    assert "FAIL" in _out(result)
