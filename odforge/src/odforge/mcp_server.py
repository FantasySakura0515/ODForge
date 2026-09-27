"""ODForge MCP server — expose the deterministic ODF rendering engine as tools.

This server is a *pure rendering engine*. The calling AI assistant is itself the
content author: it produces the Document IR (a JSON object) and hands it to a
``forge_*`` tool, which renders a native ``.odt`` / ``.odp`` / ``.ods`` file and
validates it. There is no prompt, no LLM call and no API key here — this module
deliberately never imports ``odforge.llm``.

Tools registered on the ``mcp`` app:

* ``forge_text_document(document, out_path)`` — render a text document to ``.odt``
* ``forge_presentation(document, out_path)`` — render a presentation to ``.odp``
* ``forge_spreadsheet(document, out_path)`` — render a spreadsheet to ``.ods``
* ``inspect_odf(path)`` — validate an existing ODF file
* ``preview_odf(path, out_dir)`` — rasterise an ODF file to one PNG per page

What a caller gets, and what it is responsible for
--------------------------------------------------
ODForge guarantees the *format*: every ``forge_*`` call renders spec-compliant
ODF XML and then runs the same three format gates the CLI runs (zip layout, XML
well-formedness, and a real LibreOffice round-trip when LibreOffice is
installed), plus the deterministic layout-budget check that reports text which
will overrun its frame.

The caller — the AI assistant — owns the *content*: it writes the Document IR,
and when a budget warning comes back it is the one that shortens the text and
forges again. ``preview_odf`` closes the loop by handing back one PNG per page,
so an agent can look at what it made rather than assume.

Every tool returns a plain string and **never raises**: any failure is reported
as a string beginning with ``"error:"``. Raising inside an MCP tool would surface
as a protocol-level error to the client, so all exceptions are converted to text.

Run as a stdio MCP server with ``odforge-mcp`` (installed console script) or
``python -m odforge.mcp_server``.
"""

from __future__ import annotations

import json
from pathlib import Path

try:  # mcp >= 2.0 renamed FastMCP to MCPServer and moved it.
    from mcp.server.mcpserver import MCPServer as _Server
except ModuleNotFoundError:  # pragma: no cover - exercised by the other major
    from mcp.server.fastmcp import FastMCP as _Server

from odforge.ir import Presentation, parse_ir
from odforge.preview import PreviewUnavailable, render_pages
from odforge.render import render
from odforge.textmetrics import check_budget, deck_warnings
from odforge.textutil import concise
from odforge.themes import resolve_design, resolve_style
from odforge.validate import find_soffice, validate_odf

# One object either way: ``.tool()`` and ``.run()`` are identical across both
# majors, so the shim above is the whole migration. Without it, `pip install
# odforge` on a machine that resolves mcp 2.x installed an MCP server that
# crashed on the first line of `odforge-mcp` — and the failure was invisible here
# because this repo's venv happened to be pinned at 1.28 from an earlier install.
mcp = _Server("odforge")


def _budget_warnings(ir: object) -> list[str]:
    """Layout-budget check: which frames will overrun their box, and by how much.

    This is the deterministic half of the design gate, and until now it was
    missing from the MCP path entirely — it lived only in ``llm.py`` (where it
    drives a retry) and ``cli.py`` (where it prints a warning). An agent driving
    ``forge_presentation`` could hand over twelve bullets, get back a cheerful
    ``"ok:"``, and ship a deck with text running off the page.

    The division of labour here is the whole point of MCP: **ODForge measures,
    the calling agent rewrites.** So this returns actionable text naming the page
    and the frame, not a pass/fail flag.

    Presentations only — ``.odt`` reflows and ``.ods`` has no fixed frames, so
    neither has a layout budget to blow.
    """
    if not isinstance(ir, Presentation):
        return []
    theme = resolve_design(ir)
    style = resolve_style(ir)
    warnings: list[str] = []
    for n, slide in enumerate(ir.slides, start=1):
        warnings.extend(
            f"第 {n} 頁:{message}" for message in check_budget(slide, theme, style)
        )
    warnings.extend(deck_warnings(ir.slides))
    return warnings


def _forge(document: dict, out_path: str, doc_type: str) -> str:
    """Shared pipeline: inject type -> parse_ir -> render -> validate -> budget.

    Returns a human-readable string. On success it starts with ``"ok"`` and
    lists the absolute output path plus a one-line summary per validation gate;
    if any text overruns its frame, an explicit ``warning:`` block follows.
    On any failure it returns ``"error: <reason>"``. Never raises.

    LibreOffice runs here when it is installed, so an MCP-forged file gets the
    same three format gates as one made by the CLI. It previously ran only two
    (``with_soffice=False`` was hard-coded), which meant "ok" from this tool was
    a weaker claim than "ok" from ``odforge new`` while reading identically.
    """
    try:
        # Override the caller-supplied ``type`` to the type this tool renders,
        # preventing type confusion (a payload mislabelled by the caller/LLM).
        payload = dict(document)
        payload["type"] = doc_type

        ir = parse_ir(payload)

        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        render(ir, out)

        report = validate_odf(out, with_soffice=find_soffice() is not None)
        gates = "; ".join(
            f"{name}: {'OK' if passed else 'FAIL'} — {message}"
            for name, (passed, message) in report.gates.items()
        )
        if not report.ok:
            return f"error: validation failed for {out.resolve()} — {gates}"

        result = f"ok: wrote {out.resolve()} — {gates}"
        warnings = _budget_warnings(ir)
        if warnings:
            # Not an error: the file exists and is a valid ODF package. But
            # "valid" and "readable" are different claims, and reporting only the
            # first one is how an overset deck ships under a clean bill of health.
            result += (
                "\n\nwarning: 版面預算超載——檔案已產出且格式有效,但下列內容會溢出"
                "版面。請縮短這些頁面的文字後重新 forge:\n"
                + "\n".join(f"- {w}" for w in warnings)
            )
        return result
    except Exception as exc:
        return f"error: {type(exc).__name__}: {concise(str(exc))}"


@mcp.tool()
def forge_text_document(document: dict, out_path: str) -> str:
    """Render a text document (Document IR) to a native ``.odt`` file.

    ``document`` is the IR for a text document (``odforge.ir.TextDoc``). Key
    fields:

    * ``title`` (str, required) — document title.
    * ``lang`` (str) — language tag, e.g. ``"zh-TW"``.
    * ``blocks`` (list) — body content; each block has a ``kind`` discriminator:
      ``heading`` (``level`` 1-6, ``text``), ``paragraph`` (``text``, ``style``:
      body/quote/note), ``list`` (``ordered``, ``items``), ``table`` (``header``,
      ``rows``), ``toc``, ``pagebreak``.

    The ``type`` field is forced to ``"text"``. ``out_path`` is the target file
    path; missing parent directories are created. Returns an ``"ok: ..."`` string
    with the absolute path and per-gate validation summary, or ``"error: ..."``.
    See ``odforge.ir.TextDoc.model_json_schema()`` for the full schema.
    """
    return _forge(document, out_path, "text")


@mcp.tool()
def forge_presentation(document: dict, out_path: str) -> str:
    """Render a presentation (Document IR) to a native ``.odp`` file.

    ``document`` is an ``odforge.ir.Presentation`` (full schema:
    ``Presentation.model_json_schema()``). You write the content; the engine
    owns geometry, so a good deck comes from rich, well-structured IR.

    **Deck-level fields**

    * ``title`` (required); ``theme`` — one of 13 curated palettes: academic,
      minimal, dark, teal, forest, navy, violet, crimson, slate, gold, sky,
      plum, clay (light grounds except dark/violet/slate/gold).
    * ``style`` — the composition, pick by occasion: ``passport`` (審查、計畫書、
      成果報告 — white framed cards, the takeaway as a dark right-hand panel;
      the densest, most formal look), ``report`` (顧問式匯報、議會、商業提案),
      ``academic`` (口試、課堂), ``editorial`` (人文、設計), ``keynote``
      (演講、發表; pair with a dark theme), ``zen`` (品牌、極簡), ``classic``.
      Styles change cards, heading marks, dividers and the takeaway band — two
      decks in different styles should not look like one template.

    **Every content page** (not title/section/closing/quote) should carry:
    ``kicker`` (chapter tag, e.g. "02 研究方法"), ``title`` written as a
    full-sentence conclusion (10–22 字, not a topic noun), ``subtitle`` (a
    15–40 字 lede under the title), and — on about a third of the pages,
    where the page really concludes something — ``takeaway`` (≤30 字 "so
    what", drawn as a band at the foot or, in ``passport``, a dark side panel
    (the panel only where the body still fits two-thirds of the width: 2 or 4
    cards, ≤3 metrics, two-col / comparison / title-content / big-fact /
    diagram — 3 cards or 4 metrics keep the band)
    on cards / title-content / two-col / comparison / metrics / big-fact /
    diagram pages — wide layouts keep the band).
    A takeaway must add something the title does not say (a number, an
    impact, a next step); one that paraphrases the title is noise.

    **Layouts** (``slide.layout``) and what each needs:

    * ``title`` — title, subtitle, kicker (the occasion, e.g. 「期末審查」),
      bullets (2–4 cover facts — 單位、報告人、日期 — shown as the cover's
      info card). ``closing`` — title, subtitle, bullets
      (3–4 next steps as ``{"text", "children"}``), optional kicker
      (「結語」「請求事項」, drawn above the message).
    * ``cards`` — bullets: 2–4 ``{"text": 4–14 字小標, "children": [1–2 句
      具體說明]}``. ``dashboard`` — metrics (2–4; value + label only, a
      metric's detail is not drawn in the dashboard strip) + bullets (2–3
      cards, each with ONE short child ≤12 字 — the cards sit under the strip
      and have room for a single line).
    * ``process`` — steps: 2–5 ``{"title", "detail"}``. ``timeline`` —
      events: 2–5 ``{"label", "title", "detail"}``. ``metrics`` — metrics:
      2–4 ``{"value", "label", "detail"}``.
    * ``table`` — table: ``{"columns": [2–5], "rows": [[...], ...] (≤6; ≤5
      with a takeaway), "highlight": row index}``. ``comparison`` — left/right, first item of
      each is the column header. ``two-col`` — left/right.
    * ``diagram`` — diagram: ``{"kind": "hub"|"hierarchy", "nodes": [{"id",
      "title", "detail", "emphasis"}], "edges": [{"source", "target",
      "label"}]}``. ``chart`` — chart: ``{"labels", "values", "unit",
      "highlight"}``, real data only.
    * ``big-fact`` — title (what the number means — never the number
      itself), fact (the number), subtitle (the baseline: 相較去年、全國平均),
      bullets (one caption line); at most one big-fact per ten pages — a
      lone number is emphasis, not a page type. ``quote`` — quote,
      attribution (at most one quote page per deck).
    * ``title-content`` — bullets (at most 2 such pages per deck);
      ``agenda`` — bullets, un-numbered (the page numbers them; only for decks
      ≥ 15 pages); ``section`` — title, with ``kicker`` "02 章節名" (only
      for decks ≥ 20 pages, at most 2 — 3 from 30 pages — otherwise put the
      chapter in the next page's ``kicker``). The divider's big numeral is
      taken from that kicker in classic / report / keynote / editorial /
      passport; ``academic`` draws its divider between two rules and ``zen``
      under a dot — no numeral, by design. Agenda, dividers, quotes and
      big-facts together stay under ~18% of the deck, and no card layout
      (cards / process / timeline / metrics) should carry more than a fifth
      of the pages — a table, chart, diagram or comparison says the same
      thing without the deck reading as one page repeated. A count in a
      title (「三項」「五個建議」) must match the items the page shows. The
      tool reports all of these as warnings.

    Practical lengths (longer text is shrunk, then cut with "…"): cards (3
    across) title ≤10 字, each child ≤22 字; process 4 steps title ≤7 字 /
    detail ≤18 字 (5 steps: 5 / 14); timeline 5 events title ≤6 字 / detail
    ≤18 字 (4 events: 8 / 24); metric value ≤7 字 incl. unit, label ≤10 字;
    chart labels ≤5 字; diagram node title ≤6 字 / detail ≤10 字; table ≤5
    columns, cells ≤12 字; closing text ≤10 字 (4 items: ≤6), child ≤20 字. Beside a
    ``passport`` side panel the body is two-thirds wide: card titles ≤6 字.
    ``two-col`` columns have no header — for 「改善前／改善後」 use
    ``comparison`` (its left[0] / right[0] are the column headers).

    Write insights, not topics ("誤判率從 32% 降到 8%" beats "降低誤判"); fill
    every ``detail``; never use the same layout on two adjacent pages. Any
    slide may carry ``notes`` (speaker notes).

    The ``type`` field is forced to ``"presentation"``. ``out_path`` is the target
    file path; missing parent directories are created. Returns an ``"ok: ..."``
    string with the absolute path and per-gate validation summary, or
    ``"error: ..."``.

    **If any text will overrun its frame**, the ``"ok: ..."`` string is followed
    by a ``warning:`` block naming each page and frame. The file is valid ODF and
    was written — but it will look wrong. Shorten the flagged pages and call this
    tool again; do not present an overset deck as finished. Then call
    ``preview_odf`` and look at the pages before handing the deck over.
    """
    return _forge(document, out_path, "presentation")


@mcp.tool()
def forge_spreadsheet(document: dict, out_path: str) -> str:
    """Render a spreadsheet (Document IR) to a native ``.ods`` file.

    ``document`` is the IR for a spreadsheet (``odforge.ir.Spreadsheet``). Key
    fields:

    * ``title`` (str, required) — workbook title.
    * ``sheets`` (list) — each sheet has ``name``, ``columns`` (header labels),
      ``rows`` (list of rows; cells are str/int/float) and optional ``formulas``
      (each ``{"cell": "B5", "formula": "of:=SUM([.B2:.B4])"}`` — formulas must
      use the ``of:=`` OpenFormula prefix).

    The ``type`` field is forced to ``"spreadsheet"``. ``out_path`` is the target
    file path; missing parent directories are created. Returns an ``"ok: ..."``
    string with the absolute path and per-gate validation summary, or
    ``"error: ..."``. See ``odforge.ir.Spreadsheet.model_json_schema()``.
    """
    return _forge(document, out_path, "spreadsheet")


@mcp.tool()
def inspect_odf(path: str) -> str:
    """Validate an existing ODF file and report each gate as markdown.

    Runs ODForge's validation gates over the file at ``path``: ``structure``
    (zip layout, stored mimetype, manifest) and ``xml`` (well-formedness), plus
    ``soffice`` (a LibreOffice round-trip) when LibreOffice is installed. Returns
    one markdown bullet per gate — ``- {gate}: {OK|FAIL} — {message}`` — or
    ``"error: file not found: {path}"`` when the file does not exist. Never raises.
    """
    try:
        target = Path(path)
        if not target.exists():
            return f"error: file not found: {path}"
        report = validate_odf(target, with_soffice=find_soffice() is not None)
        lines = [
            f"- {name}: {'OK' if passed else 'FAIL'} — {message}"
            for name, (passed, message) in report.gates.items()
        ]
        return "\n".join(lines)
    except Exception as exc:
        return f"error: {type(exc).__name__}: {concise(str(exc))}"


@mcp.tool()
def preview_odf(path: str, out_dir: str) -> str:
    """Rasterise an ODF file to one PNG per page for an agent's visual QA loop.

    Converts the ODF at ``path`` to PDF via a headless LibreOffice round-trip and
    renders each page to a zero-padded PNG (``page-01.png``, ``page-02.png``, ...)
    in ``out_dir``, which is created if missing. This hands the QA harness's
    rendering capability to any calling agent: after forging a deck, preview it
    and *look* at the PNGs to catch overflow, contrast or layout problems that
    ``inspect_odf`` (structure/XML validation) cannot see.

    On success returns a JSON string ``{"pages": [<png path>, ...], "count": N}``
    with the absolute PNG paths in page order. When LibreOffice is not installed
    it returns ``"error: preview unavailable: <reason>"``; any other failure
    returns ``"error: <reason>"``. Never raises (an exception inside an MCP tool
    would surface as a protocol-level error).
    """
    try:
        pages = render_pages(Path(path), Path(out_dir))
        return json.dumps(
            {"pages": [str(p.resolve()) for p in pages], "count": len(pages)}
        )
    except PreviewUnavailable as exc:
        return f"error: preview unavailable: {concise(str(exc))}"
    except Exception as exc:
        return f"error: {type(exc).__name__}: {concise(str(exc))}"


def main() -> None:  # pragma: no cover
    """Console-script entry point (``odforge-mcp``): run the stdio MCP server.

    Exists so that ``pip install odforge`` yields a *command* an MCP host can be
    pointed at. Without it, wiring ODForge into Claude Code / Cursor / any other
    host meant knowing the module path and which interpreter to use — a barrier
    that had nothing to do with the tool's capabilities.
    """
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
