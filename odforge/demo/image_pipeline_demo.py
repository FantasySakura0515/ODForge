"""Build a small native ODP that exercises both image layouts."""

from __future__ import annotations

import argparse
from pathlib import Path

from odforge.ir import Presentation, Slide
from odforge.preview import render_pages
from odforge.render import render
from odforge.validate import validate_odf


def build_deck() -> Presentation:
    return Presentation(
        title="從候診到協作：流程改善實證",
        theme="navy",
        slides=[
            Slide(
                layout="title",
                title="從候診到協作",
                subtitle="把流程改善變成團隊每天看得見的成果",
                notes="用真實現場作為主線，先談人，再談流程。",
            ),
            Slide(
                layout="image-focus",
                title="改善不是一張流程圖，而是一群人共同看見問題",
                kicker="FIELD NOTE / 01",
                image={
                    "src": "asset://workshop",
                    "alt": "醫療團隊共同檢視病人流程看板",
                    "caption": "跨職類團隊以同一張病人旅程圖校準優先順序",
                    "credit": "ODForge generated validation asset",
                },
                sources=[{"label": "內部改善工作坊"}],
                notes="請觀眾先看每個人的視線：焦點一致，才有共同決策。",
            ),
            Slide(
                layout="image-split",
                title="同一個現場，轉成三個可執行決策",
                kicker="DECISIONS / 02",
                image={
                    "src": "asset://workshop",
                    "alt": "臨床團隊圍繞流程看板討論",
                    "caption": "以現場證據支撐行動，而不是用裝飾圖片填空",
                },
                bullets=[
                    {
                        "text": "先處理交接斷點",
                        "children": ["明確標示下一位負責人"],
                    },
                    {
                        "text": "把等待時間拆開",
                        "children": ["區分檢查、行政與資訊等待"],
                    },
                    {
                        "text": "每週只追一個瓶頸",
                        "children": ["小步修正，比大型專案更快"],
                    },
                ],
                notes="用右側三點連回照片中的協作情境。",
            ),
            Slide(
                layout="closing",
                title="讓每一次等待，都有下一個行動",
                subtitle="ODForge image pipeline · native ODP",
                notes="收在行動承諾。",
            ),
        ],
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--preview-dir", type=Path)
    args = parser.parse_args()

    render(build_deck(), args.out, assets={"workshop": args.image})
    report = validate_odf(args.out, with_soffice=True)
    if not report.ok:
        raise SystemExit(f"validation failed: {report.gates}")
    if args.preview_dir is not None:
        render_pages(args.out, args.preview_dir)


if __name__ == "__main__":
    main()
