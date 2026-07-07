"""ODForge command-line interface.

``odforge new`` turns a natural-language prompt into a native ODF file:
generate IR -> render -> validate -> present the outcome. This module holds
no business logic: it only parses arguments, wires the existing building
blocks together and presents the result. Task 8.2 adds a ``check`` subcommand.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from odforge.check import check_odf, diff_docx_odt
from odforge.ir import Outline
from odforge.llm import generate_ir, generate_outline, generate_slides
from odforge.render import render
from odforge.textmetrics import check_budget
from odforge.textutil import concise
from odforge.themes import resolve_design
from odforge.validate import find_soffice, validate_odf

# Output extension -> IR doc_type. Also the source of the "supported types"
# message shown when an unknown extension is given.
_EXT_DOC_TYPE = {
    ".odt": "text",
    ".odp": "presentation",
    ".ods": "spreadsheet",
}


class Theme(str, Enum):
    academic = "academic"
    minimal = "minimal"
    dark = "dark"


class Backend(str, Enum):
    deepseek = "deepseek"
    ollama = "ollama"


class Mode(str, Enum):
    detailed = "detailed"
    presenter = "presenter"


app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="將自然語言鍛造成原生 ODF 文件（.odt / .odp / .ods）。",
)

_out = Console()
_err = Console(stderr=True)

def _concise(exc: Exception) -> str:
    """One-line, length-bounded, rich-markup-safe rendering of an exception."""
    return escape(concise(str(exc)))


def _print_outline(outline: Outline) -> None:
    """Render a stage-1 outline for the interactive checkpoint.

    Prints a page-role table (頁碼 / 版型 / 標題 / gist) and a design summary:
    the palette shown as five hex colour swatches, the font pairing, density
    scale and narrative mode. LLM-provided strings (titles, gists, fonts) are
    ``escape``\\ d before interpolation into rich markup; palette values are
    already validated ``#RRGGBB`` hex, safe as both a style and displayed text.
    """
    table = Table(title="大綱")
    table.add_column("頁碼", justify="right")
    table.add_column("版型")
    table.add_column("標題", overflow="fold")
    table.add_column("gist", overflow="fold")
    for i, page in enumerate(outline.pages, start=1):
        table.add_row(str(i), escape(page.role), escape(page.title), escape(page.gist))
    _out.print(table)

    design = outline.design
    if design is not None:
        pal = design.palette
        swatches = "  ".join(
            f"[on {c}]    [/]"
            for c in (pal.bg, pal.surface, pal.text, pal.muted, pal.accent)
        )
        _out.print(
            f"色盤 {swatches}  bg={pal.bg} surface={pal.surface} "
            f"text={pal.text} muted={pal.muted} accent={pal.accent}"
        )
        _out.print(
            f"字體 {escape(design.fonts.display)} / {escape(design.fonts.body)}"
            f"　密度 {escape(design.scale)}"
        )
    else:
        _out.print("美術方向：未指定（將套用預設主題）")
    _out.print(f"講述型態（mode）：{escape(outline.mode)}")


@app.callback()
def _root() -> None:
    """ODForge：自然語言 → 原生 ODF。"""
    # A no-op callback keeps ``new`` (and Task 8.2's ``check``) as named
    # subcommands rather than collapsing into a single implicit command.


@app.command()
def new(
    prompt: str = typer.Argument(..., help="自然語言的文件需求描述。"),
    out: Path = typer.Option(
        ...,
        "-o",
        "--out",
        help="輸出檔案路徑；副檔名決定文件類型（.odt / .odp / .ods）。",
    ),
    theme: Optional[Theme] = typer.Option(
        None, "--theme", help="簡報主題，僅對 .odp 有效；省略則尊重 LLM 的選擇。"
    ),
    mode: Optional[Mode] = typer.Option(
        None,
        "--mode",
        help="講述型態，僅對 .odp 兩段式流程有效：presenter（講者型）/ detailed（自讀型）；省略則沿用大綱的判斷。",
    ),
    backend: Optional[Backend] = typer.Option(
        None, "--backend", help="LLM 後端；省略則使用預設。"
    ),
    interactive: bool = typer.Option(
        False,
        "--interactive",
        help="簡報兩段式流程：先印出大綱供確認，確認後才生成投影片。",
    ),
    one_shot: bool = typer.Option(
        False,
        "--one-shot",
        help="簡報改用 v1 單次呼叫（一段式）產生，供弱模型 / Ollama 的逃生路徑使用。",
    ),
    soffice: bool = typer.Option(
        True,
        "--soffice/--no-soffice",
        help="驗證時若找得到 LibreOffice 就跑 soffice 轉檔驗證。",
    ),
) -> None:
    """由 PROMPT 產生一份 ODF 文件並輸出到 -o 指定的檔案。

    簡報（.odp）預設走兩段式流程：先產生大綱（美術方向 + 頁面骨架），再逐頁填內容。
    ``--interactive`` 會在兩段之間停下來印出大綱供確認；``--one-shot`` 則退回單次
    呼叫的一段式路徑。文字（.odt）與試算表（.ods）一律走單次呼叫。
    """
    ext = out.suffix.lower()
    doc_type = _EXT_DOC_TYPE.get(ext)
    if doc_type is None:
        supported = ", ".join(sorted(_EXT_DOC_TYPE))
        _err.print(
            f"[red]FAIL[/red] 不支援的副檔名 {escape(repr(ext or '(無)'))}；"
            f"支援的類型：{escape(supported)}"
        )
        raise typer.Exit(code=2)

    backend_name = backend.value if backend else None
    # Two-stage (outline -> slides) is the presentation default; .odt/.ods and
    # the --one-shot escape hatch stay on the v1 single-call generate_ir path.
    if doc_type == "presentation" and not one_shot:
        try:
            outline = generate_outline(prompt, backend=backend_name)
        except Exception as exc:  # noqa: BLE001 - concise message, no traceback
            _err.print(f"[red]FAIL[/red] 大綱產生失敗：{_concise(exc)}")
            raise typer.Exit(code=1)

        # --mode overrides the outline's narrative register before stage 2.
        if mode is not None:
            outline = outline.model_copy(update={"mode": mode.value})

        # Interactive checkpoint: show the outline, then gate stage 2 on confirm.
        if interactive:
            _print_outline(outline)
            if not typer.confirm("依此大綱生成?"):
                _out.print("已取消")
                raise typer.Exit(code=0)

        try:
            ir = generate_slides(outline, backend=backend_name)
        except Exception as exc:  # noqa: BLE001 - concise message, no traceback
            _err.print(f"[red]FAIL[/red] 內容產生失敗：{_concise(exc)}")
            raise typer.Exit(code=1)
    else:
        try:
            ir = generate_ir(prompt, doc_type, backend=backend_name)
        except Exception as exc:  # noqa: BLE001 - concise message, no traceback
            _err.print(f"[red]FAIL[/red] 內容產生失敗：{_concise(exc)}")
            raise typer.Exit(code=1)

    # --theme only matters for presentations. When given, it always overrides
    # the LLM's theme; when omitted (None), the LLM's choice is respected.
    if doc_type == "presentation" and theme is not None:
        ir = ir.model_copy(update={"theme": theme.value})

    # Soft layout-budget gate: warn (never fail) when a slide's text overruns its
    # frames. The hard enforcement + LLM retry loop is Task 15.2's job; here we
    # only surface the estimate so a human can see it before opening the file.
    if doc_type == "presentation":
        resolved = resolve_design(ir)
        for slide in ir.slides:
            for warning in check_budget(slide, resolved):
                _err.print(f"[yellow]WARN[/yellow] {escape(warning)}")

    out_path = out.resolve()
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        render(ir, out_path)
        with_soffice = bool(soffice) and find_soffice() is not None
        report = validate_odf(out_path, with_soffice=with_soffice)
    except Exception as exc:  # noqa: BLE001 - present a concise message, no traceback
        _err.print(f"[red]FAIL[/red] 輸出或驗證失敗：{_concise(exc)}")
        raise typer.Exit(code=1)

    table = Table(title="驗證結果")
    table.add_column("Gate")
    table.add_column("結果")
    table.add_column("訊息", overflow="fold")
    for name, (passed, message) in report.gates.items():
        mark = "[green]OK[/green]" if passed else "[red]FAIL[/red]"
        table.add_row(escape(name), mark, escape(message))
    _out.print(table)

    if report.ok:
        _out.print(f"[green]OK[/green] {escape(str(out_path))}")
        raise typer.Exit(code=0)

    _out.print("[red]FAIL[/red] 驗證未通過。")
    raise typer.Exit(code=1)


@app.command()
def check(
    file: Path = typer.Argument(
        ..., help="要檢測的檔案；.odt/.odp/.ods 做 ODF 檢測，.docx 做結構比對。"
    ),
    out: Optional[Path] = typer.Option(
        None,
        "-o",
        "--out",
        help="同時把報告寫入此檔案（UTF-8）。",
    ),
) -> None:
    """檢測一份 ODF 檔（.odt/.odp/.ods），或比對 .docx 與其轉出的 .odt 結構。"""
    ext = file.suffix.lower()
    if ext == ".docx":
        report = diff_docx_odt(file)
    elif ext in _EXT_DOC_TYPE:
        report = check_odf(file)
    else:
        supported = ", ".join(sorted((*_EXT_DOC_TYPE, ".docx")))
        report = (
            f"error: unsupported extension {ext or '(無)'!r}; "
            f"supported: {supported}"
        )

    # Print the raw markdown so stdout is exactly the report body; keep any
    # side notes on stderr so ``-o`` output and stdout stay identical.
    typer.echo(report)

    if out is not None:
        out.write_text(report, encoding="utf-8")
        _err.print(f"[green]OK[/green] 報告已寫入 {escape(str(out))}")

    failed = report.startswith("error:") or "FAIL" in report
    raise typer.Exit(code=1 if failed else 0)


if __name__ == "__main__":  # pragma: no cover
    app()
