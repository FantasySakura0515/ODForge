import zipfile

import lxml.etree as etree

from odforge.package import (
    ODP_MIMETYPE,
    ODS_MIMETYPE,
    ODT_MIMETYPE,
    build_manifest,
    write_odf_package,
)


def test_mimetype_is_first_and_stored(tmp_path):
    out = write_odf_package(tmp_path / "t.odp", ODP_MIMETYPE,
                            {"content.xml": "<a/>", "styles.xml": "<b/>"})
    with zipfile.ZipFile(out) as z:
        infos = z.infolist()
        assert infos[0].filename == "mimetype"
        assert infos[0].compress_type == zipfile.ZIP_STORED
        assert z.read("mimetype").decode() == ODP_MIMETYPE

def test_other_parts_deflated(tmp_path):
    out = write_odf_package(tmp_path / "t.odp", ODP_MIMETYPE, {"content.xml": "<a/>" * 200})
    with zipfile.ZipFile(out) as z:
        info = z.getinfo("content.xml")
        assert info.compress_type == zipfile.ZIP_DEFLATED

def test_manifest_lists_all_parts(tmp_path):
    out = write_odf_package(tmp_path / "t.odp", ODP_MIMETYPE, {"content.xml": "<a/>", "styles.xml": "<b/>"})
    with zipfile.ZipFile(out) as z:
        m = z.read("META-INF/manifest.xml").decode()
    assert 'manifest:full-path="/"' in m
    assert 'manifest:full-path="content.xml"' in m
    assert 'manifest:full-path="styles.xml"' in m
    assert ODP_MIMETYPE in m

def test_manifest_is_wellformed_xml():
    xml = build_manifest(ODT_MIMETYPE, {"content.xml": "<a/>", "styles.xml": "<b/>", "meta.xml": "<c/>"})
    root = etree.fromstring(xml.encode())
    assert root.tag.endswith("manifest")

def test_part_content_roundtrip_utf8(tmp_path):
    xml = '<?xml version="1.0" encoding="UTF-8"?><x>中文測試</x>'
    out = write_odf_package(tmp_path / "t.odt", ODT_MIMETYPE, {"content.xml": xml})
    with zipfile.ZipFile(out) as z:
        assert z.read("content.xml").decode("utf-8") == xml

def test_mimetype_constants():
    assert ODT_MIMETYPE == "application/vnd.oasis.opendocument.text"
    assert ODP_MIMETYPE == "application/vnd.oasis.opendocument.presentation"
    assert ODS_MIMETYPE == "application/vnd.oasis.opendocument.spreadsheet"

def test_binary_part_written_and_manifest_media_type(tmp_path):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
    out = write_odf_package(tmp_path / "t.odt", ODT_MIMETYPE,
                            {"content.xml": "<a/>", "Pictures/a.svg": svg})
    with zipfile.ZipFile(out) as z:
        assert "Pictures/a.svg" in z.namelist()
        assert z.read("Pictures/a.svg") == svg
        m = z.read("META-INF/manifest.xml").decode()
    assert 'manifest:full-path="Pictures/a.svg" manifest:media-type="image/svg+xml"' in m
    # regression: existing XML part is unchanged
    assert 'manifest:full-path="content.xml" manifest:media-type="text/xml"' in m

def test_binary_media_type_by_extension():
    m = build_manifest(ODT_MIMETYPE, {
        "content.xml": "<a/>",
        "Pictures/p.png": b"\x89PNG",
        "Pictures/s.svg": b"<svg/>",
        "Pictures/j.jpg": b"\xff\xd8\xff",
    })
    assert 'manifest:full-path="content.xml" manifest:media-type="text/xml"' in m
    assert 'manifest:full-path="Pictures/p.png" manifest:media-type="image/png"' in m
    assert 'manifest:full-path="Pictures/s.svg" manifest:media-type="image/svg+xml"' in m
    assert 'manifest:full-path="Pictures/j.jpg" manifest:media-type="image/jpeg"' in m


def test_rewrite_replaces_atomically_never_leaves_a_truncated_zip(tmp_path):
    # QA repair / 重生 rewrite the same path a download may be streaming; the
    # old truncate-in-place write handed that reader a half-written archive.
    # The write must land in a temp sibling and os.replace into place, so the
    # destination is at every instant either the old zip or the new one.
    out = tmp_path / "t.odp"
    write_odf_package(out, ODP_MIMETYPE, {"content.xml": "<old/>"})
    write_odf_package(out, ODP_MIMETYPE, {"content.xml": "<new/>"})
    with zipfile.ZipFile(out) as z:
        assert z.read("content.xml") == b"<new/>"
        assert z.testzip() is None
    # No .part temp residue after either write.
    assert [p.name for p in tmp_path.iterdir()] == ["t.odp"]
