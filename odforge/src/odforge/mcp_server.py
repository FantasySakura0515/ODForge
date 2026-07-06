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

Every tool returns a plain string and **never raises**: any failure is reported
as a string beginning with ``"error:"``. Raising inside an MCP tool would surface
as a protocol-level error to the client, so all exceptions are converted to text.

Run as a stdio MCP server with ``python -m odforge.mcp_server``.
"""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from odforge.ir import parse_ir
from odforge.render import render
from odforge.validate import find_soffice, validate_odf

mcp = FastMCP("odforge")


def _forge(document: dict, out_path: str, doc_type: str) -> str:
    """Shared pipeline: inject type -> parse_ir -> render -> validate.

    Returns a human-readable string. On success it starts with ``"ok"`` and
    lists the absolute output path plus a one-line summary per validation gate.
    On any failure it returns ``"error: <reason>"``. Never raises.
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

        report = validate_odf(out, with_soffice=False)
        gates = "; ".join(
            f"{name}: {'OK' if passed else 'FAIL'} — {message}"
            for name, (passed, message) in report.gates.items()
        )
        if not report.ok:
            return f"error: validation failed for {out.resolve()} — {gates}"
        return f"ok: wrote {out.resolve()} — {gates}"
    except Exception as exc:  # noqa: BLE001 - tools must never raise
        return f"error: {type(exc).__name__}: {exc}"


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

    ``document`` is the IR for a presentation (``odforge.ir.Presentation``). Key
    fields:

    * ``title`` (str, required) — deck title.
    * ``theme`` (str) — ``academic`` | ``minimal`` | ``dark``.
    * ``slides`` (list) — each slide has a ``layout``: ``title`` (``title``,
      ``subtitle``), ``title-content`` (``title``, ``bullets``), ``two-col``
      (``title``, ``left``, ``right``), ``section`` (``title``), ``big-fact``
      (``fact``). Any slide may carry ``notes`` (speaker notes).

    The ``type`` field is forced to ``"presentation"``. ``out_path`` is the target
    file path; missing parent directories are created. Returns an ``"ok: ..."``
    string with the absolute path and per-gate validation summary, or
    ``"error: ..."``. See ``odforge.ir.Presentation.model_json_schema()``.
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
    except Exception as exc:  # noqa: BLE001 - tools must never raise
        return f"error: {type(exc).__name__}: {exc}"


if __name__ == "__main__":  # pragma: no cover
    mcp.run()
