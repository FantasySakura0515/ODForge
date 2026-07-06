"""Small, dependency-free text helpers shared across ODForge entry points.

Kept deliberately import-light (stdlib only) so both the CLI and the MCP server
can use it without pulling in ``rich`` or any other UI dependency.
"""

from __future__ import annotations

# A real-API failure can carry huge pydantic error dumps (tens of KB); bound
# what reaches a terminal or an MCP client.
MAX_ERROR_LEN = 500

_TRUNCATION_MARK = "…(訊息截斷)"


def concise(message: str, *, limit: int = MAX_ERROR_LEN) -> str:
    """Return ``message`` truncated to ``limit`` chars, flagging any truncation.

    Plain text only — no markup escaping — so it is safe for both rich-rendered
    terminals (the caller escapes) and raw MCP string responses.
    """
    if len(message) > limit:
        return message[:limit] + _TRUNCATION_MARK
    return message
