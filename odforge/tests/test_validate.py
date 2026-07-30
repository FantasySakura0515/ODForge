import subprocess
import zipfile
import pytest
from pathlib import Path
from odforge import validate
from odforge.validate import (
    validate_odf,
    find_soffice,
    run_soffice_convert,
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
    ).encode("utf-8")

    root = safe_fromstring(probe)  # must not raise or read the file
    assert root[0].text is None  # entity left unresolved
    assert marker not in etree.tostring(root).decode("utf-8")
