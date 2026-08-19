"""Three-gate ODF validation.

``validate_odf`` runs up to three independent gates over a generated ODF
file and reports the outcome of each without ever raising:

1. ``"structure"`` - zip opens; first entry is a ZIP_STORED ``mimetype``
   whose value matches the file extension; ``META-INF/manifest.xml`` exists
   and every listed full-path (other than ``"/"``) is present in the zip.
2. ``"xml"`` - every ``.xml`` member parses as well-formed XML via lxml.
3. ``"soffice"`` - only when ``with_soffice=True`` and LibreOffice is found;
   round-trips the file through ``soffice --convert-to pdf`` and checks that
   a PDF is produced.

Depends only on the stdlib, lxml and the mimetype constants from
``package.py`` - never on ``render/*`` or the IR.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from .package import ODP_MIMETYPE, ODS_MIMETYPE, ODT_MIMETYPE
from .xmlsafe import safe_fromstring
from .zipguard import (
    MAX_MANIFEST_MEMBER,
    MAX_MIMETYPE_MEMBER,
    MAX_XML_MEMBER,
    ArchiveLimitError,
    inspect_archive,
    read_member,
    read_xml_member,
)

_MANIFEST_NS = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
_MANIFEST_PATH = "META-INF/manifest.xml"

_EXT_MIMETYPE = {
    ".odt": ODT_MIMETYPE,
    ".odp": ODP_MIMETYPE,
    ".ods": ODS_MIMETYPE,
}

_SOFFICE_TIMEOUT = 120

# Parts that, when present in the package, must not be empty. Presence is
# not enforced here - the rule is "if a required-named part exists and is
# empty, fail" (e.g. settings.xml need not exist at all).
_REQUIRED_PARTS = {"content.xml", "styles.xml", "meta.xml", "settings.xml", "META-INF/manifest.xml"}


@dataclass
class ValidationReport:
    ok: bool                                # 所有跑過的 gate 都 True
    # gate 名 → (通過?, 訊息);鍵:"structure" | "xml" | "soffice"
    gates: dict[str, tuple[bool, str]]


def find_soffice() -> Path | None:
    """Locate the LibreOffice ``soffice`` executable, or ``None``."""
    found = shutil.which("soffice")
    if found:
        return Path(found)
    for candidate in (
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ):
        p = Path(candidate)
        if p.exists():
            return p
    return None


def _gate_structure(path: Path) -> tuple[bool, str]:
    """Gate 1: zip layout, mimetype-first-stored, extension match, manifest."""
    if not zipfile.is_zipfile(path):
        return False, f"not a valid zip file: {path.name}"
    with zipfile.ZipFile(path) as z:
        try:
            infos = inspect_archive(z)
        except ArchiveLimitError as exc:
            return False, f"archive exceeds safety limits: {exc}"
        if not infos:
            return False, "empty zip archive"

        first = infos[0]
        if first.filename != "mimetype":
            return False, f"first entry is {first.filename!r}, expected 'mimetype'"
        if first.compress_type != zipfile.ZIP_STORED:
            return False, "mimetype entry is not stored (ZIP_STORED)"

        try:
            mimetype = read_member(
                z, "mimetype", max_bytes=MAX_MIMETYPE_MEMBER
            ).decode("utf-8", errors="replace").strip()
        except ArchiveLimitError as exc:
            return False, f"archive exceeds safety limits: {exc}"
        expected = _EXT_MIMETYPE.get(path.suffix.lower())
        if expected is None:
            return False, f"unknown extension {path.suffix!r}"
        if mimetype != expected:
            return False, (
                f"mimetype {mimetype!r} does not match extension "
                f"{path.suffix!r} (expected {expected!r})"
            )

        names = set(z.namelist())
        if _MANIFEST_PATH not in names:
            return False, f"missing {_MANIFEST_PATH}"

        try:
            # Untrusted input: hardened parse (no external-entity resolution).
            root = safe_fromstring(
                read_xml_member(z, _MANIFEST_PATH, max_bytes=MAX_MANIFEST_MEMBER)
            )
        except ArchiveLimitError as exc:
            return False, f"archive exceeds safety limits: {exc}"
        except etree.XMLSyntaxError as exc:
            return False, f"manifest is not well-formed XML: {exc}"

        missing = []
        for entry in root.iter(f"{{{_MANIFEST_NS}}}file-entry"):
            full_path = entry.get(f"{{{_MANIFEST_NS}}}full-path")
            if not full_path or full_path == "/":
                continue
            if full_path.endswith("/"):
                # Directory entry: satisfied if any member lives under it.
                if not any(n.startswith(full_path) for n in names):
                    missing.append(full_path)
            elif full_path not in names:
                missing.append(full_path)
        if missing:
            return False, f"manifest lists parts absent from zip: {', '.join(sorted(missing))}"

    return True, "structure ok"


def _gate_xml(path: Path) -> tuple[bool, str]:
    """Gate 2: every .xml member is well-formed."""
    if not zipfile.is_zipfile(path):
        return False, f"not a valid zip file: {path.name}"
    bad = []
    with zipfile.ZipFile(path) as z:
        try:
            infos = inspect_archive(z)
        except ArchiveLimitError as exc:
            return False, f"archive exceeds safety limits: {exc}"
        for info in infos:
            name = info.filename
            if not name.lower().endswith(".xml"):
                continue
            try:
                data = read_xml_member(z, name, max_bytes=MAX_XML_MEMBER)
            except ArchiveLimitError as exc:
                return False, f"archive exceeds safety limits: {exc}"
            if not data.strip():
                # Mandatory document parts must carry content; other empty
                # placeholders (e.g. Configurations2/.../current.xml) are
                # emitted by real ODF producers and are fine.
                if name in _REQUIRED_PARTS:
                    bad.append(f"{name}: empty/whitespace-only required part")
                continue
            try:
                # Untrusted input: hardened parse (no external-entity resolution,
                # bounded expansion) — well-formedness is all this gate asserts.
                safe_fromstring(data)
            except etree.XMLSyntaxError as exc:
                bad.append(f"{name}: {exc}")
    if bad:
        return False, "malformed XML in " + "; ".join(bad)
    return True, "all xml well-formed"


def _build_soffice_cmd(
    soffice: Path, src: Path, fmt: str, outdir: Path, profile_dir: Path
) -> list[str]:
    """Build the soffice conversion argv (pure function, so it is testable).

    Always injects ``-env:UserInstallation=<file URI>`` (single-dash env switch)
    so the headless conversion uses a private profile and never collides with a
    desktop LibreOffice instance the user may have open.
    """
    return [
        str(soffice),
        "--headless",
        f"-env:UserInstallation={profile_dir.as_uri()}",
        "--convert-to",
        fmt,
        "--outdir",
        str(outdir),
        str(src),
    ]


def run_soffice_convert(
    soffice: Path,
    src: Path,
    fmt: str,
    outdir: Path,
    timeout: int = _SOFFICE_TIMEOUT,
) -> subprocess.CompletedProcess:
    """Convert ``src`` to ``fmt`` in ``outdir`` via an isolated soffice profile.

    The single shared entry point for every soffice ``--convert-to`` call in the
    codebase. Creates a throwaway profile directory, passes it as
    ``-env:UserInstallation``, captures stdout/stderr and cleans the profile up.
    Raises :class:`subprocess.TimeoutExpired` on timeout (callers handle it).
    """
    profile_dir = Path(tempfile.mkdtemp(prefix="odforge-soffice-profile-"))
    try:
        return subprocess.run(
            _build_soffice_cmd(soffice, src, fmt, outdir, profile_dir),
            # stdin must NOT be inherited: under an MCP/stdio host the parent's
            # stdin is an overlapped named pipe (anyio/Node create these on
            # Windows), and soffice blocks forever on it after profile init —
            # every conversion then dies at the timeout. A headless converter
            # has no business reading stdin, so hand it the null device.
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    finally:
        shutil.rmtree(profile_dir, ignore_errors=True)


# The error values LibreOffice writes into a cell whose formula it could not
# resolve. Finding one of these means the file opens *and is wrong*: a formula
# named a function LibreOffice does not have, pointed at a deleted cell, or was
# handed the wrong argument type. "It opened" is not the claim the gate makes.
_CALC_ERROR_TOKENS = (
    "#NAME?",
    "#VALUE!",
    "#REF!",
    "#DIV/0!",
    "#NUM!",
    "#N/A",
    "#NULL!",
    "Err:",
)


def _scan_calc_errors(csv_text: str) -> list[str]:
    """Every distinct Calc error token present in a converted sheet's CSV."""
    return [token for token in _CALC_ERROR_TOKENS if token in csv_text]


# ``--convert-to csv`` exports the FIRST sheet and stops. Every sheet after it
# was certified without being looked at: a workbook whose sheet 1 totals cleanly
# and whose sheet 2 is solid ``#DIV/0!`` passed with "formulas evaluated without
# errors". This filter string is the same CSV export with the trailing token set
# to ``-1`` — "every sheet", one ``{stem}-{SheetName}.csv`` each.
_CSV_ALL_SHEETS = "csv:Text - txt - csv (StarCalc):44,34,76,1,,0,false,true,true,false,false,-1"

_TABLE_TAG = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}table"


def _sheet_count(path: Path) -> int:
    """How many sheets ``path`` declares, straight from its own content.xml.

    The number the export has to match. Without it there is no way to tell "one
    CSV because there is one sheet" from "one CSV because this LibreOffice
    ignored the all-sheets token" — and those two must not be reported alike.
    Returns ``0`` when the package cannot be read; the caller treats that as
    "cannot verify" rather than "verified".
    """
    try:
        with zipfile.ZipFile(path) as z:
            data = read_xml_member(z, "content.xml", max_bytes=MAX_XML_MEMBER)
        return len(safe_fromstring(data).findall(f".//{_TABLE_TAG}"))
    except (ArchiveLimitError, KeyError, OSError, zipfile.BadZipFile,
            etree.XMLSyntaxError):
        return 0


def _gate_formulas(path: Path, soffice: Path, tmpdir: Path) -> tuple[bool, str]:
    """Spreadsheets only: make LibreOffice *compute*, then read *every* result.

    Converting to PDF proves the file opens. It does not prove the formulas
    work — a sheet full of ``#NAME?`` renders to a perfectly valid PDF. Round-
    tripping through CSV forces evaluation and hands back the computed values as
    text, where a broken formula is visible.

    Every sheet is exported and scanned, and the export is *counted* against the
    workbook's own sheet count. If they disagree the gate fails rather than
    reporting on the subset it happened to receive: a partial check reported as a
    pass is the failure mode this gate exists to remove. HTML export is the
    fallback for a LibreOffice too old for the all-sheets CSV token — it writes
    every sheet into one document.
    """
    expected = _sheet_count(path)
    if expected == 0:
        return False, "could not read the sheet list from content.xml"

    outdir = tmpdir / "calc"
    outdir.mkdir(parents=True, exist_ok=True)
    proc = run_soffice_convert(soffice, path, _CSV_ALL_SHEETS, outdir)
    produced = sorted(outdir.glob("*.csv"))
    if proc.returncode != 0 or not produced:
        stderr = (proc.stderr or proc.stdout or "").strip()
        return False, f"soffice could not evaluate the sheets: {stderr[:300]}"

    if len(produced) < expected:
        # This LibreOffice ignored the all-sheets token. Fall back rather than
        # quietly grade the workbook on its first sheet.
        htmldir = tmpdir / "calc-html"
        htmldir.mkdir(parents=True, exist_ok=True)
        html_proc = run_soffice_convert(soffice, path, "html", htmldir)
        exported = sorted(htmldir.glob("*.htm*"))
        if html_proc.returncode != 0 or not exported:
            return False, (
                f"only {len(produced)} of {expected} sheets could be evaluated; "
                "refusing to certify the rest unchecked"
            )
        produced = exported

    errors: dict[str, list[str]] = {}
    for exported in produced:
        found = _scan_calc_errors(
            exported.read_text(encoding="utf-8", errors="replace")
        )
        if found:
            errors[exported.stem] = found
    if errors:
        detail = "; ".join(
            f"{name}: {', '.join(tokens)}" for name, tokens in sorted(errors.items())
        )
        return False, (
            f"LibreOffice computed formula errors in {len(errors)} of {expected} "
            f"sheet(s) — {detail} — the file opens but its numbers are wrong"
        )
    return True, f"formulas evaluated without errors across {expected} sheet(s)"


def _gate_soffice(path: Path, soffice: Path) -> tuple[bool, str]:
    """Gate 3: round-trip through soffice --convert-to pdf.

    For ``.ods`` the gate additionally makes LibreOffice evaluate the sheet and
    checks the computed cells for error values — opening cleanly is a weaker
    claim than computing correctly, and only the latter is worth certifying.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="odforge-validate-"))
    try:
        proc = run_soffice_convert(soffice, path, "pdf", tmpdir)
        if proc.returncode != 0:
            stderr = (proc.stderr or proc.stdout or "").strip()
            return False, f"soffice exited {proc.returncode}: {stderr[:500]}"
        pdf = tmpdir / (path.stem + ".pdf")
        if not pdf.exists():
            stderr = (proc.stderr or proc.stdout or "").strip()
            return False, f"soffice produced no pdf: {stderr[:500]}"
        detail = f"converted to pdf ({pdf.stat().st_size} bytes)"
        if path.suffix.lower() == ".ods":
            ok, message = _gate_formulas(path, soffice, tmpdir)
            if not ok:
                return False, message
            detail = f"{detail}; {message}"
        return True, detail
    except subprocess.TimeoutExpired:
        return False, f"soffice timed out after {_SOFFICE_TIMEOUT}s"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def validate_odf(path: Path, *, with_soffice: bool = False) -> ValidationReport:
    """Validate an ODF file through the structure/xml/(soffice) gates.

    Never raises: any unexpected exception inside a gate becomes a
    ``(False, message)`` result for that gate. Gates run independently so a
    failure in an earlier gate does not stop later gates from reporting.
    """
    path = Path(path)
    gates: dict[str, tuple[bool, str]] = {}

    for name, fn in (("structure", _gate_structure), ("xml", _gate_xml)):
        try:
            gates[name] = fn(path)
        except Exception as exc:
            gates[name] = (False, f"{name} gate raised {type(exc).__name__}: {exc}")

    if with_soffice and not all(
        gates[gate][0] for gate in ("structure", "xml")
    ):
        gates["soffice"] = (
            False,
            "skipped because structure or xml preflight failed",
        )
    elif with_soffice:
        soffice = find_soffice()
        if soffice is not None:
            try:
                gates["soffice"] = _gate_soffice(path, soffice)
            except Exception as exc:
                gates["soffice"] = (False, f"soffice gate raised {type(exc).__name__}: {exc}")

    ok = all(passed for passed, _ in gates.values())
    return ValidationReport(ok=ok, gates=gates)
