import zipfile
import lxml.etree as etree
import pytest
from odforge.ir import Spreadsheet, Sheet
from odforge.render.ods import render_ods
from odforge.render import render

NS = {"office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
      "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
      "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0"}

def content_root(path):
    with zipfile.ZipFile(path) as z:
        return etree.fromstring(z.read("content.xml"))

def test_ods_mimetype(tmp_path, sample_spreadsheet):
    out = render_ods(sample_spreadsheet, tmp_path / "s.ods")
    with zipfile.ZipFile(out) as z:
        assert z.read("mimetype").decode() == "application/vnd.oasis.opendocument.spreadsheet"

def test_sheet_count_and_names(tmp_path, sample_spreadsheet):
    root = content_root(render_ods(sample_spreadsheet, tmp_path / "s.ods"))
    tables = root.findall(".//table:table", NS)
    assert len(tables) == len(sample_spreadsheet.sheets)
    assert tables[0].get("{%s}name" % NS["table"]) == sample_spreadsheet.sheets[0].name

def test_numbers_are_numeric_cells(tmp_path, sample_spreadsheet):
    root = content_root(render_ods(sample_spreadsheet, tmp_path / "s.ods"))
    cells = root.findall(".//table:table-cell[@office:value-type='float']", NS)
    assert cells, "no numeric cells found"

def test_header_and_data_text_present(tmp_path, sample_spreadsheet):
    root = content_root(render_ods(sample_spreadsheet, tmp_path / "s.ods"))
    all_text = "".join(root.itertext())
    for col in sample_spreadsheet.sheets[0].columns:
        assert col in all_text

def test_formula_attribute_set(tmp_path, sample_spreadsheet):
    root = content_root(render_ods(sample_spreadsheet, tmp_path / "s.ods"))
    formulas = [c.get("{%s}formula" % NS["table"]) for c in root.findall(".//table:table-cell", NS)]
    formulas = [f for f in formulas if f]
    expected = sample_spreadsheet.sheets[0].formulas[0].formula
    assert any(expected in f for f in formulas)

def test_bad_cell_ref_raises(tmp_path):
    s = Spreadsheet(title="t", sheets=[Sheet(name="s", columns=["a"], rows=[[1]],
        formulas=[{"cell": "!!", "formula": "of:=SUM([.A1:.A2])"}])])
    with pytest.raises(ValueError, match="!!"):
        render_ods(s, tmp_path / "s.ods")

def test_dispatch_spreadsheet(tmp_path, sample_spreadsheet):
    out = render(sample_spreadsheet, tmp_path / "s.ods")
    assert out.exists()
