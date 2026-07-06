import pytest

from odforge.ir import (
    FormulaSpec,
    HeadingBlock,
    ListBlock,
    PageBreakBlock,
    ParagraphBlock,
    Presentation,
    Sheet,
    Slide,
    Spreadsheet,
    TableBlock,
    TextDoc,
    TocBlock,
)


@pytest.fixture
def sample_text_doc() -> TextDoc:
    return TextDoc(
        title="ODForge 示範文件",
        blocks=[
            TocBlock(),
            HeadingBlock(level=1, text="第一章 緒論"),
            ParagraphBlock(text="這是一段引言。", style="quote"),
            HeadingBlock(level=2, text="1.1 研究背景"),
            ListBlock(ordered=False, items=["重點一", "重點二"]),
            ListBlock(ordered=True, items=["步驟一", "步驟二"]),
            TableBlock(header=["項目", "說明"], rows=[["A", "甲"], ["B", "乙"]]),
            PageBreakBlock(),
        ],
    )


@pytest.fixture
def sample_presentation() -> Presentation:
    return Presentation(
        title="ODForge 示範簡報",
        slides=[
            Slide(layout="title", title="ODForge", subtitle="自然語言轉 ODF"),
            Slide(
                layout="title-content",
                title="大綱",
                bullets=["背景", "方法", "成果"],
                notes="開場說明本次簡報結構。",
            ),
            Slide(
                layout="two-col",
                title="比較",
                left=["傳統做法", "耗時"],
                right=["ODForge", "自動化"],
            ),
            Slide(layout="section", title="研究方法"),
            Slide(
                layout="big-fact",
                fact="99%",
                notes="強調自動化涵蓋率。",
            ),
        ],
    )


@pytest.fixture
def sample_spreadsheet() -> Spreadsheet:
    return Spreadsheet(
        title="ODForge 示範試算表",
        sheets=[
            Sheet(
                name="銷售",
                columns=["項目", "數量", "單價"],
                rows=[
                    ["筆記本", 3, 25.5],
                    ["原子筆", 10, 12.0],
                    ["資料夾", 5, 8.75],
                ],
                formulas=[FormulaSpec(cell="B5", formula="of:=SUM([.B2:.B4])")],
            ),
        ],
    )
