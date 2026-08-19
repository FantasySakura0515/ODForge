import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from odforge import validate
from odforge.render.odp import render_odp
from odforge.render.odt import render_odt
from odforge.validate import (
    find_soffice,
    run_soffice_convert,
    validate_odf,
)


def test_soffice_gate_isolates_user_installation(tmp_path, monkeypatch):
    # The soffice conversion MUST pass -env:UserInstallation=... so a running
    # desktop LibreOffice instance does not block the headless conversion.
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        outdir = Path(cmd[cmd.index("--outdir") + 1])
        (outdir / (Path(cmd[-1]).stem + ".pdf")).write_bytes(b"%PDF-1.4")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(validate.subprocess, "run", fake_run)
    passed, msg = validate._gate_soffice(tmp_path / "d.odt", Path("soffice"))
    assert passed, msg
    env_args = [a for a in captured["cmd"] if str(a).startswith("-env:UserInstallation=")]
    assert env_args, captured["cmd"]
    # single-dash env switch, file URI value
    assert env_args[0].startswith("-env:UserInstallation=file:")


def test_run_soffice_convert_builds_env_arg(tmp_path, monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "out", "")

    monkeypatch.setattr(validate.subprocess, "run", fake_run)
    proc = run_soffice_convert(Path("soffice"), tmp_path / "s.docx", "odt", tmp_path)
    assert proc.returncode == 0
    assert any(str(a).startswith("-env:UserInstallation=") for a in captured["cmd"])
    assert "odt" in captured["cmd"]


def test_run_soffice_convert_detaches_stdin(tmp_path, monkeypatch):
    # Regression: without an explicit stdin, soffice inherits the host's. Under
    # an MCP/stdio server that handle is an overlapped named pipe and soffice
    # blocks on it forever after profile init — every gate and preview then
    # dies at the timeout instead of converting (observed on Windows).
    captured = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, "out", "")

    monkeypatch.setattr(validate.subprocess, "run", fake_run)
    run_soffice_convert(Path("soffice"), tmp_path / "s.odp", "pdf", tmp_path)
    assert captured.get("stdin") == subprocess.DEVNULL

def test_valid_odt_passes(tmp_path, sample_text_doc):
    out = render_odt(sample_text_doc, tmp_path / "d.odt")
    r = validate_odf(out)
    assert r.ok and r.gates["structure"][0] and r.gates["xml"][0]
    assert "soffice" not in r.gates

def test_valid_odp_passes(tmp_path, sample_presentation):
    out = render_odp(sample_presentation, tmp_path / "p.odp")
    assert validate_odf(out).ok

def test_mimetype_not_first_fails(tmp_path):
    bad = tmp_path / "bad.odt"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("content.xml", "<a/>")
        zi = zipfile.ZipInfo("mimetype")
        zi.compress_type = zipfile.ZIP_STORED
        z.writestr(zi, "application/vnd.oasis.opendocument.text")
        z.writestr("META-INF/manifest.xml", "<m/>")
    r = validate_odf(bad)
    assert not r.ok and not r.gates["structure"][0]

def test_wrong_mimetype_for_extension_fails(tmp_path):
    bad = tmp_path / "bad.odp"
    with zipfile.ZipFile(bad, "w") as z:
        zi = zipfile.ZipInfo("mimetype")
        zi.compress_type = zipfile.ZIP_STORED
        z.writestr(zi, "application/vnd.oasis.opendocument.text")  # odt mimetype in .odp
        z.writestr("content.xml", "<a/>")
        z.writestr("META-INF/manifest.xml", "<m/>")
    assert not validate_odf(bad).gates["structure"][0]

def test_malformed_xml_fails(tmp_path):
    bad = tmp_path / "bad.odt"
    with zipfile.ZipFile(bad, "w") as z:
        zi = zipfile.ZipInfo("mimetype")
        zi.compress_type = zipfile.ZIP_STORED
        z.writestr(zi, "application/vnd.oasis.opendocument.text")
        z.writestr("content.xml", "<open><unclosed>")
        z.writestr("META-INF/manifest.xml",
                   '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"/>')
    r = validate_odf(bad)
    assert not r.ok and not r.gates["xml"][0]

def test_manifest_missing_part_fails(tmp_path):
    bad = tmp_path / "bad.odt"
    manifest = ('<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">'
                '<manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/>'
                '<manifest:file-entry manifest:full-path="ghost.xml" manifest:media-type="text/xml"/>'
                '</manifest:manifest>')
    with zipfile.ZipFile(bad, "w") as z:
        zi = zipfile.ZipInfo("mimetype")
        zi.compress_type = zipfile.ZIP_STORED
        z.writestr(zi, "application/vnd.oasis.opendocument.text")
        z.writestr("content.xml", "<a/>")
        z.writestr("META-INF/manifest.xml", manifest)
    assert not validate_odf(bad).gates["structure"][0]

def test_empty_content_xml_fails(tmp_path):
    bad = tmp_path / "bad.odt"
    with zipfile.ZipFile(bad, "w") as z:
        zi = zipfile.ZipInfo("mimetype")
        zi.compress_type = zipfile.ZIP_STORED
        z.writestr(zi, "application/vnd.oasis.opendocument.text")
        z.writestr("content.xml", "")
        z.writestr("META-INF/manifest.xml",
                   '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"/>')
    r = validate_odf(bad)
    assert not r.ok and not r.gates["xml"][0]

def test_not_a_zip_fails(tmp_path):
    bad = tmp_path / "bad.odt"
    bad.write_text("not a zip", encoding="utf-8")
    r = validate_odf(bad)
    assert not r.ok and not r.gates["structure"][0]


def test_oversized_xml_member_fails_without_unbounded_read(tmp_path, monkeypatch):
    from odforge.package import ODT_MIMETYPE, write_odf_package

    out = tmp_path / "oversized.odt"
    write_odf_package(
        out,
        ODT_MIMETYPE,
        {"content.xml": "<root>" + ("x" * 128) + "</root>"},
    )
    monkeypatch.setattr(validate, "MAX_XML_MEMBER", 64)

    report = validate_odf(out)

    assert not report.gates["xml"][0]
    assert "safety limits" in report.gates["xml"][1]


def test_soffice_is_skipped_when_archive_preflight_fails(tmp_path, monkeypatch):
    bad = tmp_path / "bad.odt"
    bad.write_text("not a zip", encoding="utf-8")
    monkeypatch.setattr(
        validate,
        "run_soffice_convert",
        lambda *args, **kwargs: pytest.fail("soffice must not receive rejected input"),
    )

    report = validate_odf(bad, with_soffice=True)

    assert not report.gates["soffice"][0]
    assert "skipped" in report.gates["soffice"][1]

@pytest.mark.skipif(find_soffice() is None, reason="LibreOffice not installed")
def test_soffice_gate_on_valid_file(tmp_path, sample_presentation):
    out = render_odp(sample_presentation, tmp_path / "p.odp")
    r = validate_odf(out, with_soffice=True)
    assert r.gates["soffice"][0], r.gates["soffice"][1]
    assert r.ok

def test_find_soffice_returns_path_or_none():
    p = find_soffice()
    assert p is None or Path(p).exists()


# ---------------------------------------------------------------------------
# Security: XXE hardening. validate_odf parses attacker-controllable ODF XML
# (the manifest in the structure gate, every .xml member in the xml gate). Its
# parser must never resolve external entities (local-file disclosure) nor expand
# nested entities (billion-laughs DoS) — mirroring extract.py's hardening.
# ---------------------------------------------------------------------------

# ODF content.xml declaring an external SYSTEM entity (the canonical XXE vector)
# and referencing it in element text. A hardened parser leaves &xxe; unresolved
# and never reads the target file; the bare default parser instead raised
# "Entity not defined", failing the xml gate.
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


def test_external_entity_in_xml_gate_is_not_resolved(tmp_path):
    from odforge.package import ODP_MIMETYPE, write_odf_package

    secret = tmp_path / "secret.txt"
    marker = "TOPSECRET_validate_9c1f2a"
    secret.write_text(marker, encoding="utf-8")
    content = _XXE_CONTENT_TEMPLATE.format(uri=secret.resolve().as_uri())
    out = tmp_path / "xxe.odp"
    write_odf_package(out, ODP_MIMETYPE, {"content.xml": content})

    report = validate_odf(out)  # must not raise or hang
    # (b) the local file's content was never disclosed anywhere in the report.
    assert marker not in repr(report)
    # (c) the entity was left unresolved: the doc is well-formed (no external
    # read, no expansion), so the hardened xml gate passes rather than raising
    # "Entity not defined" the way the bare parser did.
    assert report.gates["xml"][0] is True, report.gates["xml"]


def test_safe_parser_leaves_external_entity_unresolved(tmp_path):
    # White-box guard on the shared helper: an external SYSTEM entity must be
    # left unresolved (not dereferenced), mirroring extract.py's parser test.
    import lxml.etree as etree

    from odforge.xmlsafe import safe_fromstring

    secret = tmp_path / "secret.txt"
    marker = "TOPSECRET_helper_7a3d10"
    secret.write_text(marker, encoding="utf-8")
    probe = (
        '<?xml version="1.0"?>'
        f'<!DOCTYPE r [<!ENTITY e SYSTEM "{secret.resolve().as_uri()}">]>'
        "<r><c>&e;</c></r>"
    ).encode()

    root = safe_fromstring(probe)  # must not raise or read the file
    assert root[0].text is None  # entity left unresolved
    assert marker not in etree.tostring(root).decode("utf-8")


# ---------------------------------------------------------------------------
# P1-04 — a spreadsheet that opens is not a spreadsheet that computes.
# ---------------------------------------------------------------------------


def test_scan_calc_errors_finds_every_error_token():
    from odforge.validate import _scan_calc_errors

    assert _scan_calc_errors("月份,金額\n一月,100\n總計,#NAME?\n") == ["#NAME?"]
    found = _scan_calc_errors("a,#VALUE!\nb,Err:502\n")
    assert "#VALUE!" in found and "Err:" in found
    assert _scan_calc_errors("月份,金額\n一月,100\n總計,100\n") == []


def _one_sheet_ods(tmp_path) -> Path:
    """A real single-sheet .ods.

    These tests fake the *conversion* but not the package: the gate now counts
    the workbook's own sheets to know how many exports it must see, so a
    ``b"not really an ods"`` placeholder can no longer stand in for one — and
    should not, since "this file is not a spreadsheet" and "this spreadsheet
    computes cleanly" are answers that must never look alike.
    """
    from odforge.ir import Sheet, Spreadsheet
    from odforge.render import render

    doc = Spreadsheet(title="t", sheets=[Sheet(
        name="s", columns=["月份", "金額"], rows=[["一月", 100]],
        formulas=[{"cell": "B3", "formula": "of:=SUM([.B2:.B2])"}],
    )])
    return render(doc, tmp_path / "s.ods")


def test_formula_gate_fails_on_computed_errors(tmp_path, monkeypatch):
    """轉出 PDF 成功 ≠ 公式算得出來:一整片 #NAME? 也能印出完美的 PDF。"""
    from odforge import validate as V

    ods = _one_sheet_ods(tmp_path)

    def fake_convert(soffice, src, fmt, outdir, timeout=120):
        # pdf leg keeps the plain name the gate looks for; the calc leg writes
        # the per-sheet name the all-sheets export produces.
        name = f"{Path(src).stem}.pdf" if fmt == "pdf" else f"{Path(src).stem}-s.csv"
        out = Path(outdir) / name
        out.write_text("月份,金額\n一月,100\n總計,#NAME?\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(V, "run_soffice_convert", fake_convert)
    ok, message = V._gate_soffice(ods, Path("soffice"))
    assert ok is False
    assert "#NAME?" in message


def test_formula_gate_passes_when_values_compute(tmp_path, monkeypatch):
    from odforge import validate as V

    ods = _one_sheet_ods(tmp_path)

    def fake_convert(soffice, src, fmt, outdir, timeout=120):
        # pdf leg keeps the plain name the gate looks for; the calc leg writes
        # the per-sheet name the all-sheets export produces.
        name = f"{Path(src).stem}.pdf" if fmt == "pdf" else f"{Path(src).stem}-s.csv"
        out = Path(outdir) / name
        out.write_text("月份,金額\n一月,100\n總計,100\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(V, "run_soffice_convert", fake_convert)
    ok, message = V._gate_soffice(ods, Path("soffice"))
    assert ok is True
    assert "formulas evaluated" in message


def test_formula_gate_refuses_a_package_it_cannot_read(tmp_path, monkeypatch):
    """A file that is not a readable spreadsheet is 'cannot check', not 'ok'."""
    from odforge import validate as V

    ods = tmp_path / "broken.ods"
    ods.write_bytes(b"not really an ods")
    monkeypatch.setattr(
        V, "run_soffice_convert",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    ok, message = V._gate_formulas(ods, Path("soffice"), tmp_path / "w")
    assert ok is False
    assert "sheet list" in message


def test_odp_is_not_put_through_the_formula_gate(tmp_path, monkeypatch):
    """簡報沒有公式可算;別為它多跑一次 soffice。"""
    from odforge import validate as V

    odp = tmp_path / "d.odp"
    odp.write_bytes(b"x")
    formats: list[str] = []

    def fake_convert(soffice, src, fmt, outdir, timeout=120):
        formats.append(fmt)
        out = Path(outdir) / (Path(src).stem + "." + fmt)
        out.write_bytes(b"%PDF-1.4")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(V, "run_soffice_convert", fake_convert)
    ok, _ = V._gate_soffice(odp, Path("soffice"))
    assert ok is True
    assert formats == ["pdf"]


@pytest.mark.skipif(find_soffice() is None, reason="LibreOffice not installed")
def test_real_libreoffice_computes_the_same_numbers(tmp_path):
    """真 LibreOffice 端到端:公式算出來的值必須與 Python 算的一致。"""
    from odforge.ir import Sheet, Spreadsheet
    from odforge.render.ods import render_ods

    sheet = Sheet(
        name="營收",
        columns=["月份", "金額"],
        rows=[["一月", 120], ["二月", 300], ["三月", 80], ["總計", None]],
        formulas=[{"cell": "B5", "formula": "of:=SUM([.B2:.B4])"}],
    )
    out = render_ods(Spreadsheet(title="營收表", sheets=[sheet]), tmp_path / "s.ods")

    report = validate_odf(out, with_soffice=True)
    assert report.gates["soffice"][0], report.gates["soffice"][1]

    outdir = tmp_path / "csv"
    outdir.mkdir()
    run_soffice_convert(find_soffice(), out, "csv", outdir)
    text = (outdir / "s.csv").read_text(encoding="utf-8", errors="replace")
    assert "500" in text  # 120 + 300 + 80,由 LibreOffice 自己算出來
    assert "#" not in text


# ---------------------------------------------------------------------------
# R1-04 — the formula gate must read EVERY sheet
#
# ``--convert-to csv`` exports sheet 1 and stops. A workbook whose first sheet
# totals cleanly and whose second sheet is solid ``#DIV/0!`` used to come back
# "formulas evaluated without errors": the gate graded the cover page.
# ---------------------------------------------------------------------------

def _two_sheet_workbook(second_formula: str):
    from odforge.ir import Spreadsheet

    def sheet(name, formula):
        return {
            "name": name,
            "columns": ["項目", "金額"],
            "rows": [["甲", 120], ["乙", 300]],
            "formulas": [{"cell": "B4", "formula": formula}],
        }

    return Spreadsheet(title="兩張工作表", sheets=[
        sheet("Good", "of:=SUM([.B2:.B3])"),
        sheet("Bad", second_formula),
    ])


def test_sheet_count_reads_the_package_itself(tmp_path):
    from odforge.render import render

    out = render(_two_sheet_workbook("of:=SUM([.B2:.B3])"), tmp_path / "two.ods")
    assert validate._sheet_count(out) == 2
    # An unreadable package is "cannot verify" (0), never a confident answer.
    broken = tmp_path / "broken.ods"
    broken.write_bytes(b"not a zip")
    assert validate._sheet_count(broken) == 0


@pytest.mark.skipif(find_soffice() is None, reason="LibreOffice not installed")
def test_formula_error_on_the_second_sheet_fails_the_gate(tmp_path):
    from odforge.render import render

    out = render(_two_sheet_workbook("of:=1/0"), tmp_path / "bad-second.ods")
    report = validate_odf(out, with_soffice=True)

    passed, message = report.gates["soffice"]
    assert not passed, message
    assert "#DIV/0!" in message
    # The message must name WHICH sheet, or the user has to hunt for it.
    assert "Bad" in message
    assert not report.ok


@pytest.mark.skipif(find_soffice() is None, reason="LibreOffice not installed")
def test_clean_multi_sheet_workbook_reports_how_many_it_checked(tmp_path):
    from odforge.render import render

    out = render(_two_sheet_workbook("of:=SUM([.B2:.B3])"), tmp_path / "ok.ods")
    report = validate_odf(out, with_soffice=True)

    passed, message = report.gates["soffice"]
    assert passed, message
    # "evaluated without errors" is only worth reading if it says over how much.
    assert "2 sheet(s)" in message


@pytest.mark.skipif(find_soffice() is None, reason="LibreOffice not installed")
def test_gate_fails_when_fewer_sheets_are_exported_than_the_file_declares(
    tmp_path, monkeypatch
):
    # Simulates a LibreOffice too old for the all-sheets token AND an HTML
    # export that also fails: the honest answer is "could not check", never a
    # pass earned by the sheets that happened to come back.
    from odforge.render import render

    out = render(_two_sheet_workbook("of:=SUM([.B2:.B3])"), tmp_path / "partial.ods")
    real = validate.run_soffice_convert

    def only_first_sheet(soffice, src, fmt, outdir, timeout=120):
        if fmt == "html":
            return SimpleNamespace(returncode=1, stdout="", stderr="no html filter")
        return real(soffice, src, "csv", outdir, timeout)

    monkeypatch.setattr(validate, "run_soffice_convert", only_first_sheet)
    passed, message = validate._gate_formulas(
        out, find_soffice(), tmp_path / "work"
    )
    assert not passed
    assert "1 of 2 sheets" in message or "1 of 2" in message
