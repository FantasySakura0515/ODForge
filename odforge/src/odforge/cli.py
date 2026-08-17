"""ODForge command-line interface.

``odforge new`` turns a natural-language prompt into a native ODF file:
generate IR -> render -> validate -> present the outcome. This module holds
no business logic: it only parses arguments, wires the existing building
blocks together and presents the result. Task 8.2 adds a ``check`` subcommand.
"""

from __future__ import annotations

import ipaddress
import os
from enum import Enum
from pathlib import Path
from typing import List, Mapping, Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from odforge.benchmark import run_benchmark
from odforge.check import check_odf_verdict, diff_docx_odt_verdict
from odforge.critic import QAReport, run_qa_loop
from odforge.extract import extract_design
from odforge.ir import Branding, MediaAssetRef, Outline
from odforge.llm import (
    DroppedContent,
    generate_ir,
    generate_outline,
    generate_slides,
)
from odforge.media import AssetBlob, AssetInput, MediaError, load_local_image
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
    teal = "teal"
    forest = "forest"
    navy = "navy"
    violet = "violet"
    crimson = "crimson"
    slate = "slate"
    gold = "gold"
    sky = "sky"
    plum = "plum"
    clay = "clay"


class StyleName(str, Enum):
    classic = "classic"
    report = "report"
    academic = "academic"
    keynote = "keynote"
    editorial = "editorial"
    zen = "zen"


class Language(str, Enum):
    zh_tw = "zh-TW"
    en = "en"
    bilingual = "bilingual"


class LogoPlacement(str, Enum):
    cover = "cover"
    cover_closing = "cover-closing"
    all = "all"


class Backend(str, Enum):
    deepseek = "deepseek"
    ollama = "ollama"
    custom = "custom"


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

    Prints a page-role table (頁碼 / 版型 / 標題 / gist / 視覺意圖) and a design summary:
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
    table.add_column("視覺意圖", overflow="fold")
    for i, page in enumerate(outline.pages, start=1):
        table.add_row(
            str(i),
            escape(page.role),
            escape(page.title),
            escape(page.gist),
            escape(page.visual_intent),
        )
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


def _print_dropped_content(dropped: List[DroppedContent]) -> None:
    """Name every line the layout budget removed, on stderr, in full.

    The engine's last-resort degrade used to leave one parenthetical in the
    speaker notes. That is technically a disclosure and practically a silent
    deletion: nobody reads the notes of a page they did not know had changed.
    The user asked for this content — if it could not be kept, they are told
    exactly what went, so they can decide whether to re-run with more pages.
    """
    if not dropped:
        return
    table = Table(title="因版面限制未能保留的內容")
    table.add_column("頁", justify="right")
    table.add_column("標題", overflow="fold")
    table.add_column("被移除的內容", overflow="fold")
    for record in dropped:
        table.add_row(
            str(record.slide_no),
            escape(record.title),
            escape("、".join(record.items)),
        )
    _err.print(table)
    _err.print(
        "[yellow]提醒[/yellow] 以上內容已寫進該頁的備忘稿。"
        "若不希望被刪,請提高 --pages 或縮短需求中的重點數量後重新生成。"
    )


def _print_qa_report(report: QAReport) -> None:
    """Render a run_qa_loop outcome as a before/after summary table."""
    table = Table(title="設計評審(第四道閘)")
    table.add_column("輪次", justify="right")
    table.add_column("error", justify="right")
    table.add_column("warn", justify="right")
    for i, findings in enumerate(report.findings_by_round, start=1):
        errors = sum(1 for f in findings if f.severity == "error")
        warns = sum(1 for f in findings if f.severity == "warn")
        table.add_row(str(i), str(errors), str(warns))
    _out.print(table)
    if report.note:
        _out.print(f"[dim]{escape(report.note)}[/dim]")
    # The three verdicts print as three different things, because they mean three
    # different things. "設計 OK" is reserved for a review that actually
    # completed and found nothing; a review that never happened prints 未完成,
    # never a green tick — 無法檢查不等於通過.
    if report.verdict == "unknown":
        _out.print("[yellow]設計評審未完成[/yellow]（視覺品檢無法執行，等同未檢查）")
    elif report.verdict == "pass":
        _out.print(f"[green]設計 OK[/green]（共 {report.rounds} 輪）")
    elif report.failure and report.repaired:
        _out.print(
            f"[yellow]設計評審未完成[/yellow]（完成 {report.rounds} 輪後中斷；"
            "已套用的修補未經複驗）"
        )
    elif report.failure:
        _out.print(f"[yellow]設計評審未完成[/yellow]（完成 {report.rounds} 輪後中斷）")
    else:
        _out.print(
            f"[yellow]設計仍有 error[/yellow]（達上限 {report.rounds} 輪仍未收斂）"
        )


def _run_qa(
    ir,
    out_path: Path,
    outline: Optional[Outline],
    llm_backend: Optional[str],
    assets: Mapping[str, AssetInput] | None = None,
) -> bool:
    """Run the design-QA loop (第四道閘) and print a before/after summary.

    Returns whether QA **rewrote and re-rendered** the deck. The caller needs to
    know: a repaired deck is a different file from the one the three format gates
    just approved, and reporting the old verdict for new bytes is how a broken
    package could ship under a green table.

    Requires LibreOffice (soffice) and a vision backend
    (``ODFORGE_VISION_BACKEND != off``). If either is missing, print a clear
    message and fall back to the deterministic budget gate (already run) — the
    command never fails on ``--qa``. Any QA-side error is likewise swallowed
    into a message rather than crashing the run.
    """
    vision_backend = os.environ.get("ODFORGE_VISION_BACKEND", "off")
    reasons = []
    if find_soffice() is None:
        reasons.append("找不到 LibreOffice(soffice)")
    if vision_backend == "off":
        reasons.append("未設定視覺後端(ODFORGE_VISION_BACKEND=off)")
    if reasons:
        _err.print(
            "[yellow]--qa 已略過[/yellow] 設計評審需要 soffice 與視覺後端："
            + "、".join(reasons)
            + "。已改跑內建的版面預算閘(deterministic)。"
        )
        return False

    # QA's repair path re-runs the layout budget, so it can drop bullets too.
    # Silent there is no better than silent anywhere else.
    dropped: List[DroppedContent] = []
    try:
        qa_kwargs = {
            "outline": outline,
            "backend": vision_backend,
            "llm_backend": llm_backend,
            "dropped": dropped,
        }
        if assets:
            qa_kwargs["render_assets"] = assets
        report = run_qa_loop(ir, out_path, **qa_kwargs)
    except Exception as exc:
        _err.print(f"[yellow]--qa 已略過[/yellow] 設計評審發生問題：{_concise(exc)}")
        _print_dropped_content(dropped)
        # The loop works on a candidate file and only swaps it in on the way out,
        # so a failure inside it leaves the deck on disk untouched — the version
        # the three format gates already approved. Re-validate anyway: it costs
        # one soffice run, and "the file cannot have changed" is a claim worth
        # having checked rather than reasoned about.
        return True

    _print_qa_report(report)
    _print_dropped_content(dropped)
    return report.repaired


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
        help="講述型態，僅對 .odp 兩段式流程有效：presenter（講者型）/ "
        "detailed（自讀型）；省略則沿用大綱的判斷。",
    ),
    style: Optional[StyleName] = typer.Option(
        None,
        "--style",
        help="版式（僅對 .odp 有效）：classic 學院派 / report 顧問報告 / "
        "academic 學術簡潔 / keynote 舞台 / editorial 編輯風 / zen 極簡；"
        "省略則用主題配對的預設版式。",
    ),
    language: Language = typer.Option(
        Language.zh_tw,
        "--language",
        help="輸出語言：zh-TW（繁體中文，預設）/ en（英文）/ bilingual（中英對照）。",
    ),
    byline: str = typer.Option(
        "",
        "--byline",
        help="封面署名（單位／講者／日期），僅對 .odp 有效。",
    ),
    logo: Optional[Path] = typer.Option(
        None,
        "--logo",
        exists=True,
        dir_okay=False,
        readable=True,
        help="封面校徽／機構標誌（PNG 或 JPEG），僅對 .odp 有效。",
    ),
    logo_placement: LogoPlacement = typer.Option(
        LogoPlacement.cover_closing,
        "--logo-placement",
        help="校徽出現在哪些頁：cover（只封面）/ cover-closing（封面與結尾，預設）/ all（每頁）。",
    ),
    backend: Optional[Backend] = typer.Option(
        None, "--backend", help="LLM 後端；省略則使用預設。"
    ),
    image: Optional[List[Path]] = typer.Option(
        None,
        "--image",
        exists=True,
        dir_okay=False,
        readable=True,
        help="加入可供簡報使用的 PNG/JPEG 素材；可重複指定。",
    ),
    from_template: Optional[Path] = typer.Option(
        None,
        "--from-template",
        help="吃現有 ODF 公版範本(.otp/.odp/.ott/.odt):抽出其樣式並鎖定設計,"
        "LLM 只負責內容。僅對 .odp 有效。",
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
    qa: bool = typer.Option(
        False,
        "--qa",
        help="簡報專用:算圖後跑「第四道閘」設計評審迴圈(render→critique→repair);"
        "需 soffice + 視覺後端,缺任一則略過。",
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
    asset_blobs: dict[str, AssetBlob] = {}
    asset_refs: list[MediaAssetRef] = []
    if image:
        if doc_type != "presentation":
            _err.print("[dim]--image 僅適用於 .odp，已忽略。[/dim]")
        else:
            try:
                for index, image_path in enumerate(image, start=1):
                    asset_id = f"image-{index:02d}"
                    asset_blobs[asset_id] = load_local_image(image_path)
                    asset_refs.append(
                        MediaAssetRef(
                            id=asset_id,
                            description=image_path.stem.replace("_", " "),
                        )
                    )
            except MediaError as exc:
                _err.print(f"[red]FAIL[/red] 圖片素材無法使用：{_concise(exc)}")
                raise typer.Exit(code=1) from None

    # --from-template (吃現有範本): extract a DesignSpec from a public template and
    # LOCK it — the LLM only produces content, the look comes from the template.
    # Only meaningful for presentations; extracted here (before the pipeline) so
    # it can be locked onto the stage-1 outline AND enforced on the final deck.
    extracted_design = None
    if from_template is not None:
        if doc_type != "presentation":
            _err.print("[dim]--from-template 僅適用於簡報(.odp),已略過[/dim]")
        else:
            try:
                extracted_design = extract_design(from_template)
            except Exception as exc:
                _err.print(f"[red]FAIL[/red] 範本樣式抽取失敗：{_concise(exc)}")
                raise typer.Exit(code=1) from None
            _out.print(f"[cyan]套用範本樣式[/cyan] {escape(str(from_template))}")

    # The stage-1 outline, when the two-stage path runs — captured so --qa can
    # hand it to the repair step for per-page regeneration (None otherwise).
    outline: Optional[Outline] = None
    # Two-stage (outline -> slides) is the presentation default; .odt/.ods and
    # the --one-shot escape hatch stay on the v1 single-call generate_ir path.
    if doc_type == "presentation" and not one_shot:
        try:
            outline = generate_outline(
                prompt, backend=backend_name, language=language.value
            )
        except Exception as exc:
            _err.print(f"[red]FAIL[/red] 大綱產生失敗：{_concise(exc)}")
            raise typer.Exit(code=1) from None

        # --mode overrides the outline's narrative register before stage 2.
        if mode is not None:
            outline = outline.model_copy(update={"mode": mode.value})
        # --language likewise: stage 2 reads it off the outline.
        outline = outline.model_copy(update={"language": language.value})
        outline = outline.model_copy(
            update={
                "media_assets": asset_refs,
                "image_generation_available": (
                    os.getenv("ODFORGE_IMAGE_BACKEND", "off").lower() == "http"
                    and bool(os.getenv("ODFORGE_IMAGE_ENDPOINT", "").strip())
                ),
            }
        )

        # --from-template locks the design: overwrite the outline's design with
        # the extracted one so stage 2 fills content against the locked art
        # direction (generate_slides re-attaches outline.design onto the deck).
        if extracted_design is not None:
            outline = outline.model_copy(update={"design": extracted_design})

        # Interactive checkpoint: show the outline, then gate stage 2 on confirm.
        if interactive:
            _print_outline(outline)
            if not typer.confirm("依此大綱生成?"):
                _out.print("已取消")
                raise typer.Exit(code=0)

        dropped: List[DroppedContent] = []
        try:
            ir = generate_slides(outline, backend=backend_name, dropped=dropped)
        except Exception as exc:
            _err.print(f"[red]FAIL[/red] 內容產生失敗：{_concise(exc)}")
            raise typer.Exit(code=1) from None
        _print_dropped_content(dropped)
    else:
        try:
            ir = generate_ir(
                prompt, doc_type, backend=backend_name, language=language.value
            )
        except Exception as exc:
            _err.print(f"[red]FAIL[/red] 內容產生失敗：{_concise(exc)}")
            raise typer.Exit(code=1) from None

    # --theme only matters for presentations. When given, it locks a built-in
    # preset: the theme is overridden AND any LLM-attached DesignSpec is dropped.
    # Dropping the design is essential — resolve_design lets a present design win
    # over p.theme, so leaving it intact would make --theme a silent no-op on the
    # two-stage path. When omitted (None), the LLM's own choice is respected.
    if doc_type == "presentation" and theme is not None:
        dropped_design = ir.design is not None
        ir = ir.model_copy(update={"theme": theme.value, "design": None})
        if dropped_design:
            _err.print("[dim]--theme 指定,已改用預設主題(捨棄 AI 自選設計)[/dim]")

    # --from-template wins outright: enforce the extracted design on the final
    # deck (after --theme, so the template look always beats a preset/LLM choice
    # and is honoured even if stage 2 dropped the locked design).
    if extracted_design is not None:
        ir = ir.model_copy(update={"design": extracted_design})

    # --style locks the layout personality (cover composition, heading marks,
    # divider treatment, footer furniture). Independent of --theme: any palette
    # can be worn by any composition.
    if doc_type == "presentation" and style is not None:
        ir = ir.model_copy(update={"style": style.value})

    # Cover branding (署名 / 校徽). App-owned, so it is written onto the deck
    # AFTER generation — never asked of the model, never taken from it.
    if doc_type == "presentation" and (byline.strip() or logo is not None):
        logo_ref = ""
        if logo is not None:
            try:
                asset_blobs["logo"] = load_local_image(logo)
            except MediaError as exc:
                _err.print(f"[red]FAIL[/red] 校徽無法讀取：{_concise(exc)}")
                raise typer.Exit(code=2) from None
            logo_ref = "asset://logo"
        ir = ir.model_copy(
            update={
                "branding": Branding(
                    byline=byline.strip(),
                    logo=logo_ref,
                    placement=logo_placement.value,
                )
            }
        )
    elif byline.strip() or logo is not None:
        _err.print("[dim]--byline / --logo 僅適用於 .odp，已忽略。[/dim]")

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
        render(ir, out_path, assets=asset_blobs)
        with_soffice = bool(soffice) and find_soffice() is not None
        report = validate_odf(out_path, with_soffice=with_soffice)
    except Exception as exc:
        _err.print(f"[red]FAIL[/red] 輸出或驗證失敗：{_concise(exc)}")
        raise typer.Exit(code=1) from None

    table = Table(title="驗證結果")
    table.add_column("Gate")
    table.add_column("結果")
    table.add_column("訊息", overflow="fold")
    for name, (passed, message) in report.gates.items():
        mark = "[green]OK[/green]" if passed else "[red]FAIL[/red]"
        table.add_row(escape(name), mark, escape(message))
    _out.print(table)

    # Fourth gate (design QA): opt-in, presentations only. Runs after the three
    # deterministic format gates; degrades to a message (never fails) when
    # soffice or a vision backend is missing.
    if qa:
        if doc_type == "presentation":
            repaired = _run_qa(
                ir,
                out_path,
                outline=outline,
                llm_backend=backend_name,
                assets=asset_blobs,
            )
            if repaired:
                # QA rewrote slides and re-rendered the deck. The gate table
                # printed above described the file that existed *before* that,
                # and the exit code was about to be based on it. Re-validate the
                # bytes actually on disk — the final verdict must come from the
                # same artifact the user is going to open.
                _out.print("[dim]設計評審已修補並重新算圖,重新執行格式驗證…[/dim]")
                try:
                    report = validate_odf(out_path, with_soffice=with_soffice)
                except Exception as exc:
                    _err.print(f"[red]FAIL[/red] 修補後重新驗證失敗:{_concise(exc)}")
                    raise typer.Exit(code=1) from None
                table = Table(title="驗證結果(QA 修補後)")
                table.add_column("Gate")
                table.add_column("結果")
                table.add_column("訊息", overflow="fold")
                for name, (passed, message) in report.gates.items():
                    mark = "[green]OK[/green]" if passed else "[red]FAIL[/red]"
                    table.add_row(escape(name), mark, escape(message))
                _out.print(table)
        else:
            _err.print("[dim]--qa 僅適用於簡報(.odp),已略過[/dim]")

    if report.ok:
        _out.print(f"[green]OK[/green] {escape(str(out_path))}")
        raise typer.Exit(code=0)

    _out.print("[red]FAIL[/red] 驗證未通過。")
    raise typer.Exit(code=1)


@app.command()
def benchmark(
    prompt: str = typer.Argument(..., help="所有候選模型共用的簡報題目。"),
    out_dir: Path = typer.Option(
        Path("benchmark-results"),
        "--out-dir",
        help="儲存各候選 outline、IR、ODP、預覽與比較報告的目錄。",
    ),
    candidate: Optional[List[Backend]] = typer.Option(
        None,
        "--backend",
        help="要比較的 backend；可重複指定，預設 deepseek + ollama。",
    ),
    pages: Optional[int] = typer.Option(
        None, "--pages", min=3, max=30, help="所有候選共用的目標頁數。"
    ),
    image: Optional[List[Path]] = typer.Option(
        None,
        "--image",
        exists=True,
        dir_okay=False,
        readable=True,
        help="所有候選共用的 PNG/JPEG 素材；可重複指定。",
    ),
) -> None:
    """同題執行多個模型，保留完整產物並輸出可稽核的結構品質報告。"""

    assets: dict[str, AssetBlob] = {}
    refs: list[MediaAssetRef] = []
    try:
        for index, image_path in enumerate(image or [], start=1):
            asset_id = f"image-{index:02d}"
            assets[asset_id] = load_local_image(image_path)
            refs.append(
                MediaAssetRef(
                    id=asset_id,
                    description=image_path.stem.replace("_", " "),
                )
            )
    except MediaError as exc:
        _err.print(f"[red]FAIL[/red] 圖片素材無法使用：{_concise(exc)}")
        raise typer.Exit(code=1) from None

    names = (
        [item.value for item in candidate]
        if candidate
        else [Backend.deepseek.value, Backend.ollama.value]
    )
    report = run_benchmark(
        prompt,
        names,
        out_dir.resolve(),
        pages=pages,
        assets=assets,
        asset_refs=refs,
    )

    table = Table(title="模型同題 benchmark")
    table.add_column("Candidate")
    table.add_column("Status")
    table.add_column("Score", justify="right")
    table.add_column("Slides", justify="right")
    table.add_column("Visual", justify="right")
    table.add_column("Seconds", justify="right")
    for entry in report.entries:
        table.add_row(
            escape(entry.candidate),
            "[green]OK[/green]" if entry.success else "[red]FAIL[/red]",
            f"{entry.score:.1f}",
            str(entry.metrics.slide_count),
            f"{entry.metrics.visual_slide_ratio:.0%}",
            f"{entry.elapsed_seconds:.1f}",
        )
    _out.print(table)
    if report.winner:
        _out.print(f"[green]結構分數最高[/green] {escape(report.winner)}")
    _out.print(f"報告：{escape(str(out_dir.resolve() / 'report.md'))}")
    raise typer.Exit(code=0 if any(entry.success for entry in report.entries) else 1)


def _is_loopback_host(host: str) -> bool:
    """Return whether a uvicorn bind host is explicitly loopback-only."""
    candidate = host.strip().strip("[]")
    if candidate.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


@app.command()
def serve(
    host: str = typer.Option(
        "127.0.0.1", "--host", help="伺服器綁定的位址（本機工具預設 127.0.0.1）。"
    ),
    port: int = typer.Option(8000, "--port", help="伺服器連接埠。"),
    allow_remote: bool = typer.Option(
        False,
        "--allow-remote",
        help="明確允許非 loopback 綁定；API 無認證，僅限受信任網路。",
    ),
) -> None:
    """啟動 ODForge Web API 伺服器（FastAPI + SSE），供前端控制台驅動兩段式生成。

    需安裝 web 相依：``pip install "odforge[web]"``。fastapi / uvicorn /
    sse-starlette 只在此指令內延遲載入，核心套件不因它們而變重。
    """
    if not _is_loopback_host(host) and not allow_remote:
        raise typer.BadParameter(
            "非本機綁定會暴露無認證且可花用 LLM 金鑰的 API；"
            "若已確認網路與防火牆可信，請加上 --allow-remote。",
            param_hint="--host",
        )
    if not _is_loopback_host(host):
        _err.print(
            "[bold yellow]警告[/bold yellow] 遠端模式無認證；請只在受信任網路使用，"
            "並設定明確的 ODFORGE_CORS_ORIGINS。"
        )
        # Subscription-authenticated backends (codex) refuse to run behind a
        # public bind: with no authentication in front of the API, they would
        # spend the operator's personal plan for whoever reaches the port.
        os.environ["ODFORGE_PUBLIC_BIND"] = "1"

    # Load a local .env (odforge/.env) so DEEPSEEK_API_KEY etc. can live in a file
    # instead of the shell env. Best-effort: python-dotenv ships with the web extra,
    # and this runs only for `serve`, never at import time (so tests stay unaffected).
    # The project-relative path makes it independent of the current working dir.
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
        load_dotenv()  # also honour a .env in the current working dir, if present
    except ImportError:
        pass

    try:
        import uvicorn

        from odforge.webapi import create_app, frontend_dist
    except ImportError as exc:
        _err.print(
            "[red]FAIL[/red] 缺少 web 相依，請先安裝："
            "[cyan]pip install \"odforge[web]\"[/cyan]"
            f"（{_concise(exc)}）"
        )
        raise typer.Exit(code=1) from None

    if frontend_dist() is not None:
        _out.print(
            f"[green]ODForge[/green] 完整控制台啟動於 http://{escape(host)}:{port}/  "
            "（前端已掛載，Ctrl+C 結束）"
        )
    else:
        # 不要假設「現在的工作目錄就是 repo 根目錄」。從 wheel 安裝的使用者根本
        # 沒有 odforge/web 這個目錄,叫他去那裡跑 npm 只會讓人更困惑;那種情況是
        # 安裝檔本身沒帶前端(見 hatch_build.py),要重裝而不是重 build。
        from odforge.webapi import __file__ as _webapi_file

        source_web = Path(_webapi_file).resolve().parents[2] / "web"
        if (source_web / "package.json").is_file():
            hint = (
                f"前端尚未 build:先執行 `npm --prefix {escape(str(source_web))} ci` "
                f"與 `npm --prefix {escape(str(source_web))} run build`,"
                "或另開一個 `npm run dev` 開發伺服器"
            )
        else:
            hint = (
                "這份安裝沒有內含前端資產(API-only wheel)。"
                "請改裝含前端的版本:[cyan]pip install --force-reinstall \"odforge[web]\"[/cyan]"
            )
        _out.print(
            f"[green]ODForge Web API[/green] 啟動於 http://{escape(host)}:{port}  "
            f"（API-only:{hint};Ctrl+C 結束）"
        )
    uvicorn.run(create_app(), host=host, port=port)


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
        report, failed = diff_docx_odt_verdict(file)
    elif ext in _EXT_DOC_TYPE:
        report, failed = check_odf_verdict(file)
    else:
        supported = ", ".join(sorted((*_EXT_DOC_TYPE, ".docx")))
        report = (
            f"error: unsupported extension {ext or '(無)'!r}; "
            f"supported: {supported}"
        )
        failed = True

    # Print the raw markdown so stdout is exactly the report body; keep any
    # side notes on stderr so ``-o`` output and stdout stay identical.
    typer.echo(report)

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
        _err.print(f"[green]OK[/green] 報告已寫入 {escape(str(out))}")

    # The exit code comes from the structured verdict, never from grepping the
    # report: its free text echoes file/style names, and a valid file named
    # q4-FAIL-review.odt must not exit 1.
    raise typer.Exit(code=1 if failed else 0)


if __name__ == "__main__":  # pragma: no cover
    app()
