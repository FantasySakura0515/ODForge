"""``wait_for_feedback``: let an agent sit in the editor's loop instead of the chat.

The person works in the browser — pins comments, presses「交給 AI 修改」— and the
agent, which started the editor for them, waits here until that happens. The
handoff goes through the file itself: submitted comments are annotations marked
``odforge:state="submitted"``, and pressing「完成」marks the presentation
``odforge:session="done"``. No server has to know the agent exists.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import List, Optional, Tuple

from .odpedit import Document
from .scene import scene_of

POLL_SECONDS = 1.0


def _stamp(path: Path) -> Optional[Tuple[int, int]]:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _text_of(element: dict) -> str:
    text = element.get("text")
    if not text:
        return ""
    joined = " / ".join("".join(r["text"] for r in p["runs"]) for p in text["paragraphs"])
    return joined if len(joined) <= 80 else joined[:80] + "…"


def pending(path: Path) -> Tuple[List[str], bool]:
    """Lines describing submitted comments, and whether the person ended the session."""
    scene = scene_of(path)
    lines: List[str] = []
    for index, page in enumerate(scene["pages"]):
        by_id = {e["id"]: e for e in page["elements"]}
        for comment in page.get("comments", []):
            if comment.get("state") != "submitted":
                continue
            target = comment.get("target")
            if target and target in by_id:
                about = f"關於 [{target}] {by_id[target]['kind']}「{_text_of(by_id[target])}」"
            else:
                about = "整頁"
            lines.append(f"- [{comment['id']}] 第 {index + 1} 頁,{about}:{comment['text']}")
    done = Document(path).state().get("session") == "done"
    return lines, done


async def wait_for_feedback(path: Path, timeout_seconds: float) -> str:
    deadline = time.monotonic() + max(1.0, timeout_seconds)
    last = None
    while True:
        stamp = _stamp(path)
        if stamp is None:
            return f"error: file not found: {path}"
        if stamp != last:
            last = stamp
            lines, done = pending(path)
            if lines:
                return (
                    f"feedback: 使用者在網頁上送出 {len(lines)} 則留言:\n"
                    + "\n".join(lines)
                    + "\n\n請用 edit_odp 逐則修改,並在同一次呼叫裡用 comment_delete "
                    "刪掉處理完的留言(同一頁由大到小刪);需要更多版面資訊時先 outline_odp。"
                    "改完後再呼叫 wait_for_feedback 繼續等使用者的下一批。"
                )
            if done:
                Document(path).apply([{"op": "session", "state": "open"}])
                return (
                    "done: 使用者在網頁上按了「完成」,這輪協作結束。向使用者簡短回報做了哪些修改。"
                )
        if time.monotonic() >= deadline:
            return (
                "waiting: 目前還沒有新的回饋。使用者可能還在網頁上留言;"
                "直接再呼叫一次 wait_for_feedback 繼續等。"
            )
        await asyncio.sleep(POLL_SECONDS)
