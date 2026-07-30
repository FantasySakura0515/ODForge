import zipfile

import lxml.etree as etree
import pytest

from odforge.ir import TextDoc, TableBlock
from odforge.render.odt import render_odt
from odforge.render import render

NS = {"text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
      "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
      "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
      "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0"}


def content_xml(path):
    with zipfile.ZipFile(path) as z:
        return etree.fromstring(z.read("content.xml"))


def test_mimetype(tmp_path, sample_text_doc):
    out = render_odt(sample_text_doc, tmp_path / "d.odt")
    with zipfile.ZipFile(out) as z:
        assert z.read("mimetype").decode() == "application/vnd.oasis.opendocument.text"


def test_heading_levels(tmp_path, sample_text_doc):
    root = content_xml(render_odt(sample_text_doc, tmp_path / "d.odt"))
    hs = root.findall(".//text:h", NS)
    assert len(hs) >= 2
    assert hs[0].get("{%s}outline-level" % NS["text"]) == "1"


def test_paragraph_text_present(tmp_path, sample_text_doc):
    root = content_xml(render_odt(sample_text_doc, tmp_path / "d.odt"))
    all_text = "".join(root.itertext())
    for b in sample_text_doc.blocks:
        if b.kind == "paragraph":
            assert b.text in all_text


def test_table_rendered(tmp_path):
    doc = TextDoc(title="t", blocks=[TableBlock(header=["科目", "分數"], rows=[["資結", "95"]])])
    root = content_xml(render_odt(doc, tmp_path / "d.odt"))
    assert len(root.findall(".//table:table-row", NS)) == 2  # header 列 + 1 資料列
    assert "資結" in "".join(root.itertext())


def test_lists_rendered(tmp_path, sample_text_doc):
    root = content_xml(render_odt(sample_text_doc, tmp_path / "d.odt"))
    assert len(root.findall(".//text:list", NS)) >= 2  # fixture 有 ordered+unordered 各一


def test_toc_present(tmp_path, sample_text_doc):
    root = content_xml(render_odt(sample_text_doc, tmp_path / "d.odt"))
    assert root.findall(".//text:table-of-content", NS)


def test_render_dispatch(tmp_path, sample_text_doc):
    out = render(sample_text_doc, tmp_path / "d.odt")
    assert out.exists()


def test_render_dispatch_unknown_type(tmp_path):
    class Fake:
        type = "banana"

    with pytest.raises(ValueError, match="banana"):
        render(Fake(), tmp_path / "x.odt")


def test_output_is_valid_zip_with_styles(tmp_path, sample_text_doc):
    out = render_odt(sample_text_doc, tmp_path / "d.odt")
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert "content.xml" in names and "styles.xml" in names and "META-INF/manifest.xml" in names
