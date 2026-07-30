# ODForge Security Architecture — Run 1

## Application and baseline

ODForge is a local-first Python document-generation engine that turns natural-language prompts or validated Pydantic IR into native `.odt`, `.odp`, and `.ods` files. It exposes the same core through a Typer CLI, a local stdio MCP server, and a FastAPI/SSE Web UI. The Web API currently generates presentations only. The comparable baseline is a local document converter/generator such as LibreOffice CLI plus a local AI helper—not a multi-tenant SaaS. Lack of user accounts is therefore intentional only while `odforge serve` remains bound to loopback and the OS user, MCP client, and local processes are trusted.

There are no prior security-audit runs for this repository. A later run should vary hunting scopes because one pass cannot cover every path.

## Technology and entry points

- Python `>=3.10`, Pydantic v2, odfdo, lxml, PyMuPDF, OpenAI-compatible LLM clients, Typer/Rich, FastMCP, FastAPI/Uvicorn/sse-starlette (`odforge/pyproject.toml`).
- CLI entry point and local server bootstrap: `odforge/src/odforge/cli.py`.
- MCP tools and caller-selected input/output paths: `odforge/src/odforge/mcp_server.py`.
- HTTP job creation, SSE replay, outline approval, regeneration, preview, download, and snapshots: `odforge/src/odforge/webapi.py`.
- LLM prompt/schema generation and cloud/local backend selection: `odforge/src/odforge/llm.py`.
- Untrusted ODF/DOCX parsing and style extraction: `odforge/src/odforge/check.py`, `extract.py`, `validate.py`, and `xmlsafe.py`.
- LibreOffice subprocess boundary and PDF/PNG rasterization: `odforge/src/odforge/validate.py` and `preview.py`.
- ZIP package construction and hand-written ODP XML: `odforge/src/odforge/package.py` and `render/odp.py`.
- React HTTP/SSE client and state machine: `odforge/web/src/App.tsx`, `state/api.ts`, `state/sse.ts`, and `state/cockpit.ts`.

## Deployment and trust model

`odforge serve` defaults to `127.0.0.1:8000`. The API has no authentication or authorization and stores jobs in an in-memory dictionary. Artifacts live under a server-created `%TEMP%/odforge-jobs/{uuid4}/` directory. Job identifiers are high-entropy UUID hex values, but they are identifiers rather than a multi-user authorization mechanism. The current boundary trusts the local OS user and local processes; binding to a non-loopback address or placing the service behind a shared proxy would require authentication, job ownership, quotas, and lifecycle controls.

Browser cross-origin access is limited to an explicit localhost development allowlist with credentials disabled. The allowlist can be overridden by `ODFORGE_CORS_ORIGINS`. The production frontend is served same-origin by FastAPI. JSON POSTs trigger CORS preflight for ordinary cross-origin websites, while command-line/local processes are inside the intended trust boundary.

The stdio MCP server intentionally accepts caller-selected paths and writes with the current user's permissions. This is documented and is safe only for trusted MCP clients. CLI paths are likewise explicit user capabilities. DeepSeek requests send prompts/content to a cloud API; Ollama targets a local OpenAI-compatible endpoint. API keys come from environment variables or ignored local `.env` files. A local untracked `odforge/.env` currently contains a `DEEPSEEK_API_KEY`; the value was not copied into audit artifacts and should be rotated after the audit.

## Input and sink inventory

1. **HTTP:** `POST /api/generate`; SSE `GET /api/jobs/{id}/events`; outline edit/approve; unit/slide regeneration; PNG preview; ODP download; job snapshot. Inputs include prompt, mode, theme, backend, page count, edited outline, instruction, job id, and page index. Sinks include paid/local LLM calls, in-memory event logs, temp artifacts, LibreOffice, and responses.
2. **CLI:** prompt, output path, document type inferred from suffix, theme/mode/backend, template path, check input/report output, server host/port. Sinks include arbitrary user-authorized reads/writes, LLM calls, XML/ZIP processing, and subprocesses.
3. **MCP:** validated IR JSON plus caller-selected output/input directories. Sinks are document writes, ODF parsing, preview directories, and LibreOffice.
4. **Files:** ODF/OTP/OTT/DOCX ZIP containers, XML parts, embedded binary assets, `.env`, and generated PDFs/PNGs. Dangerous operations include `ZipFile.read`, XML parsing, odfdo parsing, PyMuPDF PDF opening, and LibreOffice processing.
5. **External services:** DeepSeek or another OpenAI-compatible endpoint; optional Anthropic vision API; local Ollama. Untrusted model output is repaired then Pydantic-validated before rendering.
6. **Subprocess:** LibreOffice is invoked with an argv list (no shell), a private temporary user profile, captured output, and a timeout. No direct command interpolation or `shell=True` path was identified.

## Existing defenses and likely gaps

Positive controls include Pydantic IR validation, explicit XML escaping for hand-written ODP, a hardened lxml parser (`resolve_entities=False`, no network, bounded trees), isolated LibreOffice profiles, fixed server-owned Web artifact paths, UUID job ids, no wildcard CORS-with-credentials, ignored `.env` files, and bounded page count (3–30).

High-value hunting areas are ZIP/XML decompression limits, parser differentials through odfdo/LibreOffice, Web request and job lifecycle limits, concurrent regeneration/state transitions, temp/job cleanup, prompt/instruction/outline size limits, dependency vulnerabilities, CORS override behavior, information leakage in raw exception messages, and second-order content such as spreadsheet formulas. The current npm lock contains known Vite/Vitest development-server advisories, and the Python environment resolved a vulnerable lxml 5.1.0 even though the project's own parser is hardened.
