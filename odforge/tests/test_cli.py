"""CLI tests for `odforge new`.

Presentations default to the two-stage pipeline (generate_outline ->
generate_slides); those tests mock ``odforge.cli.generate_outline`` and
``odforge.cli.generate_slides``. The ``--one-shot`` escape hatch and the
non-presentation types (.odt/.ods) still go through ``odforge.cli.generate_ir``.
No real API is ever hit; rendering and validation run for real against a
tmp_path output file.
"""

import zipfile

from typer.testing import CliRunner

from odforge.cli import app
from odforge.ir import Outline, PageRole

runner = CliRunner()


def _out(result) -> str:
    """Combined stdout + stderr for robust assertions across click versions."""
    err = ""
    try:
        err = result.stderr
    except (ValueError, AttributeError):
        err = ""
    return (result.stdout or "") + (err or "")


def _sample_outline(mode="presenter", design=None) -> Outline:
    return Outline(
        design=design,
        mode=mode,
        pages=[
            PageRole(role="title", title="標題頁", gist="開場破題"),
            PageRole(role="title-content", title="重點", gist="三個要點"),
            PageRole(role="closing", title="結語", gist="收束全場"),
        ],
    )


def _mock_two_stage(monkeypatch, presentation, outline=None):
    """Route the presentation two-stage pipeline through fakes (no real API)."""
    if outline is None:
        outline = _sample_outline()
    monkeypatch.setattr(
        "odforge.cli.generate_outline", lambda prompt, backend=None: outline
    )
    monkeypatch.setattr(
        "odforge.cli.generate_slides", lambda outline, backend=None: presentation
    )


# ---------------------------------------------------------------------------
# Two-stage pipeline (the default for presentations)
# ---------------------------------------------------------------------------


def test_new_odp_success(tmp_path, monkeypatch, sample_presentation):
    _mock_two_stage(monkeypatch, sample_presentation)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["new", "做簡報", "-o", "out.odp", "--no-soffice"])
    assert result.exit_code == 0, _out(result)
    assert (tmp_path / "out.odp").exists()
    assert "OK" in _out(result)


def test_default_odp_uses_two_stage(tmp_path, monkeypatch, sample_presentation):
    # The default presentation path invokes BOTH stages and never generate_ir;
    # the --backend option flows through to both stage functions.
    calls = {"outline": 0, "slides": 0, "ir": 0, "backend": None}

    def fake_outline(prompt, backend=None):
        calls["outline"] += 1
        calls["backend"] = backend
        return _sample_outline()

    def fake_slides(outline, backend=None):
        calls["slides"] += 1
        return sample_presentation

    def fake_ir(prompt, doc_type, backend=None):
        calls["ir"] += 1
        return sample_presentation

    monkeypatch.setattr("odforge.cli.generate_outline", fake_outline)
    monkeypatch.setattr("odforge.cli.generate_slides", fake_slides)
    monkeypatch.setattr("odforge.cli.generate_ir", fake_ir)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app, ["new", "做簡報", "-o", "out.odp", "--backend", "ollama", "--no-soffice"]
    )
    assert result.exit_code == 0, _out(result)
    assert calls["ir"] == 0
    assert calls["outline"] == 1 and calls["slides"] == 1
    assert calls["backend"] == "ollama"
    assert (tmp_path / "out.odp").exists()


def test_interactive_yes_generates_file(tmp_path, monkeypatch, sample_presentation):
    _mock_two_stage(monkeypatch, sample_presentation)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app,
        ["new", "做簡報", "-o", "out.odp", "--interactive", "--no-soffice"],
        input="y\n",
    )
    assert result.exit_code == 0, _out(result)
    assert (tmp_path / "out.odp").exists()


def test_interactive_no_cancels_no_file(tmp_path, monkeypatch, sample_presentation):
    calls = {"slides": 0}
    monkeypatch.setattr(
        "odforge.cli.generate_outline", lambda prompt, backend=None: _sample_outline()
    )

    def fake_slides(outline, backend=None):
        calls["slides"] += 1
        return sample_presentation

    monkeypatch.setattr("odforge.cli.generate_slides", fake_slides)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app,
        ["new", "做簡報", "-o", "out.odp", "--interactive", "--no-soffice"],
        input="n\n",
    )
    assert result.exit_code == 0, _out(result)
    assert not (tmp_path / "out.odp").exists()
    assert "已取消" in _out(result)
    assert calls["slides"] == 0


def test_interactive_prints_outline_and_design(tmp_path, monkeypatch, sample_presentation):
    # The interactive checkpoint prints the page table + a design summary (with
    # palette swatches) without crashing on the rich colour markup.
    from odforge.ir import DesignSpec, FontPair, Palette

    design = DesignSpec(
        palette=Palette(
            bg="#FBF9F4",
            surface="#EFEADD",
            text="#1F2733",
            muted="#5B6470",
            accent="#1A4B8C",
        ),
        fonts=FontPair(display="Noto Serif TC", body="Noto Sans TC"),
        scale="standard",
        mode="presenter",
    )
    outline = _sample_outline(design=design)
    _mock_two_stage(monkeypatch, sample_presentation, outline=outline)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app,
        ["new", "做簡報", "-o", "out.odp", "--interactive", "--no-soffice"],
        input="n\n",
    )
    assert result.exit_code == 0, _out(result)
    assert result.exception is None
    out = _out(result)
    assert "標題頁" in out  # the outline table rendered a page title
    assert "已取消" in out
    assert "#1A4B8C" in out  # a palette hex from the design summary appears verbatim
    assert "presenter" in out  # the narrative mode string appears verbatim


def test_mode_override_passes_through(tmp_path, monkeypatch, sample_presentation):
    # --mode overrides the outline's mode before stage 2: generate_slides must
    # receive an outline whose mode is the CLI-supplied one.
    outline = _sample_outline(mode="detailed")
    seen = {}
    monkeypatch.setattr(
        "odforge.cli.generate_outline", lambda prompt, backend=None: outline
    )

    def fake_slides(outline, backend=None):
        seen["mode"] = outline.mode
        return sample_presentation

    monkeypatch.setattr("odforge.cli.generate_slides", fake_slides)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app, ["new", "做簡報", "-o", "out.odp", "--mode", "presenter", "--no-soffice"]
    )
    assert result.exit_code == 0, _out(result)
    assert seen["mode"] == "presenter"


def test_mode_default_preserves_outline_mode(tmp_path, monkeypatch, sample_presentation):
    # Without --mode the outline's own mode is passed through unchanged.
    outline = _sample_outline(mode="detailed")
    seen = {}
    monkeypatch.setattr(
        "odforge.cli.generate_outline", lambda prompt, backend=None: outline
    )

    def fake_slides(outline, backend=None):
        seen["mode"] = outline.mode
        return sample_presentation

    monkeypatch.setattr("odforge.cli.generate_slides", fake_slides)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["new", "做簡報", "-o", "out.odp", "--no-soffice"])
    assert result.exit_code == 0, _out(result)
    assert seen["mode"] == "detailed"


def test_outline_failure_exit_1(tmp_path, monkeypatch):
    def boom(prompt, backend=None):
        raise RuntimeError("outline boom")

    monkeypatch.setattr("odforge.cli.generate_outline", boom)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["new", "x", "-o", "out.odp", "--no-soffice"])
    assert result.exit_code == 1
    out = _out(result)
    assert "outline boom" in out
    assert "Traceback" not in out


def test_slides_failure_exit_1(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "odforge.cli.generate_outline", lambda prompt, backend=None: _sample_outline()
    )

    def boom(outline, backend=None):
        raise RuntimeError("slides boom")

    monkeypatch.setattr("odforge.cli.generate_slides", boom)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["new", "x", "-o", "out.odp", "--no-soffice"])
    assert result.exit_code == 1
    assert "slides boom" in _out(result)


def test_theme_override(tmp_path, monkeypatch, sample_presentation):
    assert sample_presentation.theme == "academic"
    _mock_two_stage(monkeypatch, sample_presentation)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app, ["new", "做簡報", "-o", "out.odp", "--theme", "dark", "--no-soffice"]
    )
    assert result.exit_code == 0, _out(result)
    with zipfile.ZipFile(tmp_path / "out.odp") as z:
        styles = z.read("styles.xml").decode("utf-8")
    from odforge.themes import THEMES

    assert THEMES["dark"].bg in styles


def test_theme_explicit_academic_overrides_llm(tmp_path, monkeypatch, sample_presentation):
    # Regression: an explicit --theme must override the LLM's theme even when
    # its value equals the option's former default (academic). Here the deck
    # from stage 2 is dark; --theme academic must win, so styles.xml carries the
    # academic background, not the dark one.
    from odforge.themes import THEMES

    dark_pres = sample_presentation.model_copy(update={"theme": "dark"})
    assert dark_pres.theme == "dark"
    _mock_two_stage(monkeypatch, dark_pres)
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
    # When --theme is omitted, the deck's chosen theme must be respected: a dark
    # deck from stage 2 stays dark, with no override applied.
    from odforge.themes import THEMES

    dark_pres = sample_presentation.model_copy(update={"theme": "dark"})
    _mock_two_stage(monkeypatch, dark_pres)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["new", "做簡報", "-o", "out.odp", "--no-soffice"])
    assert result.exit_code == 0, _out(result)
    with zipfile.ZipFile(tmp_path / "out.odp") as z:
        styles = z.read("styles.xml").decode("utf-8")
    assert THEMES["dark"].bg in styles


def test_theme_override_discards_deck_design(tmp_path, monkeypatch, sample_presentation):
    # An explicit --theme locks a built-in preset: it must win over any
    # DesignSpec the two-stage pipeline attached, otherwise resolve_design lets
    # the design's palette win and --theme is a silent no-op. Here the deck
    # carries a dark-ish design (bg #171826); --theme academic must drop it, so
    # styles.xml shows the academic bg, not the design's, plus a discard note.
    from odforge.ir import DesignSpec, FontPair, Palette
    from odforge.themes import THEMES

    design = DesignSpec(
        palette=Palette(
            bg="#171826",
            surface="#252842",
            text="#E8EAF2",
            muted="#9BA0C4",
            accent="#3DD6E6",
        ),
        fonts=FontPair(display="Noto Sans TC", body="Noto Sans TC"),
        scale="standard",
        mode="presenter",
    )
    designed = sample_presentation.model_copy(update={"design": design})
    assert designed.design is not None
    _mock_two_stage(monkeypatch, designed)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app, ["new", "做簡報", "-o", "out.odp", "--theme", "academic", "--no-soffice"]
    )
    assert result.exit_code == 0, _out(result)
    with zipfile.ZipFile(tmp_path / "out.odp") as z:
        styles = z.read("styles.xml").decode("utf-8")
    assert THEMES["academic"].bg in styles
    assert "#171826" not in styles  # the discarded design's bg must not leak in
    assert "捨棄 AI 自選設計" in _out(result)  # informational discard note


def test_over_budget_deck_warns_but_exits_0(tmp_path, monkeypatch):
    # A deck whose slide overflows its frame must emit a yellow WARN but still
    # succeed (exit 0); the hard enforcement loop lives in stage 2, not the CLI.
    from odforge.ir import Presentation, Slide

    over = Presentation(
        title="超載簡報",
        slides=[
            Slide(
                layout="title-content",
                title="爆量頁",
                bullets=[f"這是第{i}條非常冗長的項目內容說明文字" for i in range(40)],
            ),
        ],
    )
    _mock_two_stage(monkeypatch, over)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["new", "做簡報", "-o", "out.odp", "--no-soffice"])
    assert result.exit_code == 0, _out(result)
    out = _out(result)
    assert "WARN" in out
    assert "爆量頁" in out
    assert "bullets" in out
    assert (tmp_path / "out.odp").exists()


def test_new_creates_missing_parent_dir(tmp_path, monkeypatch, sample_presentation):
    # -o into a not-yet-existing subdirectory must succeed: the CLI creates the
    # parent directory before rendering (matching the MCP server's behaviour).
    _mock_two_stage(monkeypatch, sample_presentation)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app, ["new", "做簡報", "-o", "sub/deep/out.odp", "--no-soffice"]
    )
    assert result.exit_code == 0, _out(result)
    assert (tmp_path / "sub" / "deep" / "out.odp").exists()


# ---------------------------------------------------------------------------
# --from-template: style extraction locks the design (殺手功能:吃現有範本)
# ---------------------------------------------------------------------------


def _extracted_design():
    from odforge.ir import DesignSpec, FontPair, Palette

    # A distinctive dark-ish extracted design (bg #171826) so its presence in
    # the final render is unambiguous.
    return DesignSpec(
        palette=Palette(
            bg="#171826",
            surface="#252842",
            text="#E8EAF2",
            muted="#9BA0C4",
            accent="#3DD6E6",
        ),
        fonts=FontPair(display="Noto Sans TC", body="Noto Sans TC"),
        scale="standard",
        mode="presenter",
    )


def test_from_template_locks_extracted_design(tmp_path, monkeypatch, sample_presentation):
    # --from-template extracts a design and LOCKS it: generate_outline still runs
    # for page roles, but stage 2 fills against the extracted design (handed in on
    # the outline), overriding whatever the LLM had chosen.
    extracted = _extracted_design()
    monkeypatch.setattr("odforge.cli.extract_design", lambda path: extracted)

    outline = _sample_outline(design=None)
    seen = {}
    monkeypatch.setattr(
        "odforge.cli.generate_outline", lambda prompt, backend=None: outline
    )

    def fake_slides(outline, backend=None):
        seen["design"] = outline.design
        # Mimic the real generate_slides: re-attach the outline's design.
        return sample_presentation.model_copy(update={"design": outline.design})

    monkeypatch.setattr("odforge.cli.generate_slides", fake_slides)

    tmpl = tmp_path / "gov_template.otp"
    tmpl.write_bytes(b"placeholder - extract_design is monkeypatched")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app,
        ["new", "做簡報", "-o", "out.odp", "--from-template", str(tmpl), "--no-soffice"],
    )
    assert result.exit_code == 0, _out(result)
    # generate_slides received the locked (extracted) design on its outline.
    assert seen["design"] == extracted
    # The rendered deck carries the extracted bg.
    with zipfile.ZipFile(tmp_path / "out.odp") as z:
        styles = z.read("styles.xml").decode("utf-8")
    assert "#171826" in styles
    # An info line names the template that was applied.
    out = _out(result)
    assert "gov_template.otp" in out


def test_from_template_extraction_failure_exit_1(tmp_path, monkeypatch, sample_presentation):
    # A genuinely unreadable template surfaces as a concise FAIL (exit 1), never a
    # traceback.
    from odforge.extract import TemplateExtractionError

    def boom(path):
        raise TemplateExtractionError("無法讀取範本")

    monkeypatch.setattr("odforge.cli.extract_design", boom)
    _mock_two_stage(monkeypatch, sample_presentation)
    tmpl = tmp_path / "broken.otp"
    tmpl.write_bytes(b"garbage")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app,
        ["new", "做簡報", "-o", "out.odp", "--from-template", str(tmpl), "--no-soffice"],
    )
    assert result.exit_code == 1
    out = _out(result)
    assert "無法讀取範本" in out
    assert "Traceback" not in out


# ---------------------------------------------------------------------------
# One-shot escape hatch + non-presentation types (generate_ir path)
# ---------------------------------------------------------------------------


def test_one_shot_uses_generate_ir(tmp_path, monkeypatch, sample_presentation):
    # --one-shot forces the v1 single-call path: generate_ir runs, the two-stage
    # generate_outline is never touched.
    calls = {"ir": 0, "outline": 0}

    def fake_ir(prompt, doc_type, backend=None):
        calls["ir"] += 1
        return sample_presentation

    def fake_outline(prompt, backend=None):
        calls["outline"] += 1
        return _sample_outline()

    monkeypatch.setattr("odforge.cli.generate_ir", fake_ir)
    monkeypatch.setattr("odforge.cli.generate_outline", fake_outline)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app, ["new", "做簡報", "-o", "out.odp", "--one-shot", "--no-soffice"]
    )
    assert result.exit_code == 0, _out(result)
    assert calls["ir"] == 1
    assert calls["outline"] == 0
    assert (tmp_path / "out.odp").exists()


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
    # For .odp the generate_ir path is exercised via --one-shot; .odt uses it by
    # default. Both must receive the extension-inferred doc_type.
    seen = {}

    def fake(prompt, doc_type, backend=None):
        seen["doc_type"] = doc_type
        return sample_presentation if doc_type == "presentation" else sample_text_doc

    monkeypatch.setattr("odforge.cli.generate_ir", fake)
    monkeypatch.chdir(tmp_path)

    runner.invoke(app, ["new", "p", "-o", "out.odp", "--one-shot", "--no-soffice"])
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


def test_generate_failure_exit_1(tmp_path, monkeypatch):
    def boom(prompt, doc_type, backend=None):
        raise RuntimeError("boom")

    monkeypatch.setattr("odforge.cli.generate_ir", boom)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app, ["new", "x", "-o", "out.odp", "--one-shot", "--no-soffice"]
    )
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
        app,
        ["new", "p", "-o", "out.odp", "--one-shot", "--backend", "ollama", "--no-soffice"],
    )
    assert seen["backend"] == "ollama"


def test_serve_rejects_remote_bind_without_explicit_acknowledgement():
    result = runner.invoke(app, ["serve", "--host", "0.0.0.0"])

    assert result.exit_code != 0
    assert "--allow-remote" in result.output


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
    result = runner.invoke(
        app, ["new", "x", "-o", "out.odp", "--one-shot", "--no-soffice"]
    )
    assert result.exit_code == 1
    assert "input_value" in _out(result)


def test_long_error_message_truncated(tmp_path, monkeypatch):
    # A real-API failure once dumped ~18KB of pydantic errors to the terminal;
    # the printed message must be bounded and flag the truncation.
    def boom(prompt, doc_type, backend=None):
        raise RuntimeError("x" * 2000)

    monkeypatch.setattr("odforge.cli.generate_ir", boom)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app, ["new", "x", "-o", "out.odp", "--one-shot", "--no-soffice"]
    )
    assert result.exit_code == 1
    out = _out(result)
    assert "訊息截斷" in out
    # 500-char cap + FAIL prefix + suffix + rich line wrapping newlines.
    assert len(out) < 1000


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


# ---------------------------------------------------------------------------
# --qa: the fourth gate (design QA loop). Fully mocked — no soffice, no vision
# backend, no real loop; odforge.cli.run_qa_loop and find_soffice are patched.
# ---------------------------------------------------------------------------


def test_qa_flag_runs_loop_and_prints_summary(tmp_path, monkeypatch, sample_presentation):
    from odforge.critic import Finding, QAReport

    _mock_two_stage(monkeypatch, sample_presentation)
    monkeypatch.setattr("odforge.cli.find_soffice", lambda: __import__("pathlib").Path("soffice"))
    monkeypatch.setenv("ODFORGE_VISION_BACKEND", "ollama")

    captured = {}

    def fake_loop(ir, out_path, *, outline=None, backend=None, llm_backend=None):
        captured["backend"] = backend
        captured["outline_pages"] = None if outline is None else len(outline.pages)
        return QAReport(
            rounds=2,
            findings_by_round=[
                [Finding(slide_no=2, issue="溢出", severity="error", fix_hint="縮短")],
                [],
            ],
            final_ok=True,
        )

    monkeypatch.setattr("odforge.cli.run_qa_loop", fake_loop)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app, ["new", "做簡報", "-o", "out.odp", "--qa", "--no-soffice"]
    )
    assert result.exit_code == 0, _out(result)
    out = _out(result)
    assert "設計評審" in out  # the before/after summary table title
    assert "設計 OK" in out  # final_ok=True line
    assert captured["backend"] == "ollama"  # vision backend resolved from env
    assert captured["outline_pages"] == 3  # the stage-1 outline was handed through


def test_qa_flag_reports_unconverged(tmp_path, monkeypatch, sample_presentation):
    from odforge.critic import Finding, QAReport

    _mock_two_stage(monkeypatch, sample_presentation)
    monkeypatch.setattr("odforge.cli.find_soffice", lambda: __import__("pathlib").Path("soffice"))
    monkeypatch.setenv("ODFORGE_VISION_BACKEND", "ollama")

    err = Finding(slide_no=2, issue="溢出", severity="error", fix_hint="縮短")
    monkeypatch.setattr(
        "odforge.cli.run_qa_loop",
        lambda *a, **k: QAReport(
            rounds=2, findings_by_round=[[err], [err]], final_ok=False
        ),
    )
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app, ["new", "做簡報", "-o", "out.odp", "--qa", "--no-soffice"]
    )
    assert result.exit_code == 0, _out(result)
    assert "設計仍有 error" in _out(result)


def test_qa_flag_falls_back_when_vision_off(tmp_path, monkeypatch, sample_presentation):
    _mock_two_stage(monkeypatch, sample_presentation)
    monkeypatch.setattr("odforge.cli.find_soffice", lambda: __import__("pathlib").Path("soffice"))
    monkeypatch.delenv("ODFORGE_VISION_BACKEND", raising=False)  # default "off"

    def boom(*a, **k):
        raise AssertionError("run_qa_loop must not run when the vision backend is off")

    monkeypatch.setattr("odforge.cli.run_qa_loop", boom)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app, ["new", "做簡報", "-o", "out.odp", "--qa", "--no-soffice"]
    )
    assert result.exit_code == 0, _out(result)  # fallback, never fails
    out = _out(result)
    assert "--qa 已略過" in out
    assert "視覺後端" in out


def test_qa_flag_falls_back_when_no_soffice(tmp_path, monkeypatch, sample_presentation):
    _mock_two_stage(monkeypatch, sample_presentation)
    monkeypatch.setattr("odforge.cli.find_soffice", lambda: None)
    monkeypatch.setenv("ODFORGE_VISION_BACKEND", "ollama")

    def boom(*a, **k):
        raise AssertionError("run_qa_loop must not run without soffice")

    monkeypatch.setattr("odforge.cli.run_qa_loop", boom)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app, ["new", "做簡報", "-o", "out.odp", "--qa", "--no-soffice"]
    )
    assert result.exit_code == 0, _out(result)
    out = _out(result)
    assert "--qa 已略過" in out
    assert "找不到" in out  # names the missing LibreOffice (soffice) dependency


def test_qa_flag_ignored_for_non_presentation(tmp_path, monkeypatch, sample_text_doc):
    monkeypatch.setattr(
        "odforge.cli.generate_ir",
        lambda prompt, doc_type, backend=None: sample_text_doc,
    )

    def boom(*a, **k):
        raise AssertionError("QA loop must not run for .odt")

    monkeypatch.setattr("odforge.cli.run_qa_loop", boom)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app, ["new", "寫文件", "-o", "out.odt", "--qa", "--no-soffice"]
    )
    assert result.exit_code == 0, _out(result)
    assert "僅適用於簡報" in _out(result)
