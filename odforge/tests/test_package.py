import zipfile
import lxml.etree as etree
from odforge.package import write_odf_package, build_manifest, ODP_MIMETYPE, ODT_MIMETYPE, ODS_MIMETYPE

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
    xml = build_manifest(ODT_MIMETYPE, ["content.xml", "styles.xml", "meta.xml"])
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
