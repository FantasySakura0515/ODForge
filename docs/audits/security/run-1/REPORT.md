# ODForge security audit — run 1

Date: 2026-07-16  
Scope: Python core/CLI/MCP/Web API, React client, ODF/OOXML input handling,
dependency manifests and lockfiles, local secret handling, temporary artifacts.

## Verdict

No confirmed exploitable vulnerabilities remain in the reviewed tree. The
machine-readable finding set is therefore empty. ODForge remains a local-first,
single-user tool; remote mode is an explicit, unauthenticated trusted-network
option rather than a public deployment mode.

## Remediated during this audit

| Area | Prior risk | Remediation |
|---|---|---|
| Untrusted ODF ZIP/XML | Direct, unbounded `ZipFile.read()` could decompress attacker-controlled XML into memory and build a very large lxml tree. Failed preflight could still be handed to LibreOffice. | Added shared archive member-count, total-size, per-member byte and XML markup-complexity guards; applied them to validate, check and template extraction; LibreOffice now runs only after structure/XML preflight passes. |
| Python supply chain | Python 3.10 forced `odfdo` to pin vulnerable `lxml<5.1.1`; the old environment also contained vulnerable pip/setuptools tooling. | Raised the supported floor to Python 3.11, constrained `lxml>=6.1,<7`, verified a clean Python 3.12 install, and upgraded environment tooling. |
| Frontend supply chain | The prior Vite/Vitest lock contained one critical, one high and three moderate advisories. | Upgraded Vite, Vitest, jsdom and the React plugin; regenerated the lockfile; `npm audit` reports zero vulnerabilities. |
| Browser/LAN abuse | `ODFORGE_CORS_ORIGINS=*` was accepted, non-loopback binding needed no acknowledgement, and paid/local generation had no concurrency or retention bounds. | CORS overrides now fail closed unless each entry is an explicit HTTP(S) origin; remote bind requires `--allow-remote`; generation and regeneration have global caps, per-job mutation locks, bounded inputs, cancellation, approval timeout, retention and artifact pruning. |
| Secret hygiene | A real local DeepSeek key exists in ignored `odforge/.env`. It was not tracked and no common key pattern was found in Git history. | Added a safe `.env.example`; the real value was never copied into reports or source. Because the value was exposed to the audit tool transcript, the owner should rotate it once. |

## Trust boundaries and residual assumptions

- Default bind is loopback and the API intentionally has no authentication.
- `--allow-remote` is only appropriate on a trusted network with firewall rules;
  it is not an authorization system.
- Cancellation prevents later pipeline stages but cannot recall a request already
  accepted by an external LLM provider.
- Users still choose whether to open/check a supplied office file. Resource
  guards bound that parser path but do not claim to sandbox LibreOffice itself.

## Evidence

- Architecture and source/sink map: `architecture.md`.
- Independent re-check: `verification.md`.
- Structured findings: `findings.json` (empty because no current finding met the
  confirmed exploitability bar).
- Secret history checks found no tracked `.env` and no matching key material.
- Final dependency and test commands are summarized in the repository quality
  audit and final handoff.
