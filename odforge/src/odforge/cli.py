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
from odforge.llm import generate_ir
from odforge.render import render
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


app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="將自然語言鍛造成原生 ODF 文件（.odt / .odp / .ods）。",
)

_out = Console()
_err = Console(stderr=True)


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
    theme: Theme = typer.Option(
        Theme.academic, "--theme", help="簡報主題，僅對 .odp 有效。"
    ),
    backend: Optional[Backend] = typer.Option(
        None, "--backend", help="LLM 後端；省略則使用預設。"
    ),
    soffice: bool = typer.Option(
        True,
        "--soffice/--no-soffice",
        help="驗證時若找得到 LibreOffice 就跑 soffice 轉檔驗證。",
    ),
) -> None:
    """由 PROMPT 產生一份 ODF 文件並輸出到 -o 指定的檔案。"""
    ext = out.suffix.lower()
    doc_type = _EXT_DOC_TYPE.get(ext)
    if doc_type is None:
        supported = ", ".join(sorted(_EXT_DOC_TYPE))
        _err.print(
            f"[red]FAIL[/red] 不支援的副檔名 {ext or '(無)'!r}；"
            f"支援的類型：{supported}"
        )
        raise typer.Exit(code=2)

    try:
        ir = generate_ir(prompt, doc_type, backend=backend.value if backend else None)
    except Exception as exc:  # noqa: BLE001 - present a concise message, no traceback
        _err.print(f"[red]FAIL[/red] 內容產生失敗：{escape(str(exc))}")
        raise typer.Exit(code=1)

    # --theme only means anything for presentations; leave the default alone.
    if doc_type == "presentation" and theme != Theme.academic:
        ir = ir.model_copy(update={"theme": theme.value})

    out_path = out.resolve()
    try:
        render(ir, out_path)
        with_soffice = bool(soffice) and find_soffice() is not None
        report = validate_odf(out_path, with_soffice=with_soffice)
    except Exception as exc:  # noqa: BLE001 - present a concise message, no traceback
        _err.print(f"[red]FAIL[/red] 輸出或驗證失敗：{escape(str(exc))}")
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
        _out.print(f"[green]OK[/green] {out_path}")
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
        _err.print(f"[green]OK[/green] 報告已寫入 {out}")

    failed = report.startswith("error:") or "FAIL" in report
    raise typer.Exit(code=1 if failed else 0)


if __name__ == "__main__":  # pragma: no cover
    app()
