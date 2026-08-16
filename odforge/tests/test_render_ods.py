import zipfile

import lxml.etree as etree
import pytest
from pydantic import ValidationError

from odforge.ir import Sheet, Spreadsheet
from odforge.render import render
from odforge.render.ods import render_ods

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
    # 現在攔在契約層(IR),而不是等到算圖時才炸 —— 錯誤訊息裡照樣有闖禍的 ref。
    with pytest.raises(ValidationError, match="!!"):
        Sheet(name="s", columns=["a"], rows=[[1]],
              formulas=[{"cell": "!!", "formula": "of:=SUM([.A1:.A2])"}])
    # 算圖層的防線保留(有人繞過契約直接構造物件時仍要擋)。
    s = Spreadsheet(title="t", sheets=[
        Sheet(name="s", columns=["a"], rows=[[1]],
              formulas=[{"cell": "A2", "formula": "of:=SUM([.A1:.A2])"}])
    ])
    s.sheets[0].formulas[0].__dict__["cell"] = "!!"
    with pytest.raises(ValueError, match="!!"):
        render_ods(s, tmp_path / "s.ods")


# ---------------------------------------------------------------------------
# P1-04 — numeric and formula semantics, not just "the zip opens".
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_are_rejected(bad):
    """ODF 沒有 NaN/Infinity 的表示法;寫出去 LibreOffice 顯示 #VALUE!。"""
    with pytest.raises(ValidationError, match="finite"):
        Sheet(name="s", columns=["a", "b"], rows=[[1, bad]])


def test_duplicate_formula_target_is_rejected():
    with pytest.raises(ValidationError, match="duplicate formula target"):
        Sheet(
            name="s", columns=["a"], rows=[[1], [2]],
            formulas=[
                {"cell": "A4", "formula": "of:=SUM([.A2:.A3])"},
                {"cell": "A4", "formula": "of:=MAX([.A2:.A3])"},
            ],
        )


def test_out_of_bounds_formula_target_is_rejected():
    with pytest.raises(ValidationError, match="outside sheet"):
        Sheet(name="s", columns=["a"], rows=[[1]],
              formulas=[{"cell": "Z99", "formula": "of:=SUM([.A1:.A2])"}])


def test_out_of_bounds_reference_is_rejected():
    with pytest.raises(ValidationError, match="outside sheet"):
        Sheet(name="s", columns=["a"], rows=[[1]],
              formulas=[{"cell": "A3", "formula": "of:=SUM([.A1:.A99])"}])


def test_unbalanced_parentheses_are_rejected():
    with pytest.raises(ValidationError, match="unbalanced parentheses"):
        Sheet(name="s", columns=["a"], rows=[[1]],
              formulas=[{"cell": "A3", "formula": "of:=SUM([.A1:.A2]"}])


def test_unsupported_function_is_refused_rather_than_shipped_broken():
    """不支援就閉嘴:與其寫出一個在使用者機器上顯示 #NAME? 的公式,不如當場拒絕。"""
    with pytest.raises(ValidationError, match="unsupported spreadsheet function"):
        Sheet(name="s", columns=["a"], rows=[[1]],
              formulas=[{"cell": "A3", "formula": "of:=XLOOKUP([.A1];[.A1];[.A1])"}])


def test_supported_functions_still_pass():
    sheet = Sheet(
        name="s", columns=["月份", "金額"], rows=[["一月", 100], ["二月", 200]],
        formulas=[
            {"cell": "B4", "formula": "of:=SUM([.B2:.B3])"},
            {"cell": "A4", "formula": "of:=IF(SUM([.B2:.B3])>0;\"有\";\"無\")"},
        ],
    )
    assert len(sheet.formulas) == 2

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
