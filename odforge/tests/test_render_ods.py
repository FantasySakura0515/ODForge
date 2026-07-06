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

def test_none_cell_renders_empty_and_keeps_alignment(tmp_path):
    # A null cell (formula target) must render as an empty cell that still
    # occupies its position, so following cells land in the right column.
    from odforge.validate import validate_odf
    s = Spreadsheet(title="t", sheets=[Sheet(
        name="s", columns=["a", "b", "c"],
        rows=[["x", None, 5], [None, 2, None]])])
    out = render_ods(s, tmp_path / "s.ods")
    report = validate_odf(out, with_soffice=False)
    assert report.ok, report.gates

    def cell_count(row):
        # Count table:table-cell elements including empty ones, honouring
        # table:number-columns-repeated (odfdo may compress empty runs).
        total = 0
        for c in row.findall("table:table-cell", NS):
            total += int(c.get("{%s}number-columns-repeated" % NS["table"], "1"))
        return total

    root = content_root(out)
    rows = [r for r in root.findall(".//table:table-row", NS) if cell_count(r)]
    # header + 2 data rows, each still spanning all 3 columns.
    assert len(rows) == 3
    for r in rows:
        assert cell_count(r) == 3
    # The value after the None cell survived in its own cell (column C).
    assert "5" in "".join(root.itertext())
