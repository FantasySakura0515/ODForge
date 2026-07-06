import pytest
from pydantic import ValidationError

import odforge
from odforge.ir import (
    Presentation,
    Sheet,
    Spreadsheet,
    TextDoc,
    parse_ir,
)


def test_import():
    assert odforge.__version__ == "0.1.0"


def test_parse_text_doc():
    ir = parse_ir({"type": "text", "title": "測試", "blocks": [
        {"kind": "heading", "level": 1, "text": "第一章"},
        {"kind": "paragraph", "text": "內文"}]})
    assert isinstance(ir, TextDoc) and ir.blocks[0].level == 1


def test_heading_level_out_of_range_rejected():
    with pytest.raises(ValidationError):
        parse_ir({"type": "text", "title": "x",
                  "blocks": [{"kind": "heading", "level": 9, "text": "x"}]})


def test_parse_presentation_default_theme():
    ir = parse_ir({"type": "presentation", "title": "簡報", "slides": [
        {"layout": "title", "title": "封面", "subtitle": "副標"}]})
    assert ir.theme == "academic"


def test_formula_must_use_of_namespace():
    with pytest.raises(ValidationError):
        Sheet(name="s", columns=["a"], rows=[[1]],
              formulas=[{"cell": "B1", "formula": "=SUM(A1)"}])  # 缺 "of:" 前綴


def test_schema_exportable():
    assert "properties" in Presentation.model_json_schema()


def test_unknown_type_rejected():
    with pytest.raises(ValidationError):
        parse_ir({"type": "banana", "title": "x"})


def test_sample_text_doc_fixture(sample_text_doc):
    assert isinstance(sample_text_doc, TextDoc)
    kinds = [b.kind for b in sample_text_doc.blocks]
    assert "toc" in kinds and "pagebreak" in kinds


def test_sample_presentation_fixture(sample_presentation):
    assert isinstance(sample_presentation, Presentation)
    assert len(sample_presentation.slides) == 5
    assert {s.layout for s in sample_presentation.slides} == {
        "title", "title-content", "two-col", "section", "big-fact"}


def test_sample_spreadsheet_fixture(sample_spreadsheet):
    assert isinstance(sample_spreadsheet, Spreadsheet)
    assert sample_spreadsheet.sheets[0].columns == ["項目", "數量", "單價"]
    assert sample_spreadsheet.sheets[0].formulas[0].formula.startswith("of:=")
