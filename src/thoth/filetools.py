"""file.write: the AI's first content producer (the branch protocol's missing half).

``git.commit`` commits the index as it finds it - by design, it stages nothing.
That made the branch protocol a closed loop: the runner could commit only what
the OPERATOR staged. ``file.write`` completes the chain:

    file.write (level 1, this module) -> operator stages -> git.commit (level 2)

Division of labor is unchanged (ADR-003 / ADR-004): the guard answers *whether*
the file domain may be written at all - the operator's {domain -> level} ceiling
plus one typed confirmation per action. This tool answers *what a legal write
is*, in tool code, at every permission level:

  - workspace-scoped: the path must resolve inside the workspace (the same rule
    file.read enforces); escapes are refused before any disk write.
  - create-only: an existing file (or directory) is refused with blocked=True -
    this tool cannot overwrite or destroy operator content. Overwrite and
    delete are different tools with higher levels, deliberately not built yet.
    The create itself opens in ``"xb"`` mode, so even a file that appears
    between the check and the open refuses the write (no overwrite race).
  - text only: content must be a non-empty str, capped at _MAX_WRITE_BYTES;
    the bytes written are exactly the bytes verified. Binary payloads are not
    a model surface today.
  - flat: the parent directory must already exist - no directory-creation side
    effects from a single tool call.

Verifier evidence is disk truth, not the model's claim: the result records a
byte-for-byte readback of what landed, and the verifier pins that evidence -
a write whose readback disagrees with the payload fails verify, honestly.

Ships INERT: the file domain's default ceiling is 0, so file.write is denied
at the guard until the operator raises it (``thoth permission set file 1``)
and types the per-action token - exactly how the git tools shipped.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from . import guard
from .tools import ToolRegistry, ToolSpec, VerifyReport, _emit_tool_event, _result

# Minimal blast radius: a runaway planner cannot dump unbounded content to
# disk. 64 KiB is far above any note/report this surface writes today.
_MAX_WRITE_BYTES = 64 * 1024


def _run_write(path: str, content: str, workspace: str | None = None,
               _conn: sqlite3.Connection | None = None, **_: Any) -> dict[str, Any]:
    if not isinstance(path, str) or not path:
        return _result(False, "path must be a non-empty string", blocked=True)
    if not isinstance(content, str) or not content:
        return _result(False, "content must be a non-empty string", blocked=True)
    payload = content.encode("utf-8")
    if len(payload) > _MAX_WRITE_BYTES:
        return _result(False,
                       f"content too large: {len(payload)} bytes "
                       f"(cap {_MAX_WRITE_BYTES})",
                       blocked=True)
    ws = Path(workspace) if workspace else Path.cwd()
    try:
        resolved = (ws / path).resolve()
        resolved.relative_to(ws.resolve())
    except (ValueError, OSError):
        return _result(False, "path escapes the workspace", blocked=True)
    if resolved.exists():
        return _result(False,
                       f"refusing to overwrite existing target: {resolved.name}",
                       blocked=True)
    if not resolved.parent.is_dir():
        return _result(False,
                       f"parent directory does not exist: {resolved.parent.name}",
                       blocked=True)
    try:
        with open(resolved, "xb") as fh:  # 'x': the create refuses an existing file
            fh.write(payload)
    except FileExistsError:
        return _result(False,
                       f"refusing to overwrite existing target: {resolved.name}",
                       blocked=True)
    except OSError as exc:
        return _result(False, f"write failed: {exc}")
    # Disk evidence, recorded not claimed: read the bytes back.
    try:
        readback = resolved.read_bytes()
    except OSError as exc:
        return _result(False, f"write landed but readback failed: {exc}")
    result = _result(
        True, f"wrote {len(readback)} bytes to {resolved.name}",
        path=str(resolved), bytes=len(readback),
        readback_matches=(readback == payload), workspace=str(ws),
    )
    _emit_tool_event(_conn, "tool.file_write",
                     {"path": str(resolved), "bytes": len(readback), "ok": True})
    return result


def _verify_write(result: dict[str, Any]) -> VerifyReport:
    if result.get("blocked"):
        return VerifyReport(False, str(result.get("detail", "blocked")))
    ok = (result.get("ok") is True
          and result.get("readback_matches") is True
          and bool(result.get("path")))
    if result.get("ok") is True and result.get("readback_matches") is not True:
        return VerifyReport(False, f"readback MISMATCH at {result.get('path')}")
    return VerifyReport(ok,
                        f"readback ok ({result.get('bytes')} bytes at {result.get('path')})")


def _summarize_write(result: dict[str, Any]) -> str:
    if result.get("ok") is not True:
        return f"file.write failed: {result.get('detail', '')}"
    return (f"Wrote {result.get('bytes')} bytes to {result.get('path')} "
            f"({result.get('detail', '')})")


def register(reg: ToolRegistry) -> None:
    reg.register(ToolSpec(
        name="file.write",
        description=("Create a new text file inside the workspace. "
                     "Refuses to overwrite; content must be non-empty text."),
        permission_level=1, idempotent=True, privacy_floor=guard.PRIVATE,
        input_schema={"path": "path", "content": "str", "workspace": "str"},
        required={"path", "content"},
        run=_run_write, verify=_verify_write, summarize=_summarize_write,
    ))
