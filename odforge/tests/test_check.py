"""Tests for Task 8.2: ``check_odf`` / ``diff_docx_odt`` and the ``check`` CLI.

``check.py`` only consumes a ``Path`` and the ``validate`` layer; the renderers
appear here solely to manufacture real ODF inputs to check against. Neither
``check_odf`` nor ``diff_docx_odt`` ever raises - failures come back as report
text or ``error:`` strings.
"""

import subprocess
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from odforge.check import check_odf, diff_docx_odt
from odforge.cli import app
from odforge.render.odp import render_odp
from odforge.render.odt import render_odt
from odforge.validate import find_soffice

runner = CliRunner()


def _out(result) -> str:
    err = ""
    try:
        err = result.stderr
    except (ValueError, AttributeError):
        err = ""
    return (result.stdout or "") + (err or "")


def test_check_valid_odp_report(tmp_path, sample_presentation, monkeypatch):
    monkeypatch.setattr("odforge.check.find_soffice", lambda: None)
    out = render_odp(sample_presentation, tmp_path / "p.odp")
    report = check_odf(out)
    assert "# ODF 檢測報告" in report
    assert "structure: OK" in report
    assert "樣式引用" in report


def test_check_missing_file(tmp_path):
    report = check_odf(tmp_path / "nope.odt")
    assert report.startswith("error: file not found")


def test_check_reports_fail_for_broken(tmp_path, monkeypatch):
    monkeypatch.setattr("odforge.check.find_soffice", lambda: None)
    bad = tmp_path / "bad.odt"
    # mimetype not first -> structure gate fails.
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("content.xml", "<a/>")
        zi = zipfile.ZipInfo("mimetype")
        zi.compress_type = zipfile.ZIP_STORED
        z.writestr(zi, "application/vnd.oasis.opendocument.text")
        z.writestr("META-INF/manifest.xml", "<m/>")
    report = check_odf(bad)
    assert "FAIL" in report


def test_style_refs_ok_for_own_output(tmp_path, sample_text_doc, monkeypatch):
    monkeypatch.setattr("odforge.check.find_soffice", lambda: None)
    out = render_odt(sample_text_doc, tmp_path / "d.odt")
    report = check_odf(out)
    section = report.split("## 樣式引用", 1)[1]
    assert "OK" in section


def test_diff_requires_soffice(tmp_path, monkeypatch):
    monkeypatch.setattr("odforge.check.find_soffice", lambda: None)
    report = diff_docx_odt(tmp_path / "whatever.docx")
    assert "需要 LibreOffice" in report


@pytest.mark.skipif(find_soffice() is None, reason="LibreOffice not installed")
def test_diff_real_docx(tmp_path, sample_text_doc):
    # No new deps: build the .docx input by reverse-converting our own .odt.
    odt = render_odt(sample_text_doc, tmp_path / "d.odt")
    soffice = find_soffice()
    profile = (tmp_path / "profile").as_uri()
    proc = subprocess.run(
        [
            str(soffice),
            "--headless",
            f"-env:UserInstallation={profile}",
            "--convert-to",
            "docx",
            "--outdir",
            str(tmp_path),
            str(odt),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    docx = tmp_path / "d.docx"
    assert docx.exists(), proc.stderr or proc.stdout
    report = diff_docx_odt(docx)
    assert "heuristic" in report
    assert "段落" in report or "paragraph" in report


def test_diff_never_raises_on_subprocess_error(tmp_path, monkeypatch):
    # A stale soffice path makes subprocess.run raise FileNotFoundError;
    # diff_docx_odt must swallow it into an "error:" string, never raise.
    fake_docx = tmp_path / "x.docx"
    fake_docx.write_bytes(b"not a real docx")
    monkeypatch.setattr(
        "odforge.check.find_soffice", lambda: Path("C:/nonexistent/soffice.exe")
    )
    result = diff_docx_odt(fake_docx)  # must not raise
    assert result.startswith("error:")


def test_cli_check_exit_codes(tmp_path, sample_text_doc, monkeypatch):
    monkeypatch.setattr("odforge.check.find_soffice", lambda: None)
    out = render_odt(sample_text_doc, tmp_path / "d.odt")
    result = runner.invoke(app, ["check", str(out)])
    assert result.exit_code == 0, _out(result)

    monkeypatch.setattr(
        "odforge.cli.check_odf_verdict", lambda path: ("error: x", True)
    )
    result = runner.invoke(app, ["check", str(out)])
    assert result.exit_code == 1, _out(result)


def test_cli_check_exit_code_ignores_fail_substring_in_filename(
    tmp_path, sample_text_doc, monkeypatch
):
    # 判定必須來自結構化 verdict:報告開頭就是檔名,舊的 `"FAIL" in report`
    # 讓一份完全合格、只是檔名帶 FAIL 的檔案 exit 1,搞壞所有靠 exit code 的腳本。
    monkeypatch.setattr("odforge.check.find_soffice", lambda: None)
    out = render_odt(sample_text_doc, tmp_path / "q4-FAIL-review.odt")
    result = runner.invoke(app, ["check", str(out)])
    assert result.exit_code == 0, _out(result)


def test_cli_check_out_creates_missing_parent_dir(
    tmp_path, sample_text_doc, monkeypatch
):
    # `new` 會替 -o 補目錄,`check -o` 以前不會:直接把 FileNotFoundError
    # traceback 丟給使用者。
    monkeypatch.setattr("odforge.check.find_soffice", lambda: None)
    out = render_odt(sample_text_doc, tmp_path / "d.odt")
    report_path = tmp_path / "reports" / "r.md"
    result = runner.invoke(app, ["check", str(out), "-o", str(report_path)])
    assert result.exit_code == 0, _out(result)
    assert report_path.exists()


def test_cli_check_writes_report(tmp_path, sample_text_doc, monkeypatch):
    monkeypatch.setattr("odforge.check.find_soffice", lambda: None)
    out = render_odt(sample_text_doc, tmp_path / "d.odt")
    report_path = tmp_path / "report.md"
    result = runner.invoke(app, ["check", str(out), "-o", str(report_path)])
    assert result.exit_code == 0, _out(result)
    assert report_path.exists()
    text = report_path.read_text(encoding="utf-8")
    assert "# ODF 檢測報告" in text
    # The written file is exactly the report body printed to stdout.
    assert text.strip() in _out(result)


# ---------------------------------------------------------------------------
# Security: XXE hardening. check_odf parses the user-pointed content.xml /
# styles.xml (via validate_odf and the style-reference pass). An external SYSTEM
# entity must never be resolved — mirroring extract.py's hardening.
# ---------------------------------------------------------------------------

_XXE_CONTENT_TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<!DOCTYPE office:document-content [<!ENTITY xxe SYSTEM "{uri}">]>'
    '<office:document-content '
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'office:version="1.2">'
    "<office:body><office:presentation>"
    "<text:p>&xxe;</text:p>"
    "</office:presentation></office:body>"
    "</office:document-content>"
)


def test_external_entity_is_not_resolved(tmp_path, monkeypatch):
    monkeypatch.setattr("odforge.check.find_soffice", lambda: None)
    from odforge.package import ODP_MIMETYPE, write_odf_package

    secret = tmp_path / "secret.txt"
    marker = "TOPSECRET_check_51e7bd"
    secret.write_text(marker, encoding="utf-8")
    content = _XXE_CONTENT_TEMPLATE.format(uri=secret.resolve().as_uri())
    out = tmp_path / "xxe.odp"
    write_odf_package(out, ODP_MIMETYPE, {"content.xml": content})

    report = check_odf(out)  # must not raise or hang
    # (b) the local file's content was never disclosed in the report.
    assert marker not in report
    # (c) the entity was left unresolved: content.xml parsed cleanly (no external
    # read), so the xml gate is OK and the style-reference pass ran rather than
    # bailing with the bare parser's "無法解析" parse error.
    assert "xml: OK" in report
    assert "無法解析" not in report
