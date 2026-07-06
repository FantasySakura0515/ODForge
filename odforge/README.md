# ODForge

Forge natural language into native ODF documents (`.odt`/`.odp`/`.ods`).

## Status

Early scaffold. This repository currently contains the project skeleton only;
the intermediate representation (IR) models, ODF renderers, CLI, and MCP server
will be added in subsequent tasks.

## Installation

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
```

## Usage

### MCP server

ODForge ships an MCP server that exposes its rendering engine to AI assistants.
The assistant authors the Document IR itself and calls a tool
(`forge_text_document`, `forge_presentation`, `forge_spreadsheet`, or
`inspect_odf`); a real `.odt` / `.odp` / `.ods` file is written and validated. No
prompt is sent and no API key is required — ODForge is a pure renderer here.

Register it in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "odforge": {
      "command": "C:\\Users\\User\\Desktop\\project\\ODF\\odforge\\.venv\\Scripts\\python.exe",
      "args": ["-m", "odforge.mcp_server"]
    }
  }
}
```

## Development

Run the test suite with:

```powershell
pytest -v
```

## License

MIT. See [LICENSE](LICENSE).
