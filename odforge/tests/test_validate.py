import subprocess
import zipfile
import pytest
from pathlib import Path
from odforge import validate
from odforge.validate import (
    validate_odf,
    find_soffice,
    run_soffice_convert,
    ValidationReport,
)
from odforge.render.odt import render_odt
from odforge.render.odp import render_odp


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
        zi = zipfile.ZipInfo("mimetype"); zi.compress_type = zipfile.ZIP_STORED
        z.writestr(zi, "application/vnd.oasis.opendocument.text")
        z.writestr("META-INF/manifest.xml", "<m/>")
    r = validate_odf(bad)
    assert not r.ok and not r.gates["structure"][0]

def test_wrong_mimetype_for_extension_fails(tmp_path):
    bad = tmp_path / "bad.odp"
    with zipfile.ZipFile(bad, "w") as z:
        zi = zipfile.ZipInfo("mimetype"); zi.compress_type = zipfile.ZIP_STORED
        z.writestr(zi, "application/vnd.oasis.opendocument.text")  # odt mimetype in .odp
        z.writestr("content.xml", "<a/>")
        z.writestr("META-INF/manifest.xml", "<m/>")
    assert not validate_odf(bad).gates["structure"][0]

def test_malformed_xml_fails(tmp_path):
    bad = tmp_path / "bad.odt"
    with zipfile.ZipFile(bad, "w") as z:
        zi = zipfile.ZipInfo("mimetype"); zi.compress_type = zipfile.ZIP_STORED
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
        zi = zipfile.ZipInfo("mimetype"); zi.compress_type = zipfile.ZIP_STORED
        z.writestr(zi, "application/vnd.oasis.opendocument.text")
        z.writestr("content.xml", "<a/>")
        z.writestr("META-INF/manifest.xml", manifest)
    assert not validate_odf(bad).gates["structure"][0]

def test_empty_content_xml_fails(tmp_path):
    bad = tmp_path / "bad.odt"
    with zipfile.ZipFile(bad, "w") as z:
        zi = zipfile.ZipInfo("mimetype"); zi.compress_type = zipfile.ZIP_STORED
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

@pytest.mark.skipif(find_soffice() is None, reason="LibreOffice not installed")
def test_soffice_gate_on_valid_file(tmp_path, sample_presentation):
    out = render_odp(sample_presentation, tmp_path / "p.odp")
    r = validate_odf(out, with_soffice=True)
    assert r.gates["soffice"][0], r.gates["soffice"][1]
    assert r.ok

def test_find_soffice_returns_path_or_none():
    p = find_soffice()
    assert p is None or Path(p).exists()
